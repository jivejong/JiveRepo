"""agent/ddl.py: the deployment DDL is rendered from config only and run once through StatementClient.write."""
import tempfile
import unittest
from pathlib import Path

import _support as S
from _support import FakeClock, FakeOpener, succeeded
from agent import config, ddl, dbsql

FILE = S.ROOT / "agent" / "sql" / "create_deployment.sql"


class FakeDb:
    def __init__(self):
        self.writes = []

    def write(self, sql, params=None):
        self.writes.append((sql, params))
        return []


class Render(unittest.TestCase):
    def test_only_the_deployment_placeholder_is_replaced(self):
        template = "CREATE TABLE IF NOT EXISTS {deployment} (a {other}, b {disturbance})"
        self.assertEqual(ddl.render(template, "dev"),
                         "CREATE TABLE IF NOT EXISTS force.dev_jivejong.deployment (a {other}, b {disturbance})")

    def test_the_table_name_comes_from_config(self):
        self.assertIn(config.tables("prod")["deployment"], ddl.render("{deployment}", "prod"))
        self.assertEqual(ddl.render("{deployment}", "prod"), "force.gold.deployment")
        self.assertEqual(ddl.render("{deployment}", "dev"), "force.dev_jivejong.deployment")

    def test_a_bad_target_raises(self):
        with self.assertRaises(ValueError):
            ddl.render("{deployment}", "staging")

    def test_a_bad_table_name_in_config_raises(self):
        from unittest import mock
        bad = {"prod": dict(config.TARGETS["prod"], deployment="force.gold.x; DROP TABLE y"), "dev": config.TARGETS["dev"]}
        with mock.patch.dict(config.TARGETS, bad, clear=True):
            with self.assertRaises(ValueError):
                ddl.render("{deployment}", "prod")

    def test_the_rendered_file_equals_the_file_with_the_name_substituted(self):
        text = FILE.read_text(encoding="utf-8")
        self.assertEqual(ddl.render(text, "dev"), text.replace("{deployment}", "force.dev_jivejong.deployment"))
        self.assertNotIn("{", ddl.render(text, "dev"))


class Run(unittest.TestCase):
    def test_runs_the_file_once_through_write_with_no_parameters(self):
        db = FakeDb()
        name = ddl.create_deployment_table("dev", db)
        self.assertEqual(name, "force.dev_jivejong.deployment")
        self.assertEqual(len(db.writes), 1)
        sql, params = db.writes[0]
        self.assertEqual(sql, ddl.render(FILE.read_text(encoding="utf-8"), "dev"))
        self.assertEqual(params, {})
        self.assertTrue(sql.startswith("CREATE TABLE IF NOT EXISTS force.dev_jivejong.deployment ("))

    def test_matches_the_query_module_text(self):
        from agent import queries
        self.assertEqual(ddl.render(FILE.read_text(encoding="utf-8"), "prod"), queries.create_deployment("prod")[0])

    def test_a_custom_template_file_is_used_as_given(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.sql"
            path.write_text("SELECT '{deployment}' AS name, '{second}' AS other", encoding="utf-8")
            db = FakeDb()
            ddl.create_deployment_table("dev", db, template_path=path)
            self.assertEqual(db.writes[0][0], "SELECT 'force.dev_jivejong.deployment' AS name, '{second}' AS other")

    def test_a_bad_target_raises_before_any_statement(self):
        db = FakeDb()
        with self.assertRaises(ValueError):
            ddl.create_deployment_table("staging", db)
        self.assertEqual(db.writes, [])

    def test_through_a_real_statement_client_it_is_one_post(self):
        opener = FakeOpener([(200, succeeded([], []))])
        clock = FakeClock()
        client = dbsql.StatementClient(S.HOST, S.TOKEN, S.WAREHOUSE, opener=opener, sleep=clock.sleep, monotonic=clock.monotonic)
        ddl.create_deployment_table("dev", client)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(client.statements, 1)
        self.assertIn("force.dev_jivejong.deployment", opener.requests[0]["body"]["statement"])


class Constants(unittest.TestCase):
    def test_only_the_databricks_keys_are_needed(self):
        self.assertEqual(ddl.DDL_ENV_KEYS, ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID"))

    def test_the_sql_file_is_the_agents(self):
        self.assertEqual(ddl.CREATE_DEPLOYMENT_SQL, FILE)


if __name__ == "__main__":
    unittest.main()
