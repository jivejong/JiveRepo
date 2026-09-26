#!/usr/bin/env python3
"""Collector bridge (docs 02, 04): MQTT subscribe -> buffer -> flush NDJSON to the landing zone.

Responsibilities implemented here (Phase 2):
  * subscribe to force/telemetry/# with a fixed client id, a persistent session (clean_session=False) and
    QoS 1, so the broker queues messages while the bridge is down or restarting
  * validate envelope STRUCTURE only, never payload contents; structural failures go to a local
    dead-letter file
  * accumulate and flush NDJSON to dt=YYYY-MM-DD/hh=HH/{source_id}-{ulid}.ndjson, either into a local
    directory (--local-dir) or, in workspace mode, a UC volume through the Files API
  * console counters (buffer depth, flush count, last flush); the HTTP health endpoint is Phase 7
Not implemented yet: POST /api/report (Phase 5).

What it must never do (doc 04): overwrite event_time, reorder events, or deduplicate. Lines are the
bytes that were published; the only exception is a payload that contains a raw newline, which is
re-serialised compactly with the same values so that one line is one event.

Flush conditions (doc 02): 4 MB accumulated; 90 s since the first buffered event; or a complete scan_id
(all 60 planets) has arrived. The third normally fires; a complete scan becomes its own file.

Uploads use upload.upload_file (live mode): the path and its ULID are chosen once per batch and reused on
every retry, and the upload never overwrites. A 409 after an attempt with an unknown outcome means that
attempt landed; a 409 with no unknown attempt is a real collision and the batch is re-keyed. (The backfill
uses the same core in deterministic mode, where any 409 means "already landed".) Events buffered but not yet
flushed when the bridge dies are lost (doc 02: acceptable; the probe's own buffer is the durability layer).

The path prefix is ingest wall-clock time at flush, not event time (doc 02). Filenames use the events'
source_id, so a flush containing several sources produces one file per source, each in arrival order.

Configuration is command-line flags plus environment variables (docs 04, 05). Workspace mode needs
DATABRICKS_HOST, BRIDGE_DATABRICKS_CLIENT_ID and BRIDGE_DATABRICKS_CLIENT_SECRET, read from the process
environment or from a gitignored .env.bridge that holds ONLY those three keys (the environment wins). The
bridge never uses DATABRICKS_TOKEN, and refuses an env file that contains it.

Usage:
    python ingest/bridge/bridge.py --local-dir DIR                              # local landing directory
    python ingest/bridge/bridge.py                                              # workspace, reads .env.bridge
    python ingest/bridge/bridge.py --env-file path/to/.env.bridge --volume-path /Volumes/force/raw/telemetry
"""
import argparse
import json
import os
import queue
import re
import secrets
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from files_api import CONFLICT, DEFAULT_SCOPE, FilesApiUploader, TokenProvider, normalise_scope
from upload import ALREADY_LANDED, UPLOADED, upload_file

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEAD_LETTER = REPO_ROOT / "logs" / "bridge_dead_letter.ndjson"
ENV_FILE_DEFAULT = REPO_ROOT / ".env.bridge"
BRIDGE_ENV_KEYS = ("DATABRICKS_HOST", "BRIDGE_DATABRICKS_CLIENT_ID", "BRIDGE_DATABRICKS_CLIENT_SECRET")

DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_SECONDS = 90
DEFAULT_SCAN_SIZE = 60
DEFAULT_TOPIC = "force/telemetry/#"
DEFAULT_CLIENT_ID = "force-bridge"
DEFAULT_VOLUME_PATH = "/Volumes/force/raw/telemetry"

ENVELOPE_KEYS = ("event_id", "source_id", "source_type", "schema_version", "event_time", "mode",
                 "scan_id", "sector_id", "is_synthetic", "synthetic_ingest_ts", "payload")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(timestamp_ms, randomness=None):
    """A ULID (48-bit ms timestamp + 80 random bits, 26 Crockford base32 characters)."""
    if randomness is None:
        randomness = int.from_bytes(secrets.token_bytes(10), "big")
    value = (timestamp_ms << 80) | randomness
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def _parse_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def check_structure(obj):
    """Problems with an envelope's STRUCTURE (doc 02); [] means it may be written. Payload contents,
    sector membership, mode values and ranges are NOT checked: those are silver's job."""
    if not isinstance(obj, dict):
        return ["envelope is not a JSON object"]
    problems = []
    missing = [k for k in ENVELOPE_KEYS if k not in obj]
    if missing:
        return [f"missing keys {missing}"]
    if not isinstance(obj["event_id"], str) or not obj["event_id"]:
        problems.append("event_id is not a non-empty string")
    if not isinstance(obj["source_id"], str) or not _SOURCE_ID_RE.match(obj["source_id"]):
        problems.append("source_id is not a safe identifier (it names the file)")
    if obj["source_type"] not in ("probe", "report"):
        problems.append("source_type must be probe or report")
    if isinstance(obj["schema_version"], bool) or not isinstance(obj["schema_version"], int):
        problems.append("schema_version is not an integer")
    try:
        if not isinstance(obj["event_time"], str):
            raise ValueError
        _parse_iso(obj["event_time"])
    except ValueError:
        problems.append("event_time is not an ISO 8601 timestamp string")
    for key in ("mode", "scan_id"):
        if obj[key] is not None and not isinstance(obj[key], str):
            problems.append(f"{key} is neither a string nor null")
    if not isinstance(obj["sector_id"], str) or not obj["sector_id"]:
        problems.append("sector_id is not a non-empty string")
    if not isinstance(obj["is_synthetic"], bool):
        problems.append("is_synthetic is not a boolean")
    if obj["synthetic_ingest_ts"] is not None and not isinstance(obj["synthetic_ingest_ts"], str):
        problems.append("synthetic_ingest_ts is neither a string nor null")
    if not isinstance(obj["payload"], dict):
        problems.append("payload is not a JSON object")
    return problems


class Batch:
    """The lines destined for one file. The relative path is chosen once and reused by every retry."""

    def __init__(self, source_id, lines, now):
        self.source_id = source_id
        self.lines = lines
        moment = datetime.fromtimestamp(now, timezone.utc)
        self._dt, self._hh, self._ms = f"{moment:%Y-%m-%d}", f"{moment:%H}", int(now * 1000)
        self.ulid = new_ulid(self._ms)

    @property
    def relpath(self):
        return f"dt={self._dt}/hh={self._hh}/{self.source_id}-{self.ulid}.ndjson"

    def rekey(self):
        """A new ULID after a real name collision; returns the new path (same dt/hh directory)."""
        self.ulid = new_ulid(self._ms)
        return self.relpath

    @property
    def data(self):
        return b"".join(self.lines)


class LocalDirUploader:
    """Stands in for the Files API with the same contract: never overwrites (409 if the path exists),
    and a file appears whole or not at all."""

    def __init__(self, root):
        self.root = Path(root)

    def put(self, relpath, data):
        path = self.root.joinpath(*relpath.split("/"))
        if path.exists():
            return CONFLICT
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return 201


class Bridge:
    """Buffering, flush rules and upload. Synchronous and thread-safe; handle_message() and tick() return
    the batches that are ready, and upload() sends one. Time, sleep and randomness are injectable."""

    def __init__(self, uploader, *, dead_letter_path=DEFAULT_DEAD_LETTER, max_bytes=DEFAULT_MAX_BYTES,
                 max_seconds=DEFAULT_MAX_SECONDS, scan_size=DEFAULT_SCAN_SIZE, clock=time.time,
                 sleep=time.sleep, max_attempts=5, backoff=(1, 2, 4, 8, 16), log=print):
        self.uploader = uploader
        self.dead_letter_path = Path(dead_letter_path)
        self.max_bytes, self.max_seconds, self.scan_size = max_bytes, max_seconds, scan_size
        self.clock, self.sleep, self.max_attempts, self.backoff, self.log = clock, sleep, max_attempts, backoff, log
        self._buffer = []       # (arrival, source_id, scan_id, sector_id, line bytes), in arrival order
        self._bytes = 0
        self._scans = {}        # scan_id -> set of sector_ids seen
        self._lock = threading.Lock()
        self.stats = {"received": 0, "dead_lettered": 0, "files_uploaded": 0, "events_written": 0,
                      "bytes_written": 0, "retries": 0, "landed_before_retry": 0, "rekeyed": 0,
                      "failed_batches": 0, "last_flush_utc": None}

    # ---- receiving -------------------------------------------------------------------------------
    def handle_message(self, payload):
        """One MQTT message. Returns the batches that are now ready to upload (possibly none)."""
        now = self.clock()
        with self._lock:
            self.stats["received"] += 1
            try:
                obj = json.loads(payload)
            except ValueError:
                self._dead_letter(payload, "not JSON", now)
                return []
            problems = check_structure(obj)
            if problems:
                self._dead_letter(payload, "structure: " + "; ".join(problems), now)
                return []
            text = payload.rstrip(b"\r\n")
            if b"\n" in text or b"\r" in text:  # one line must be one event: same values, compact form
                text = json.dumps(obj, separators=(",", ":")).encode()
            line = text + b"\n"
            self._buffer.append((now, obj["source_id"], obj["scan_id"], obj["sector_id"], line))
            self._bytes += len(line)
            scan_id = obj["scan_id"]
            if scan_id is not None:
                seen = self._scans.setdefault(scan_id, set())
                seen.add(obj["sector_id"])
                if len(seen) >= self.scan_size:
                    return self._take(lambda item: item[2] == scan_id, now)
            if self._bytes >= self.max_bytes:
                return self._take(lambda item: True, now)
            return []

    def tick(self):
        """Called about once a second: flush everything once the oldest buffered event is max_seconds old."""
        now = self.clock()
        with self._lock:
            if self._buffer and now - self._buffer[0][0] >= self.max_seconds:
                return self._take(lambda item: True, now)
            return []

    def drain(self):
        """Everything still buffered (a clean shutdown)."""
        with self._lock:
            return self._take(lambda item: True, self.clock())

    def depth(self):
        with self._lock:
            return len(self._buffer)

    def _take(self, wanted, now):
        taken = [item for item in self._buffer if wanted(item)]
        self._buffer = [item for item in self._buffer if not wanted(item)]
        self._bytes = sum(len(item[4]) for item in self._buffer)
        remaining_scans = {item[2] for item in self._buffer if item[2] is not None}
        self._scans = {s: ids for s, ids in self._scans.items() if s in remaining_scans}
        by_source = {}
        for _, source_id, _, _, line in taken:
            by_source.setdefault(source_id, []).append(line)  # arrival order kept within each file
        return [Batch(source_id, lines, now) for source_id, lines in by_source.items()]

    # ---- uploading -------------------------------------------------------------------------------
    def upload(self, batch):
        """Upload one batch (live semantics, see upload.py). Returns True when the batch is in the landing
        zone; on permanent failure the lines go to the dead-letter file."""
        result = upload_file(self.uploader, batch.relpath, batch.data, deterministic=False, rekey=batch.rekey,
                             max_attempts=self.max_attempts, backoff=self.backoff, sleep=self.sleep, log=self.log)
        with self._lock:
            self.stats["retries"] += result.retries
            self.stats["rekeyed"] += result.rekeys
            self.stats["landed_before_retry"] += int(result.landed_before_retry)
        if result.outcome in (UPLOADED, ALREADY_LANDED):
            self._uploaded(batch)
            return True
        return self._fail(batch, result.reason)

    def _uploaded(self, batch):
        with self._lock:
            self.stats["files_uploaded"] += 1
            self.stats["events_written"] += len(batch.lines)
            self.stats["bytes_written"] += len(batch.data)
            self.stats["last_flush_utc"] = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _fail(self, batch, reason):
        with self._lock:
            self.stats["failed_batches"] += 1
            now = self.clock()
            for line in batch.lines:
                self._dead_letter(line.rstrip(b"\r\n"), f"upload_failed: {reason}", now)
        self.log(f"bridge: gave up on {batch.relpath} ({reason}); {len(batch.lines)} events written to the dead-letter file")
        return False

    def _dead_letter(self, raw, reason, now):
        self.stats["dead_lettered"] += 1
        record = {"reason": reason, "received_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                  "raw": raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)}
        self.dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
        with self.dead_letter_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record) + "\n")

    def summary(self):
        s = self.stats
        return (f"buffer_depth={self.depth()} received={s['received']} files={s['files_uploaded']} "
                f"events={s['events_written']} bytes={s['bytes_written']} dead_lettered={s['dead_lettered']} "
                f"retries={s['retries']} failed_batches={s['failed_batches']} last_flush={s['last_flush_utc']}")


# ---- credentials --------------------------------------------------------------------------------------
def read_env_file(path):
    """The bridge's env file: KEY=VALUE lines, # comments, optional quotes. It may hold ONLY the three
    bridge keys. DATABRICKS_TOKEN (the dbt personal access token) is refused outright. Error messages
    name the file and line, never a value."""
    path = Path(path)
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path.name} line {number}: expected KEY=VALUE")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key == "DATABRICKS_TOKEN":
            raise SystemExit(f"{path.name} line {number}: DATABRICKS_TOKEN is your dbt personal access token and "
                             "never belongs in the bridge's env file (doc 05). Remove it.")
        if key not in BRIDGE_ENV_KEYS:
            raise SystemExit(f"{path.name} line {number}: {key} is not allowed; this file holds only "
                             + ", ".join(BRIDGE_ENV_KEYS))
        if key in values:
            raise SystemExit(f"{path.name} line {number}: {key} is set twice")
        values[key] = value
    return values


def build_uploader(args, environ=os.environ, default_env_file=ENV_FILE_DEFAULT):
    if args.local_dir:
        return LocalDirUploader(args.local_dir)  # local mode never reads credentials or env files
    env = dict(environ)
    env_file = Path(args.env_file) if args.env_file else Path(default_env_file)
    if env_file.exists():
        for key, value in read_env_file(env_file).items():
            if value and not env.get(key):  # the process environment wins over the file
                env[key] = value
    elif args.env_file:
        raise SystemExit(f"--env-file {args.env_file} does not exist")
    missing = [k for k in BRIDGE_ENV_KEYS if not env.get(k)]
    if missing:
        raise SystemExit("workspace mode needs " + ", ".join(missing) + f" in the environment or in {env_file.name} "
                         "(copy .env.bridge.example). The bridge never uses DATABRICKS_TOKEN (doc 05). For a "
                         "local run use --local-dir.")
    try:
        scope = normalise_scope(args.oauth_scope)
    except ValueError as e:
        raise SystemExit(f"--oauth-scope: {e}")
    tokens = TokenProvider(env["DATABRICKS_HOST"], env["BRIDGE_DATABRICKS_CLIENT_ID"],
                           env["BRIDGE_DATABRICKS_CLIENT_SECRET"], scope=scope)
    return FilesApiUploader(env["DATABRICKS_HOST"], args.volume_path, tokens)


# ---- command line -------------------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--local-dir", type=Path, help="write files under this directory instead of the volume")
    p.add_argument("--volume-path", default=DEFAULT_VOLUME_PATH,
                   help="workspace mode: the UC volume landing path (default %(default)s)")
    p.add_argument("--oauth-scope", default=DEFAULT_SCOPE,
                   help="workspace mode: the scope requested in the OAuth token request; space-separated for several. "
                        "The force-bridge secret is scoped to the Files API, scope 'files'; use 'all-apis' only with "
                        "an unscoped secret (default %(default)s)")
    p.add_argument("--env-file", type=Path, help="workspace mode: the bridge's credentials file (default .env.bridge "
                                                 "at the repo root, if it exists)")
    p.add_argument("--mqtt-host", default="localhost")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--topic", default=DEFAULT_TOPIC)
    p.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                   help="fixed MQTT client id; with a persistent session the broker queues while the bridge is "
                        "down (default %(default)s)")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    p.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    p.add_argument("--scan-size", type=int, default=DEFAULT_SCAN_SIZE, help="events in a complete scan (default %(default)s)")
    p.add_argument("--dead-letter", type=Path, default=DEFAULT_DEAD_LETTER)
    p.add_argument("--stats-interval", type=float, default=30.0, help="seconds between console counters")
    p.add_argument("--exit-after-files", type=int, default=0, help="exit after uploading N files (0 = run until stopped)")
    p.add_argument("--idle-exit", type=float, default=0.0, help="exit after S seconds without a message (0 = never)")
    return p.parse_args(argv)


def build_client(mqtt, args):
    """The MQTT client: a fixed id and clean_session=False, so the broker keeps the session (and queues QoS 1
    messages for it) while the bridge is away."""
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.client_id, clean_session=False)


def make_on_connect(topic):
    """Subscribe at QoS 1 on every connect (a persistent session keeps it, and re-subscribing is harmless)."""
    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            print(f"bridge: MQTT connect refused: {reason_code}", flush=True)
            return
        print(f"bridge: connected (session present: {bool(getattr(flags, 'session_present', False))})", flush=True)
        client.subscribe(topic, qos=1)
    return on_connect


def run(bridge, args):
    """Wire the bridge to MQTT. Uploads happen on a worker thread so a slow upload never blocks the
    MQTT network loop (and its keepalive)."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise SystemExit("paho-mqtt is not installed: pip install -r edge/requirements.txt (in the edge venv)")
    work = queue.Queue()
    stop = threading.Event()
    last_message = [time.time()]

    def worker():
        while True:
            batch = work.get()
            if batch is None:
                return
            bridge.upload(batch)
            if args.exit_after_files and bridge.stats["files_uploaded"] >= args.exit_after_files:
                stop.set()

    uploader_thread = threading.Thread(target=worker, name="uploader", daemon=True)
    uploader_thread.start()

    client = build_client(mqtt, args)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        print(f"bridge: subscribed to {args.topic} (QoS 1, client id {args.client_id})", flush=True)

    def on_message(client, userdata, message):
        last_message[0] = time.time()
        for batch in bridge.handle_message(message.payload):
            work.put(batch)

    client.on_connect, client.on_subscribe, client.on_message = make_on_connect(args.topic), on_subscribe, on_message
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    except OSError as e:
        raise SystemExit(f"cannot reach the MQTT broker at {args.mqtt_host}:{args.mqtt_port} ({e})")
    client.loop_start()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    next_stats = time.time() + args.stats_interval
    try:
        while not stop.is_set():
            time.sleep(0.25)
            for batch in bridge.tick():
                work.put(batch)
            if args.idle_exit and time.time() - last_message[0] > args.idle_exit:
                print(f"bridge: idle for {args.idle_exit:g}s, exiting", flush=True)
                break
            if time.time() >= next_stats:
                print("bridge: " + bridge.summary(), flush=True)
                next_stats = time.time() + args.stats_interval
    finally:
        client.loop_stop()
        client.disconnect()
        for batch in bridge.drain():
            work.put(batch)
        work.put(None)
        uploader_thread.join(timeout=120)
    print("bridge: final " + bridge.summary(), flush=True)
    return 0


def main(argv=None):
    args = parse_args(argv)
    uploader = build_uploader(args)
    bridge = Bridge(uploader, dead_letter_path=args.dead_letter, max_bytes=args.max_bytes,
                    max_seconds=args.max_seconds, scan_size=args.scan_size)
    mode = f"local directory {args.local_dir}" if args.local_dir else f"workspace volume {args.volume_path}"
    print(f"bridge: writing to {mode}; flush at {args.max_bytes} bytes, {args.max_seconds:g} s, or a complete "
          f"scan of {args.scan_size}", flush=True)
    return run(bridge, args)


if __name__ == "__main__":
    sys.exit(main())
