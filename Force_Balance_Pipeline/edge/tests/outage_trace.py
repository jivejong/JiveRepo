"""The simulated outage scenarios of Phase 3, on a fake clock, and a readable trace of what the probe did.

    python edge/tests/outage_trace.py            # the 45-minute outage of the doc 07 checkpoint
    python edge/tests/outage_trace.py long       # a 3-hour outage: the drain in batches of 500, with a live scan interleaved
    python edge/tests/outage_trace.py crash      # a crash in the middle of the drain, then recovery

The tests assert on the same rigs; this file only adds the printing.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import Rig, epoch, iso  # noqa: E402


def outage_45_minutes(detect_after=90.0):
    """12:00 start, the link is cut at 12:31:00 and restored at 13:16:00 (45 minutes), run to 14:00."""
    from _probe_support import FakePublisher
    rig = Rig(start="2026-09-27T12:00:00", publisher=FakePublisher(detect_after=detect_after))
    rig.run_until("2026-09-27T14:00:05", at=[(31 * 60, rig.publisher.cut), (76 * 60, rig.publisher.restore)])
    return rig


def outage_long(restore="2026-09-27T15:14:55", end="2026-09-27T16:00:05"):
    """A long outage that ends 5 seconds before a scan, as the backfill models it: the drain is still running when the live
    scan is taken. Cut 12:31; the default restore is 15:14:55 (10 scans buffered, 600 rows: batches of 500 and 100); restoring at
    15:44:55 buffers 12 scans (720 rows: batches of 500 and 220)."""
    rig = Rig(start="2026-09-27T12:00:00")
    rig.run_until(end, at=[(31 * 60, rig.publisher.cut), (epoch(restore) - epoch("2026-09-27T12:00:00"), rig.publisher.restore)])
    return rig


def crash_in_the_drain():
    """The long outage, but the process dies 3 seconds after the link returns: batch 1 (500 rows) has been acknowledged and deleted,
    batch 2 (220) has not been sent (the 10 s pause). The restart finds 220 rows in buffer.db and drains them."""
    rig = Rig(start="2026-09-27T12:00:00")
    rig.run_until("2026-09-27T15:14:58", at=[(31 * 60, rig.publisher.cut), (3 * 3600 + 14 * 60 + 55, rig.publisher.restore)])
    rig.crash()
    rig.run_until("2026-09-27T16:00:05")
    return rig


def short(rig, text):
    return text.replace("2026-09-27T", "")


def arrival_groups(rig):
    """The published events grouped by (arrival second, mode, scan): (arrival, mode, first_event_time, count)."""
    groups = {}
    for arrival, _, e in rig.received():
        key = (round(arrival, 1), e["mode"], e["scan_id"])
        first = groups.setdefault(key, [e["event_time"], 0])
        first[1] += 1
    return [(k[0], k[1], v[0], v[1]) for k, v in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[1][0]))]


def format_trace(rig):
    out = ["mode transitions (mode_transitions.jsonl):"]
    for t in rig.transitions():
        out.append(f"  {short(rig, t['ts_utc'])}  {t['from']} -> {t['to']}  offline={t['offline']}  reason={t['reason']}  backlog={t['backlog']}")
    out.append("what the tick did:")
    for now, text in list(getattr(rig, "history", [])) + list(rig.runtime.trace):
        out.append(f"  {short(rig, iso(now))}  {text}")
    out.append("what reached the broker (arrival time, mode, first event_time, events), grouped by scan:")
    for arrival, mode, event_time, n in arrival_groups(rig):
        out.append(f"  {short(rig, iso(arrival))}  {mode:<12} scan starting {short(rig, event_time)}  {n} events")
    ids = Counter(key for _, key, _ in rig.received())
    out.append(f"unique events {len(ids)}, delivered more than once: {sum(1 for c in ids.values() if c > 1)}; "
               f"buffer depth at the end: {rig.buffer.depth()}")
    return out


def main(argv=None):
    which = (argv or sys.argv[1:] or ["45"])[0]
    if which == "long":
        rig = outage_long()
    elif which == "crash":
        rig = crash_in_the_drain()
    else:
        rig = outage_45_minutes()
    print("\n".join(format_trace(rig)))
    rig.cleanup()


if __name__ == "__main__":
    main()
