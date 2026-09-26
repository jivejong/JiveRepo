"""probe_sim: scan alignment, one QoS 1 message per event, the sent log, dry run (doc 04). Offline: the
MQTT client is a fake, so neither a broker nor paho-mqtt is needed."""
import io
import json
import sys
import unittest
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probe_sim  # noqa: E402
from forcesim.envelope import check_envelope, decode_ulid_time, to_ndjson_line, ts_ms  # noqa: E402
from forcesim.probe import SimProbe  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402

SECTORS = load_sectors()


class FakeInfo:
    def __init__(self, published=True):
        self._published = published
        self.waited = None

    def wait_for_publish(self, timeout=None):
        self.waited = timeout

    def is_published(self):
        return self._published


class FakeClient:
    def __init__(self, drop=()):
        self.messages = []
        self.drop = set(drop)

    def publish(self, topic, payload, qos=0):
        self.messages.append((topic, payload, qos))
        return FakeInfo(published=(len(self.messages) - 1) not in self.drop)


class Clock:
    """A controllable clock; sleep() advances it."""

    def __init__(self, start):
        self.t = start
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def args_for(**over):
    ns = probe_sim.parse_args(["--immediate", "--scans", "3", "--interval", "4"])
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


class ScheduleTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(probe_sim.boundary_at_or_before(1000.5, 900), 900)
        self.assertEqual(probe_sim.boundary_at_or_before(1800.0, 900), 1800)
        self.assertEqual(probe_sim.boundary_at_or_before(1799.999, 900), 900)

    def test_scan_times_are_one_interval_apart_and_utc(self):
        it = probe_sim.scan_times(1_800_000_000 // 900 * 900, 900)
        first, second, third = next(it), next(it), next(it)
        self.assertEqual(first.tzinfo, timezone.utc)
        self.assertEqual((second - first).total_seconds(), 900)
        self.assertEqual((third - second).total_seconds(), 900)
        self.assertEqual(first.minute % 15, 0)
        self.assertEqual(first.second, 0)


class PublishTests(unittest.TestCase):
    def sweep(self):
        probe = SimProbe(SECTORS, seed=1)
        return probe.sweep(0, probe_sim.datetime.fromtimestamp(1_800_000_000 // 900 * 900, timezone.utc))

    def test_one_qos1_message_per_event_in_order_on_the_probe_topic(self):
        client, envelopes = FakeClient(), self.sweep()
        acked = probe_sim.publish_sweep(client, "force/telemetry/probe-01", envelopes)
        self.assertEqual(acked, 60)
        self.assertEqual(len(client.messages), 60)
        self.assertTrue(all(t == "force/telemetry/probe-01" and q == 1 for t, _, q in client.messages))
        self.assertEqual([json.loads(p) for _, p, _ in client.messages], envelopes)
        self.assertTrue(all("\n" not in p for _, p, _ in client.messages))

    def test_the_sent_log_holds_exactly_the_published_events(self):
        client, envelopes, log = FakeClient(), self.sweep(), io.StringIO()
        probe_sim.publish_sweep(client, "t", envelopes, sent_log=log)
        self.assertEqual(log.getvalue(), "".join(to_ndjson_line(e) for e in envelopes))
        self.assertEqual([p + "\n" for _, p, _ in client.messages], log.getvalue().splitlines(keepends=True))

    def test_unacknowledged_messages_are_not_counted(self):
        client = FakeClient(drop={3, 10})
        self.assertEqual(probe_sim.publish_sweep(client, "t", self.sweep()), 58)

    def test_event_time_is_the_scan_boundary_plus_the_planet_offset(self):
        boundary_ms = 1_800_000_000 // 900 * 900 * 1000
        for j, e in enumerate(self.sweep()):
            self.assertEqual(check_envelope(e), [])
            self.assertEqual(decode_ulid_time(e["scan_id"]), boundary_ms)
            self.assertEqual(decode_ulid_time(e["event_id"]), boundary_ms + 50 * j)


class RunTests(unittest.TestCase):
    def test_dry_run_prints_valid_ndjson_for_each_scan(self):
        clock, out = Clock(1_800_000_001.5), io.StringIO()
        n = probe_sim.run(args_for(dry_run=True, seed=5), None, now=clock.now, sleep=clock.sleep, out=out)
        lines = out.getvalue().splitlines()
        self.assertEqual((n, len(lines)), (180, 180))
        envelopes = [json.loads(l) for l in lines]
        for e in envelopes:
            self.assertEqual(check_envelope(e), [])
            self.assertEqual((e["source_id"], e["mode"], e["is_synthetic"], e["synthetic_ingest_ts"]),
                             ("probe-01", "CONNECTED", False, None))
            self.assertTrue(all(v is not None for v in e["payload"].values()), "CONNECTED readings are complete")
        scans = [envelopes[i:i + 60] for i in range(0, 180, 60)]
        self.assertEqual([len({e["scan_id"] for e in s}) for s in scans], [1, 1, 1])
        self.assertEqual(len({s[0]["scan_id"] for s in scans}), 3)
        # three scans, one interval (4 s) apart, on interval boundaries
        starts = [decode_ulid_time(s[0]["scan_id"]) for s in scans]
        self.assertEqual([b - a for a, b in zip(starts, starts[1:])], [4000, 4000])
        self.assertTrue(all(t % 4000 == 0 for t in starts))

    def test_immediate_scans_at_the_boundary_at_or_before_now_then_waits(self):
        clock = Clock(1_800_000_001.5)
        probe_sim.run(args_for(dry_run=True, scans=2), None, now=clock.now, sleep=clock.sleep, out=io.StringIO())
        self.assertEqual(len(clock.slept), 1)  # no wait for the first scan (already past); one wait for the second
        self.assertAlmostEqual(clock.slept[0], 4 - (1_800_000_001.5 - 1_800_000_000), places=6)

    def test_without_immediate_the_first_scan_waits_for_the_next_boundary(self):
        clock = Clock(1_800_000_001.5)
        probe_sim.run(args_for(dry_run=True, scans=1, immediate=False), None, now=clock.now, sleep=clock.sleep,
                      out=io.StringIO())
        self.assertAlmostEqual(clock.slept[0], 2.5, places=6)

    def test_publishing_run_sends_180_messages_and_logs_them(self):
        clock, client = Clock(1_800_000_000.0), FakeClient()
        with self.subTest("sent log"):
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                log = Path(d) / "sent.ndjson"
                n = probe_sim.run(args_for(sent_log=log, seed=2), client, now=clock.now, sleep=clock.sleep)
                self.assertEqual(n, 180)
                self.assertEqual(len(client.messages), 180)
                self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 180)
                self.assertEqual([json.loads(p) for _, p, _ in client.messages],
                                 [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()])

    def test_same_seed_same_events(self):
        def run_once():
            clock, out = Clock(1_800_000_000.0), io.StringIO()
            probe_sim.run(args_for(dry_run=True, seed=9, scans=2), None, now=clock.now, sleep=clock.sleep, out=out)
            return out.getvalue()
        self.assertEqual(run_once(), run_once())

    def test_zero_interval_is_refused(self):
        with self.assertRaises(SystemExit):
            probe_sim.main(["--interval", "0", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
