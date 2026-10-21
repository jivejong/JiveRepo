"""The dashboard backend: a read-only JSON API over the local serving Postgres, plus the built dashboard files
(doc 05 "Serving (Phase 7)": "Dashboard backend"; doc 01 D4 amended: the dashboard reads Postgres only; doc 07 Phase 7).

    serving\\.venv\\Scripts\\python.exe -m serving.server [--database NAME] [--port 8081]

Stdlib http.server bound to 127.0.0.1 only. GET and HEAD only; the Host header must be this address (a DNS-rebinding guard). Every API
request opens its own connection, starts with SET TRANSACTION READ ONLY and closes it. Static files come from dashboard/dist and are
served only if their exact relative path was in the allow-list built at start; the request path is never normalised or joined to a
folder. Errors give {"error": ...}; the detail of a 500 goes to logs\\dashboard.log with any URL scrubbed. The security headers follow
intake/intake_server.py (Content-Security-Policy, X-Content-Type-Options, Referrer-Policy) on every response.
"""
import argparse
import hmac
import json
import logging
import math
import re
import secrets
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.seen import SeenStore
from serving import api_queries as q
from serving import config, deploy
from serving.publish import scrub

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8081
DIST_DIR = config.ROOT / "dashboard" / "dist"
LOG_PATH = config.ROOT / "logs" / "dashboard.log"
SECTOR_RE = re.compile(r"[a-z0-9_]{1,64}")
STATUSES = ("open", "decided", "historical")
DEFAULT_LIMIT, MAX_LIMIT = 50, 200
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
       "base-uri 'none'; frame-ancestors 'none'")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json", ".woff2": "font/woff2", ".map": "application/json"}
LOGGER = logging.getLogger("force-dashboard")
CREDENTIAL_KEYS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID")
MAX_DEPLOY_BODY = 8192
DEPLOY_KEYS = {"disturbance_id", "jedi_ids", "starship_id", "rationale"}
SEEN_PROD = config.ROOT / "logs" / "agent_seen_prod.json"


class ApiError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def clean(value):
    """A JSON-ready value: aware datetimes as ISO 8601 UTC with Z, Decimal as float, NaN and infinity as null."""
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def open_session(env, database, connect=config.connect):
    """A connection whose first statement is SET TRANSACTION READ ONLY, so nothing it runs can write."""
    conn = connect(env, database)
    try:
        conn.execute("SET TRANSACTION READ ONLY")
    except BaseException:
        conn.close()
        raise
    return conn


def rows(conn, sql, params=None):
    cur = conn.execute(sql, params)
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def parse_limit(params):
    raw = params.get("limit", [str(DEFAULT_LIMIT)])
    if len(raw) != 1 or not re.fullmatch(r"[0-9]{1,6}", raw[0]) or not 1 <= int(raw[0]) <= MAX_LIMIT:
        raise ApiError(400, "bad_limit")
    return int(raw[0])


def only(params, allowed):
    if set(params) - set(allowed) or any(len(v) != 1 for v in params.values()):
        raise ApiError(400, "bad_query")


# ---- one function per endpoint: (conn, database, params) -> data
def ep_health(conn, database, params):
    return {"ok": True}


def ep_meta(conn, database, params):
    only(params, ())
    latest = rows(conn, q.META)
    return {"publish": latest[0] if latest else None, "database": rows(conn, q.DATABASE)[0]["current_database"],
            "server_time": datetime.now(timezone.utc)}


def ep_galaxy(conn, database, params):
    return rows(conn, q.GALAXY)


def ep_signatures(conn, database, params):
    return rows(conn, q.SIGNATURES)


def ep_source_health(conn, database, params):
    return {"sources": rows(conn, q.SOURCE_HEALTH), "scans": rows(conn, q.SCAN_COMPLETENESS)}


def ep_ingest_lag(conn, database, params):
    return rows(conn, q.INGEST_LAG)


def ep_rejections(conn, database, params):
    return {"rate": rows(conn, q.REJECTION_RATE), "by_reason": rows(conn, q.REJECTS_BY_REASON)}


def ep_incidents(conn, database, params):
    only(params, ("status", "limit"))
    status = params.get("status", [None])[0]
    if status is not None and status not in STATUSES:
        raise ApiError(400, "bad_status")
    return rows(conn, q.INCIDENTS, {"status": status, "limit": parse_limit(params)})


def ep_deployments(conn, database, params):
    only(params, ("limit",))
    return rows(conn, q.DEPLOYMENTS, (parse_limit(params),))


def ep_planet(conn, sector_id):
    planet = rows(conn, q.PLANET, (sector_id,))
    if not planet:
        raise ApiError(404, "unknown_sector")
    return {"sector": planet[0], "baseline": rows(conn, q.PLANET_BANDS, (sector_id,)),
            "readings": rows(conn, q.PLANET_READINGS, {"sector": sector_id}), "incidents": rows(conn, q.PLANET_INCIDENTS, (sector_id,))}


ROUTES = {"/api/health": ep_health, "/api/meta": ep_meta, "/api/galaxy": ep_galaxy, "/api/signatures": ep_signatures,
          "/api/source-health": ep_source_health, "/api/ingest-lag": ep_ingest_lag, "/api/rejections": ep_rejections,
          "/api/incidents": ep_incidents, "/api/deployments": ep_deployments}


def default_deps(env, database, connect):
    """The real dependencies of one deploy: the agent's writing client, the read-only serving client, the agent's Job 1 check and a Postgres
    connection that may write."""
    agent_client, serving_client = deploy.default_clients(env)
    return {"client": agent_client, "serving_client": serving_client, "jobs": deploy.default_job1_check(env),
            "pg_conn": connect(env, database)}


class DeployService:
    """The state of the deploy endpoints: the per-process CSRF token, the one-at-a-time lock and the enabled check (doc 06)."""

    def __init__(self, env, database, connect, *, live_database, deploy_fn, deps_factory, clock, seen_path):
        self.env, self.database, self.connect = env, database, connect
        self.live_database, self.deploy_fn, self.deps_factory = live_database, deploy_fn, deps_factory
        self.clock, self.seen_path = clock, seen_path
        self.token = secrets.token_urlsafe(32)
        self.busy = threading.Lock()

    def disabled_why(self, conn):
        """None when deploy is enabled, else fixture_database, fixture_rows or no_credentials. `conn` is a read-only session."""
        if self.database != self.live_database:
            return "fixture_database"
        if conn.execute(q.FIXTURE_ROWS).fetchone()[0]:
            return "fixture_rows"
        if any(not self.env.get(k) for k in CREDENTIAL_KEYS):
            return "no_credentials"
        return None

    def current_why(self):
        conn = open_session(self.env, self.database, self.connect)
        try:
            return self.disabled_why(conn)
        finally:
            conn.close()

    def options(self, conn, disturbance_id):
        incident = rows(conn, q.OPTIONS_INCIDENT, (disturbance_id,))
        if not incident:
            raise ApiError(404, "not_open")
        now = self.clock()
        return {"incident": incident[0], "jedi": rows(conn, q.OPTIONS_JEDI, {"now": now}),
                "starships": rows(conn, q.OPTIONS_STARSHIPS, {"now": now}),
                "open_deployments": rows(conn, q.OPEN_DEPLOYMENTS, (now,))[0]["n"],
                "agent_first_seen": SeenStore(self.seen_path).load().get(disturbance_id)}

    def run(self, request):
        """Call the deploy service with its real (or injected) dependencies; the writable connection is closed afterwards."""
        deps = self.deps_factory(self.env, self.database, self.connect)
        try:
            return self.deploy_fn(request, env=self.env, now=self.clock(), lock_path=deploy.AGENT_LOCK, sleep=time.sleep,
                                  log=deploy.default_log, **deps)
        finally:
            conn = deps.get("pg_conn")
            if conn is not None:
                conn.close()


def build_allow_list(dist):
    """{relative POSIX path: (file, MIME type)} for every file under dist whose extension has a MIME type."""
    allowed = {}
    dist = Path(dist)
    if dist.is_dir():
        for path in sorted(dist.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in MIME:
                allowed[path.relative_to(dist).as_posix()] = (path, MIME[path.suffix.lower()])
    return allowed


def make_handler(env, database, allowed, connect, service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ForceDashboard/0.1"
        sys_version = ""
        timeout = 30

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, content_type="application/json", cache="no-store", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", cache)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, status, code, extra=None):
            body = json.dumps({"error": code}).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", extra=extra)

        def _host_ok(self):
            port = self.server.server_port
            return (self.headers.get("Host") or "").lower() in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _dispatch(self):
            if not self._host_ok():
                return self._error(403, "bad_host")
            path, _, query = self.path.partition("?")
            if self.command == "POST" and path == "/api/deploy":
                return self._deploy_post()
            if self.command not in ("GET", "HEAD"):
                return self._error(405, "method_not_allowed", {"Allow": "GET, HEAD"})
            if path == "/api" or path.startswith("/api/"):
                return self._api(path, query)
            return self._static(path)

        def _api(self, path, query):
            conn = None
            try:
                params = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=10)
                planet = path[len("/api/planet/"):] if path.startswith("/api/planet/") else None
                if path == "/api/deploy/token":
                    only(params, ())
                    return self._json(200, {"token": service.token})
                if planet is None and path not in ROUTES and path != "/api/deploy/options":
                    raise ApiError(404, "not_found")
                if planet is not None:
                    only(params, ())
                    if not SECTOR_RE.fullmatch(planet):
                        raise ApiError(400, "bad_sector")
                conn = open_session(env, database, connect)
                if path == "/api/deploy/options":
                    only(params, ("disturbance_id",))
                    wanted = params.get("disturbance_id", [""])[0]
                    if not deploy.DISTURBANCE_ID.fullmatch(wanted):
                        raise ApiError(400, "bad_disturbance_id")
                    data = service.options(conn, wanted)
                elif planet is not None:
                    data = ep_planet(conn, planet)
                else:
                    data = ROUTES[path](conn, database, params)
                    if path == "/api/meta":
                        why = service.disabled_why(conn)
                        data["deploy_enabled"], data["deploy_disabled_why"] = why is None, why
                self._json(200, {"data": clean(data)})
            except ApiError as e:
                self._error(e.status, e.code)
            except Exception as e:
                LOGGER.error("%s %s: %s", self.command, path, scrub(f"{type(e).__name__}: {e}"))
                self._error(500, "internal")
            finally:
                if conn is not None:
                    conn.close()

        def _deploy_post(self):
            """POST /api/deploy (doc 06 "Manual deployment path"): Origin, Content-Type, token, enabled, size, body, one at a time."""
            self.close_connection = True                 # an early refusal leaves the body unread, so the connection must not be reused
            port = self.server.server_port
            if self.headers.get("Origin") not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                return self._error(403, "bad_origin")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._error(415, "unsupported_media_type")
            token = (self.headers.get("X-Deploy-Token") or "").encode("utf-8")
            if not hmac.compare_digest(token, service.token.encode("utf-8")):
                return self._error(403, "bad_token")
            try:
                why = service.current_why()
            except Exception as e:
                LOGGER.error("POST /api/deploy: %s", scrub(f"{type(e).__name__}: {e}"))
                return self._error(500, "internal")
            if why:
                return self._json(503, {"error": "deploy_disabled", "why": why})
            length = self.headers.get("Content-Length") or ""
            if not length.isascii() or not length.isdigit():
                return self._error(400, "bad_body")
            if int(length) > MAX_DEPLOY_BODY:
                return self._error(413, "body_too_large")
            raw = self.rfile.read(int(length))
            try:
                request = json.loads(raw.decode("utf-8"))
            except ValueError:
                return self._error(400, "bad_body")
            if not isinstance(request, dict) or set(request) != DEPLOY_KEYS:
                return self._error(400, "bad_body")
            if not service.busy.acquire(blocking=False):
                return self._error(409, "deploy_in_progress")
            try:
                result = service.run(request)
                self._json(200, clean(result))
            except Exception as e:
                LOGGER.error("POST /api/deploy: %s", scrub(f"{type(e).__name__}: {e}"))
                self._error(500, "internal")
            finally:
                service.busy.release()

        def _static(self, path):
            if path == "/":
                path = "/index.html"
                if "index.html" not in allowed:
                    return self._send(503, b"dashboard not built", "text/plain; charset=utf-8")
            if ".." in path or "\\" in path or "%" in path:
                return self._error(400, "bad_path")
            entry = allowed.get(path[1:])
            if entry is None:
                return self._error(404, "not_found")
            file, mime = entry
            try:
                body = file.read_bytes()
            except OSError:
                return self._error(404, "not_found")
            cache = "public, max-age=31536000, immutable" if path.startswith("/assets/") else "no-store"
            self._send(200, body, mime, cache)

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _dispatch

    return Handler


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def make_server(database, port, dist=DIST_DIR, env=None, connect=config.connect, *, deploy_fn=None, deps_factory=None,
                live_database=None, clock=None, seen_path=None):
    env = env or config.load_env()
    service = DeployService(env, database, connect, live_database=live_database or config.PG["dbname"],
                            deploy_fn=deploy_fn or deploy.deploy, deps_factory=deps_factory or default_deps,
                            clock=clock or (lambda: datetime.now(timezone.utc)), seen_path=seen_path or SEEN_PROD)
    server = DashboardServer((BIND_HOST, port), make_handler(env, database, build_allow_list(dist), connect, service))
    server.deploy_service = service
    return server


def configure_logging(path=LOG_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    for old in LOGGER.handlers:
        old.close()
    LOGGER.handlers[:] = [handler]
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serving.server")
    parser.add_argument("--database", default=config.PG["dbname"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    configure_logging()
    try:
        server = make_server(args.database, args.port)
    except (OSError, config.ConfigError) as e:
        print(f"serving.server: cannot start ({type(e).__name__})")
        return 1
    print(f"serving.server: http://{BIND_HOST}:{server.server_port}/  database {args.database}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
