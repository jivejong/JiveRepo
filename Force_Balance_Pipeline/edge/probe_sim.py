#!/usr/bin/env python3
"""Probe simulator, CONNECTED mode only (doc 04): every scan interval it sweeps all 60 planets and
publishes the sweep as 60 doc 02 envelopes over MQTT, one message per event, QoS 1, on
force/telemetry/<source_id>. The readings come from forcesim (the same generator the backfill uses).

Not in Phase 2: the other three modes, fault injection, the SQLite buffer, the control topic (Phase 3).

Scans align to interval boundaries of the UTC clock (900 s: :00, :15, :30, :45), so event_time is the
scan boundary plus a 0-3 s offset in planet order. With --immediate the first scan is the boundary at or
before now (handy for tests); otherwise the probe waits for the next boundary.

Usage:
    python edge/probe_sim.py                                   # live: a scan every 15 minutes
    python edge/probe_sim.py --immediate --scans 3 --interval 4 --sent-log sent.ndjson   # 3 quick scans
    python edge/probe_sim.py --dry-run --scans 1               # print one sweep, no broker needed
"""
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from forcesim.envelope import to_ndjson_line
from forcesim.probe import SimProbe
from forcesim.sectors import DEFAULT_SEED, load_sectors

DEFAULT_INTERVAL = 900
TOPIC_PREFIX = "force/telemetry"
PUBLISH_TIMEOUT_S = 30.0


def boundary_at_or_before(now, interval):
    return int(now // interval * interval)


def scan_times(first_boundary, interval):
    """Aware UTC datetimes of successive scans, one interval apart."""
    n = 0
    while True:
        yield datetime.fromtimestamp(first_boundary + n * interval, timezone.utc)
        n += 1


def publish_sweep(client, topic, envelopes, sent_log=None, timeout=PUBLISH_TIMEOUT_S):
    """Publish each envelope as one QoS 1 message, in order, then wait for the broker's acknowledgements.
    Returns the number acknowledged. The message body is the compact JSON of the envelope (no newline);
    the optional sent log gets the same bytes as NDJSON so a run can be checked against what landed."""
    infos = []
    for envelope in envelopes:
        line = to_ndjson_line(envelope)
        infos.append(client.publish(topic, line.rstrip("\n"), qos=1))
        if sent_log is not None:
            sent_log.write(line)
    if sent_log is not None:
        sent_log.flush()
    acknowledged = 0
    for info in infos:
        info.wait_for_publish(timeout)
        acknowledged += bool(info.is_published())
    return acknowledged


def connect(args):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise SystemExit("paho-mqtt is not installed: pip install -r edge/requirements.txt (in the edge venv)")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.source_id)
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    except OSError as e:
        raise SystemExit(f"cannot reach the MQTT broker at {args.mqtt_host}:{args.mqtt_port} ({e})")
    client.loop_start()
    return client


def run(args, client=None, now=time.time, sleep=time.sleep, out=sys.stdout):
    sectors = load_sectors(args.sectors)
    probe = SimProbe(sectors, seed=args.seed, source_id=args.source_id)
    topic = f"{TOPIC_PREFIX}/{args.source_id}"
    sent_log = open(args.sent_log, "a", encoding="utf-8", newline="\n") if args.sent_log else None
    first = boundary_at_or_before(now(), args.interval)
    if not args.immediate:
        first += args.interval
    published = 0
    try:
        for index, scan_time in enumerate(scan_times(first, args.interval)):
            if args.scans and index >= args.scans:
                break
            wait = scan_time.timestamp() - now()
            if wait > 0:
                sleep(wait)
            envelopes = probe.sweep(index, scan_time, mode="CONNECTED")
            if args.dry_run:
                for envelope in envelopes:
                    out.write(to_ndjson_line(envelope))
                acked = len(envelopes)
            else:
                acked = publish_sweep(client, topic, envelopes, sent_log)
            published += acked
            print(f"probe: scan {index} at {scan_time:%Y-%m-%dT%H:%M:%SZ}: {acked} of {len(envelopes)} events "
                  f"{'printed' if args.dry_run else 'acknowledged'}", file=sys.stderr, flush=True)
    finally:
        if sent_log is not None:
            sent_log.close()
    return published


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--mqtt-host", default="localhost")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--source-id", default="probe-01")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="seconds between scans (default 900)")
    p.add_argument("--scans", type=int, default=0, help="stop after N scans (0 = run until stopped)")
    p.add_argument("--immediate", action="store_true", help="first scan at the boundary at or before now")
    p.add_argument("--seed", type=int, default=None, help="deterministic readings and ids (default: real randomness)")
    p.add_argument("--sectors", type=Path, default=DEFAULT_SEED, help="dim_sector.csv to read the parameters from")
    p.add_argument("--sent-log", type=Path, help="also append every published event here, as NDJSON")
    p.add_argument("--dry-run", action="store_true", help="print the sweep as NDJSON instead of publishing")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.interval < 1:
        raise SystemExit("--interval must be at least 1 second")
    client = None if args.dry_run else connect(args)
    try:
        published = run(args, client)
    except KeyboardInterrupt:
        print("probe: stopped", file=sys.stderr)
        published = 0
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()
    print(f"probe: done, {published} events", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
