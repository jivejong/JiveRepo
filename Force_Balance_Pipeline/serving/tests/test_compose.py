"""Tests for infra/serving/compose.yml as plain text (doc 05 "Serving (Phase 7)"): loopback-only port, image, no env_file, restart."""
import re
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "serving" / "compose.yml"


class Compose(unittest.TestCase):
    def setUp(self):
        self.lines = [ln.strip() for ln in COMPOSE.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")]

    def test_the_port_is_published_on_loopback_only(self):
        ports = [ln for ln in self.lines if ln.startswith("ports:")]
        self.assertEqual(ports, ['ports: ["127.0.0.1:5433:5432"]'])
        self.assertFalse([ln for ln in self.lines if re.search(r"0\.0\.0\.0", ln)])

    def test_the_image_is_postgres_17(self):
        images = [ln for ln in self.lines if ln.startswith("image:")]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].split(":", 1)[1].strip().startswith("postgres:17"))

    def test_no_env_file_key(self):
        self.assertFalse([ln for ln in self.lines if ln.startswith("env_file")])

    def test_project_and_volume_names_are_explicit(self):
        self.assertIn("name: force-serving", self.lines)
        self.assertIn("force-postgres-data: { name: force-postgres-data }", self.lines)

    def test_restart_policy(self):
        self.assertIn("restart: unless-stopped", self.lines)


if __name__ == "__main__":
    unittest.main()
