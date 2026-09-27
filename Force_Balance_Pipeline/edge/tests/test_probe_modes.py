"""The mode controller (doc 04, The four modes; doc 07): one mode at a time, every transition logged with a reason, DISCONNECTED on a
lost or forced link, BURST on the way back with a backlog, STEALTH keeps its label offline, and the schedule is OFF by default. Offline."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _probe_support  # noqa: E402,F401
from probe.modes import BURST, CONNECTED, DISCONNECTED, STEALTH, ModeController, ModeSchedule  # noqa: E402

T0 = 1_790_000_100.0
DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="modes-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "sub" / "mode_transitions.jsonl"

    def controller(self, **kw):
        return ModeController(self.log, now=T0, **kw)

    def lines(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def pairs(self):
        return [(e["from"], e["to"], e["reason"]) for e in self.lines()]


class TransitionTests(Base):
    def test_it_starts_connected_and_says_so(self):
        c = self.controller()
        self.assertEqual(c.mode, CONNECTED)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])
        self.assertEqual(self.lines()[0]["ts_utc"], "2026-09-21T14:15:00.000Z")

    def test_a_steady_link_logs_nothing_more(self):
        c = self.controller()
        for i in range(50):
            c.update(T0 + i, True, 0)
        self.assertEqual(len(self.lines()), 1)

    def test_a_lost_link_is_disconnected_with_the_reason_and_a_restored_one_is_burst_then_connected(self):
        c = self.controller()
        c.update(T0 + 1, False, 0, "link_lost:keepalive")
        c.update(T0 + 2, False, 180)
        c.update(T0 + 3, True, 180)
        c.update(T0 + 4, True, 60)
        c.update(T0 + 5, True, 0)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup"), (CONNECTED, DISCONNECTED, "link_lost:keepalive"),
                                        (DISCONNECTED, BURST, "link_restored"), (BURST, CONNECTED, "drain_complete")])
        self.assertEqual([e["backlog"] for e in self.lines()], [0, 0, 180, 0])

    def test_each_entry_says_whether_the_probe_is_offline(self):
        c = self.controller()
        c.update(T0 + 1, False, 0, "link_lost:x")
        c.update(T0 + 2, True, 5)
        self.assertEqual([e["offline"] for e in self.lines()], [False, True, False])

    def test_a_restored_link_with_nothing_buffered_goes_straight_to_connected(self):
        c = self.controller()
        c.update(T0 + 1, False, 0, "link_lost:x")
        c.update(T0 + 2, True, 0)
        self.assertEqual(self.pairs()[-1], (DISCONNECTED, CONNECTED, "link_restored"))

    def test_losing_the_link_again_during_a_drain_goes_back_to_disconnected_and_resumes_after(self):
        c = self.controller()
        c.update(T0 + 1, False, 100, "link_lost:x")
        c.update(T0 + 2, True, 100)
        c.update(T0 + 3, False, 60, "link_lost:y")
        self.assertEqual(c.mode, DISCONNECTED)
        c.update(T0 + 4, True, 60)
        self.assertEqual(c.mode, BURST)
        c.update(T0 + 5, True, 0)
        self.assertEqual(c.mode, CONNECTED)

    def test_rows_left_from_before_a_restart_are_drained_as_a_startup_backlog(self):
        c = self.controller(backlog=220)
        c.update(T0 + 1, True, 220)
        c.update(T0 + 2, True, 0)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup"), (CONNECTED, BURST, "startup_backlog"),
                                        (BURST, CONNECTED, "drain_complete")])

    def test_a_restart_while_still_cut_is_disconnected_first(self):
        c = self.controller(backlog=220)
        c.update(T0 + 1, False, 220, "link_lost:client_disconnected")
        c.update(T0 + 2, True, 220)
        self.assertEqual([p[:2] for p in self.pairs()], [(None, CONNECTED), (CONNECTED, DISCONNECTED), (DISCONNECTED, BURST)])


class OverrideTests(Base):
    def test_forcing_disconnected_is_offline_until_it_expires(self):
        c = self.controller()
        c.force(DISCONNECTED, until=T0 + 100)
        c.update(T0 + 1, True, 0)
        self.assertEqual((c.mode, c.offline), (DISCONNECTED, True))
        c.update(T0 + 50, True, 30)
        c.update(T0 + 101, True, 30)
        self.assertEqual(c.mode, BURST)
        c.update(T0 + 102, True, 0)
        self.assertEqual(self.pairs()[1:], [(CONNECTED, DISCONNECTED, "operator"), (DISCONNECTED, BURST, "operator_ended"),
                                            (BURST, CONNECTED, "drain_complete")])

    def test_stealth_changes_the_base_mode_and_back(self):
        c = self.controller()
        c.force(STEALTH, until=T0 + 100)
        c.update(T0 + 1, True, 0)
        self.assertEqual((c.mode, c.base), (STEALTH, STEALTH))
        c.update(T0 + 101, True, 0)
        self.assertEqual((c.mode, c.base), (CONNECTED, CONNECTED))
        self.assertEqual(self.pairs()[1][2], "operator")

    def test_stealth_that_loses_the_link_keeps_its_label_buffers_and_drains_as_burst(self):
        c = self.controller()
        c.force(STEALTH)
        c.update(T0 + 1, True, 0)
        c.update(T0 + 2, False, 0, "link_lost:x")
        self.assertEqual((c.mode, c.offline), (STEALTH, True))
        c.update(T0 + 3, True, 60)
        self.assertEqual(c.mode, BURST)
        c.update(T0 + 4, True, 0)
        self.assertEqual((c.mode, c.base), (STEALTH, STEALTH))
        modes = [(e["to"], e["offline"]) for e in self.lines()]
        self.assertEqual(modes, [(CONNECTED, False), (STEALTH, False), (STEALTH, True), (BURST, False), (STEALTH, False)])

    def test_an_override_without_an_end_stays_until_replaced(self):
        c = self.controller()
        c.force(STEALTH)
        c.update(T0 + 10 * DAY, True, 0)
        self.assertEqual(c.mode, STEALTH)
        c.force(CONNECTED)
        c.update(T0 + 10 * DAY + 1, True, 0)
        self.assertEqual(c.mode, CONNECTED)

    def test_only_connected_disconnected_and_stealth_can_be_forced(self):
        c = self.controller()
        for bad in (BURST, "SLEEPING", None):
            with self.assertRaises(ValueError):
                c.force(bad)


class ScheduleTests(Base):
    def test_the_schedule_is_off_unless_one_is_given(self):
        c = self.controller()
        for step in range(0, 3 * DAY, 900):
            c.update(T0 + step, True, 0)
        self.assertEqual(len(self.lines()), 1)                   # three days, never left CONNECTED

    def test_two_windows_a_day_one_to_three_hours_one_in_each_half(self):
        s = ModeSchedule(seed=5)
        for day in range(200):
            first, second = s.windows_for_day(day)
            for i, (start, end, mode) in enumerate((first, second)):
                self.assertEqual(mode, DISCONNECTED)
                self.assertTrue(3600 <= end - start <= 3 * 3600, (day, end - start))
                self.assertTrue(day * DAY + i * DAY / 2 <= start and end <= day * DAY + (i + 1) * DAY / 2)
            self.assertLess(first[1], second[0])

    def test_it_is_deterministic_for_a_seed_and_differs_between_seeds(self):
        self.assertEqual(ModeSchedule(seed=1).windows_for_day(3), ModeSchedule(seed=1).windows_for_day(3))
        self.assertNotEqual(ModeSchedule(seed=1).windows_for_day(3), ModeSchedule(seed=2).windows_for_day(3))

    def test_mode_at_is_inside_the_window_only(self):
        s = ModeSchedule(seed=9)
        start, end, _ = s.windows_for_day(10)[0]
        self.assertEqual(s.mode_at(start), DISCONNECTED)
        self.assertEqual(s.mode_at((start + end) / 2), DISCONNECTED)
        self.assertIsNone(s.mode_at(end))
        self.assertIsNone(s.mode_at(start - 1))

    def test_a_scheduled_window_takes_the_probe_offline_and_ends(self):
        s = ModeSchedule(seed=9)
        start, end, _ = s.windows_for_day(10)[0]
        c = ModeController(self.log, s, now=start - 60)
        c.update(start - 30, True, 0)
        c.update(start + 30, True, 0)
        c.update(end + 30, True, 12)
        c.update(end + 40, True, 0)
        self.assertEqual([p[2] for p in self.pairs()], ["startup", "schedule", "schedule_ended", "drain_complete"])

    def test_the_operator_can_override_the_schedule(self):
        s = ModeSchedule(seed=9)
        start, end, _ = s.windows_for_day(10)[0]
        c = ModeController(self.log, s, now=start - 60)
        c.force(CONNECTED, until=end)
        c.update(start + 30, True, 0)
        self.assertEqual(c.mode, CONNECTED)


if __name__ == "__main__":
    unittest.main()
