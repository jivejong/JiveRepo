"""The clock gate (doc 04, Clock): a scan is stamped only once NTP has synchronised and only if it is after the last one taken. Offline."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _probe_support  # noqa: E402,F401
from probe import clock  # noqa: E402
from probe.clock import BACKWARDS, UNSYNCED, ClockGate  # noqa: E402

T = 1_790_000_100.0     # a quarter-hour boundary: 1_790_000_100 % 900 == 0


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clock-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "sub" / "last_scan.json"
        self.synced = [True]

    def gate(self):
        return ClockGate(lambda: self.synced[0], self.state)

    def test_a_synchronised_clock_may_stamp(self):
        self.assertIsNone(self.gate().check(T))

    def test_an_unsynchronised_clock_may_not(self):
        self.synced[0] = False
        self.assertEqual(self.gate().check(T), UNSYNCED)

    def test_the_boundary_of_the_last_scan_is_kept_and_a_scan_not_after_it_is_refused(self):
        gate = self.gate()
        gate.record(T)
        self.assertEqual(gate.check(T), BACKWARDS)
        self.assertEqual(gate.check(T - 900), BACKWARDS)
        self.assertIsNone(gate.check(T + 900))

    def test_it_survives_a_restart(self):
        self.gate().record(T)
        again = self.gate()
        self.assertEqual(again.check(T), BACKWARDS)
        self.assertEqual(again.last_boundary, T)

    def test_unsynchronised_is_reported_before_backwards(self):
        gate = self.gate()
        gate.record(T)
        self.synced[0] = False
        self.assertEqual(gate.check(T - 900), UNSYNCED)

    def test_an_unreadable_state_file_does_not_stop_the_probe(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.gate().check(T))

    def test_the_state_file_is_written_atomically(self):
        self.gate().record(T)
        self.assertEqual([p.name for p in self.state.parent.iterdir()], ["last_scan.json"])


class TimeTests(unittest.TestCase):
    def test_iso_has_milliseconds_and_a_z(self):
        self.assertEqual(clock.iso(1_790_000_100.25), "2026-09-21T14:15:00.250Z")

    def test_iso_round_trips(self):
        for value in (0.0, 1_790_000_100.0, 1_790_000_100.5):
            self.assertEqual(clock.parse_iso(clock.iso(value)), value)

    def test_boundaries(self):
        self.assertEqual(clock.boundary_at_or_after(T, 900), T)
        self.assertEqual(clock.boundary_at_or_after(T + 0.1, 900), T + 900)
        self.assertEqual(clock.boundary_after(T, 900), T + 900)
        self.assertEqual(clock.boundary_after(T + 899, 900), T + 900)
        self.assertEqual(clock.boundary_at_or_after(T + 1, 3600) % 3600, 0)


class TimedatectlTests(unittest.TestCase):
    def run_with(self, **kw):
        with mock.patch.object(subprocess, "run", **kw) as run:
            return clock.timedatectl_synced(), run

    def completed(self, out, code=0):
        return mock.Mock(return_value=subprocess.CompletedProcess([], code, stdout=out, stderr=""))

    def test_yes_means_synchronised_and_it_asks_for_ntpsynchronized(self):
        result, run = self.run_with(new=self.completed("yes" + chr(10)))
        self.assertIs(result, True)

    def test_anything_else_is_not(self):
        for out, code in (("no" + chr(10), 0), ("", 0), ("yes", 1)):
            with self.subTest(out=out, code=code):
                result, _ = self.run_with(new=self.completed(out, code))
                self.assertIs(result, False)

    def test_a_machine_without_timedatectl_is_not_synchronised(self):
        result, _ = self.run_with(side_effect=FileNotFoundError())
        self.assertIs(result, False)
        result, _ = self.run_with(side_effect=subprocess.TimeoutExpired("timedatectl", 5))
        self.assertIs(result, False)

    def test_the_command_is_timedatectl_show_ntpsynchronized(self):
        fake = self.completed("yes")
        with mock.patch.object(subprocess, "run", new=fake):
            clock.timedatectl_synced()
        self.assertEqual(fake.call_args[0][0], ["timedatectl", "show", "-p", "NTPSynchronized", "--value"])


if __name__ == "__main__":
    unittest.main()
