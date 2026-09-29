"""The mode controller (doc 04, The four modes; doc 07): one mode at a time, every transition logged with a reason, DISCONNECTED on a
lost or forced link, BURST on the way back with a backlog, STEALTH keeps its label offline, and the schedule is OFF by default. Offline."""
import contextlib
import io
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
        c.update(T0, True, 0)          # the first synced tick: the deferred "startup" snapshot is written now
        self.assertEqual(c.mode, CONNECTED)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])
        self.assertEqual(self.lines()[0]["ts_utc"], "2026-09-21T14:15:00.000Z")

    def test_every_transition_is_also_printed_to_stderr_for_journald(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            c = self.controller()
            c.update(T0 + 0.5, True, 0)                  # establish the first connect; no print (no transition)
            c.update(T0 + 1, False, 0, "link_lost:keepalive")
        self.assertEqual(buf.getvalue().splitlines(),
                         ["probe: mode None -> CONNECTED (startup, offline=False, backlog=0)",
                          "probe: mode CONNECTED -> DISCONNECTED (link_lost:keepalive, offline=True, backlog=0)"])

    def test_a_steady_link_logs_nothing_more(self):
        c = self.controller()
        for i in range(50):
            c.update(T0 + i, True, 0)
        self.assertEqual(len(self.lines()), 1)

    def test_a_lost_link_is_disconnected_with_the_reason_and_a_restored_one_is_burst_then_connected(self):
        c = self.controller()
        c.update(T0 + 0.5, True, 0)                          # establish the first real connect; a later loss is a genuine one
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
        c.update(T0 + 0.5, True, 0)
        c.update(T0 + 1, False, 0, "link_lost:x")
        c.update(T0 + 2, True, 5)
        self.assertEqual([e["offline"] for e in self.lines()], [False, True, False])

    def test_a_restored_link_with_nothing_buffered_goes_straight_to_connected(self):
        c = self.controller()
        c.update(T0 + 0.5, True, 0)
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

    def test_a_restart_while_still_cut_goes_straight_to_burst_once_it_connects(self):
        """Found live on the Pi (the broker log showed one continuous connection, no disconnect near a restart that
        mode_transitions.jsonl nonetheless logged as a link loss): while still cut, the probe has never connected in
        this process, so that is not a link loss and nothing is recorded for it; the real first connect goes straight
        to BURST with the startup backlog reason, never through a false DISCONNECTED step."""
        c = self.controller(backlog=220)
        c.update(T0 + 1, False, 220, "link_lost:client_disconnected")   # still cut: not yet connected, nothing recorded
        c.update(T0 + 2, True, 220)                                      # the real first connect
        self.assertEqual([p[:2] for p in self.pairs()], [(None, CONNECTED), (CONNECTED, BURST)])
        self.assertEqual(self.pairs()[-1][2], "startup_backlog")


class StartupConnectTests(Base):
    """Before the probe's first successful connect, link_up is False for a reason that is not a link loss (found live on the Pi:
    the broker log showed one continuous connection, no disconnect near a restart mode_transitions.jsonl logged as one anyway).
    Both startup paths, with nothing forced."""

    def test_waiting_for_the_first_connect_logs_nothing_and_a_clean_first_connect_with_no_backlog_logs_nothing_either(self):
        c = self.controller()
        for i in range(5):
            c.update(T0 + i, False, 0)                    # still waiting for the first connect; not a link loss
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])            # nothing recorded for the wait
        self.assertEqual((c.mode, c.offline), (CONNECTED, False))
        c.update(T0 + 5, True, 0)                          # the real first connect; nothing was buffered, so nothing to drain
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])            # still nothing: no false DISCONNECTED/BURST
        self.assertEqual((c.mode, c.offline), (CONNECTED, False))

    def test_waiting_for_the_first_connect_with_a_backlog_goes_straight_to_burst_not_through_disconnected(self):
        c = self.controller(backlog=60)
        for i in range(5):
            c.update(T0 + i, False, 60)                    # still waiting; the backlog from disk changes nothing here
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])
        c.update(T0 + 5, True, 60)                         # the real first connect: straight to BURST
        self.assertEqual([p[:2] for p in self.pairs()], [(None, CONNECTED), (CONNECTED, BURST)])
        self.assertEqual(self.pairs()[-1][2], "startup_backlog")
        c.update(T0 + 6, True, 0)
        self.assertEqual(c.mode, CONNECTED)
        self.assertEqual(self.pairs()[-1], (BURST, CONNECTED, "drain_complete"))

    def test_a_forced_disconnected_is_honoured_even_while_waiting_for_the_first_connect(self):
        """A forced outage has nothing to do with the link, so it is not suppressed by the first-connect wait."""
        c = self.controller()
        c.force(DISCONNECTED, until=T0 + 50)
        c.update(T0 + 1, False, 0)                          # never connected AND forced offline: the force still applies
        self.assertEqual((c.mode, c.offline), (DISCONNECTED, True))
        self.assertEqual(self.pairs()[-1], (CONNECTED, DISCONNECTED, "operator"))


class BootWithoutBrokerTests(Base):
    """A boot with no broker is offline, not merely slow to connect: once the runtime says the wait has run past its window
    (first_connect_overdue), DISCONNECTED is recorded with reason no_initial_connect."""

    def test_an_overdue_first_connect_is_disconnected_with_its_own_reason_and_recorded_once(self):
        c = self.controller()
        c.update(T0 + 5, False, 0)                                        # inside the window: nothing
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])
        for i in range(6, 12):
            c.update(T0 + i, False, 0, first_connect_overdue=True)        # past the window, still no broker
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup"), (CONNECTED, DISCONNECTED, "no_initial_connect")])
        self.assertEqual((c.mode, c.offline), (DISCONNECTED, True))

    def test_the_first_connect_after_an_overdue_wait_drains_through_burst(self):
        c = self.controller()
        c.update(T0 + 6, False, 0, first_connect_overdue=True)
        c.update(T0 + 7, False, 120, first_connect_overdue=True)          # scans buffered while offline
        c.update(T0 + 8, True, 120)
        c.update(T0 + 9, True, 0)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup"), (CONNECTED, DISCONNECTED, "no_initial_connect"),
                                        (DISCONNECTED, BURST, "link_restored"), (BURST, CONNECTED, "drain_complete")])

    def test_an_overdue_first_connect_with_nothing_buffered_goes_straight_back_to_connected(self):
        c = self.controller()
        c.update(T0 + 6, False, 0, first_connect_overdue=True)
        c.update(T0 + 7, True, 0)
        self.assertEqual(self.pairs()[-1], (DISCONNECTED, CONNECTED, "link_restored"))

    def test_a_connect_inside_the_window_records_nothing(self):
        c = self.controller()
        c.update(T0 + 1, False, 0)
        c.update(T0 + 2, True, 0)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])

    def test_rows_buffered_while_the_first_connect_was_pending_drain_at_the_first_connect(self):
        """A scan can fall inside the wait (never offline, so nothing ever marked a drain): the first connect must still drain it,
        not leave the rows in the buffer until the next restart."""
        c = self.controller()                                             # no backlog at construction
        c.update(T0 + 1, False, 60)                                       # a scan was buffered during the wait
        c.update(T0 + 2, True, 60)
        self.assertEqual(self.pairs()[-1], (CONNECTED, BURST, "startup_backlog"))
        c.update(T0 + 3, True, 0)
        self.assertEqual(self.pairs()[-1], (BURST, CONNECTED, "drain_complete"))

    def test_a_normal_scans_unacknowledged_rows_are_not_a_startup_drain(self):
        """After the first connect, a live scan's rows sit in the buffer until their PUBACKs: a momentary backlog on a healthy link,
        which must never be mistaken for rows left over from before the first connect."""
        c = self.controller()
        c.update(T0 + 1, True, 0)                                         # first connect
        for i, depth in enumerate((60, 60, 20, 0)):
            c.update(T0 + 2 + i, True, depth)                             # a scan published, its acks trickling in
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])
        self.assertEqual(c.mode, CONNECTED)

    def test_the_overdue_flag_means_nothing_once_the_probe_has_connected(self):
        c = self.controller()
        c.update(T0 + 1, True, 0)
        c.update(T0 + 2, False, 0, "link_lost:keepalive", first_connect_overdue=True)
        self.assertEqual(self.pairs()[-1], (CONNECTED, DISCONNECTED, "link_lost:keepalive"))

    def test_a_forced_disconnected_keeps_its_own_reason_during_an_overdue_wait(self):
        c = self.controller()
        c.force(DISCONNECTED, until=T0 + 50)
        c.update(T0 + 1, False, 0, first_connect_overdue=True)
        self.assertEqual(self.pairs()[-1], (CONNECTED, DISCONNECTED, "operator"))


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


class ClockSyncTests(Base):
    """Nothing is WRITTEN before the clock is confirmed synced (module docstring): the mode still updates correctly in memory
    throughout, and the first synced update() call writes exactly one snapshot -- "startup" if nothing happened in the meantime,
    or "clock_synced" plus a count and (if one of them entered DISCONNECTED) disconnected_before_sync/_uptime_s if something did."""

    def test_nothing_is_written_before_sync_though_the_mode_keeps_updating_in_memory(self):
        c = self.controller()
        c.update(T0 + 1, True, 0, clock_synced=False, uptime_s=1.0)
        c.update(T0 + 2, False, 0, "link_lost:x", clock_synced=False, uptime_s=2.0)
        c.update(T0 + 3, True, 40, clock_synced=False, uptime_s=3.0)
        self.assertFalse(self.log.exists())                       # nothing on disk yet
        self.assertEqual((c.mode, c.offline), (BURST, False))     # but the mode kept updating correctly throughout

    def test_the_first_synced_tick_writes_startup_when_nothing_happened_while_waiting_for_sync(self):
        c = self.controller()
        for i in range(1, 4):
            c.update(T0 + i, True, 0, clock_synced=False, uptime_s=float(i))     # link steady; nothing ever changes
        self.assertFalse(self.log.exists())
        c.update(T0 + 4, True, 0, clock_synced=True, uptime_s=4.0)
        self.assertEqual(self.pairs(), [(None, CONNECTED, "startup")])
        self.assertNotIn("suppressed_transitions", self.lines()[0])

    def test_something_suppressed_but_never_disconnected_writes_clock_synced_with_a_count_only(self):
        c = self.controller()
        c.force(STEALTH)
        c.update(T0 + 1, True, 0, clock_synced=False, uptime_s=1.0)      # the override applies; suppressed, never DISCONNECTED
        self.assertEqual((c.mode, c.base), (STEALTH, STEALTH))          # state still correct in memory
        self.assertFalse(self.log.exists())
        c.update(T0 + 2, True, 0, clock_synced=True, uptime_s=2.0)
        entry = self.lines()[0]
        self.assertEqual((entry["from"], entry["to"], entry["reason"]), (None, STEALTH, "clock_synced"))
        self.assertEqual(entry["suppressed_transitions"], 1)
        self.assertNotIn("disconnected_before_sync", entry)

    def test_a_boot_with_no_broker_before_sync_is_captured_as_disconnected_before_sync_with_its_uptime(self):
        """The case the clock finding exists for: a boot with the broker down must not be hidden behind a boot with a merely
        slow-to-sync clock. disconnected_before_sync and its uptime_s (not the wall clock, which was not yet trustworthy) must
        survive into the one snapshot written once synced."""
        c = self.controller()
        c.update(T0 + 1, False, 0, clock_synced=False, uptime_s=1.0)                                      # inside the window
        c.update(T0 + 40, False, 0, first_connect_overdue=True, clock_synced=False, uptime_s=40.0)        # no_initial_connect
        self.assertFalse(self.log.exists())
        self.assertEqual((c.mode, c.offline), (DISCONNECTED, True))          # correct in memory despite being unsynced
        c.update(T0 + 41, False, 0, first_connect_overdue=True, clock_synced=True, uptime_s=41.0)
        entry = self.lines()[0]
        self.assertEqual((entry["from"], entry["to"], entry["reason"]), (None, DISCONNECTED, "clock_synced"))
        self.assertTrue(entry["offline"])
        self.assertEqual(entry["suppressed_transitions"], 1)
        self.assertEqual(entry["disconnected_before_sync"], "no_initial_connect")
        self.assertEqual(entry["disconnected_before_sync_uptime_s"], 40.0)     # timed by uptime, not wall time
        c.update(T0 + 42, True, 0)                                             # ordinary logging resumes right after
        self.assertEqual(self.pairs()[-1], (DISCONNECTED, CONNECTED, "link_restored"))


if __name__ == "__main__":
    unittest.main()
