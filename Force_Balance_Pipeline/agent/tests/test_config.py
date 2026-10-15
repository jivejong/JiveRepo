"""agent/config.py: constants, table names per target, the .env.agent loader and the .gitignore entry."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import _support as S
from agent import config

GOOD = {"DATABRICKS_HOST": "https://example.invalid", "DATABRICKS_TOKEN": "tok-NOT-A-REAL-ONE-5521",
        "DATABRICKS_WAREHOUSE_ID": "0123456789abcdef", "GEMINI_API_KEY": "key-NOT-A-REAL-ONE-8830",
        "INFERENCE_MODEL": "gemini-3.1-flash-lite"}


def write_env(directory, values, extra=""):
    path = Path(directory) / ".env.agent"
    path.write_text("# comment\n" + "".join(f"{k}={v}\n" for k, v in values.items()) + extra, encoding="utf-8")
    return path


class Constants(unittest.TestCase):
    def test_values(self):
        self.assertEqual(config.AGENT_EPOCH, datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(config.MAX_INCIDENTS_PER_RUN, 5)
        self.assertEqual(config.JOB1_ID, 692051683030737)

    def test_the_default_env_file_is_env_agent_at_the_project_root(self):
        self.assertEqual(config.DEFAULT_ENV_FILE, S.ROOT / ".env.agent")


class Tables(unittest.TestCase):
    def test_prod(self):
        self.assertEqual(config.tables("prod"), {
            "disturbance": "force.gold.gold_disturbance", "deployment": "force.gold.deployment",
            "reading": "force.gold.gold_sector_reading", "sector": "force.gold.dim_sector",
            "jedi": "force.gold.dim_jedi", "starship": "force.gold.dim_starship"})

    def test_dev(self):
        self.assertEqual(config.tables("dev"), {
            "disturbance": "force.dev_jivejong.gold_disturbance", "deployment": "force.dev_jivejong.deployment",
            "reading": "force.dev_jivejong.gold_sector_reading", "sector": "force.dev_jivejong.dim_sector",
            "jedi": "force.dev_jivejong.dim_jedi", "starship": "force.dev_jivejong.dim_starship"})

    def test_bad_target(self):
        with self.assertRaises(ValueError):
            config.tables("staging")

    def test_a_bad_table_name_is_refused(self):
        for bad in ("force.gold.deployment; DROP TABLE x", "force.gold", "Force.gold.deployment", "force.gold.a b", ""):
            with self.subTest(bad=bad):
                patched = {"prod": dict(config.TARGETS["prod"], deployment=bad)}
                with mock.patch.dict(config.TARGETS, patched, clear=True):
                    with self.assertRaises(ValueError):
                        config.tables("prod")

    def test_a_missing_table_key_is_refused(self):
        patched = {"prod": {k: v for k, v in config.TARGETS["prod"].items() if k != "jedi"}}
        with mock.patch.dict(config.TARGETS, patched, clear=True):
            with self.assertRaises(ValueError):
                config.tables("prod")

    def test_the_returned_dict_is_a_copy(self):
        config.tables("prod")["deployment"] = "x"
        self.assertEqual(config.tables("prod")["deployment"], "force.gold.deployment")


class LoadEnv(unittest.TestCase):
    def test_reads_all_five_keys(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(config.load_env(write_env(d, GOOD)), GOOD)

    def test_quotes_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_env(d, dict(GOOD, GEMINI_API_KEY='"quoted-NOT-REAL"'), extra="\n# trailing comment\n")
            self.assertEqual(config.load_env(path)["GEMINI_API_KEY"], "quoted-NOT-REAL")

    def test_a_missing_key_is_named_without_any_value(self):
        for key in GOOD:
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as d:
                    values = {k: v for k, v in GOOD.items() if k != key}
                    with self.assertRaises(config.ConfigError) as caught:
                        config.load_env(write_env(d, values))
                    message = str(caught.exception)
                    self.assertIn(key, message)
                    for value in values.values():
                        self.assertNotIn(value, message)

    def test_an_empty_value_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as caught:
                config.load_env(write_env(d, dict(GOOD, DATABRICKS_TOKEN="")))
            self.assertIn("DATABRICKS_TOKEN", str(caught.exception))

    def test_an_unknown_key_is_refused_by_name_only(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as caught:
                config.load_env(write_env(d, GOOD, extra="SOMETHING_ELSE=secret-value-NOT-REAL\n"))
            self.assertIn("SOMETHING_ELSE", str(caught.exception))
            self.assertNotIn("secret-value-NOT-REAL", str(caught.exception))

    def test_a_duplicate_key_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError):
                config.load_env(write_env(d, GOOD, extra="INFERENCE_MODEL=other\n"))

    def test_a_line_without_equals_is_refused_without_its_text(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as caught:
                config.load_env(write_env(d, GOOD, extra="just-some-secret-text\n"))
            self.assertNotIn("just-some-secret-text", str(caught.exception))


class GitIgnore(unittest.TestCase):
    def test_env_agent_is_listed_in_gitignore(self):
        lines = (S.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env.agent", [line.strip() for line in lines])


if __name__ == "__main__":
    unittest.main()
