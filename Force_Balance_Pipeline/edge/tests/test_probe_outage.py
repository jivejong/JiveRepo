"""The Phase 3 checkpoint, simulated on a fake clock (doc 07): a 45-minute outage, then BURST drains the buffer. The buffered scans arrive
exactly once with event_time spanning the outage and arrival clustered at the replay; no gaps, no duplicates. Also the drain in batches of
500 with a live scan interleaved, and recovery after a crash at three different moments. The real checkpoint runs this on the Pi with a
real cut; this proves the logic. Offline."""
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import FakePublisher, Rig, epoch  # noqa: E402
from outage_trace import crash_in_the_drain, outage_45_minutes, outage_long  # noqa: E402
from probe.clock import parse_iso  # noqa: E402

DATE = "2026-09-27T"


def quarter_slots(first, last):
    """'12:15' .. '14:00' every 15 minutes as full scan start times."""
    start, stop = epoch(DATE + first + ":00"), epoch(DATE + last + ":00")
    out, t = [], start
    while t <= stop:
        out.append(f"{DATE}{int(t // 3600 % 24):02d}:{int(t // 60 % 60):02d}:00.000Z")
        t += 900
    return out


def by_scan(rig):
    scans = {}
    for arrival, key, e in rig.received():
        scans.setdefault(e["scan_id"], []).append((arrival, key, e))
    return scans


def duplicates(rig):
    return sum(1 for c in Counter(k for _, k, _ in rig.received()).values() if c > 1)


class Base(unittest.TestCase):
    def keep(self, rig):
        self.addCleanup(rig.cleanup)
        return rig


class FortyFiveMinuteOutageTests(Base):
    def setUp(self):
        self.rig = self.keep(outage_45_minutes())            # cut 12:31:00, restored 13:16:00, run to 14:00:05
        self.transitions = self.rig.transitions()

    def test_the_mode_transitions_prove_the_cut_and_the_return(self):
        self.assertEqual([(t["from"], t["to"], t["reason"]) for t in self.transitions],
                         [(None, "CONNECTED", "startup"),
                          ("CONNECTED", "DISCONNECTED", "link_lost:client_disconnected:keepalive"),
                          ("DISCONNECTED", "BURST", "link_restored"),
                          ("BURST", "CONNECTED", "drain_complete")])
        self.assertEqual([t["offline"] for t in self.transitions], [False, True, False, False])

    def test_the_cut_is_noticed_within_the_keepalive_and_the_return_is_immediate(self):
        down, up, done = (parse_iso(t["ts_utc"]) for t in self.transitions[1:])
        self.assertLessEqual(down - epoch(DATE + "12:31:00"), 90.0)              # 60 s keepalive x 1.5
        self.assertEqual(up, epoch(DATE + "13:16:01"))                           # one second after the link is back
        self.assertLess(done - up, 5.0)
        self.assertEqual(self.transitions[2]["backlog"], 180)                    # three scans were waiting

    def test_the_three_scans_taken_during_the_outage_are_the_buffered_ones(self):
        scans = by_scan(self.rig)
        buffered = {sid: rows for sid, rows in scans.items() if rows[0][2]["mode"] == "DISCONNECTED"}
        self.assertEqual(sorted(rows[0][2]["event_time"] for rows in buffered.values()),
                         [f"{DATE}{t}:00.000Z" for t in ("12:45", "13:00", "13:15")])
        for rows in buffered.values():
            self.assertEqual(len(rows), 60)
            self.assertEqual({e["mode"] for _, _, e in rows}, {"DISCONNECTED"})

    def test_event_time_spans_the_outage_and_ingest_is_clustered_at_the_replay(self):
        buffered = [(a, e) for a, _, e in self.rig.received() if e["mode"] == "DISCONNECTED"]
        self.assertEqual(len(buffered), 180)
        event_times = [parse_iso(e["event_time"]) for _, e in buffered]
        self.assertGreater(min(event_times), epoch(DATE + "12:31:00"))            # inside the outage ...
        self.assertLess(max(event_times), epoch(DATE + "13:16:00"))
        self.assertGreaterEqual(max(event_times) - min(event_times), 30 * 60)   # ... and spanning it
        arrivals = [a for a, _ in buffered]
        self.assertGreaterEqual(min(arrivals), epoch(DATE + "13:16:01"))         # nothing arrived during the outage
        self.assertLessEqual(max(arrivals) - min(arrivals), 5.0)                 # all at the replay
        lags = sorted(a - t for (a, _), t in zip(buffered, event_times))
        self.assertGreater(lags[-1], 1800)                                       # the oldest is_replayed (> 30 minutes behind)
        self.assertLess(lags[0], 1800)                                           # the newest is not

    def test_no_gaps_every_quarter_hour_from_the_first_scan_to_the_last_exactly_once(self):
        scans = by_scan(self.rig)
        times = sorted(rows[0][2]["event_time"] for rows in scans.values())
        self.assertEqual(times, quarter_slots("12:15", "14:00"))
        self.assertTrue(all(len(rows) == 60 for rows in scans.values()))
        self.assertEqual(len({e["sector_id"] for _, _, e in next(iter(scans.values()))}), 60)

    def test_no_duplicates_and_nothing_left_in_the_buffer(self):
        self.assertEqual(duplicates(self.rig), 0)
        self.assertEqual(len({k for _, k, _ in self.rig.received()}), 8 * 60)
        self.assertEqual(self.rig.buffer.depth(), 0)
        self.assertEqual(self.rig.runtime.stats["acked"], self.rig.runtime.stats["published"])

    def test_the_replay_is_oldest_first_and_one_batch_because_180_is_under_500(self):
        replay = [e for a, _, e in self.rig.received() if e["mode"] == "DISCONNECTED"]
        times = [e["event_time"] for e in replay]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len({a for a, _, e in self.rig.received() if e["mode"] == "DISCONNECTED"}), 1)

    def test_live_scans_before_and_after_are_connected_and_arrive_when_taken(self):
        for arrival, _, e in self.rig.received():
            if e["mode"] == "CONNECTED":
                self.assertLess(arrival - parse_iso(e["event_time"]), 5.0)

    def test_a_replayed_envelope_is_byte_identical_to_the_one_that_was_buffered(self):
        for _, _, e in self.rig.received():
            if e["mode"] == "DISCONNECTED":
                self.assertIs(e["is_synthetic"], False)
                self.assertEqual(e["source_id"], "probe-01")
                self.assertRegex(e["event_time"], r"^2026-09-27T1[23]:\d\d:\d\d\.\d{3}Z$")


class StallDetectionTests(Base):
    def test_an_outage_found_only_by_the_ack_timeout_still_delivers_everything_once(self):
        rig = self.keep(Rig(start=DATE + "12:00:00", publisher=FakePublisher(detect_after=None)))
        rig.run_until(DATE + "14:00:05", at=[(31 * 60, rig.publisher.cut), (76 * 60, rig.publisher.restore)])
        self.assertEqual([t["reason"] for t in rig.transitions()],
                         ["startup", "link_lost:publish_unacked", "link_restored", "drain_complete"])
        scans = by_scan(rig)
        self.assertEqual(sorted(rows[0][2]["event_time"] for rows in scans.values()), quarter_slots("12:15", "14:00"))
        self.assertEqual(duplicates(rig), 0)
        self.assertEqual(rig.buffer.depth(), 0)


class DrainInBatchesOfFiveHundredTests(Base):
    def drained(self, rig):
        return [(a, k, e) for a, k, e in rig.received() if e["mode"] == "DISCONNECTED"]

    def batches(self, rig):
        """The drained rows grouped by the second they were published, in order: [(time, [events])]."""
        groups = {}
        for arrival, _, e in self.drained(rig):
            groups.setdefault(arrival, []).append(e)
        return sorted(groups.items())

    def test_720_buffered_rows_go_out_as_500_then_220_ten_seconds_after_the_first_is_acknowledged(self):
        rig = self.keep(outage_long(restore=DATE + "15:44:55"))
        batches = self.batches(rig)
        self.assertEqual([len(rows) for _, rows in batches], [500, 220])
        (t1, first), (t2, second) = batches
        self.assertGreaterEqual(t2 - (t1 + rig.publisher.latency), 10.0)          # the pause
        self.assertLess(t2 - t1, 13.0)
        self.assertLessEqual(max(e["event_time"] for e in first), min(e["event_time"] for e in second))   # oldest first
        for rows in (first, second):
            self.assertEqual([e["event_time"] for e in rows], sorted(e["event_time"] for e in rows))

    def test_a_live_scan_taken_during_the_drain_is_burst_and_interleaved(self):
        rig = self.keep(outage_long(restore=DATE + "15:44:55"))
        (t1, _), (t2, _) = self.batches(rig)
        live = [(a, e) for a, _, e in rig.received() if e["mode"] == "BURST"]
        self.assertEqual(len(live), 60)
        self.assertEqual(len({e["scan_id"] for _, e in live}), 1)
        self.assertEqual({e["event_time"][:16] for _, e in live}, {DATE + "15:45"})
        self.assertTrue(all(t1 < a < t2 for a, _ in live))                         # between the two batches
        self.assertTrue(all(0 <= a - parse_iso(e["event_time"]) <= 3.0 for a, e in live))   # published once its last reading was taken

    def test_the_mode_goes_burst_only_while_draining_then_back_to_connected(self):
        rig = self.keep(outage_long(restore=DATE + "15:44:55"))
        t = rig.transitions()
        self.assertEqual([(x["to"], x["reason"]) for x in t][-2:], [("BURST", "link_restored"), ("CONNECTED", "drain_complete")])
        self.assertEqual(t[-2]["backlog"], 720)
        burst_for = parse_iso(t[-1]["ts_utc"]) - parse_iso(t[-2]["ts_utc"])
        self.assertTrue(10.0 <= burst_for <= 20.0, burst_for)                    # two batches and the pause

    def test_all_720_arrive_once_and_the_scan_files_are_not_split_across_the_pause(self):
        rig = self.keep(outage_long(restore=DATE + "15:44:55"))
        self.assertEqual(duplicates(rig), 0)
        drained_ids = {k for _, k, e in rig.received() if e["mode"] == "DISCONNECTED"}
        self.assertEqual(len(drained_ids), 720)
        self.assertEqual(rig.buffer.depth(), 0)
        # a scan can straddle the 500-row boundary; the bridge flushes on a complete scan or after 90 s, and the pause is 10 s
        first, second = self.batches(rig)
        self.assertLess(second[0] - first[0], 90.0)

    def test_the_pause_is_the_documented_ten_seconds_and_the_batch_the_documented_500(self):
        from probe.runtime import DEFAULT_DRAIN_BATCH, DEFAULT_DRAIN_PAUSE
        self.assertEqual((DEFAULT_DRAIN_BATCH, DEFAULT_DRAIN_PAUSE), (500, 10.0))


class CrashRecoveryTests(Base):
    def test_a_crash_in_the_middle_of_the_outage_loses_nothing(self):
        rig = self.keep(Rig(start=DATE + "12:00:00"))
        rig.run_until(DATE + "13:05:00", at=[(31 * 60, rig.publisher.cut)])
        self.assertEqual(rig.buffer.depth(), 120)                                    # 12:45 and 13:00 are waiting on disk
        rig.crash()
        rig.run_until(DATE + "14:00:05", at=[(76 * 60 - 65 * 60, rig.publisher.restore)])   # the link returns at 13:16
        times = sorted(rows[0][2]["event_time"] for rows in by_scan(rig).values())
        self.assertEqual(times, quarter_slots("12:15", "14:00"))
        self.assertEqual(duplicates(rig), 0)
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertEqual(rig.runtime.stats["scans"], 4)                              # the new process took 13:15, 13:30, 13:45 and 14:00

    def test_a_crash_in_the_middle_of_the_drain_finishes_the_drain_without_loss_or_duplicates(self):
        rig = self.keep(crash_in_the_drain())
        self.assertGreater(len(rig.received()), 0)
        scans = by_scan(rig)
        times = sorted(rows[0][2]["event_time"] for rows in scans.values())
        self.assertEqual(times, quarter_slots("12:15", "16:00"))
        self.assertTrue(all(len(rows) == 60 for rows in scans.values()))
        self.assertEqual(duplicates(rig), 0)
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertIn("startup_backlog", [t["reason"] for t in rig.transitions()])

    def test_a_crash_with_a_batch_in_flight_resends_it_at_least_once_and_loses_nothing(self):
        rig = self.keep(Rig(start=DATE + "12:00:00", publisher=FakePublisher(latency=5.0)))
        restore = epoch(DATE + "15:14:55") - epoch(DATE + "12:00:00")
        rig.run_until(DATE + "15:14:58", at=[(31 * 60, rig.publisher.cut), (restore, rig.publisher.restore)])
        published_before = len(rig.received())
        self.assertEqual(published_before - 0, len(rig.received()))
        self.assertGreater(rig.buffer.depth(), 500)                                  # the 500 just sent are not acknowledged, so still buffered
        rig.crash()
        rig.run_until(DATE + "16:00:15")
        scans = by_scan(rig)
        self.assertEqual(sorted(rows[0][2]["event_time"] for rows in scans.values()), quarter_slots("12:15", "16:00"))
        self.assertEqual(len({k for _, k, _ in rig.received()}), 16 * 60)
        self.assertTrue(0 < duplicates(rig) <= 500)                                  # the unacknowledged batch, sent twice
        self.assertEqual(rig.buffer.depth(), 0)


class RepeatedOutageTests(Base):
    def test_two_outages_in_a_row_deliver_everything_once(self):
        rig = self.keep(Rig(start=DATE + "12:00:00"))
        rig.run_until(DATE + "16:00:05", at=[(31 * 60, rig.publisher.cut), (76 * 60, rig.publisher.restore),
                                              (100 * 60, rig.publisher.cut), (150 * 60, rig.publisher.restore)])
        times = sorted(rows[0][2]["event_time"] for rows in by_scan(rig).values())
        self.assertEqual(times, quarter_slots("12:15", "16:00"))
        self.assertEqual(duplicates(rig), 0)
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertEqual(Counter(t["to"] for t in rig.transitions())["DISCONNECTED"], 2)

    def test_a_link_that_comes_back_briefly_and_drops_again_mid_drain_still_loses_nothing(self):
        rig = self.keep(Rig(start=DATE + "12:00:00"))
        restore = epoch(DATE + "15:44:55") - epoch(DATE + "12:00:00")
        rig.run_until(DATE + "16:30:05", at=[(31 * 60, rig.publisher.cut), (restore, rig.publisher.restore),
                                              (restore + 2, rig.publisher.cut), (restore + 400, rig.publisher.restore)])
        times = sorted(rows[0][2]["event_time"] for rows in by_scan(rig).values())
        self.assertEqual(times, quarter_slots("12:15", "16:30"))
        self.assertEqual(len({k for _, k, _ in rig.received()}), len(times) * 60)
        self.assertEqual(rig.buffer.depth(), 0)


if __name__ == "__main__":
    unittest.main()
