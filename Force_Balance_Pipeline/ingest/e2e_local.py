#!/usr/bin/env python3
"""Local end-to-end check for Phase 2, step 4: probe simulator -> local Mosquitto -> collector bridge
(--local-dir). No workspace, no Files API, no OAuth. Needs a broker on --host/--port, for example:

    docker run -d --name force-mosquitto -p 127.0.0.1:1883:1883 ^
        -v "<repo>\\infra\\mosquitto\\mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2

Scenario "basic": start the bridge, wait until it has subscribed, run the probe for N quick scans, wait for the
bridge to land N files, then verify:
  * exactly N files under dt=YYYY-MM-DD/hh=HH/probe-01-<ULID>.ndjson, and nothing else (no temp files)
  * each file has 60 lines, one scan_id, the 60 planets in dim_sector order, and every line is a valid
    doc 02 envelope (forcesim.check_envelope)
  * every live line carries is_synthetic=false and synthetic_ingest_ts=null (both keys, always)
  * the path's dt/hh are the ingest time (the ULID's flush time, inside this run), not the event time
  * the landed lines are byte-for-byte the lines the probe published: same order, no duplicates, event_time
    untouched
  * the bridge's own counters agree, and nothing was dead-lettered

Scenario "restart": the bridge is stopped mid-run (killed, like a crash) after landing scan 1; scan 2 is published
while it is down; with --broker-container the broker is restarted too, to prove the queue is persisted; the
bridge restarts with the same client id and persistent session; scan 3 is published. Every scan must land
exactly once, byte for byte, and the restarted bridge must report "session present: True".

Each run uses its own MQTT client id and removes its session from the broker afterwards. Everything is written
under a temp directory outside the repo.

Usage:  edge/.venv/Scripts/python.exe ingest/e2e_local.py [--scenario basic|restart|all] [--broker-container NAME]
"""
import argparse
import json
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "edge"))

from forcesim.envelope import check_envelope, decode_ulid_time  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402

PATH_RE = re.compile(r"^dt=(\d{4}-\d{2}-\d{2})/hh=(\d{2})/probe-01-([0-9A-HJKMNP-TV-Z]{26})\.ndjson$")
LIVE_FLAGS = '"is_synthetic":false,"synthetic_ingest_ts":null'


def read_lines(stream, sink):
    for line in stream:
        sink.put(line.rstrip("\n"))
    sink.put(None)


def wait_for(sink, needle, timeout, seen):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = sink.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            return False
        seen.append(line)
        if needle in line:
            return True
    return False


def wait_until(condition, timeout, step=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(step)
    return False


def land_count(landing):
    return len([p for p in landing.rglob("*.ndjson")]) if landing.exists() else 0


def broker_up(args):
    try:
        socket.create_connection((args.host, args.port), timeout=2).close()
        return True
    except OSError:
        return False


class RunningBridge:
    def __init__(self, args, landing, dead, client_id, exit_after_files=0):
        cmd = [sys.executable, "-B", str(ROOT / "ingest" / "bridge" / "bridge.py"), "--local-dir", str(landing),
               "--mqtt-host", args.host, "--mqtt-port", str(args.port), "--client-id", client_id,
               "--idle-exit", "90", "--stats-interval", "5", "--dead-letter", str(dead)]
        if exit_after_files:
            cmd += ["--exit-after-files", str(exit_after_files)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.sink, self.seen = queue.Queue(), []
        threading.Thread(target=read_lines, args=(self.proc.stdout, self.sink), daemon=True).start()

    def wait_subscribed(self, timeout=20):
        if not wait_for(self.sink, "subscribed", timeout, self.seen):
            raise SystemExit("the bridge did not subscribe within 20 s:\n" + "\n".join(self.seen))

    def wait_final(self, timeout=60):
        wait_for(self.sink, "final", timeout, self.seen)  # printed after the last file when --exit-after-files is set
        self.proc.wait(timeout=30)

    def kill(self):
        self.proc.terminate()  # a hard kill on Windows: like a crash, nothing is flushed or unsubscribed
        self.proc.wait(timeout=15)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()

    @property
    def summary(self):
        return next((l for l in reversed(self.seen) if l.startswith("bridge: final")), None)


def run_probe(args, sent, scans):
    probe = subprocess.run(
        [sys.executable, "-B", str(ROOT / "edge" / "probe_sim.py"), "--immediate", "--scans", str(scans),
         "--interval", str(args.interval), "--sent-log", str(sent), "--mqtt-host", args.host,
         "--mqtt-port", str(args.port)], capture_output=True, text=True, timeout=120)
    print("  " + probe.stderr.strip().replace("\n", "\n  "))
    if probe.returncode:
        raise SystemExit("probe failed")


def drop_session(args, client_id):
    """Remove the run's persistent session from the broker (connect once with a clean session)."""
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        client.connect(args.host, args.port, keepalive=10)
        client.loop_start()
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
    except Exception as e:  # cleanup only; never fail a check because of it
        print(f"  (could not drop session {client_id}: {e})")


def verify(landing, sent_path, dead_path, scans, started, finished, counters=None, session_present=None):
    failures = []

    def check(ok, message):
        print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
        if not ok:
            failures.append(message)

    files = sorted(p for p in landing.rglob("*") if p.is_file())
    rels = [p.relative_to(landing).as_posix() for p in files]
    check(len(files) == scans and all(r.endswith(".ndjson") for r in rels),
          f"{len(files)} files, all .ndjson, no temp files left (expected {scans})")
    window = set()
    t = started.replace(minute=0, second=0, microsecond=0)
    while t <= finished:
        window.add((f"{t:%Y-%m-%d}", f"{t:%H}"))
        t += timedelta(hours=1)
    sent = sent_path.read_text(encoding="utf-8").splitlines(keepends=True)
    check(len(sent) == 60 * scans, f"the probe published {len(sent)} events ({scans} scans x 60)")
    sent_by_scan = {}
    for line in sent:
        sent_by_scan.setdefault(json.loads(line)["scan_id"], []).append(line)
    planets = [s.sector_id for s in load_sectors()]

    landed_scans, all_lines = [], []
    for rel, path in zip(rels, files):
        m = PATH_RE.match(rel)
        check(m is not None, f"path {rel} is dt=/hh=/probe-01-<ULID>.ndjson")
        if not m:
            continue
        dt, hh, ulid = m.groups()
        flushed = datetime.fromtimestamp(decode_ulid_time(ulid) / 1000, timezone.utc)
        check((dt, hh) == (f"{flushed:%Y-%m-%d}", f"{flushed:%H}") and (dt, hh) in window and
              started - timedelta(seconds=1) <= flushed <= finished + timedelta(seconds=1),
              f"  dt={dt} hh={hh} are the ingest (flush) time {flushed:%H:%M:%S}Z, inside this run")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        all_lines += lines
        envelopes = [json.loads(l) for l in lines]
        problems = [p for e in envelopes for p in check_envelope(e)]
        scan_ids = {e["scan_id"] for e in envelopes}
        check(len(lines) == 60 and not problems and len(scan_ids) == 1 and [e["sector_id"] for e in envelopes] == planets,
              f"  {len(lines)} lines, valid envelopes ({len(problems)} problems), one scan_id, 60 planets in dim_sector order")
        (scan_id,) = scan_ids
        landed_scans.append(scan_id)
        check(lines == sent_by_scan.get(scan_id), "  the lines are byte-for-byte what the probe published for that scan")
    check(sorted(landed_scans) == sorted(sent_by_scan), "every published scan landed exactly once")
    check(len(set(landed_scans)) == scans, f"{scans} distinct scan_ids, so {scans} separate files")
    ids = [json.loads(l)["event_id"] for l in all_lines]
    check(len(ids) == len(set(ids)) == 60 * scans, f"{len(ids)} unique event_ids")
    envelopes = [json.loads(l) for l in all_lines]
    flags_ok = (len(all_lines) == 60 * scans and all(LIVE_FLAGS in l for l in all_lines)
                and all(e["is_synthetic"] is False and e["synthetic_ingest_ts"] is None for e in envelopes)
                and all(LIVE_FLAGS in l for l in sent))
    check(flags_ok, f"all {len(all_lines)} landed lines (and all sent lines) carry is_synthetic=false and "
                    "synthetic_ingest_ts=null, both keys present")
    check(not dead_path.exists() or dead_path.stat().st_size == 0, "nothing was dead-lettered")
    if counters is not None:
        summary, want_files, want_events = counters
        m = re.search(r"files=(\d+) events=(\d+) bytes=(\d+) dead_lettered=(\d+)", summary or "")
        check(bool(m) and (int(m[1]), int(m[2]), int(m[4])) == (want_files, want_events, 0),
              f"bridge counters agree: {m.group(0) if m else 'no final summary line'}")
    if session_present is not None:
        check(session_present, "the restarted bridge reconnected to its persistent session (session present: True)")
    return failures


def scenario_basic(args):
    print("\n=== scenario: basic (3 scans -> 3 files) ===")
    work = Path(tempfile.mkdtemp(prefix="force_bridge_e2e_"))
    landing, sent, dead = work / "landing", work / "sent.ndjson", work / "dead_letter.ndjson"
    print(f"working directory (outside the repo): {work}")
    client_id = f"force-bridge-e2e-{uuid.uuid4().hex[:8]}"
    started = datetime.now(timezone.utc)
    bridge = RunningBridge(args, landing, dead, client_id, exit_after_files=args.scans)
    try:
        bridge.wait_subscribed()
        print("bridge subscribed; starting the probe")
        run_probe(args, sent, args.scans)
        bridge.wait_final()
    finally:
        bridge.stop()
        drop_session(args, client_id)
    finished = datetime.now(timezone.utc)
    print("\n".join("  " + l for l in bridge.seen if l.startswith("bridge:")))
    print("verification:")
    return verify(landing, sent, dead, args.scans, started, finished, counters=(bridge.summary, args.scans, 60 * args.scans))


def scenario_restart(args):
    print("\n=== scenario: restart (bridge stopped mid-run, scan published while it is down) ===")
    work = Path(tempfile.mkdtemp(prefix="force_bridge_e2e_restart_"))
    landing, sent, dead = work / "landing", work / "sent.ndjson", work / "dead_letter.ndjson"
    print(f"working directory (outside the repo): {work}")
    client_id = f"force-bridge-e2e-{uuid.uuid4().hex[:8]}"
    started = datetime.now(timezone.utc)
    first = second = None
    try:
        first = RunningBridge(args, landing, dead, client_id)
        first.wait_subscribed()
        print("bridge A subscribed; publishing scan 1")
        run_probe(args, sent, 1)
        if not wait_until(lambda: land_count(landing) == 1, 30):
            raise SystemExit("scan 1 did not land in 30 s")
        print("scan 1 landed; killing bridge A (buffer is empty, so nothing buffered is lost)")
        first.kill()
        print("publishing scan 2 while the bridge is down")
        run_probe(args, sent, 1)
        if args.broker_container:
            print(f"restarting the broker container {args.broker_container} to prove the queue is persisted")
            subprocess.run(["docker", "restart", args.broker_container], check=True, capture_output=True, timeout=120)
            if not wait_until(lambda: broker_up(args), 30):
                raise SystemExit("the broker did not come back")
            time.sleep(2)
        print("restarting the bridge (same client id, persistent session)")
        second = RunningBridge(args, landing, dead, client_id, exit_after_files=2)
        second.wait_subscribed()
        if not wait_until(lambda: land_count(landing) >= 2, 30):
            raise SystemExit("the scan queued while the bridge was down did not land in 30 s:\n" + "\n".join(second.seen))
        print("scan 2 (queued while down) landed; publishing scan 3")
        run_probe(args, sent, 1)
        second.wait_final()
    finally:
        for b in (first, second):
            if b is not None:
                b.stop()
        drop_session(args, client_id)
    finished = datetime.now(timezone.utc)
    print("\n".join("  " + l for l in (second.seen if second else []) if l.startswith("bridge:")))
    session_present = any("session present: True" in l for l in (second.seen if second else []))
    print("verification:")
    return verify(landing, sent, dead, 3, started, finished, counters=(second.summary if second else None, 2, 120),
                  session_present=session_present)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--scans", type=int, default=3, help="scans for the basic scenario")
    p.add_argument("--interval", type=int, default=4)
    p.add_argument("--scenario", choices=("basic", "restart", "all"), default="all")
    p.add_argument("--broker-container", help="restart this Docker container during the restart scenario")
    args = p.parse_args()
    if not broker_up(args):
        raise SystemExit(f"no broker at {args.host}:{args.port}. Start Mosquitto first (see the docstring).")
    failures = []
    if args.scenario in ("basic", "all"):
        failures += scenario_basic(args)
    if args.scenario in ("restart", "all"):
        failures += scenario_restart(args)
    print(f"\n{'ALL CHECKS PASSED' if not failures else str(len(failures)) + ' CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
