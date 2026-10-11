"""monitor.py wired into the bridge (doc 05, "Monitoring"): the landed hook, the ping-URL settings, and run()/main() calling the
monitor. The rules: S3 fires only for a batch that LANDED (never a dead-lettered one) and a failing hook never reaches the upload
path; the two optional env keys are accepted, the process environment wins over .env.bridge, and any other key is still refused;
a --local-dir run (a test, an e2e) never monitors; run() ticks S1 only while the client is connected and closes the monitor at
shutdown; and a monitor that fails every ping never stops the bridge. Offline."""
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
import monitor  # noqa: E402
from test_bridge_ack import FakeClient as AckFakeClient  # noqa: E402

S1_URL = "https://hc.invalid/11111111-1111-4111-8111-s1-not-a-real-url"
S3_URL = "https://hc.invalid/33333333-3333-4333-8333-s3-not-a-real-url"
BOTH = {"BRIDGE_PING_S1_URL": S1_URL, "BRIDGE_PING_S3_URL": S3_URL}
NO_ENV_FILE = Path(tempfile.gettempdir()) / "no-such-env-bridge-file"       # tests never read a real .env.bridge


def write_env(text):
    path = Path(tempfile.mkdtemp()) / ".env.bridge"
    path.write_text(text, encoding="utf-8")
    return path


class LandedHookTests(unittest.TestCase):
    def rig(self, uploader=None, on_landed=None):
        self.dir = Path(tempfile.mkdtemp())
        self.calls = []
        hook = on_landed if on_landed is not None else self.calls.append
        return bridge.Bridge(uploader or bridge.LocalDirUploader(self.dir / "landing"), dead_letter_path=self.dir / "dead.ndjson",
                             sleep=lambda s: None, log=lambda m: None, max_attempts=2, backoff=(0, 0), on_landed=hook)

    def scan(self, b):
        (batch,) = [x for p in S.scan_messages(0) for x in b.handle_message(p)]
        return batch

    def test_a_landed_batch_reports_its_line_count(self):
        b = self.rig()
        self.assertTrue(b.upload(self.scan(b)))
        self.assertEqual(self.calls, [60])

    def test_a_dead_lettered_batch_is_not_reported_as_landed(self):
        b = self.rig(uploader=S.ScriptedUploader([500] * 10))
        self.assertFalse(b.upload(self.scan(b)))
        self.assertEqual(self.calls, [], "S3 means data LANDED; a batch that went to the dead-letter file is the opposite")

    def test_a_hook_that_raises_never_reaches_the_upload_path(self):
        def explode(lines):
            raise RuntimeError("monitoring is broken")
        b = self.rig(on_landed=explode)
        batch = self.scan(b)
        try:
            landed = b.upload(batch)
        except Exception as e:
            self.fail(f"a monitoring hook that raised reached the upload path: {e!r}")
        self.assertTrue(landed, "the upload reported failure because the monitoring hook raised")


class SettingsTests(unittest.TestCase):
    def test_the_two_ping_keys_are_accepted_in_the_env_file_and_other_keys_still_are_not(self):
        path = write_env(f"BRIDGE_PING_S1_URL={S1_URL}\nBRIDGE_PING_S3_URL={S3_URL}\nDATABRICKS_HOST=h.invalid\n")
        self.assertEqual(bridge.read_env_file(path), {**BOTH, "DATABRICKS_HOST": "h.invalid"})
        with self.assertRaises(SystemExit) as cm:
            bridge.read_env_file(write_env("SOMETHING_ELSE=1\n"))
        self.assertIn("is not allowed", str(cm.exception))
        with self.assertRaises(SystemExit):
            bridge.read_env_file(write_env("DATABRICKS_TOKEN=x\n"))

    def test_a_local_dir_run_never_monitors_even_with_ping_urls_in_the_environment(self):
        args = bridge.parse_args(["--local-dir", "somewhere"])
        self.assertIsNone(bridge.monitoring_env(args, environ=BOTH, default_env_file=NO_ENV_FILE))
        self.assertIsNone(bridge.make_monitor(args, environ=BOTH, default_env_file=NO_ENV_FILE, log=lambda m: None))

    def test_workspace_mode_reads_the_environment(self):
        m = bridge.make_monitor(bridge.parse_args([]), environ=BOTH, default_env_file=NO_ENV_FILE, log=lambda m: None, start=False)
        self.assertTrue(m.enabled)
        self.assertEqual(m.urls, {"S1": S1_URL, "S3": S3_URL})

    def test_the_process_environment_wins_over_the_file_and_the_file_fills_the_gaps(self):
        file_s1 = "https://hc.invalid/from-the-file-s1"
        path = write_env(f"BRIDGE_PING_S1_URL={file_s1}\nBRIDGE_PING_S3_URL={S3_URL}\n")
        args = bridge.parse_args(["--env-file", str(path)])
        env = bridge.monitoring_env(args, environ={"BRIDGE_PING_S1_URL": S1_URL})
        self.assertEqual(env, {"BRIDGE_PING_S1_URL": S1_URL, "BRIDGE_PING_S3_URL": S3_URL})

    def test_no_keys_anywhere_gives_a_disabled_monitor_and_one_warning(self):
        logs = []
        m = bridge.make_monitor(bridge.parse_args([]), environ={}, default_env_file=NO_ENV_FILE, log=logs.append)
        self.assertFalse(m.enabled)
        self.assertEqual(len(logs), 1, logs)

    def test_the_template_lists_the_ping_keys_empty(self):
        template = (S.ROOT / ".env.bridge.example").read_text(encoding="utf-8")
        for key in bridge.BRIDGE_OPTIONAL_ENV_KEYS:
            self.assertIn(f"\n{key}=\n", template + "\n")
        self.assertEqual(bridge.read_env_file(S.ROOT / ".env.bridge.example")["BRIDGE_PING_S1_URL"], "")


class FakeClient(AckFakeClient):
    """The acknowledgement tests' fake paho client, plus is_connected()."""

    def is_connected(self):
        return self.script.get("connected", True)


class FakeMqtt:
    CallbackAPIVersion = type("V", (), {"VERSION2": "v2"})

    def __init__(self, script):
        self.script, self.client = script, None

    def Client(self, *args, **kwargs):
        self.client = FakeClient(self.script)
        return self.client


class Recorder:
    """A monitor sender that records and can be told to fail; wait() blocks until `count` pings have been attempted."""

    def __init__(self, fail=False):
        self.urls, self.fail, self._cv = [], fail, threading.Condition()

    def __call__(self, url, timeout=None):
        with self._cv:
            self.urls.append(url)
            self._cv.notify_all()
        if self.fail:
            raise RuntimeError("down " + url)

    def wait(self, count, timeout=5.0):
        with self._cv:
            return self._cv.wait_for(lambda: len(self.urls) >= count, timeout)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(bridge.logging.getLogger(bridge.LOGGER_NAME).handlers.clear)

    def args(self, *extra):
        return bridge.parse_args(["--local-dir", str(self.dir / "landing"), "--dead-letter", str(self.dir / "dead.ndjson"),
                                  "--log-file", str(self.dir / "bridge.log"), "--lock-file", str(self.dir / "bridge.lock"),
                                  "--stats-interval", "1000", *extra])

    def run_with(self, m, script, *extra):
        args = self.args(*extra)
        b = bridge.Bridge(bridge.build_uploader(args), dead_letter_path=args.dead_letter, sleep=lambda s: None, log=lambda x: None)
        with contextlib.redirect_stdout(io.StringIO()):
            return bridge.run(b, args, mqtt=FakeMqtt(script), monitor=m)

    def test_s1_is_pinged_from_the_main_loop_while_connected(self):
        rec = Recorder()
        m = monitor.Monitor({"S1": S1_URL}, sender=rec, log=lambda x: None)
        self.assertEqual(self.run_with(m, {"messages": [], "connected": True}, "--idle-exit", "0.6"), 0)
        self.assertTrue(rec.wait(1), "run() never ticked the monitor")
        self.assertEqual(rec.urls, [S1_URL])

    def test_nothing_is_pinged_while_the_client_is_not_connected(self):
        rec = Recorder()
        m = monitor.Monitor({"S1": S1_URL}, sender=rec, log=lambda x: None)
        self.run_with(m, {"messages": [], "connected": False}, "--idle-exit", "0.6")
        m.drain(2)
        self.assertEqual(rec.urls, [])

    def test_the_monitor_is_closed_at_shutdown(self):
        m = monitor.Monitor({"S1": S1_URL}, sender=Recorder(), log=lambda x: None)
        self.run_with(m, {"messages": [], "connected": True}, "--idle-exit", "0.6")
        m._thread.join(3)
        self.assertFalse(m._thread.is_alive(), "run() left the monitor's sender thread running")

    def test_a_monitor_that_fails_every_ping_never_stops_the_bridge(self):
        rec = Recorder(fail=True)
        m = monitor.Monitor({"S1": S1_URL}, sender=rec, log=lambda x: None)
        try:
            rc = self.run_with(m, {"messages": [], "connected": True}, "--idle-exit", "0.6")
        except Exception as e:
            self.fail(f"a failing monitor stopped the bridge: {e!r}")
        self.assertEqual(rc, 0)


class MainTests(unittest.TestCase):
    """bridge.main() end to end with a fake broker: the monitor main() builds is handed to run() and its landed hook."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(bridge.logging.getLogger(bridge.LOGGER_NAME).handlers.clear)

    def argv(self, *extra):
        return ["--log-file", str(self.dir / "bridge.log"), "--lock-file", str(self.dir / "bridge.lock"),
                "--dead-letter", str(self.dir / "dead.ndjson"), "--env-file", str(NO_ENV_FILE), "--stats-interval", "1000", *extra]

    def run_main(self, argv, script, build):
        creds = mock.patch.object(bridge, "apply_mqtt_credentials", lambda client, *a, **k: False)
        uploader = mock.patch.object(bridge, "build_uploader", lambda args: bridge.LocalDirUploader(self.dir / "landing"))
        built = mock.patch.object(bridge, "build_monitor", build)
        with creds, uploader, built, mock.patch.dict(os.environ, BOTH), contextlib.redirect_stdout(io.StringIO()):
            return bridge.main(argv, process_wide=False, mqtt=FakeMqtt(script))

    def test_workspace_mode_pings_s3_after_a_landing_and_s1_while_connected(self):
        rec = Recorder()

        def build(env, log=print, **kw):
            urls, _ = monitor.load_ping_urls(env)
            return monitor.Monitor(urls, sender=rec, log=log)
        script = {"messages": [(i + 1, p) for i, p in enumerate(S.scan_messages(0))], "connected": True}
        rc = self.run_main(self.argv("--exit-after-files", "1"), script, build)
        self.assertEqual(rc, 0)
        self.assertTrue(rec.wait(2), f"expected an S1 and an S3 ping, saw {len(rec.urls)}")
        self.assertEqual(sorted(rec.urls), sorted([S1_URL, S3_URL]))

    def test_a_local_dir_run_builds_no_monitor_at_all(self):
        def build(env, log=print, **kw):
            raise AssertionError("main() built a monitor in --local-dir mode")
        script = {"messages": [], "connected": True}
        try:
            rc = self.run_main(self.argv("--local-dir", str(self.dir / "landing"), "--idle-exit", "0.5"), script, build)
        except AssertionError as e:
            self.fail(str(e))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
