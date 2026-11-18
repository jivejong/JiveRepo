"""scripts/demo.py: stopping the backend that make demo leaves running (Phase 8 Round 10). No Docker, no real kill, no real port."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import demo  # noqa: E402


class StopBackend(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.pid_file = self.dir / "backend.pid"
        self.killed, self.checked = [], []

    def stop(self, in_use):
        def port_in_use(port):
            self.checked.append(port)
            return in_use
        return demo.stop_backend(self.pid_file, 8082, port_in_use=port_in_use, kill=self.killed.append)

    def test_a_pid_file_and_the_port_in_use_stops_the_process_and_removes_the_file(self):
        self.pid_file.write_text("4242", encoding="ascii")
        self.assertEqual(self.stop(True), "stopped")
        self.assertEqual(self.killed, [4242])
        self.assertEqual(self.checked, [8082])
        self.assertFalse(self.pid_file.exists())

    def test_a_pid_file_and_the_port_free_only_removes_the_file(self):
        self.pid_file.write_text("4242", encoding="ascii")
        self.assertEqual(self.stop(False), "stale")
        self.assertEqual(self.killed, [])
        self.assertFalse(self.pid_file.exists())

    def test_no_pid_file_does_nothing(self):
        self.assertEqual(self.stop(True), "none")
        self.assertEqual(self.killed, [])
        self.assertEqual(self.checked, [])                  # not even the port is looked at

    def test_a_pid_file_that_holds_no_pid_is_removed_and_nothing_is_killed(self):
        for text in ("", "abc", "-5", "0"):
            with self.subTest(text=text):
                self.pid_file.write_text(text, encoding="ascii")
                self.assertEqual(self.stop(True), "stale")
                self.assertEqual(self.killed, [])
                self.assertFalse(self.pid_file.exists())

    def test_the_defaults_are_the_demo_pid_file_and_port(self):
        self.assertEqual(demo.PID_FILE, demo.DEMO_DIR / "backend.pid")
        with mock.patch.object(demo, "PID_FILE", self.pid_file), mock.patch.object(demo, "port_free", return_value=False), \
                mock.patch.object(demo, "kill_process") as kill:
            self.pid_file.write_text("77", encoding="ascii")
            self.assertEqual(demo.stop_backend(), "stopped")
        kill.assert_called_once_with(77)


class Wiring(unittest.TestCase):
    """down and clean stop the backend before they touch Docker or .demo; the server writes the PID file before it serves."""

    def run_command(self, command):
        order = []
        with mock.patch.object(demo, "check_docker"), mock.patch.object(demo, "stop_backend", side_effect=lambda: order.append("stop") or "none"), \
                mock.patch.object(demo, "compose", side_effect=lambda *a, **k: order.append("compose")), \
                mock.patch.object(demo, "shutil"), mock.patch.object(demo, "ENV_FILE", Path(tempfile.mkdtemp()) / "none"):
            command()
        return order

    def test_down_stops_the_backend_first(self):
        self.assertEqual(self.run_command(demo.cmd_down), ["stop", "compose"])

    def test_clean_stops_the_backend_before_it_removes_anything(self):
        self.assertEqual(self.run_command(demo.cmd_clean), ["stop", "compose"])

    def test_serve_writes_the_pid_file_before_it_serves(self):
        directory = Path(tempfile.mkdtemp())
        events = []

        class Srv:
            def serve_forever(self, poll_interval):
                events.append(("serving", (directory / "backend.pid").exists()))

            def server_close(self):
                events.append(("closed", None))
        fake_server = mock.Mock()
        fake_server.make_server.return_value = Srv()
        with mock.patch.object(demo, "DEMO_DIR", directory), mock.patch.object(demo, "PID_FILE", directory / "backend.pid"), \
                mock.patch.object(demo, "inner_env", return_value={"POSTGRES_PASSWORD": "x"}), \
                mock.patch.dict(sys.modules, {"serving.server": fake_server}), mock.patch("serving.server", fake_server, create=True):
            demo.cmd_serve()
        self.assertEqual(events[0], ("serving", True))
        self.assertFalse((directory / "backend.pid").exists())          # removed again when it exits normally


if __name__ == "__main__":
    unittest.main()
