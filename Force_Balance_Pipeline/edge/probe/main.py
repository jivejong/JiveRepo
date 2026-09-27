"""The live probe: `python edge/probe_sim.py --live` (what the systemd unit runs on the Pi) wires the runtime to real time, the real
MQTT client and the state directory. Nothing here is needed by the tests, which build the same objects with fakes.

Configuration: flags for the non-secret settings; the broker address and credentials come from the ENVIRONMENT
(PROBE_MQTT_HOST, PROBE_MQTT_USERNAME, PROBE_MQTT_PASSWORD), which on the Pi systemd fills from /etc/force-probe/probe.env (root-owned,
mode 0600, never in the repository). The state directory holds buffer.db, mode_transitions.jsonl, fault_injection.jsonl,
probe_events.jsonl and last_scan.json.

The mode schedule (simulated DISCONNECTED windows, doc 04) is OFF unless --mode-schedule is given; per doc 07 it stays off until
the Phase 3 checkpoint has passed. --assume-clock-synced skips the NTP check and is for a development machine, never the Pi.
"""
import argparse
import os
import queue
import random
import signal
import sys
import time
from pathlib import Path

from forcesim.probe import SimProbe
from forcesim.sectors import DEFAULT_SEED, load_sectors

from .buffer import DEFAULT_CAP, Buffer
from .clock import ClockGate, timedatectl_synced
from .faults import FaultInjector
from .modes import ModeController, ModeSchedule
from .publisher import MqttPublisher
from .runtime import (DEFAULT_ACK_TIMEOUT, DEFAULT_DRAIN_BATCH, DEFAULT_DRAIN_PAUSE, DEFAULT_INTERVAL,
                      DEFAULT_STEALTH_INTERVAL, Runtime)

CONTROL_TOPIC_PREFIX = "force/control"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--live", action="store_true", help="run the live probe (this entry point)")
    p.add_argument("--state-dir", type=Path, default=Path(os.environ.get("PROBE_STATE_DIR", "probe-state")))
    p.add_argument("--source-id", default="probe-01")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--fault-rate", type=float, default=1.0, help="scales the four fault rates of doc 04 (0 = off) (default %(default)s)")
    p.add_argument("--mode-schedule", action="store_true", help="turn on the simulated DISCONNECTED windows (off by default)")
    p.add_argument("--mode-schedule-seed", type=int, default=None)
    p.add_argument("--buffer-cap", type=int, default=DEFAULT_CAP)
    p.add_argument("--scan-interval", type=int, default=DEFAULT_INTERVAL)
    p.add_argument("--stealth-interval", type=int, default=DEFAULT_STEALTH_INTERVAL)
    p.add_argument("--ack-timeout", type=float, default=DEFAULT_ACK_TIMEOUT)
    p.add_argument("--drain-batch", type=int, default=DEFAULT_DRAIN_BATCH)
    p.add_argument("--drain-pause", type=float, default=DEFAULT_DRAIN_PAUSE)
    p.add_argument("--assume-clock-synced", action="store_true", help="development only: skip the NTP check")
    p.add_argument("--immediate", action="store_true", help="first scan at the boundary at or before now")
    p.add_argument("--sectors", type=Path, default=DEFAULT_SEED)
    p.add_argument("--seed", type=int, default=None, help="deterministic readings (default: real randomness)")
    p.add_argument("--stop-after-scans", type=int, default=0, help="exit after N scans (0 = run until stopped)")
    return p.parse_args(argv)


def build(args, environ=os.environ, mqtt=None, publisher=None, now=time.time):
    """The runtime and its parts from parsed arguments. `publisher` may be given (tests); otherwise the real MQTT client."""
    state = args.state_dir
    state.mkdir(parents=True, exist_ok=True)
    sectors = load_sectors(args.sectors)
    buffer = Buffer(state / "buffer.db", cap=args.buffer_cap)
    schedule = ModeSchedule(seed=args.mode_schedule_seed) if args.mode_schedule else None
    modes = ModeController(state / "mode_transitions.jsonl", schedule, now=now(), backlog=buffer.depth())
    sync = (lambda: True) if args.assume_clock_synced else timedatectl_synced
    gate = ClockGate(sync, state / "last_scan.json")
    rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
    faults = FaultInjector(rng, [s.sector_id for s in sectors], state / "fault_injection.jsonl", rate=args.fault_rate)
    sim = SimProbe(sectors, seed=args.seed, source_id=args.source_id)
    controls = queue.Queue()
    if publisher is None:
        publisher = MqttPublisher(environ.get("PROBE_MQTT_HOST", "localhost"), args.mqtt_port, args.source_id,
                                  environ.get("PROBE_MQTT_USERNAME"), environ.get("PROBE_MQTT_PASSWORD"),
                                  control_topic=f"{CONTROL_TOPIC_PREFIX}/{args.source_id}", on_control=controls.put, mqtt=mqtt)
    runtime = Runtime(sim=sim, sectors=sectors, buffer=buffer, publisher=publisher, gate=gate, modes=modes, faults=faults,
                      state_dir=state, source_id=args.source_id, interval=args.scan_interval,
                      stealth_interval=args.stealth_interval, ack_timeout=args.ack_timeout, drain_batch=args.drain_batch,
                      drain_pause=args.drain_pause, id_rng=rng, immediate=args.immediate)
    return runtime, publisher, controls


def version_line(environ=os.environ):
    path = environ.get("PROBE_VERSION_FILE")
    try:
        return Path(path).read_text(encoding="utf-8").strip() if path else "unknown"
    except OSError:
        return "unknown"


def main(argv=None):
    args = parse_args(argv)
    if args.scan_interval < 1 or args.stealth_interval < 1:
        raise SystemExit("--scan-interval and --stealth-interval must be at least 1 second")
    runtime, publisher, controls = build(args, now=time.time)
    stop = []
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.append(True))
    print(f"probe: {args.source_id} version {version_line()}; state {args.state_dir}; faults x{args.fault_rate:g}; "
          f"mode schedule {'on' if args.mode_schedule else 'off'}; buffer {runtime.buffer.depth()} rows", file=sys.stderr, flush=True)
    if hasattr(publisher, "start"):
        publisher.start()
    try:
        while not stop:
            now = time.time()
            while not controls.empty():
                runtime.on_control(controls.get_nowait(), now)
            runtime.tick(now)
            if args.stop_after_scans and runtime.stats["scans"] + runtime.stats["skipped"] >= args.stop_after_scans:
                break
            time.sleep(0.5)
    finally:
        publisher.close()
        runtime.buffer.close()
    print(f"probe: stopped; {runtime.stats}", file=sys.stderr)
    return 0
