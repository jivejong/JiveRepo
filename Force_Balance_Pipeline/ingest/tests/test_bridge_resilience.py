"""The bridge survives an unattended reboot (doc 05, "Bridge auto-start"): an unreachable broker at start is retried in
the background, not fatal; Ctrl+C is handled before the connect; output and uncaught exceptions reach a rotating UTC log
file even with no console (pythonw.exe); and a second bridge cannot run beside the first. Offline: a fake MQTT module,
temporary directories, and one real paho client aimed at a port nothing listens on."""
import contextlib
import io
import logging
import os
import re
import signal
import socket
import sys
import tempfile
import threading
import types
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402

ROOT = S.ROOT
UTC_STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"


class FakeClient:
    """Records how the bridge drives paho. connect() is the old blocking path and fails like an unreachable broker."""

    def __init__(self, fail_attempts=0, ctrl_c_in_loop_start=False):
        self.fail_attempts, self.ctrl_c_in_loop_start = fail_attempts, ctrl_c_in_loop_start
        self.sync_connects, self.async_connects = [], []
        self.reconnect_delay = None
        self.loop_started = False
        self.sigint_handler_at_connect = None

    def username_pw_set(self, username, password):
        pass

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)

    def connect(self, host, port, keepalive=60):
        self.sync_connects.append((host, port, keepalive))
        raise OSError("connection refused")

    def connect_async(self, host, port, keepalive=60):
        self.async_connects.append((host, port, keepalive))
        self.sigint_handler_at_connect = signal.getsignal(signal.SIGINT)

    def loop_start(self):
        self.loop_started = True
        for _ in range(self.fail_attempts):
            self.on_connect_fail(self, None)
        if self.ctrl_c_in_loop_start:
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)   # what the interpreter does on Ctrl+C

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


class FakeMqtt:
    CallbackAPIVersion = types.SimpleNamespace(VERSION2="v2")

    def __init__(self, **client_options):
        self.client_options, self.client = client_options, None

    def Client(self, *args, **kwargs):
        self.client = FakeClient(**self.client_options)
        return self.client


def close_bridge_logging():
    logger = logging.getLogger(bridge.LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(close_bridge_logging)
        saved_sigint = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, saved_sigint)
        creds = mock.patch.object(bridge, "apply_mqtt_credentials", lambda client, *a, **k: False)   # never read .env.mqtt
        creds.start()
        self.addCleanup(creds.stop)

    def argv(self, *extra):
        return ["--local-dir", str(self.dir / "landing"), "--dead-letter", str(self.dir / "dead.ndjson"),
                "--log-file", str(self.dir / "logs" / "bridge.log"), "--lock-file", str(self.dir / "logs" / "bridge.lock"),
                "--stats-interval", "1000", *extra]

    def run_bridge(self, mqtt, *extra):
        args = bridge.parse_args(self.argv(*extra))
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = bridge.run(bridge.Bridge(bridge.build_uploader(args)), args, mqtt=mqtt)
        except SystemExit as e:
            self.fail(f"run() exited instead of waiting for the broker: {e}")
        return rc, out.getvalue()


class StartupTests(BridgeTestCase):
    def test_an_unreachable_broker_at_start_is_retried_in_the_background_not_fatal(self):
        mqtt = FakeMqtt(fail_attempts=3)
        rc, _ = self.run_bridge(mqtt, "--idle-exit", "0.5")
        self.assertEqual(rc, 0)
        client = mqtt.client
        self.assertEqual(client.async_connects, [("localhost", 1883, 60)])
        self.assertEqual(client.sync_connects, [], "a blocking connect() raises on an unreachable broker")
        self.assertTrue(client.loop_started, "the loop thread is what makes the first attempt and every retry")

    def test_the_reconnect_backoff_is_1_to_60_seconds(self):
        mqtt = FakeMqtt()
        self.run_bridge(mqtt, "--idle-exit", "0.5")
        self.assertEqual(mqtt.client.reconnect_delay, (1, 60))

    def test_failed_attempts_are_logged_at_most_once_a_minute(self):
        now = [1_790_000_000.0]
        on_fail = bridge.make_on_connect_fail("broker.invalid", 1883, clock=lambda: now[0])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for offset in (0, 10, 20, 30, 61):
                now[0] = 1_790_000_000.0 + offset
                on_fail(None, None)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("broker.invalid:1883", lines[0])
        self.assertIn("1 failed attempt(s)", lines[0])
        self.assertIn("4 failed attempt(s)", lines[1])
        self.assertIn("2026-09-21T14:14:21.000000Z", lines[1])    # the UTC stamp of the second report

    def test_many_failed_attempts_in_a_run_write_one_line(self):
        _, out = self.run_bridge(FakeMqtt(fail_attempts=5), "--idle-exit", "0.5")
        self.assertEqual(out.count("cannot reach the MQTT broker"), 1)

    def test_sigint_is_handled_before_the_connect(self):
        mqtt = FakeMqtt()
        self.run_bridge(mqtt, "--idle-exit", "0.5")
        handler = mqtt.client.sigint_handler_at_connect
        self.assertTrue(callable(handler))
        self.assertIsNot(handler, signal.default_int_handler,
                         "SIGINT during the wait for the broker would raise KeyboardInterrupt (exit 0xC000013A)")

    def test_ctrl_c_while_waiting_for_the_broker_stops_the_bridge_cleanly(self):
        try:
            rc, out = self.run_bridge(FakeMqtt(ctrl_c_in_loop_start=True))   # no --idle-exit: only Ctrl+C can end it
        except KeyboardInterrupt:
            self.fail("Ctrl+C before the first connect completed was not handled")
        self.assertEqual(rc, 0)
        self.assertIn("bridge: final", out)

    def test_run_puts_the_previous_signal_handlers_back(self):
        before = signal.getsignal(signal.SIGINT)
        self.run_bridge(FakeMqtt(), "--idle-exit", "0.5")
        self.assertIs(signal.getsignal(signal.SIGINT), before)


class RealPahoTests(unittest.TestCase):
    def test_paho_retries_the_first_connection_itself(self):
        """The assumption the whole startup design rests on, checked against the installed paho rather than taken from
        its documentation: connect_async + loop_start retries a first connection that fails."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.skipTest("paho-mqtt is not installed (run the suite with the edge venv)")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]            # free now, and nothing listens on it once the socket is closed
        failures, enough = [], threading.Event()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="resilience-test-never-connects", clean_session=False)

        def on_fail(c, userdata):
            failures.append(1)
            if len(failures) >= 2:
                enough.set()
        client.on_connect_fail = on_fail
        client.reconnect_delay_set(bridge.RECONNECT_MIN_S, bridge.RECONNECT_MAX_S)
        client.connect_async("127.0.0.1", port, keepalive=60)
        client.loop_start()
        try:
            self.assertTrue(enough.wait(15), f"paho made {len(failures)} attempt(s) in 15 s; expected a retry after the first")
        finally:
            client.loop_stop()


class LoggingTests(BridgeTestCase):
    def test_log_lines_reach_a_rotating_utc_file_with_no_console(self):
        path = self.dir / "logs" / "bridge.log"
        saved = sys.stdout
        sys.stdout = None                                      # what pythonw.exe gives a process
        try:
            logger = bridge.configure_logging(path, stdout=sys.stdout)
            restore = bridge.capture_process_output(logger)
            try:
                print("bridge: hello from a windowless process", flush=True)
            finally:
                restore()
        finally:
            sys.stdout = saved
        self.assertTrue(path.exists(), "no log file was created")
        text = path.read_text(encoding="utf-8")
        self.assertRegex(text, rf"(?m)^{UTC_STAMP} INFO bridge: hello from a windowless process$")
        handlers = logging.getLogger(bridge.LOGGER_NAME).handlers
        self.assertEqual(len(handlers), 1, "no console stream without a console")
        self.assertIsInstance(handlers[0], RotatingFileHandler)
        self.assertEqual((handlers[0].maxBytes, handlers[0].backupCount), (5 * 1024 * 1024, 5))

    def test_with_a_console_the_same_lines_go_to_both(self):
        path = self.dir / "logs" / "bridge.log"
        console = io.StringIO()
        bridge.configure_logging(path, stdout=console).info("bridge: both places")
        self.assertEqual(console.getvalue(), "bridge: both places\n")
        self.assertTrue(path.exists())
        self.assertIn("bridge: both places", path.read_text(encoding="utf-8"))

    def test_calling_configure_twice_does_not_double_a_line(self):
        path = self.dir / "logs" / "bridge.log"
        bridge.configure_logging(path)
        bridge.configure_logging(path).info("bridge: once")
        self.assertEqual(path.read_text(encoding="utf-8").count("bridge: once"), 1)

    def test_uncaught_exceptions_reach_the_log_from_the_main_thread_and_from_threads(self):
        path = self.dir / "logs" / "bridge.log"
        restore = bridge.capture_process_output(bridge.configure_logging(path))
        try:
            try:
                raise ValueError("main-thread boom")
            except ValueError:
                sys.excepthook(*sys.exc_info())

            def boom():
                raise RuntimeError("worker boom")
            worker = threading.Thread(target=boom, name="resilience-worker")
            worker.start()
            worker.join()
            print("stderr text", file=sys.stderr, flush=True)
        finally:
            restore()
        text = path.read_text(encoding="utf-8")
        self.assertIn("CRITICAL bridge: uncaught exception", text)
        self.assertIn("ValueError: main-thread boom", text)
        self.assertIn("uncaught exception in thread resilience-worker", text)
        self.assertIn("RuntimeError: worker boom", text)
        self.assertRegex(text, rf"(?m)^{UTC_STAMP} ERROR stderr text$")

    def test_capture_puts_the_original_streams_and_hooks_back(self):
        before = (sys.stdout, sys.stderr, sys.excepthook, threading.excepthook)
        restore = bridge.capture_process_output(bridge.configure_logging(self.dir / "logs" / "bridge.log"))
        self.assertIsNot(sys.stdout, before[0])
        restore()
        self.assertEqual((sys.stdout, sys.stderr, sys.excepthook, threading.excepthook), before)

    def test_paths_are_absolute_and_derived_from_the_script_not_the_working_directory(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.dir)
        args = bridge.parse_args([])
        self.assertEqual(args.log_file, ROOT / "logs" / "bridge.log")
        self.assertEqual(args.lock_file, ROOT / "logs" / "bridge.lock")
        self.assertTrue(args.log_file.is_absolute() and args.lock_file.is_absolute())


class DiskFull(io.StringIO):
    def write(self, text):
        raise OSError(28, "No space left on device")


class LoggingFailureTests(BridgeTestCase):
    """A log write or a log rotation that fails must neither loop through the logger-backed stderr nor drop records,
    and must never stop the bridge. On Windows a reader of bridge.log (Get-Content -Wait, an editor, a scanner) blocks
    rotation, and that is also the case under pythonw.exe, where the log file is the only record there is."""

    def logger_with_rotation_due(self, path, console=None, err=None):
        with mock.patch.object(bridge, "LOG_MAX_BYTES", 300):          # the third record needs a rollover
            logger = bridge.configure_logging(path, stdout=console, stderr=err)
        handler = logger.handlers[0]
        stats = {"emits": 0, "depth": 0, "max_depth": 0}
        real_emit = handler.emit

        def counting_emit(record):
            stats["emits"] += 1
            stats["depth"] += 1
            stats["max_depth"] = max(stats["max_depth"], stats["depth"])
            try:
                return real_emit(record)
            finally:
                stats["depth"] -= 1
        handler.emit = counting_emit
        return logger, handler, stats

    def test_a_blocked_rotation_neither_recurses_nor_drops_records(self):
        path = self.dir / "logs" / "bridge.log"
        console, err = io.StringIO(), io.StringIO()
        logger, handler, stats = self.logger_with_rotation_due(path, console, err)
        restore = bridge.capture_process_output(logger)
        try:
            with mock.patch.object(handler, "rotate", side_effect=PermissionError(32, "in use by another process")):
                for i in range(6):
                    print(f"bridge: printed {i} " + "x" * 80, flush=True)
                for i in range(2):
                    logger.info(f"bridge: direct {i} " + "x" * 80)
        finally:
            restore()
        self.assertEqual(stats["emits"], 8, "each record is emitted once; more means a failure was logged through the logger")
        self.assertEqual(stats["max_depth"], 1, "an emit must never run inside another emit")
        text = path.read_text(encoding="utf-8")
        for name in ("printed", "direct"):
            for i in range(6 if name == "printed" else 2):
                self.assertIn(f"bridge: {name} {i} ", text, "a record was lost while rotation was blocked")
        self.assertEqual(handler.rollover_failures, 1, "a blocked rotation is retried later, not on every record")
        self.assertNotIn("Logging error", text + console.getvalue() + err.getvalue())
        handler._retry_rollover_at = 0.0                               # the blocker has gone away
        logger.info("bridge: after the blocker went away " + "x" * 80)
        self.assertTrue(Path(str(path) + ".1").exists(), "rotation should resume once it can")

    @unittest.skipUnless(os.name == "nt", "a file held open without delete sharing blocks a rename on Windows only")
    def test_a_file_held_open_like_a_tail_blocks_rotation_but_not_logging(self):
        path = self.dir / "logs" / "bridge.log"
        logger, handler, stats = self.logger_with_rotation_due(path)
        logger.info("bridge: first " + "x" * 80)
        blocker = open(path, "rb")                                     # what a tail of the file does
        self.addCleanup(blocker.close)
        for i in range(5):
            logger.info(f"bridge: held {i} " + "x" * 80)
        self.assertEqual(stats["emits"], 6)
        self.assertGreaterEqual(handler.rollover_failures, 1, "the OS did not refuse the rename; the test proves nothing")
        text = path.read_text(encoding="utf-8")
        for i in range(5):
            self.assertIn(f"bridge: held {i} ", text)

    def test_a_failing_log_write_does_not_stop_the_bridge_and_is_reported_once_to_the_original_stderr(self):
        err, console = io.StringIO(), io.StringIO()
        with mock.patch.object(bridge._SafeRotatingFileHandler, "_open", lambda self: DiskFull()):
            try:
                with contextlib.redirect_stdout(console), contextlib.redirect_stderr(err):
                    rc = bridge.main(self.argv("--idle-exit", "0.5"), process_wide=False, mqtt=FakeMqtt())
            except Exception as e:
                self.fail(f"a failing log write stopped the bridge: {e!r}")
        self.assertEqual(rc, 0)
        self.assertIn("bridge: logging failed (OSError: [Errno 28] No space left on device)", err.getvalue())
        self.assertEqual(err.getvalue().count("logging failed"), 1, "reported at most once a minute")
        self.assertNotIn("Logging error", err.getvalue())              # logging's own multi-line report
        self.assertIn("bridge: final", console.getvalue())             # the console handler kept working

    def test_a_failing_log_write_with_no_stderr_is_dropped_silently(self):
        with mock.patch.object(bridge._SafeRotatingFileHandler, "_open", lambda self: DiskFull()):
            logger = bridge.configure_logging(self.dir / "logs" / "bridge.log", stdout=None, stderr=None)
        logger.info("bridge: nobody will see this")                    # must not raise
        self.assertEqual(logger.handlers[0].failures, 1)

    def test_output_written_while_logging_is_dropped_not_looped(self):
        class Chatty(logging.Handler):
            n = 0

            def emit(self, record):
                Chatty.n += 1
                sys.stderr.write("chatty: " + record.getMessage() + "\n")   # a handler that reports through stderr
        logger = bridge.configure_logging(self.dir / "logs" / "bridge.log")
        logger.addHandler(Chatty())
        restore = bridge.capture_process_output(logger)
        try:
            try:
                print("bridge: hello", flush=True)
            except RecursionError:
                self.fail("a handler writing to the captured stderr looped through the logger")
        finally:
            restore()
        self.assertEqual(Chatty.n, 1, "the handler's own stderr output was logged again")


class SingleInstanceTests(BridgeTestCase):
    def test_a_second_holder_is_refused_until_the_first_lets_go(self):
        path = self.dir / "logs" / "bridge.lock"
        first = bridge.InstanceLock(path).acquire()
        try:
            with self.assertRaises(bridge.AlreadyRunning):
                bridge.InstanceLock(path).acquire()
        finally:
            first.release()
        second = bridge.InstanceLock(path).acquire()           # the lock was released with the first holder
        second.release()

    def test_a_second_bridge_exits_with_its_own_code_and_a_log_line_and_never_touches_mqtt(self):
        held = bridge.InstanceLock(self.dir / "logs" / "bridge.lock").acquire()
        mqtt, console = FakeMqtt(), io.StringIO()
        try:
            with contextlib.redirect_stdout(console):
                rc = bridge.main(self.argv("--idle-exit", "0.5"), process_wide=False, mqtt=mqtt)
        finally:
            held.release()
        self.assertEqual(rc, bridge.EXIT_ALREADY_RUNNING)
        self.assertEqual(bridge.EXIT_ALREADY_RUNNING, 3)
        self.assertIsNone(mqtt.client, "the loser must not create an MQTT client (client id force-bridge is shared)")
        self.assertIn("another bridge holds", (self.dir / "logs" / "bridge.log").read_text(encoding="utf-8"))
        self.assertIn("another bridge holds", console.getvalue())

    def test_main_releases_the_lock_when_it_finishes(self):
        console = io.StringIO()
        try:
            with contextlib.redirect_stdout(console):
                rc = bridge.main(self.argv("--idle-exit", "0.5"), process_wide=False, mqtt=FakeMqtt())
        except SystemExit as e:
            self.fail(f"main() exited instead of running until idle: {e}")
        self.assertEqual(rc, 0)
        bridge.InstanceLock(self.dir / "logs" / "bridge.lock").acquire().release()


if __name__ == "__main__":
    unittest.main()
