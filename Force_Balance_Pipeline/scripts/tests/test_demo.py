"""Tests for scripts/demo.py and infra/demo/compose.yml (doc 07 Phase 8 decision P8-2; doc 05 "Demo (make demo)").

No Docker, no network beyond a loopback socket. Run from Force_Balance_Pipeline:
    edge\\.venv\\Scripts\\python.exe -m unittest discover -s scripts/tests
The isolation guard keeps the demo away from the live serving stack: its files may never name the live container, the live
ports, the live credential files or the live database.
"""
import os
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import demo  # noqa: E402

DEMO_PY = ROOT / "scripts" / "demo.py"
COMPOSE = ROOT / "infra" / "demo" / "compose.yml"
MAKEFILE = ROOT / "Makefile"
FORBIDDEN = ("5433", "8081", "1883", "force-postgres", "force-mosquitto", ".env.serving", ".env.agent")


def demo_makefile_lines():
    return "\n".join(l for l in MAKEFILE.read_text(encoding="utf-8").splitlines() if "demo" in l.lower())


class PortFree(unittest.TestCase):
    def test_false_while_listening_true_after_close(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            self.assertFalse(demo.port_free(port))
        finally:
            s.close()
        self.assertTrue(demo.port_free(port))


class EnsureEnvFile(unittest.TestCase):
    def test_creates_once_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env.demo"
            first = demo.ensure_env_file(path)
            data = path.read_bytes()
            self.assertEqual(len(data.splitlines()), 1)
            line = data.decode("ascii").strip()
            self.assertTrue(line.startswith("POSTGRES_PASSWORD="))
            self.assertGreaterEqual(len(line.split("=", 1)[1]), 40)
            self.assertNotIn(b"\r", data)
            self.assertEqual(first, line.split("=", 1)[1])
            second = demo.ensure_env_file(path)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(second, first)


class ChildEnv(unittest.TestCase):
    def test_drops_live_credentials_and_keeps_path(self):
        fake = {"DATABRICKS_TOKEN": "x", "GEMINI_API_KEY": "x", "AGENT_PING_S4_URL": "x", "SERVING_PING_S5_URL": "x",
                "BUNDLE_VAR_x": "x", "PATH": "/somewhere"}
        with mock.patch.dict(os.environ, fake):
            env = demo.child_env({"EXTRA": "1"})
        for key in ("DATABRICKS_TOKEN", "GEMINI_API_KEY", "AGENT_PING_S4_URL", "SERVING_PING_S5_URL", "BUNDLE_VAR_x"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PATH"], "/somewhere")
        self.assertEqual(env["EXTRA"], "1")


class Isolation(unittest.TestCase):
    def test_no_live_names_in_the_demo_files(self):
        texts = {"demo.py": DEMO_PY.read_text(encoding="utf-8"), "compose.yml": COMPOSE.read_text(encoding="utf-8"),
                 "Makefile demo lines": demo_makefile_lines()}
        self.assertIn("demo", texts["Makefile demo lines"])
        for name, text in texts.items():
            for bad in FORBIDDEN:
                self.assertNotIn(bad, text, f"{name} names {bad}")
            self.assertIsNone(re.search(r"force_serving(?!_demo)", text), f"{name} names the live database")

    def test_demo_ports_and_database(self):
        self.assertEqual((demo.PG_PORT, demo.WEB_PORT, demo.PG_DB), (5434, 8082, "force_serving_demo"))

    def test_compose_ports_are_loopback_5434(self):
        text = COMPOSE.read_text(encoding="utf-8")
        entries = re.findall(r"^\s*-\s*\"?([0-9.]+:\d+:\d+)\"?\s*$", text, re.M)
        self.assertEqual(len(entries), 1, "expected exactly one ports entry")
        for entry in entries:
            self.assertTrue(entry.startswith("127.0.0.1:5434:"), entry)
        self.assertNotIn("0.0.0.0", text)


if __name__ == "__main__":
    unittest.main()
