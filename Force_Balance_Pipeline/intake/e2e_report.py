#!/usr/bin/env python3
"""End-to-end check of the web intake path (transport C) against a THROWAWAY Mosquitto container: POST /api/report -> the intake
process -> PUBACK -> the real, unchanged bridge (--local-dir) lands a web-<id>-<ULID>.ndjson holding the exact envelope bytes.

The model is a stub inside the intake process (no network, no key), so this run makes no model call. The broker is the repo's own
infra/mosquitto/mosquitto.lan.conf with the committed infra/mosquitto/acl and throwaway passwords. It only ever touches the
container named on --container, which must be called fbp-e2e-<something>; it refuses force-mosquitto.

    edge\\.venv\\Scripts\\python.exe intake\\e2e_report.py --run-all          # create the container, run everything, remove it

--run-all does, in order:
  1. creates the container with the anonymous development config and runs ingest/e2e_local.py's basic, restart and hardkill
     scenarios UNCHANGED against it;
  2. reconfigures the same container as the authenticated LAN broker (lan conf, password file, the committed ACL) and runs
       report       POST -> PUBACK -> the file lands, byte for byte; a repeat is answered from the log with no second model call
       broker_down  the broker stops: 503, nothing recorded, nothing lands; it returns: the same report succeeds, one event_id
  3. NEGATIVE CHECKS: with the ACL wrong (web-intake missing; web-intake allowed a different topic) the report scenario's checks MUST
     fail. A denied publish that looked like success (the silent drop) must never pass. Also shown: under MQTT 3.1.1 the broker
     acknowledges such a denied publish, which is why the intake client speaks MQTT 5.
  4. removes the container.
"""
import argparse
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ingest"))

import intake_core as core  # noqa: E402
import files_api  # noqa: E402
import e2e_local  # noqa: E402  (its bridge runner and waiters; the file is not modified)

CONTAINER_DEFAULT = "fbp-e2e-mosquitto"
CONTAINER_RE = re.compile(r"^fbp-e2e-[a-z0-9-]{1,30}$")
LAN_CONF = ROOT / "infra" / "mosquitto" / "mosquitto.lan.conf"
ANON_CONF = ROOT / "infra" / "mosquitto" / "mosquitto.conf"
ACL_FILE = ROOT / "infra" / "mosquitto" / "acl"
LANDED_RE = re.compile(r"^dt=(\d{4}-\d{2}-\d{2})/hh=(\d{2})/web-([a-z0-9]{4,16})-([0-9A-HJKMNP-TV-Z]{26})\.ndjson$")
STUB_ANSWER = {"relevance_score": 0.95, "midichlorian_ppm": 26400.0, "kyber_resonance": 30.0, "dark_side_activity": 85.0,
               "inference_rationale": "e2e stub: a named dark side figure"}
DESCRIPTION = "Saw a figure in black robes near the eastern spaceport.\nThe air felt cold \u2014 \U0001F311 caf\u00e9"
BRIDGE_FLUSH_S = 3          # the bridge's --max-seconds for these runs (production: 90)
IMAGE = "eclipse-mosquitto:2"


# ---- pure helpers (unit-tested offline) ----------------------------------------------------------------------
def guard_container(name):
    """The only containers this script may create, stop, restart or remove."""
    if name == "force-mosquitto" or not CONTAINER_RE.match(name or ""):
        raise SystemExit(f"refusing container {name!r}: the e2e only touches a throwaway container named fbp-e2e-<name>")
    return name


def wrong_acls(committed):
    """The ACLs the negative checks run with, each derived from the committed one: {label: text}.
    no_intake_user: the web-intake user is missing, so the broker denies its publish. wrong_topic: it may write a different topic."""
    lines = committed.splitlines()
    out = {"no_intake_user": [], "wrong_topic": []}
    skipping = False
    for line in lines:
        if line.strip() == "user web-intake":
            skipping = True
            out["wrong_topic"].append(line)
            continue
        if skipping and line.strip().startswith("topic "):
            out["wrong_topic"].append(line.replace("force/telemetry/web-intake", "force/telemetry/web-intakeX"))
            continue
        if skipping and not line.strip():
            skipping = False
        out["no_intake_user"].append(line)
        out["wrong_topic"].append(line)
    return {label: "\n".join(text) + "\n" for label, text in out.items()}


def expected_line(body, now):
    """The exact bytes the bridge must land for this request, computed independently of the intake server's process (same core
    functions, run here against the same stub answer): the envelope line plus its newline."""
    envelope = core.make_report_envelope(body, sectors=core.load_sectors(), api_key="e2e", model="e2e-model", now=now,
                                         http=lambda *a, **k: (200, stub_answer_body()), sleep=lambda _: None)
    return core.serialize_envelope(envelope) + b"\n"


def stub_answer_body():
    return json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(STUB_ANSWER)}]}}]}).encode()


def make_request(number, now):
    """(body bytes, report_id, reporter) for report number `number`: a plausible observed_at, so event_time is the browser's."""
    report_id = core.new_ulid(int(now.timestamp() * 1000), secrets.randbits(80))
    reporter = f"e2e{number % 10}"
    observed = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = json.dumps({"sector_id": "coruscant", "description": DESCRIPTION, "report_id": report_id, "reporter_id": reporter,
                       "observed_at": observed}).encode("utf-8")
    return body, report_id, reporter


def classify_denied_report(post_status, landed_files):
    """What a report sent through a broker that denies the publish looked like: REFUSED (loud: not 200, nothing landed),
    SILENT_DROP (the dangerous one: answered 200, nothing landed), or DELIVERED (the broker did not deny it after all)."""
    if post_status == 200:
        return "SILENT_DROP" if landed_files == 0 else "DELIVERED"
    return "REFUSED" if landed_files == 0 else "DELIVERED"


# ---- docker (only ever the throwaway container) ----------------------------------------------------------------
def docker(*args, check=True, timeout=180):
    p = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if check and p.returncode:
        raise SystemExit(f"docker {' '.join(args[:2])} failed ({p.returncode}): {(p.stderr or p.stdout).strip()[:400]}")
    return p


def wait_port(port, timeout=40):
    return e2e_local.wait_until(lambda: _can_connect(port), timeout, step=0.3)


def _can_connect(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def create_anonymous_broker(name, port):
    guard_container(name)
    if docker("ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}").stdout.strip():
        raise SystemExit(f"container {name} already exists; remove it first (docker rm -f {name}) or pass another --container")
    docker("create", "--name", name, "-p", f"127.0.0.1:{port}:1883", IMAGE, "mosquitto", "-c", "/mosquitto/config/mosquitto.conf")
    docker("cp", str(ANON_CONF), f"{name}:/mosquitto/config/mosquitto.conf")
    docker("start", name)
    if not wait_port(port):
        raise SystemExit("the throwaway broker did not start:\n" + docker("logs", name, check=False).stdout[-800:])


def configure_auth_broker(name, port, passwords, acl_text, tmp):
    """Turn the throwaway container into the authenticated LAN broker: the repo's lan conf, a password file, and `acl_text`."""
    guard_container(name)
    docker("exec", name, "sh", "-c", "mkdir -p /mosquitto/secrets")
    docker("exec", name, "rm", "-f", "/mosquitto/secrets/passwd")
    for index, (user, password) in enumerate(passwords.items()):
        docker("exec", name, "mosquitto_passwd", "-b", *(["-c"] if index == 0 else []), "/mosquitto/secrets/passwd", user, password)
    set_acl(name, acl_text, tmp, restart=False)
    docker("cp", str(LAN_CONF), f"{name}:/mosquitto/config/mosquitto.conf")
    docker("restart", name)
    if not wait_port(port):
        raise SystemExit("the authenticated broker did not start:\n" + docker("logs", name, check=False).stdout[-1200:])
    time.sleep(1.0)


def set_acl(name, acl_text, tmp, restart=True, port=None):
    guard_container(name)
    path = Path(tmp) / "acl"
    path.write_bytes(acl_text.encode("utf-8"))
    docker("cp", str(path), f"{name}:/mosquitto/secrets/acl")
    docker("exec", name, "sh", "-c", "chown mosquitto:mosquitto /mosquitto/secrets/passwd /mosquitto/secrets/acl && chmod 0600 /mosquitto/secrets/passwd /mosquitto/secrets/acl")
    if restart:
        docker("restart", name)
        if port is not None and not wait_port(port):
            raise SystemExit("the broker did not come back after the ACL change")
        time.sleep(1.0)


def remove_broker(name):
    guard_container(name)
    docker("rm", "-f", name, check=False)


# ---- the processes under test ----------------------------------------------------------------------------------
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class RunningIntake:
    """The real intake process (intake_server.main) with a stub model, on a free port."""

    def __init__(self, work, broker_port, intake_password, extra=()):
        self.port, self.work = free_port(), Path(work)
        self.calls_file = self.work / "model_calls.txt"
        (self.work / ".env.intake").write_text("GEMINI_API_KEY=e2e-not-a-real-key\nINFERENCE_MODEL=e2e-model\n", encoding="utf-8")
        (self.work / ".env.intake.mqtt").write_text(f"INTAKE_MQTT_USERNAME=web-intake\nINTAKE_MQTT_PASSWORD={intake_password}\n", encoding="utf-8")
        cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "--serve-stub", str(self.calls_file), "--",
               "--http-port", str(self.port), "--mqtt-host", "127.0.0.1", "--mqtt-port", str(broker_port),
               "--env-file", str(self.work / ".env.intake"), "--mqtt-env-file", str(self.work / ".env.intake.mqtt"),
               "--report-log", str(self.work / "reports.ndjson"), "--log-file", str(self.work / "intake.log"),
               "--lock-file", str(self.work / "intake.lock"), "--publish-timeout", "5", *extra]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        self.sink, self.seen = queue.Queue(), []
        threading.Thread(target=e2e_local.read_lines, args=(self.proc.stdout, self.sink), daemon=True).start()

    def wait_listening(self, timeout=25):
        if not e2e_local.wait_for(self.sink, "listening on 127.0.0.1", timeout, self.seen):
            raise SystemExit("the intake process did not start:\n" + "\n".join(self.seen))

    def post(self, body, timeout=60):
        return files_api.http_request("POST", f"http://127.0.0.1:{self.port}/api/report", headers={"Content-Type": "application/json"},
                                      data=body, timeout=timeout)

    def post_until_not_503(self, body, timeout=40):
        """The first reports can meet a broker connection that is not up yet (503 broker_unavailable); wait for it like a browser would."""
        deadline = time.time() + timeout
        while True:
            status, payload = self.post(body)
            if status != 503 or time.time() > deadline:
                return status, payload
            time.sleep(1.0)

    def model_calls(self):
        return len(self.calls_file.read_text(encoding="utf-8").splitlines()) if self.calls_file.exists() else 0

    def report_log(self):
        path = self.work / "reports.ndjson"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=15)


def serve_stub(calls_file, argv):
    """--serve-stub: run intake_server.main with a model that answers from STUB_ANSWER and counts its calls in `calls_file`."""
    import intake_server

    def http(method, url, *, headers=None, data=None, timeout=60, opener=None):
        with open(calls_file, "a", encoding="utf-8") as f:
            f.write(url.split("?")[0] + "\n")
        return 200, stub_answer_body()
    return intake_server.main(argv, http=http)


class Landing:
    def __init__(self, work):
        self.dir, self.dead = Path(work) / "landing", Path(work) / "dead_letter.ndjson"

    def files(self):
        return sorted(p for p in self.dir.rglob("*.ndjson")) if self.dir.exists() else []

    def rel(self, path):
        return path.relative_to(self.dir).as_posix()

    def wait_count(self, count, timeout):
        return e2e_local.wait_until(lambda: len(self.files()) >= count, timeout)

    def lines(self):
        return [line for p in self.files() for line in p.read_text(encoding="utf-8").splitlines()]


def start_bridge(args, landing, work, username, password):
    os.environ["BRIDGE_MQTT_USERNAME"], os.environ["BRIDGE_MQTT_PASSWORD"] = username, password       # inherited; the process environment wins over .env.mqtt
    bridge = e2e_local.RunningBridge(args, landing.dir, landing.dead, f"force-bridge-e2e-{secrets.token_hex(3)}",
                                     extra=("--max-seconds", str(BRIDGE_FLUSH_S), "--idle-exit", "0"))
    bridge.wait_subscribed()
    return bridge


# ---- the checks ---------------------------------------------------------------------------------------------------
class Checks:
    def __init__(self):
        self.failures = []

    def __call__(self, ok, message):
        print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
        if not ok:
            self.failures.append(message)
        return ok


def run_report_checks(ctx, check, *, repeat=True):
    """POST one report and verify everything about its journey. Returns the HTTP status and the request. Used by the report
    scenario and, against a broken broker, by the negative checks (which require it to FAIL)."""
    work, landing = ctx.work, ctx.landing
    now = datetime.now(timezone.utc)
    body, report_id, reporter = make_request(1, now)
    want = expected_line(body, now)
    status, payload = ctx.intake.post_until_not_503(body)
    check(status == 200, f"POST /api/report answered 200 after the PUBACK (got {status})")
    landed = landing.wait_count(1, BRIDGE_FLUSH_S + 12)
    check(landed, "the bridge landed a file")
    files = landing.files()
    check(len(files) == 1, f"exactly one file landed (found {len(files)})")
    if files:
        m = LANDED_RE.match(landing.rel(files[0]))
        check(bool(m) and m.group(3) == reporter, f"it is dt=/hh=/web-{reporter}-<ULID>.ndjson ({landing.rel(files[0])})")
        data = files[0].read_bytes()
        check(data == want, "the landed bytes are exactly the envelope line the intake process published")
        env = json.loads(data.decode("utf-8")) if data.strip() else {}
        check(env.get("event_id") == core.event_id_for(report_id) and env.get("source_type") == "report"
              and env.get("source_id") == f"web-{reporter}" and env.get("scan_id") is None and env.get("mode") is None,
              "event_id is the pure function of the report id; source_type report; scan_id and mode null")
        check(env.get("payload", {}).get("description") == DESCRIPTION, "the description (newline, em dash, emoji, accent) survived verbatim")
        if status == 200:
            check(json.loads(payload)["event_id"] == env.get("event_id"), "the answer to the browser names the event that landed")
    check(not landing.dead.exists() or landing.dead.stat().st_size == 0, "nothing was dead-lettered")
    if status == 200:
        log = ctx.intake.report_log()
        check(len(log) == 1 and log[0]["report_id"] == report_id and log[0]["response"] == json.loads(payload),
              "the finished report is in the idempotency log, once, with the answer")
        if repeat:
            calls_before = ctx.intake.model_calls()
            status2, payload2 = ctx.intake.post(body)
            check((status2, payload2) == (status, payload) and ctx.intake.model_calls() == calls_before == 1,
                  f"a repeat is answered from the log: same answer, no second model call ({calls_before} call so far)")
            time.sleep(BRIDGE_FLUSH_S + 3)
            check(len(landing.files()) == 1 and len(landing.lines()) == 1, "...and nothing further was published or landed")
    return status, payload, body, report_id


class Ctx:
    def __init__(self, args, work, passwords):
        self.args, self.work, self.passwords = args, Path(work), passwords
        self.landing, self.intake, self.bridge = Landing(work), None, None

    def __enter__(self):
        self.landing.dir.mkdir(parents=True, exist_ok=True)
        self.intake = RunningIntake(self.work, self.args.port, self.passwords["web-intake"])
        self.bridge = start_bridge(self.args, self.landing, self.work, "force-bridge", self.passwords["force-bridge"])
        self.intake.wait_listening()
        return self

    def __exit__(self, *exc):
        for process in (self.intake, self.bridge):
            if process is not None:
                process.stop()
                process.proc.wait(timeout=15)           # an exited process no longer holds its lock or log files open

    def intake_log(self, *needles):
        path = self.work / "intake.log"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [l[l.find("intake:"):] for l in lines if "intake:" in l and any(n in l for n in needles)]


def scenario_report(args, passwords):
    print("scenario report: POST -> PUBACK -> the bridge lands the exact bytes")
    check = Checks()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work, Ctx(args, work, passwords) as ctx:
        run_report_checks(ctx, check)
        print("  intake log: " + " | ".join(ctx.intake_log("published", "ERROR")[-3:]))
    return check.failures


def scenario_broker_down(args, passwords):
    print("scenario broker_down: the broker stops, then returns")
    check = Checks()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work, Ctx(args, work, passwords) as ctx:
        now = datetime.now(timezone.utc)
        warm_body, warm_id, _ = make_request(2, now)
        check(ctx.intake.post_until_not_503(warm_body)[0] == 200, "a first report is filed while the broker is up")
        ctx.landing.wait_count(1, BRIDGE_FLUSH_S + 12)
        body, report_id, reporter = make_request(3, now + timedelta(seconds=1))
        docker("stop", "-t", "2", args.container)
        try:
            deadline, status, payload, started = time.time() + 40, None, None, time.time()
            while time.time() < deadline:
                status, payload = ctx.intake.post(body)
                if status != 200:
                    break
                time.sleep(0.5)
            check(status in (502, 503), f"with the broker stopped the report is answered 503 (got {status}, after {time.time() - started:.1f} s)")
            code = json.loads(payload).get("error") if status in (502, 503) else None
            check(code in ("broker_unavailable", "publish_timeout", "publish_unavailable"), f"...with a broker error code ({code})")
            check(all(e["report_id"] != report_id for e in ctx.intake.report_log()), "...and nothing was recorded as done")
            check(len(ctx.landing.files()) == 1, "...and nothing new landed")
        finally:
            docker("start", args.container)
        wait_port(args.port)
        status, payload = ctx.intake.post_until_not_503(body, timeout=90)
        check(status == 200, f"once the broker is back the same report is filed (got {status})")
        check(ctx.landing.wait_count(2, BRIDGE_FLUSH_S + 20), "...and it lands")
        ids = [json.loads(l)["event_id"] for l in ctx.landing.lines()]
        mine = [i for i in ids if i == core.event_id_for(report_id)]
        check(len(mine) >= 1 and len(set(ids)) == 2, f"two distinct event_ids landed in total; this report's event_id {len(mine)} time(s) "
                                                      "(more than once would be a retry the broker delivered twice; silver keeps one)")
        check(any(e["report_id"] == report_id for e in ctx.intake.report_log()), "...and it is now in the idempotency log")
    return check.failures


def scenario_negative(args, passwords, label, acl_text, work_root):
    print(f"NEGATIVE CHECK ({label}): the broker's ACL is wrong; the report scenario's checks must FAIL")
    set_acl(args.container, acl_text, work_root, restart=True, port=args.port)
    check = Checks()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work, Ctx(args, work, passwords) as ctx:
        quiet = Checks()
        with _silenced():
            status, payload, body, report_id = run_report_checks(ctx, quiet, repeat=False)
        landed = len(ctx.landing.files())
        verdict = classify_denied_report(status, landed)
        print(f"  the standard checks, run against this broker: {len(quiet.failures)} failed of the ones that apply")
        for failure in quiet.failures:
            print(f"    - {failure}")
        print(f"  classification: POST answered {status}, {landed} file(s) landed -> {verdict}")
        check(len(quiet.failures) > 0, "the report scenario FAILS against the wrong ACL, as it must")
        check(any("landed a file" in f or "answered 200" in f for f in quiet.failures), "...and it fails on delivery, not on something incidental")
        check(verdict != "DELIVERED", "the wrong ACL really did stop delivery (the check is not vacuous)")
        if verdict == "SILENT_DROP":
            print("  (a silent drop: the answer was 200 but nothing landed; the landing check is what caught it)")
        if verdict == "REFUSED":
            print(f"  (loud: the intake process refused it: {json.loads(payload).get('error') if payload else '?'}; MQTT 5 reported the denial)")
    return check.failures


class _silenced:
    def __enter__(self):
        self._stdout, sys.stdout = sys.stdout, open(os.devnull, "w", encoding="utf-8")

    def __exit__(self, *exc):
        sys.stdout.close()
        sys.stdout = self._stdout


def premise_mqtt_311_is_silent(args, passwords):
    """Informational: with the ACL denying web-intake, an MQTT 3.1.1 client's QoS 1 publish is still acknowledged."""
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-premise-311", protocol=mqtt.MQTTv311)
    client.username_pw_set("web-intake", passwords["web-intake"])
    client.connect("127.0.0.1", args.port, keepalive=10)
    client.loop_start()
    try:
        e2e_local.wait_until(client.is_connected, 10)
        info = client.publish(core.TOPIC, b"premise", qos=1)
        info.wait_for_publish(timeout=10)
        acknowledged = info.is_published()
    finally:
        client.loop_stop()
        client.disconnect()
    print(f"  premise: under MQTT 3.1.1 the denied QoS 1 publish was {'ACKNOWLEDGED (silent)' if acknowledged else 'not acknowledged'}"
          " -> that is why the intake client speaks MQTT 5")
    return acknowledged


# ---- driver -----------------------------------------------------------------------------------------------------
def run_existing_scenarios(args):
    print("== existing scenarios, unchanged: ingest/e2e_local.py (basic, restart, hardkill) against the anonymous throwaway broker")
    p = subprocess.run([sys.executable, "-B", str(ROOT / "ingest" / "e2e_local.py"), "--port", str(args.port), "--broker-container", args.container],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    out = p.stdout + p.stderr
    for line in out.splitlines():
        if line.startswith(("[", "scenario", "==", "ALL CHECKS", "FAILED", "  [FAIL")) or "FAIL" in line:
            print("  " + line)
    summary = [l for l in out.splitlines() if "PASS" in l or "FAIL" in l]
    passed, failed = sum("[PASS]" in l for l in summary), sum("[FAIL]" in l for l in summary)
    print(f"  e2e_local exit code {p.returncode}: {passed} PASS, {failed} FAIL")
    return [] if p.returncode == 0 and not failed else [f"ingest/e2e_local.py failed (exit {p.returncode}, {failed} FAIL)"]


def run_all(args):
    guard_container(args.container)
    failures, passwords = [], {"force-bridge": secrets.token_urlsafe(12), "web-intake": secrets.token_urlsafe(12)}
    committed_acl = ACL_FILE.read_text(encoding="utf-8")
    create_anonymous_broker(args.container, args.port)
    try:
        if not args.skip_existing:
            failures += run_existing_scenarios(args)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            print("== reconfiguring the same throwaway container as the authenticated LAN broker (lan conf, password file, the committed ACL)")
            configure_auth_broker(args.container, args.port, passwords, committed_acl, tmp)
            print("  broker log, warnings or errors only: " + (" | ".join(l for l in docker("logs", args.container, check=False).stdout.splitlines()
                                                                   if re.search(r"(?i)error|warning|unable", l)) or "none"))
            for name, scenario in (("report", scenario_report), ("broker_down", scenario_broker_down)):
                if args.only and name not in args.only:
                    continue
                found = scenario(args, passwords)
                failures += [f"{name}: {f}" for f in found]
                print(f"  -> {name}: {'PASS' if not found else 'FAIL'}")
            if not args.only or "negative" in args.only:
                for label, acl_text in wrong_acls(committed_acl).items():
                    found = scenario_negative(args, passwords, label, acl_text, tmp)
                    failures += [f"negative {label}: {f}" for f in found]
                    print(f"  -> negative {label}: {'PASS (the e2e fails on a wrong ACL, as it must)' if not found else 'FAIL'}")
                    if label == "no_intake_user":
                        premise_mqtt_311_is_silent(args, passwords)
                set_acl(args.container, committed_acl, tmp, restart=True, port=args.port)
    finally:
        if not args.keep:
            remove_broker(args.container)
            print(f"== removed container {args.container}")
    print()
    print("ALL CHECKS PASSED" if not failures else "FAILED:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--run-all", action="store_true", help="create the throwaway container, run everything, remove it")
    p.add_argument("--serve-stub", metavar="CALLS_FILE", help="(internal) run the intake process with a stub model, then the intake args after --")
    p.add_argument("--container", default=CONTAINER_DEFAULT, help="the throwaway container (must be named fbp-e2e-<name>)")
    p.add_argument("--port", type=int, default=18883, help="host port, bound on 127.0.0.1 only")
    p.add_argument("--only", action="append", choices=("report", "broker_down", "negative"))
    p.add_argument("--skip-existing", action="store_true", help="do not run ingest/e2e_local.py's scenarios")
    p.add_argument("--keep", action="store_true", help="leave the container in place afterwards")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    if args.serve_stub:
        return serve_stub(args.serve_stub, [a for a in args.rest if a != "--"])
    if not args.run_all:
        p.error("nothing to do: pass --run-all")
    args.host, args.bridge_script = "127.0.0.1", ROOT / "ingest" / "bridge" / "bridge.py"
    return run_all(args)


if __name__ == "__main__":
    sys.exit(main())
