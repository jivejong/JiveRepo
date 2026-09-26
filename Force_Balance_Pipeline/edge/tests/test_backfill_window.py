"""The backfill window and its overlap refusal (docs 04, 07): the window ends at the last 15-minute UTC boundary
before the earliest live event, and the generator refuses anything that would put a synthetic reading at or after it.
Offline."""
import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forcesim import window as W  # noqa: E402
from forcesim.probe import SimProbe  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
# The earliest live event in bronze: the first event of the 14:27Z scan on 2026-09-26 (the live checkpoint run).
LIVE = datetime(2026, 9, 26, 14, 27, 0, 0, tzinfo=UTC)


def at(h, m, s=0, ms=0, day=26):
    return datetime(2026, 9, day, h, m, s, ms * 1000, tzinfo=UTC)


class BoundaryTests(unittest.TestCase):
    def test_last_boundary_before(self):
        self.assertEqual(W.last_boundary_before(LIVE), at(14, 15))
        self.assertEqual(W.last_boundary_before(at(14, 30, 0, 1)), at(14, 30))      # a millisecond after a boundary
        self.assertEqual(W.last_boundary_before(at(14, 15, 0, 1)), at(14, 15))
        self.assertEqual(W.last_boundary_before(at(14, 44, 59, 999)), at(14, 30))

    def test_strictly_before_so_a_moment_on_a_boundary_steps_back_one(self):
        self.assertEqual(W.last_boundary_before(at(14, 30)), at(14, 15))
        self.assertEqual(W.last_boundary_before(at(0, 0)), at(23, 45, day=25))       # across midnight

    def test_time_zones_are_normalised_and_naive_times_refused(self):
        plus_two = timezone(timedelta(hours=2))
        self.assertEqual(W.last_boundary_before(LIVE.astimezone(plus_two)), at(14, 15))
        with self.assertRaises(W.OverlapRefused):
            W.last_boundary_before(datetime(2026, 9, 26, 14, 27))


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.w = W.make_window(LIVE)

    def test_the_window_for_the_real_live_data(self):
        self.assertEqual(self.w.end, at(14, 15))
        self.assertEqual(self.w.start, datetime(2026, 6, 28, 14, 15, tzinfo=UTC))   # 90 days earlier
        self.assertEqual(self.w.last_scan, at(14, 0))
        self.assertEqual(self.w.last_event_time, at(14, 0, 2, 950))
        self.assertLess(self.w.last_event_time, LIVE)

    def test_it_holds_exactly_8640_scans_on_15_minute_boundaries_all_before_the_live_event(self):
        scans = self.w.scan_times()
        self.assertEqual(len(scans), 8640)
        self.assertEqual(scans[0], self.w.start)
        self.assertEqual(scans[-1], self.w.last_scan)
        self.assertEqual({(b - a) for a, b in zip(scans, scans[1:])}, {timedelta(minutes=15)})
        self.assertTrue(all(t.minute % 15 == 0 and t.second == 0 and t.microsecond == 0 for t in scans))
        self.assertLess(scans[-1] + W.MAX_READING_OFFSET, LIVE)

    def test_an_earlier_explicit_end_is_allowed_at_or_before_the_boundary(self):
        self.assertEqual(W.make_window(LIVE, end=at(14, 15)).end, at(14, 15))
        self.assertEqual(W.make_window(LIVE, end=at(12, 0)).end, at(12, 0))

    def test_the_end_must_not_be_after_the_last_boundary_before_the_live_event(self):
        for end in (at(14, 30), at(14, 45), at(15, 0), at(14, 15) + timedelta(days=1)):
            with self.subTest(end=end), self.assertRaisesRegex(W.OverlapRefused, "is after"):
                W.make_window(LIVE, end=end)

    def test_the_end_must_be_a_boundary(self):
        for end in (at(14, 10), at(14, 15, 0, 1), at(14, 14, 59)):
            with self.subTest(end=end), self.assertRaisesRegex(W.OverlapRefused, "not a 15-minute UTC boundary"):
                W.make_window(LIVE, end=end)

    def test_a_live_event_just_after_a_boundary_pushes_the_end_back_a_whole_interval(self):
        w = W.make_window(at(14, 15, 0, 1))     # one millisecond into the 14:15 scan
        self.assertEqual(w.end, at(14, 15))
        w = W.make_window(at(14, 15))           # exactly on the boundary: that boundary is not "before" it
        self.assertEqual(w.end, at(14, 0))

    def test_no_live_event_time_means_no_window(self):
        with self.assertRaisesRegex(W.OverlapRefused, "no earliest live event time"):
            W.make_window(None)
        with self.assertRaisesRegex(W.OverlapRefused, "no earliest live event time"):
            W.OverlapGuard(None)

    def test_a_window_built_directly_past_the_allowed_end_is_refused_by_validate(self):
        """validate() applies the same rule to a BackfillWindow built without make_window."""
        with self.assertRaisesRegex(W.OverlapRefused, "is after"):
            W.BackfillWindow(end=at(14, 30), earliest_live=at(14, 15, 1)).validate()
        with self.assertRaisesRegex(W.OverlapRefused, "is after"):
            W.BackfillWindow(end=at(14, 15), earliest_live=at(14, 0, 2, 950)).validate()
        W.BackfillWindow(end=at(14, 15), earliest_live=LIVE).validate()      # and the legal one passes


class GuardTests(unittest.TestCase):
    def test_refuses_an_event_time_at_or_after_the_earliest_live_event(self):
        guard = W.OverlapGuard(LIVE)
        guard.check(LIVE - timedelta(milliseconds=1))
        for t in (LIVE, LIVE + timedelta(milliseconds=1), LIVE + timedelta(hours=1), LIVE + timedelta(days=30)):
            with self.subTest(t=t), self.assertRaisesRegex(W.OverlapRefused, "at or after the earliest live"):
                guard.check(t)
        self.assertEqual(guard.checked, 1)

    def test_the_message_names_both_times(self):
        with self.assertRaises(W.OverlapRefused) as cm:
            W.OverlapGuard(LIVE).check(at(14, 27, 0, 50))
        self.assertIn("2026-09-26T14:27:00.050Z", str(cm.exception))
        self.assertIn("2026-09-26T14:27:00.000Z", str(cm.exception))

    def test_every_event_of_the_last_synthetic_sweep_passes_and_a_sweep_at_the_live_scan_is_refused(self):
        sectors = load_sectors()
        w = W.make_window(LIVE)
        guard = W.OverlapGuard(LIVE)
        last = SimProbe(sectors, seed=1).sweep(0, w.last_scan, is_synthetic=True, synthetic_ingest_ts=LIVE)
        for envelope in last:
            guard.check(datetime.strptime(envelope["event_time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC))
        self.assertEqual(guard.checked, 60)
        # the 14:15 slot is inside the allowed time but is not in the window; the 14:27 scan itself is live
        live_sweep = SimProbe(sectors, seed=1).sweep(0, LIVE)
        with self.assertRaises(W.OverlapRefused):
            guard.check(datetime.strptime(live_sweep[0]["event_time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC))

    def test_time_zones_do_not_hide_an_overlap_or_invent_one(self):
        plus_two, minus_two = timezone(timedelta(hours=2)), timezone(timedelta(hours=-2))
        guard = W.OverlapGuard(LIVE)
        for tz in (plus_two, minus_two):   # the SAME instant as the live event, written in another zone: refused
            with self.subTest(tz=tz), self.assertRaises(W.OverlapRefused):
                guard.check(LIVE.astimezone(tz))     # 12:27-02:00 would look earlier than 14:27 if the zone were ignored
        guard.check((LIVE - timedelta(hours=1)).astimezone(plus_two))    # 15:27+02:00 is 13:27Z: allowed, not "after 14:27"
        guard.check((LIVE - timedelta(hours=1)).astimezone(minus_two))
        with self.assertRaises(W.OverlapRefused):
            W.OverlapGuard(LIVE).check(datetime(2026, 9, 26, 14, 30))    # naive: refused, never guessed


class ManifestTests(unittest.TestCase):
    def test_the_window_is_recorded_explicitly(self):
        m = W.make_window(LIVE).to_manifest()
        self.assertEqual(m["window_end_utc"], "2026-09-26T14:15:00.000Z")
        self.assertEqual(m["window_start_utc"], "2026-06-28T14:15:00.000Z")
        self.assertEqual(m["earliest_live_event_time_utc"], "2026-09-26T14:27:00.000Z")
        self.assertEqual(m["latest_allowed_window_end_utc"], "2026-09-26T14:15:00.000Z")
        self.assertEqual(m["last_scan_utc"], "2026-09-26T14:00:00.000Z")
        self.assertEqual(m["last_synthetic_event_time_utc"], "2026-09-26T14:00:02.950Z")
        self.assertEqual((m["interval_seconds"], m["days"], m["scans"]), (900, 90, 8640))
        self.assertIn("no synthetic event_time at or after", m["overlap_rule"])

    def test_it_is_json_serialisable_and_deterministic(self):
        a, b = W.make_window(LIVE).to_manifest(), W.make_window(LIVE).to_manifest()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class ExplicitNumbersTests(unittest.TestCase):
    """Standing rule: explicit numbers in the docs (cadences, counts, durations) do not change without asking. These
    pin the ones this rule touches, in the docs and in the code that mirrors them."""

    def setUp(self):
        self.doc07 = (ROOT / "docs" / "07-implementation-plan.md").read_text(encoding="utf-8")
        self.doc04 = (ROOT / "docs" / "04-edge-simulators.md").read_text(encoding="utf-8")

    def test_the_code_constants_match_the_doc_07_arithmetic(self):
        m = re.search(r"(\d+) planets × (\d+) scans/day × (\d+) days", self.doc07)
        self.assertEqual(tuple(map(int, m.groups())), (60, 96, 90))
        self.assertEqual((W.BACKFILL_DAYS, W.SCANS), (90, 96 * 90))
        self.assertEqual(60 * W.SCANS, 518_400)
        self.assertIn("~518,400", self.doc07)

    def test_the_45_minute_checkpoint_text_is_unchanged_and_the_deviation_is_a_separate_note(self):
        self.assertIn("run the live probe 45 minutes (3 scans)", self.doc07)
        self.assertIn("and see 180 rows in", self.doc07)
        self.assertIn("**Deviation, 2026-09-26:** the live checkpoint ran 3 scans at a 60 s cadence instead of 45", self.doc07)
        self.assertIn("The real 15-minute cadence (quarter-hour alignment, an idle bridge between scans,", self.doc07)
        self.assertIn("hour-boundary paths) is exercised in Phase 3.", self.doc07)

    def test_the_overlap_rule_is_in_both_docs(self):
        for doc in (self.doc04, self.doc07):
            flat = " ".join(doc.split())
            self.assertIn("last 15-minute UTC boundary before the earliest live probe event", flat)
            self.assertIn("at or after the earliest live `event_time`", flat)
            self.assertIn("explicit input", flat.replace("explicit inputs", "explicit input"))


if __name__ == "__main__":
    unittest.main()
