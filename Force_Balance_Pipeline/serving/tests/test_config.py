"""Tests for serving/config.py (doc 05 "Serving (Phase 7)"): the .env.serving loader and the source table names."""
import tempfile
import unittest
from pathlib import Path

from agent import config as agent_config
from serving import config

SECRET = "s3cr3t-value-0042"


def env_file(text):
    d = tempfile.mkdtemp()
    p = Path(d) / ".env.serving"
    p.write_text(text, encoding="utf-8")
    return p


class LoadEnv(unittest.TestCase):
    def test_reads_allowed_keys_and_skips_comments_and_blanks(self):
        p = env_file(f"# c\n\nPOSTGRES_PASSWORD={SECRET}\nSERVING_PING_S5_URL=x\n")
        self.assertEqual(config.load_env(p)["POSTGRES_PASSWORD"], SECRET)

    def test_an_unknown_key_is_rejected_by_name_only(self):
        p = env_file(f"POSTGRES_PASSWORD={SECRET}\nGEMINI_API_KEY={SECRET}\n")
        with self.assertRaises(config.ConfigError) as caught:
            config.load_env(p)
        self.assertIn("GEMINI_API_KEY", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_a_missing_required_key_is_rejected(self):
        p = env_file("SERVING_PING_S5_URL=x\n")
        with self.assertRaises(config.ConfigError) as caught:
            config.load_env(p)
        self.assertIn("POSTGRES_PASSWORD", str(caught.exception))

    def test_a_value_never_appears_in_an_exception(self):
        for text in (f"POSTGRES_PASSWORD{SECRET}\n", f"BAD_KEY={SECRET}\n", f"DATABRICKS_TOKEN={SECRET}\n"):
            with self.assertRaises(config.ConfigError) as caught:
                config.load_env(env_file(text), required=("DATABRICKS_HOST",))
            self.assertNotIn(SECRET, str(caught.exception))


class Sources(unittest.TestCase):
    def test_every_source_name_passes_the_agents_pattern(self):
        self.assertIs(config.TABLE_NAME, agent_config.TABLE_NAME)
        self.assertEqual(len(config.SOURCES), 10)
        for name in config.SOURCES.values():
            self.assertTrue(agent_config.TABLE_NAME.match(name), name)

    def test_a_malformed_name_fails(self):
        for bad in ("Force.gold.x", "force.gold", "force.gold.x; drop", "force..x", None):
            with self.assertRaises(config.ConfigError, msg=str(bad)):
                config.validate_source_name(bad)


if __name__ == "__main__":
    unittest.main()
