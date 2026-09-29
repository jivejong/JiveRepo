"""The live probe's wiring (edge/probe/main.py, `probe_sim.py --live`): defaults are the doc's numbers, the mode schedule is OFF, the broker address and
credentials come from the environment only, the password ends up in no file, and the loop applies control messages and stops when asked. Offline."""
import contextlib
import io
import os
import queue
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import FakePublisher, epoch  # noqa: E402
from test_probe_publisher import FAKE_MQTT  # noqa: E402
import probe.main as pm  # noqa: E402
import probe_sim  # noqa: E402
from probe.buffer import DEFAULT_CAP  # noqa: E402

SECRET = "s3cr3t-probe-pw-ZZ"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probe-main-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def args(self, *extra):
        return pm.parse_args(["--live", "--state-dir", str(self.tmp), "--assume-clock-synced", *extra])

    def close(self, runtime, publisher):
        runtime.faults.close()
        runtime.buffer.close()


class DefaultTests(unittest.TestCase):
    def test_defaults_are_the_documented_numbers(self):
        a = pm.parse_args(["--live"])
        self.assertEqual((a.scan_interval, a.stealth_interval), (900, 3600))
        self.assertEqual((a.drain_batch, a.drain_pause, a.buffer_cap), (500, 10.0, 100_000))
        self.assertEqual((a.fault_rate, a.ack_timeout), (1.0, 30.0))
        self.assertEqual(a.source_id, "probe-01")

    def test_the_mode_schedule_and_the_clock_bypass_are_off_by_default(self):
        a = pm.parse_args(["--live"])
        self.assertFalse(a.mode_schedule)
        self.assertFalse(a.assume_clock_synced)
        self.assertEqual(a.stop_after_scans, 0)

    def test_there_is_no_password_or_host_flag_so_none_can_end_up_in_a_process_list(self):
        for flag in ("--password", "--mqtt-password", "--mqtt-host", "--username", "--mqtt-username"):
            with self.subTest(flag):
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    pm.parse_args(["--live", flag, "x"])


class BuildTests(Fixture):
    def build(self, *extra, environ=None, publisher=None, mqtt=None):
        runtime, pub, controls = pm.build(self.args(*extra), environ=environ or {}, publisher=publisher, mqtt=mqtt,
                                          now=lambda: epoch("2026-09-27T12:00:00"))
        self.addCleanup(self.close, runtime, pub)
        return runtime, pub, controls

    def test_it_wires_the_runtime_from_the_arguments(self):
        runtime, _, _ = self.build("--fault-rate", "0.5", "--buffer-cap", "777", "--scan-interval", "60", "--stealth-interval", "120",
                                   "--drain-batch", "50", "--drain-pause", "2", publisher=FakePublisher())
        self.assertEqual((runtime.interval, runtime.stealth_interval), (60, 120))
        self.assertEqual((runtime.drain_batch, runtime.drain_pause), (50, 2.0))
        self.assertEqual((runtime.faults.rate, runtime.buffer.cap), (0.5, 777))
        self.assertEqual(runtime.topic, "force/telemetry/probe-01")
        self.assertIsNone(runtime.modes.schedule)

    def test_the_default_buffer_cap_is_100000(self):
        runtime, _, _ = self.build(publisher=FakePublisher())
        self.assertEqual(runtime.buffer.cap, DEFAULT_CAP)

    def test_the_schedule_is_only_built_when_asked_for(self):
        runtime, _, _ = self.build("--mode-schedule", "--mode-schedule-seed", "3", publisher=FakePublisher())
        self.assertIsNotNone(runtime.modes.schedule)
        self.assertEqual(runtime.modes.schedule.seed, 3)

    def test_the_clock_check_is_the_real_ntp_check_unless_the_dev_flag_is_given(self):
        real = pm.parse_args(["--live", "--state-dir", str(self.tmp)])
        runtime, pub, _ = pm.build(real, environ={}, publisher=FakePublisher(), now=lambda: epoch("2026-09-27T12:00:00"))
        self.addCleanup(self.close, runtime, pub)
        self.assertIs(runtime.gate.sync_check, pm.timedatectl_synced)
        bypass = self.build(publisher=FakePublisher())[0]                # self.args() adds --assume-clock-synced
        self.assertIsNot(bypass.gate.sync_check, pm.timedatectl_synced)
        self.assertTrue(bypass.gate.sync_check())

    def test_the_state_directory_holds_the_buffer_and_the_logs(self):
        runtime, _, _ = self.build(publisher=FakePublisher())
        self.assertTrue((self.tmp / "buffer.db").exists())
        runtime.tick(epoch("2026-09-27T12:00:00"))     # the mode log's own first write waits for the first synced tick (probe.modes)
        self.assertTrue((self.tmp / "mode_transitions.jsonl").exists())

    def test_a_state_directory_with_rows_starts_a_startup_backlog_drain(self):
        runtime, _, _ = self.build(publisher=FakePublisher())
        runtime.buffer.append([("E1", "2026-09-27T11:00:00.000Z", "S", "{}")], "2026-09-27T11:00:00.000Z")
        runtime.faults.close()
        runtime.buffer.close()
        again, _, _ = self.build(publisher=FakePublisher())
        self.assertTrue(again.modes.draining)

    def test_the_broker_and_the_credentials_come_from_the_environment_only(self):
        env = {"PROBE_MQTT_HOST": "192.0.2.10", "PROBE_MQTT_USERNAME": "probe-01", "PROBE_MQTT_PASSWORD": SECRET}
        _, publisher, controls = self.build(environ=env, mqtt=FAKE_MQTT)
        self.assertEqual(publisher.host, "192.0.2.10")
        self.assertIn(("username_pw_set", "probe-01", SECRET), publisher.client.calls)
        self.assertEqual(publisher.control_topic, "force/control/probe-01")
        publisher.on_control(b"{}")
        self.assertEqual(controls.get_nowait(), b"{}")

    def test_without_an_environment_it_targets_localhost_anonymously(self):
        _, publisher, _ = self.build(environ={}, mqtt=FAKE_MQTT)
        self.assertEqual(publisher.host, "localhost")
        self.assertNotIn("username_pw_set", [c[0] for c in publisher.client.calls])

    def test_the_password_is_in_no_file_the_probe_writes(self):
        env = {"PROBE_MQTT_HOST": "192.0.2.10", "PROBE_MQTT_USERNAME": "probe-01", "PROBE_MQTT_PASSWORD": SECRET}
        runtime, _, _ = self.build(environ=env, mqtt=FAKE_MQTT)
        runtime.on_control(b"garbage", epoch("2026-09-27T12:00:00"))
        for name in os.listdir(self.tmp):
            data = (self.tmp / name).read_bytes()
            self.assertNotIn(SECRET.encode(), data, name)


class VersionTests(unittest.TestCase):
    def test_the_commit_is_read_from_the_version_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "version"
            path.write_text("a" * 40 + chr(10), encoding="utf-8")
            self.assertEqual(pm.version_line({"PROBE_VERSION_FILE": str(path)}), "a" * 40)

    def test_a_missing_file_or_variable_is_unknown_not_an_error(self):
        self.assertEqual(pm.version_line({}), "unknown")
        self.assertEqual(pm.version_line({"PROBE_VERSION_FILE": "/no/such/file"}), "unknown")


class EntryPointTests(unittest.TestCase):
    def test_live_dispatches_to_the_runtime_with_the_remaining_arguments(self):
        with mock.patch.object(pm, "main", return_value=7) as live:
            self.assertEqual(probe_sim.main(["--live", "--fault-rate", "0"]), 7)
        live.assert_called_once_with(["--live", "--fault-rate", "0"])

    def test_without_live_it_is_still_the_phase_2_simulator(self):
        with mock.patch.object(pm, "main") as live, mock.patch.object(probe_sim, "run", return_value=60) as sim,                 contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(probe_sim.main(["--dry-run", "--scans", "1", "--immediate", "--seed", "1"]), 0)
        live.assert_not_called()
        sim.assert_called_once()


class LoopTests(Fixture):
    """main() against a fake clock and a fake publisher: it takes scans, applies a queued control message and stops."""

    class FakeTime:
        def __init__(self, publisher):
            self.t, self.publisher = epoch("2026-09-27T12:00:00"), publisher

        def time(self):
            return self.t

        def sleep(self, seconds):
            self.t += 60.0                                   # a minute of fake time per loop pass
            self.publisher.step(self.t)

    def test_main_takes_the_requested_scans_applies_control_and_closes_cleanly(self):
        publisher = FakePublisher()
        clock = self.FakeTime(publisher)
        original = pm.build
        holder = {}

        def build(args, now):
            runtime, pub, controls = original(args, publisher=publisher, now=now)
            controls.put(b'{"mode": "STEALTH", "for_seconds": 7200}')
            holder["runtime"] = runtime
            return runtime, pub, controls
        with mock.patch.object(pm, "time", clock), mock.patch.object(pm, "build", build), contextlib.redirect_stderr(io.StringIO()):
            code = pm.main(["--live", "--state-dir", str(self.tmp), "--assume-clock-synced", "--stop-after-scans", "2"])
        self.assertEqual(code, 0)
        runtime = holder["runtime"]
        with self.assertRaises(sqlite3.ProgrammingError):                 # main closed the buffer on the way out
            runtime.buffer.depth()
        self.assertEqual(runtime.stats["scans"], 2)
        self.assertEqual({e["mode"] for _, _, e in publisher.received}, {"STEALTH"})       # the control message was applied
        self.assertIn('"control_applied"', (self.tmp / "probe_events.jsonl").read_text(encoding="utf-8"))

    def test_a_scan_interval_below_one_second_is_refused(self):
        with self.assertRaises(SystemExit):
            pm.main(["--live", "--state-dir", str(self.tmp), "--scan-interval", "0"])


if __name__ == "__main__":
    unittest.main()
