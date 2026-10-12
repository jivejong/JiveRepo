#!/usr/bin/env python3
"""The web intake process (docs 02, 04; transport C): a small HTTP server on 127.0.0.1 that takes a free-text report, asks the
model for sensor-equivalent values (intake_core), publishes the report envelope to the broker (intake_mqtt) and answers only after
the broker's PUBACK. The bridge, unchanged, lands what is published.

    POST /api/report   {"sector_id", "description", "report_id", "reporter_id", "observed_at"?}
    GET  /api/sectors  [{"sector_id", "sector_name"}, ...]
    GET  /api/reports?sector_id=ID   the 20 newest finished reports for one planet (from the report log), newest first
    GET  /             the page (intake/web/index.html); its scripts and stylesheet are under /static/

Bound to 127.0.0.1 only; there is no flag to change that. Runs on demand (no scheduled task this phase), with a single-instance lock
and a rotating log under logs/, using the bridge's own lock and log classes.

What each answer means:
  200  published and acknowledged (or an exact repeat of a finished report: the stored answer, no model call, no publish)
  400/413/422  the request is not acceptable (RequestError); 422 blocked_by_model: the model refused the text. Never retry.
  409  the report_id was already used for a different text
  429  a daily cap is reached (per reporter, or global, counted from the report log); Retry-After to the next UTC midnight
  502  the broker refused the publish (e.g. ACL); 503 inference or broker unavailable, with Retry-After. Nothing is recorded as done.

Idempotency and caps use an append-only log of finished reports (logs/intake_reports.ndjson, one fsynced line per report). A report
is written to it only after the PUBACK. If the process dies between the PUBACK and the log write, the browser's retry finds no entry,
asks the model again and publishes again with the SAME event_id (a pure function of the report_id), and silver keeps the first copy.
The values shown to the reporter on that retry may differ from the stored ones; the stored ones are the first publish's.

Only intake_core makes an outbound HTTP call (through files_api.http_request, the project User-Agent). Nothing here logs a request
body, a description, the API key or the MQTT password.
"""
import argparse
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import intake_core as core  # noqa: E402  (also puts ingest/bridge on the path)
import intake_mqtt  # noqa: E402
import files_api  # noqa: E402
import bridge  # noqa: E402  (read-only: its lock and log classes are the patterns this process follows)

LOGGER_NAME = "force-intake"
BIND_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8080
DEFAULT_PER_REPORTER_CAP, DEFAULT_GLOBAL_CAP = 20, 200
DEFAULT_REPORT_LOG = core.ROOT / "logs" / "intake_reports.ndjson"
DEFAULT_LOG_PATH = core.ROOT / "logs" / "intake.log"
DEFAULT_LOCK_PATH = core.ROOT / "logs" / "intake.lock"
REQUEST_TIMEOUT_S = 30                      # a client that stalls mid-request is dropped
WEB_DIR = HERE / "web"
STATIC_NAME_RE = re.compile(r"[a-z0-9_-]+\.(js|css|html)")   # the only file names ever served; matched with fullmatch
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8"}
# Scripts and styles only from this origin, no inline code or handlers, no framing, no <base>, forms only to this origin.
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
RECENT_LIMIT = 20
EXIT_CONFIG, EXIT_ALREADY_RUNNING, EXIT_PORT = 2, bridge.EXIT_ALREADY_RUNNING, 4
UNAVAILABLE_MESSAGE = "The assessment service is not available right now. Try again shortly."
BROKER_MESSAGE = "The report could not be passed on right now. Try again shortly."
INTERNAL_MESSAGE = "Something went wrong on our side. Try again later."


@dataclass
class Response:
    status: int
    body: object
    headers: dict = field(default_factory=dict)


def error_response(status, code, message, retry_after=None):
    headers = {"Retry-After": str(int(retry_after))} if retry_after is not None else {}
    return Response(status, {"error": code, "message": message}, headers)


def fingerprint(sector_id, description):
    return hashlib.sha256(f"{sector_id}\n{description}".encode("utf-8")).hexdigest()


def seconds_until_next_utc_day(now):
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()) + 1)


# ---- the log of finished reports ----------------------------------------------------------------------------
class ReportLog:
    """Append-only NDJSON of finished reports (published and acknowledged), read into memory at start. One entry per report_id:
    {report_id, event_id, source_id, sector_id, accepted_at, fingerprint, description, response}. Every append is fsynced. A torn
    last line (a crash mid-write) is skipped, and the next append starts on a fresh line. The daily caps are counted from it, so
    they survive restarts."""
    REQUIRED = ("report_id", "event_id", "source_id", "sector_id", "accepted_at", "fingerprint", "description", "response")

    def __init__(self, path, log=print):
        self.path, self._log = Path(path), log
        self._lock = threading.Lock()
        self._by_id, self._per_reporter, self._per_day = {}, {}, {}
        self.skipped = 0
        self._needs_newline = False
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        self._needs_newline = bool(raw) and not raw.endswith(b"\n")
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line.decode("utf-8"))
                if not isinstance(entry, dict) or any(k not in entry for k in self.REQUIRED):
                    raise ValueError("missing keys")
                day = str(entry["accepted_at"])[:10]
            except (ValueError, RecursionError):
                self.skipped += 1
                continue
            if entry["report_id"] in self._by_id:
                continue                        # the first finished copy stands
            self._index(entry, day)
        if self.skipped:
            self._log(f"intake: skipped {self.skipped} unreadable line(s) in {self.path.name}")

    def _index(self, entry, day):
        self._by_id[entry["report_id"]] = entry
        self._per_reporter[(day, entry["source_id"])] = self._per_reporter.get((day, entry["source_id"]), 0) + 1
        self._per_day[day] = self._per_day.get(day, 0) + 1

    def get(self, report_id):
        with self._lock:
            return self._by_id.get(report_id)

    def count(self, day, source_id=None):
        with self._lock:
            return self._per_day.get(day, 0) if source_id is None else self._per_reporter.get((day, source_id), 0)

    def recent(self, sector_id, limit=20):
        """Newest first. Used by the page's recent-reports list; the descriptions are the reporters' own text."""
        with self._lock:
            found = [e for e in self._by_id.values() if e["sector_id"] == sector_id]
        return sorted(found, key=lambda e: e["accepted_at"], reverse=True)[:limit]

    def append(self, entry):
        """Write one finished report and fsync it. Raises OSError if it cannot be made durable (nothing is indexed then)."""
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as f:
                if self._needs_newline:
                    f.write(b"\n")
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            self._needs_newline = False
            if entry["report_id"] not in self._by_id:
                self._index(entry, str(entry["accepted_at"])[:10])


# ---- the service ----------------------------------------------------------------------------------------------
class IntakeService:
    """Everything between the HTTP layer and the world, with every dependency injected: the model transport (http), the broker
    (publisher), the finished-report log, the clock. Thread-safe."""

    def __init__(self, *, sectors, api_key, model, publisher, report_log, per_reporter_cap=DEFAULT_PER_REPORTER_CAP,
                 global_cap=DEFAULT_GLOBAL_CAP, thinking_level=core.DEFAULT_THINKING_LEVEL,
                 publish_timeout=intake_mqtt.PUBLISH_TIMEOUT_S, now=None, http=files_api.http_request, clock=time.monotonic,
                 sleep=time.sleep, log=print):
        self.sectors, self.api_key, self.model = sectors, api_key, model
        self.publisher, self.reports = publisher, report_log
        self.per_reporter_cap, self.global_cap = per_reporter_cap, global_cap
        self.thinking_level, self.publish_timeout = thinking_level, publish_timeout
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.http, self.clock, self.sleep, self.log = http, clock, sleep, log
        self._guard = threading.Lock()
        self._id_locks = {}                 # report_id -> [lock, users]: one submission of a report at a time
        self._reserved = {}                 # (day, source_id) -> reports being assessed right now, counted against the caps

    # -- one report at a time per report_id
    @contextmanager
    def _claim(self, report_id):
        with self._guard:
            slot = self._id_locks.setdefault(report_id, [threading.Lock(), 0])
            slot[1] += 1
        slot[0].acquire()
        try:
            yield
        finally:
            slot[0].release()
            with self._guard:
                slot[1] -= 1
                if slot[1] == 0:
                    del self._id_locks[report_id]

    def _reserve(self, day, source_id, now):
        """Count this report against the caps, or return the 429 that says why not. `now` is UTC, like `day`."""
        with self._guard:
            held = sum(n for (d, _), n in self._reserved.items() if d == day)
            mine = self._reserved.get((day, source_id), 0)
            if self.reports.count(day, source_id) + mine >= self.per_reporter_cap:
                return error_response(429, "reporter_daily_cap", "You have filed as many reports as are allowed today. Try again tomorrow.",
                                      seconds_until_next_utc_day(now))
            if self.reports.count(day) + held >= self.global_cap:
                return error_response(429, "daily_cap", "The council has received as many reports as it can handle today. Try again tomorrow.",
                                      seconds_until_next_utc_day(now))
            self._reserved[(day, source_id)] = mine + 1
        return None

    def _release(self, day, source_id):
        with self._guard:
            left = self._reserved.get((day, source_id), 0) - 1
            if left > 0:
                self._reserved[(day, source_id)] = left
            else:
                self._reserved.pop((day, source_id), None)

    # -- POST /api/report
    def handle_report(self, body):
        try:
            request = core.parse_request(body, self.sectors)
        except core.RequestError as e:
            return error_response(e.status, e.code, str(e))
        with self._claim(request.report_id):
            finished = self.reports.get(request.report_id)
            if finished is not None:
                if finished["fingerprint"] != fingerprint(request.sector_id, request.description):
                    return error_response(409, "report_id_reused", "That report id was already used for a different report.")
                return Response(200, finished["response"])
            now = self.now().astimezone(timezone.utc)           # the caps' day is the UTC day, whatever offset the clock carries
            day = now.strftime("%Y-%m-%d")
            refusal = self._reserve(day, request.source_id, now)
            if refusal is not None:
                self.log(f"intake: cap reached ({refusal.body['error']}) for {request.source_id}")
                return refusal
            try:
                return self._assess_and_publish(body, request, now, day)
            finally:
                self._release(day, request.source_id)

    def _assess_and_publish(self, body, request, now, day):
        if not self.publisher.ready():
            self.log("intake: refused a report: the broker is not connected (no model call made)")
            return error_response(503, "broker_unavailable", BROKER_MESSAGE, intake_mqtt.RETRY_AFTER_S["unavailable"])
        try:
            envelope, values = core.assess_report(body, sectors=self.sectors, api_key=self.api_key, model=self.model, now=now,
                                                  thinking_level=self.thinking_level, http=self.http, clock=self.clock,
                                                  sleep=self.sleep)
        except core.RequestError as e:              # the model refused the text (blocked_by_model, 422)
            self.log(f"intake: report refused: {e.code}")
            return error_response(e.status, e.code, str(e))
        except core.InferenceError as e:
            self.log(f"intake: inference failed: {e.kind} ({'retryable' if e.retryable else 'not retryable'})")
            return error_response(503, "inference_unavailable", UNAVAILABLE_MESSAGE, e.retry_after_s)
        payload = core.serialize_envelope(envelope)
        try:
            self.publisher.publish(core.TOPIC, payload, timeout=self.publish_timeout)
        except intake_mqtt.PublishError as e:
            self.log(f"intake: publish failed: {e.kind}")
            status = 502 if e.kind == "refused" else 503
            return error_response(status, "publish_" + e.kind, BROKER_MESSAGE, e.retry_after_s)
        response = core.public_response(envelope)
        entry = {"report_id": request.report_id, "event_id": envelope["event_id"], "source_id": request.source_id,
                 "sector_id": request.sector_id, "accepted_at": core.format_event_time(now.astimezone(timezone.utc)),
                 "fingerprint": fingerprint(request.sector_id, request.description), "description": request.description,
                 "response": response}
        try:
            self.reports.append(entry)
        except OSError as e:
            # The report IS published. Without the entry a retry would be assessed and published again under the same event_id,
            # which silver deduplicates; say so loudly and still answer 200.
            self.log(f"intake: ERROR the report log could not be written ({type(e).__name__}); event_id {envelope['event_id']} is published")
        self.log(f"intake: published {envelope['event_id']} for {request.sector_id} relevance {response['relevance_score']}"
                 + (f" clamped {','.join(values.clamped)}" if values.clamped else ""))
        return Response(200, response)

    # -- GET /api/sectors
    def sectors_listing(self):
        return sorted(({"sector_id": s.sector_id, "sector_name": s.sector_name} for s in self.sectors.values()),
                      key=lambda row: row["sector_name"].lower())

    # -- GET /api/reports?sector_id=
    def recent_reports(self, sector_id):
        """The newest finished reports for one planet, for the page's list. Only what the page shows: never the reporter's id, the
        report_id (the idempotency key) or the fingerprint. The descriptions are other people's text; the page renders them as text."""
        if not isinstance(sector_id, str) or sector_id not in self.sectors:
            return error_response(422, "unknown_sector", "sector_id is not a known planet")
        rows = []
        for entry in self.reports.recent(sector_id, RECENT_LIMIT):
            stored = entry["response"] if isinstance(entry["response"], dict) else {}
            rows.append({"description": entry["description"], "event_time": stored.get("event_time"),
                         "relevance_score": stored.get("relevance_score"), "midichlorian_ppm": stored.get("midichlorian_ppm"),
                         "kyber_resonance": stored.get("kyber_resonance"), "dark_side_activity": stored.get("dark_side_activity"),
                         "inference_rationale": stored.get("inference_rationale"), "event_id": entry["event_id"]})
        return Response(200, rows)


# ---- the page's files -----------------------------------------------------------------------------------------
class StaticSite:
    """The page's files, read once at start from the TOP LEVEL of the web directory. Only names matching STATIC_NAME_RE are
    taken (so not package.json, not the tests/ directory, not a dotfile, not Markdown); index.html is served at "/" and every
    other file at /static/<name>. A request is answered only by an exact match on a path read here: the request path is never
    percent-decoded or joined to a directory, so there is no way to name a file that was not loaded."""

    def __init__(self, web_dir=WEB_DIR):
        self.pages = {}
        web_dir = Path(web_dir)
        try:
            names = sorted(p.name for p in web_dir.iterdir() if p.is_file() and not p.is_symlink())
            for name in names:
                if STATIC_NAME_RE.fullmatch(name):
                    route = "/" if name == "index.html" else f"/static/{name}"
                    self.pages[route] = (STATIC_TYPES[Path(name).suffix], (web_dir / name).read_bytes())
        except OSError as e:
            raise core.ConfigError(f"the page files cannot be read from {web_dir.name} ({type(e).__name__})") from None
        if "/" not in self.pages:
            raise core.ConfigError(f"{web_dir.name}/index.html is missing")


# ---- the HTTP layer -----------------------------------------------------------------------------------------
class IntakeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False         # on Windows SO_REUSEADDR would let a second process share the port


def make_handler(service, logger, site=None):
    site = site or StaticSite(WEB_DIR)
    routes = {"/api/sectors": ("GET",), "/api/reports": ("GET",), "/api/report": ("POST",), **{path: ("GET",) for path in site.pages}}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ForceIntake/0.1"
        sys_version = ""
        timeout = REQUEST_TIMEOUT_S

        # -- plumbing
        def _origins(self):
            port = self.server.server_port
            return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

        def _send(self, response, content_type="application/json; charset=utf-8", raw=None, extra=None):
            data = raw if raw is not None else json.dumps(response.body, ensure_ascii=False).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in {**response.headers, **(extra or {})}.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _error(self, status, code, message, **headers):
            self._send(Response(status, {"error": code, "message": message}, headers))

        def log_request(self, code="-", size="-"):
            logger.info("%s %s -> %s", self.command, self.path.split("?", 1)[0], code)

        def log_error(self, fmt, *args):
            logger.warning("http: " + fmt, *args)

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        # -- routing
        def _route(self):
            return self.path.split("?", 1)[0]

        def _dispatch(self, method):
            path = self._route()
            if not self._host_ok():
                return self._error(403, "bad_host", "This service answers only on its own address.")
            if path not in routes:
                return self._error(404, "not_found", "No such page.")
            if method not in routes[path]:
                return self._error(405, "method_not_allowed", "That method is not allowed here.", Allow=", ".join(routes[path]))
            if path in site.pages:
                content_type, data = site.pages[path]
                return self._send(Response(200, None), content_type, data,
                                  {"Content-Security-Policy": PAGE_CSP, "Referrer-Policy": "no-referrer"})
            if path == "/api/sectors":
                return self._send(Response(200, service.sectors_listing()))
            if path == "/api/reports":
                return self._get_reports()
            return self._post_report()

        def _get_reports(self):
            try:
                params = urllib.parse.parse_qs(self.path.partition("?")[2], keep_blank_values=True, max_num_fields=5)
            except ValueError:
                params = {}
            if set(params) != {"sector_id"} or len(params["sector_id"]) != 1:
                return self._error(400, "bad_query", "Ask for one planet: /api/reports?sector_id=ID.")
            self._send(service.recent_reports(params["sector_id"][0]))

        def _post_report(self):
            origin = self.headers.get("Origin")
            if origin is not None and origin not in self._origins():
                return self._error(403, "bad_origin", "Reports are accepted only from this service's own page.")
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return self._error(415, "unsupported_media_type", "Send the report as application/json.")
            if self.headers.get("Transfer-Encoding"):
                return self._error(411, "length_required", "Send a Content-Length; chunked bodies are not accepted.")
            length = self.headers.get("Content-Length")
            if length is None:
                return self._error(411, "length_required", "Send a Content-Length.")
            if not (length.isascii() and length.isdigit()):
                return self._error(400, "bad_length", "The Content-Length is not a number.")
            length = int(length)
            if length > core.MAX_BODY_BYTES:
                self.close_connection = True
                return self._error(413, "body_too_large", f"The request body is over {core.MAX_BODY_BYTES} bytes.")
            body = self.rfile.read(length)
            if len(body) != length:
                return self._error(400, "short_body", "The request body ended early.")
            try:
                response = service.handle_report(body)
            except Exception:
                logger.error("unexpected error handling a report:\n%s", traceback.format_exc())
                response = error_response(500, "internal_error", INTERNAL_MESSAGE)
            self._send(response)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _other(self):
            self._dispatch(self.command)

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _other

    return Handler


def make_server(service, port, logger, site=None):
    return IntakeHTTPServer((BIND_HOST, port), make_handler(service, logger, site))


# ---- process plumbing (the bridge's patterns) -----------------------------------------------------------------
def configure_logging(log_path, stdout=None, stderr=None):
    """The intake logger: the bridge's rotating, rotation-safe file handler (UTC timestamps), and a console stream only when there
    is a console. Handlers are replaced on every call."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = bridge._SafeRotatingFileHandler(path, maxBytes=bridge.LOG_MAX_BYTES, backupCount=bridge.LOG_BACKUPS, encoding="utf-8")
    file_handler.setFormatter(bridge._UtcFormatter("%(asctime)s %(levelname)s %(message)s"))
    file_handler.report_stream = stderr
    logger.addHandler(file_handler)
    if stdout is not None:
        console = bridge._QuietStreamHandler(stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        console.report_stream = stderr
        logger.addHandler(console)
    return logger


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="port on 127.0.0.1 (the address cannot be changed; default %(default)s)")
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--env-file", type=Path, default=core.DEFAULT_ENV_FILE, help="GEMINI_API_KEY and INFERENCE_MODEL (default %(default)s)")
    p.add_argument("--mqtt-env-file", type=Path, default=intake_mqtt.MQTT_ENV_FILE_DEFAULT,
                   help="INTAKE_MQTT_USERNAME and INTAKE_MQTT_PASSWORD (default %(default)s)")
    p.add_argument("--report-log", type=Path, default=DEFAULT_REPORT_LOG, help="the append-only log of finished reports (default %(default)s)")
    p.add_argument("--log-file", type=Path, default=DEFAULT_LOG_PATH)
    p.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_PATH,
                   help="single-instance lock; a second intake process exits with code %d" % EXIT_ALREADY_RUNNING)
    p.add_argument("--per-reporter-cap", type=int, default=DEFAULT_PER_REPORTER_CAP, help="reports per reporter per UTC day (default %(default)s)")
    p.add_argument("--global-cap", type=int, default=DEFAULT_GLOBAL_CAP, help="reports per UTC day, all reporters (default %(default)s)")
    p.add_argument("--publish-timeout", type=float, default=intake_mqtt.PUBLISH_TIMEOUT_S, help="seconds to wait for the broker's PUBACK (default %(default)s)")
    p.add_argument("--thinking-level", default=core.DEFAULT_THINKING_LEVEL, choices=core.THINKING_LEVELS)
    args = p.parse_args(argv)
    if min(args.per_reporter_cap, args.global_cap) < 1 or args.publish_timeout <= 0 or not 0 <= args.http_port <= 65535:
        p.error("the caps must be at least 1, the publish timeout positive and the port 0..65535")
    return args


def main(argv=None, *, process_wide=True, publisher=None, http=files_api.http_request, run=None, environ=None, now=None):
    """process_wide=False (tests) leaves sys.stdout, sys.stderr and the exception hooks alone. `publisher` injects a broker
    stand-in; `run(server)` replaces serve_forever (tests)."""
    args = parse_args(argv)
    environ = os.environ if environ is None else environ
    logger = configure_logging(args.log_file, stdout=sys.stdout, stderr=sys.stderr)
    if process_wide:
        bridge.capture_process_output(logger)
    lock = bridge.InstanceLock(args.lock_file)
    try:
        lock.acquire()
    except bridge.AlreadyRunning as e:
        logger.error(f"intake: {e}; this one exits with code {EXIT_ALREADY_RUNNING}")
        return EXIT_ALREADY_RUNNING
    owned_publisher = None
    try:
        try:
            api_key, model = core.intake_settings(environ, args.env_file)
            sectors = core.load_sectors()
            site = StaticSite(WEB_DIR)
            if publisher is None:
                username, password = intake_mqtt.mqtt_credentials(environ, args.mqtt_env_file)
                publisher = owned_publisher = intake_mqtt.MqttPublisher(args.mqtt_host, args.mqtt_port, username, password,
                                                                         log=logger.info)
        except core.ConfigError as e:
            logger.error(f"intake: configuration problem: {e}")
            return EXIT_CONFIG
        try:
            report_log = ReportLog(args.report_log, log=logger.info)
        except OSError as e:
            logger.error(f"intake: cannot read the report log {args.report_log} ({type(e).__name__})")
            return EXIT_CONFIG
        service = IntakeService(sectors=sectors, api_key=api_key, model=model, publisher=publisher,
                                report_log=report_log, per_reporter_cap=args.per_reporter_cap,
                                global_cap=args.global_cap, thinking_level=args.thinking_level, publish_timeout=args.publish_timeout,
                                now=now, http=http, log=logger.info)
        try:
            server = make_server(service, args.http_port, logger, site)
        except OSError as e:
            logger.error(f"intake: cannot listen on {BIND_HOST}:{args.http_port} ({type(e).__name__})")
            return EXIT_PORT
        if owned_publisher is not None:
            owned_publisher.start()
        logger.info(f"intake: listening on {BIND_HOST}:{server.server_port}; model {model}; broker {args.mqtt_host}:{args.mqtt_port}; "
                    f"caps {args.per_reporter_cap} per reporter and {args.global_cap} global per UTC day; log {args.log_file}")
        try:
            (run or (lambda s: s.serve_forever(poll_interval=0.5)))(server)
        except KeyboardInterrupt:
            logger.info("intake: stopping")
        finally:
            server.server_close()
        return 0
    finally:
        if owned_publisher is not None:
            owned_publisher.close()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
