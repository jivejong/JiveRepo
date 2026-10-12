#!/usr/bin/env python3
"""The manual browser check of the web intake page: the real intake process and the real, unchanged bridge, against a THROWAWAY
Mosquitto container and the STUB model (stub_model.py). No model call, no key, no quota; nothing can reach the live broker.

    edge\\.venv\\Scripts\\python.exe intake\\manual_check.py            # then open the printed URL and follow intake\\MANUAL-CHECKLIST.md
    edge\\.venv\\Scripts\\python.exe intake\\manual_check.py --self-test # the same setup, driven over HTTP for a minute, then cleaned up

It starts: a container named fbp-e2e-<name> (default fbp-e2e-manual, host port 18885, the repo's authenticated LAN config and the
committed ACL, throwaway passwords), the bridge writing to a temporary landing folder, and the intake server on 127.0.0.1:8081 with the
stub model (answers follow the text; [stub:...] triggers make it misbehave; each reporter is limited to 4 reports so the daily cap is
quick to reach). It prints the URL and then reports each file that lands. Ctrl+C stops everything and removes the container and the
temporary folder (add --keep to leave the container).

Refuses: the live broker's port (1883), the real intake's HTTP port (8080), any container not named fbp-e2e-<name>.
"""
import argparse
import json
import secrets
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import e2e_report as e2e  # noqa: E402  (its container guard, broker setup, bridge and intake runners)

DEFAULT_CONTAINER = "fbp-e2e-manual"
DEFAULT_BROKER_PORT = 18885
DEFAULT_HTTP_PORT = 8081
REPORTER_CAP = 4                    # per reporter per UTC day, instead of 20, so the 429 path takes five reports to reach
MAX_MINUTES = 180                   # a forgotten run stops itself


def guard_manual_args(container, broker_port, http_port):
    """The checks that keep this run away from the live broker and the real intake. Raises SystemExit."""
    e2e.guard_container(container)
    problems = []
    if broker_port == e2e.LIVE_BROKER_PORT:
        problems.append(f"the broker port {broker_port} is the live broker's")
    if not 1024 <= broker_port <= 65535:
        problems.append("the broker port must be 1024..65535")
    if http_port == 8080:
        problems.append("8080 is the real intake's port")
    if not 1024 <= http_port <= 65535:
        problems.append("the HTTP port must be 1024..65535")
    if broker_port == http_port:
        problems.append("the broker and the HTTP port must differ")
    if problems:
        raise SystemExit("refusing: " + "; ".join(problems))


def make_body(description, reporter):
    now = datetime.now(timezone.utc)
    report_id = e2e.core.new_ulid(int(now.timestamp() * 1000), secrets.randbits(80))
    return json.dumps({"sector_id": "coruscant", "description": description, "report_id": report_id, "reporter_id": reporter,
                       "observed_at": now.strftime("%Y-%m-%dT%H:%M:%S.000Z")}).encode("utf-8"), report_id


def banner(args, work, landing):
    print()
    print("=" * 78)
    print(f"  Page:            http://127.0.0.1:{args.http_port}/")
    print(f"  Broker:          throwaway container {args.container} on 127.0.0.1:{args.broker_port} (the live broker is not involved)")
    print(f"  Model:           the STUB (triggers: {' '.join(e2e.stub_model.TRIGGERS)})")
    print(f"  Per-reporter cap: {REPORTER_CAP} reports per UTC day")
    print(f"  Landing folder:  {landing.dir}")
    print(f"  Intake log:      {work / 'intake.log'}")
    print(f"  Checklist:       {HERE / 'MANUAL-CHECKLIST.md'}")
    print("  Ctrl+C stops everything and cleans up.")
    print("=" * 78)
    sys.stdout.flush()


def self_test(intake, landing, http_port):
    """Drive the running setup over HTTP: the page and its files, the planets, a report that lands, each stub failure, the cap, the
    recent list. Returns the list of failed checks."""
    check = e2e.Checks()
    base = f"http://127.0.0.1:{http_port}"
    get = lambda path: e2e.files_api.http_request("GET", base + path, timeout=30)
    post = lambda body: e2e.files_api.http_request("POST", base + "/api/report", headers={"Content-Type": "application/json"}, data=body, timeout=90)
    reporter = "selftest1"

    status, page = get("/")
    check(status == 200 and b'src="/static/main.js"' in page, "the page is served at /")
    check(all(get(f"/static/{n}")[0] == 200 for n in ("main.js", "app.js", "queue.js", "style.css")), "its scripts and stylesheet are served under /static/")
    status, sectors = get("/api/sectors")
    check(status == 200 and len(json.loads(sectors)) == 60, "the 60 planets are listed")

    started = time.time()
    first, _ = make_body("Saw Darth Vader on the lower levels", reporter)
    status, payload = intake.post_until_not_503(first)
    answer = json.loads(payload) if status == 200 else {}
    check(status == 200 and answer.get("relevance_score") == 0.95, f"a report is filed and scored by the stub (status {status})")
    check(landing.wait_count(1, e2e.BRIDGE_FLUSH_S + 15), f"...and its file lands ({time.time() - started:.1f} s)")
    status, recent = get("/api/reports?sector_id=coruscant")
    check(status == 200 and [r["description"] for r in json.loads(recent)] == ["Saw Darth Vader on the lower levels"], "...and it is in the recent list")

    blocked = post(make_body("something [stub:blocked]", reporter)[0])
    check(blocked[0] == 422 and json.loads(blocked[1]).get("error") == "blocked_by_model", "[stub:blocked] -> 422 blocked_by_model")
    unavailable = post(make_body("something [stub:503]", reporter)[0])
    check(unavailable[0] == 503 and json.loads(unavailable[1]).get("error") == "inference_unavailable", "[stub:503] -> 503 inference_unavailable")
    garbage = post(make_body("something [stub:bad]", reporter)[0])
    check(garbage[0] == 503, "[stub:bad] -> 503")
    html = post(make_body("html please [stub:html]", reporter)[0])
    check(html[0] == 200 and "<img" in json.loads(html[1]).get("inference_rationale", ""), "[stub:html] -> 200 with an HTML rationale")
    weather = post(make_body("The weather on Coruscant was mild", reporter)[0])
    check(weather[0] == 200 and json.loads(weather[1]).get("relevance_score") == 0.04, "a report about the weather is scored near zero")
    filler = post(make_body("a Jedi with a lightsaber", reporter)[0])
    check(filler[0] == 200 and json.loads(filler[1]).get("relevance_score") == 0.7, "a report about a Jedi is scored in between")
    capped = post(make_body("one more", reporter)[0])
    check(capped[0] == 429 and json.loads(capped[1]).get("error") == "reporter_daily_cap",
          f"the {REPORTER_CAP + 1}th accepted-looking report is refused: 429 reporter_daily_cap (refusals do not count towards it)")
    other = post(make_body("a different reporter", "selftest2")[0])
    check(other[0] == 200, "another reporter is not affected by the first one's cap")
    check(e2e.e2e_local.wait_until(lambda: len(landing.lines()) >= 5, e2e.BRIDGE_FLUSH_S + 15),
          "...and the accepted reports land (5 envelopes in all; the bridge may put several of one reporter in one file)")
    return check.failures


def run(args):
    guard_manual_args(args.container, args.broker_port, args.http_port)
    passwords = {"force-bridge": secrets.token_urlsafe(12), "web-intake": secrets.token_urlsafe(12)}
    committed_acl = e2e.ACL_FILE.read_text(encoding="utf-8")
    e2e.create_anonymous_broker(args.container, args.broker_port)          # refuses to touch a container that already exists
    work = Path(tempfile.mkdtemp(prefix="fbp-manual-"))
    intake = bridge = None
    failures = []
    try:
        e2e.configure_auth_broker(args.container, args.broker_port, passwords, committed_acl, work)
        landing = e2e.Landing(work)
        landing.dir.mkdir(parents=True, exist_ok=True)
        bridge_args = SimpleNamespace(host="127.0.0.1", port=args.broker_port, bridge_script=e2e.ROOT / "ingest" / "bridge" / "bridge.py")
        intake = e2e.RunningIntake(work, args.broker_port, passwords["web-intake"], extra=("--per-reporter-cap", str(REPORTER_CAP)),
                                   http_port=args.http_port, varied=True)
        bridge = e2e.start_bridge(bridge_args, landing, work, "force-bridge", passwords["force-bridge"])
        intake.wait_listening()
        banner(args, work, landing)
        if args.self_test:
            failures = self_test(intake, landing, args.http_port)
            print("SELF-TEST PASSED" if not failures else "SELF-TEST FAILED:\n  " + "\n  ".join(failures))
        else:
            deadline, seen = time.time() + MAX_MINUTES * 60, set()
            while time.time() < deadline:
                for path in landing.files():
                    if path not in seen:
                        seen.add(path)
                        print(f"[landed] {landing.rel(path)}   ({len(seen)} file(s) so far)")
                        sys.stdout.flush()
                time.sleep(1.0)
            print(f"stopping: {MAX_MINUTES} minutes are up")
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        for process in (intake, bridge):
            if process is not None:
                process.stop()
                process.proc.wait(timeout=15)
        if args.keep:
            print(f"left container {args.container} running (docker rm -f {args.container} to remove it)")
        else:
            e2e.remove_broker(args.container)
            print(f"removed container {args.container}")
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--container", default=DEFAULT_CONTAINER, help="the throwaway container (must be named fbp-e2e-<name>; default %(default)s)")
    p.add_argument("--broker-port", type=int, default=DEFAULT_BROKER_PORT, help="host port for the throwaway broker (default %(default)s; never 1883)")
    p.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="port for the page (default %(default)s; never 8080)")
    p.add_argument("--self-test", action="store_true", help="drive the running setup over HTTP, report, and clean up")
    p.add_argument("--keep", action="store_true", help="leave the container in place afterwards")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
