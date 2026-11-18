"""agent/seen.py: the first-seen store of the human-first window (doc 06)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts the repo root on the path)
from agent.seen import SeenStore


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = SeenStore(self.dir / "agent_seen_dev.json")
        self.warnings = []

    def test_a_missing_file_is_empty_without_a_warning(self):
        self.assertEqual(self.store.load(self.warnings.append), {})
        self.assertEqual(self.warnings, [])

    def test_a_round_trip(self):
        self.store.save({"b": "2026-10-20T01:00:00.000Z", "a": "2026-10-20T00:00:00.000Z"})
        self.assertEqual(self.store.load(), {"a": "2026-10-20T00:00:00.000Z", "b": "2026-10-20T01:00:00.000Z"})

    def test_anything_but_an_object_of_strings_is_empty_with_one_warning(self):
        for text in ("not json", "[1]", '{"a": 1}', '{"a": null}', '"x"', ""):
            self.warnings.clear()
            self.store.path.write_text(text, encoding="utf-8")
            self.assertEqual(self.store.load(self.warnings.append), {}, text)
            self.assertEqual(len(self.warnings), 1, text)

    def test_the_file_is_utf8_json_with_lf_only(self):
        self.store.save({"a": "x"})
        raw = self.store.path.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(json.loads(raw), {"a": "x"})

    def test_no_temp_file_is_left_after_a_save(self):
        self.store.save({"a": "x"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["agent_seen_dev.json"])


class SaveRetry(unittest.TestCase):
    """Finding 12: os.replace can raise PermissionError on Windows while the server has the file open; one retry after 0.2 s."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.sleeps = []
        self.store = SeenStore(self.dir / "agent_seen_prod.json", sleep=self.sleeps.append)
        self.real = os.replace

    def test_the_first_replace_fails_and_the_second_saves(self):
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("in use")
            return self.real(src, dst)
        with mock.patch("agent.seen.os.replace", flaky):
            self.store.save({"a": "x"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps, [0.2])
        self.assertEqual(self.store.load(), {"a": "x"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["agent_seen_prod.json"])

    def test_both_replaces_fail_and_it_raises_leaving_no_temp_file(self):
        calls = []

        def broken(src, dst):
            calls.append(1)
            raise PermissionError("in use")
        with mock.patch("agent.seen.os.replace", broken):
            with self.assertRaises(PermissionError):
                self.store.save({"a": "x"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_clean_replace_does_not_sleep(self):
        self.store.save({"a": "x"})
        self.assertEqual(self.sleeps, [])

    def test_an_error_other_than_permission_is_not_retried(self):
        calls = []

        def other(src, dst):
            calls.append(1)
            raise OSError("disk")
        with mock.patch("agent.seen.os.replace", other):
            with self.assertRaises(OSError):
                self.store.save({"a": "x"})
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
