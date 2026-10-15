"""agent/queries.py: the SQL text has only validated table names and :parameters, and the deployment DDL matches doc 03."""
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

import _support as S
from agent import config, queries

DOC03 = S.ROOT / "docs" / "03-data-model.md"
EPOCH = datetime(2026, 10, 12, tzinfo=timezone.utc)
DECIDED = datetime(2026, 10, 14, 9, 15, 0, tzinfo=timezone.utc)

ROW = {
    "deployment_id": "01KMARKERDEPLOYMENTID00001", "disturbance_id": "01KMARKERDISTURBANCEID001", "decided_by": "yoda_agent",
    "decision": "deploy", "jedi_ids": ["marker_jedi_one", "marker_jedi_two"], "starship_id": "marker_ship", "eta_hours": 0.123456,
    "released_at": DECIDED, "rationale": "marker rationale text", "context_snapshot": {"marker": "marker context value"},
    "signature_at_decision": "sith_presence", "model": "marker-model", "decision_latency_ms": 98765,
    "guardrail_overrides": [{"reason": "marker override"}], "decided_at": DECIDED,
}


def all_statements(target="dev"):
    return {
        "create_deployment": queries.create_deployment(target),
        "repair": queries.repair(target),
        "poll": queries.poll(target, EPOCH, 5),
        "context": queries.context(target, "01KMARKERDISTURBANCEID001"),
        "available_jedi": queries.available_jedi(target),
        "available_starships": queries.available_starships(target),
        "write_decision": queries.write_decision(target, ROW),
        "mark": queries.mark(target, "01KMARKERDISTURBANCEID001"),
    }


class SqlText(unittest.TestCase):
    def test_no_placeholder_is_left_and_no_brace_remains(self):
        for name, (sql, _) in all_statements().items():
            with self.subTest(name=name):
                self.assertNotIn("{", sql)
                self.assertNotIn("}", sql)

    def test_every_table_in_the_sql_is_a_config_table_of_the_target(self):
        allowed = set(config.tables("dev").values())
        for name, (sql, _) in all_statements().items():
            with self.subTest(name=name):
                for table in re.findall(r"\bforce\.[a-z0-9_]+\.[a-z0-9_]+", sql):
                    self.assertIn(table, allowed)

    def test_prod_and_dev_use_their_own_schemas(self):
        self.assertIn("force.gold.deployment", queries.repair("prod")[0])
        self.assertIn("force.dev_jivejong.deployment", queries.repair("dev")[0])
        self.assertNotIn("force.gold.", queries.repair("dev")[0])

    def test_no_data_value_appears_in_any_sql_text(self):
        markers = ["MARKER", "marker", "98765", "0.123456", "2026-10-1"]
        for name, (sql, _) in all_statements().items():
            with self.subTest(name=name):
                for marker in markers:
                    self.assertNotIn(marker, sql)

    def test_every_parameter_marker_has_a_parameter_and_back(self):
        for name, (sql, params) in all_statements().items():
            with self.subTest(name=name):
                used = set(re.findall(r"(?<![:\w]):([a-z_][a-z0-9_]*)", sql))
                self.assertEqual(used, set(params))

    def test_every_parameter_type_is_allowed(self):
        from agent.dbsql import PARAM_TYPES
        for name, (_, params) in all_statements().items():
            for key, (_, type_name) in params.items():
                with self.subTest(name=name, key=key):
                    self.assertIn(type_name, PARAM_TYPES)

    def test_a_bad_target_raises(self):
        for fn in (queries.repair, queries.available_jedi, queries.available_starships, queries.create_deployment):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(ValueError):
                    fn("staging")
        with self.assertRaises(ValueError):
            queries.poll("staging", EPOCH, 5)

    def test_a_bad_table_name_raises(self):
        bad = {"prod": dict(config.TARGETS["prod"], deployment="force.gold.deployment; DROP TABLE x"),
               "dev": config.TARGETS["dev"]}
        with mock.patch.dict(config.TARGETS, bad, clear=True):
            with self.assertRaises(ValueError):
                queries.repair("prod")


class Repair(unittest.TestCase):
    def test_marks_incidents_that_already_have_a_deployment(self):
        sql, params = queries.repair("dev")
        self.assertIn("UPDATE force.dev_jivejong.gold_disturbance SET agent_processed = true", sql)
        self.assertIn("agent_processed = false", sql)
        self.assertIn("SELECT disturbance_id FROM force.dev_jivejong.deployment", sql)
        self.assertEqual(params, {})


class Poll(unittest.TestCase):
    def test_sql(self):
        sql, params = queries.poll("prod", EPOCH, 5)
        self.assertIn("d.agent_processed = false", sql)
        self.assertIn("d.detected_at >= :epoch", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM force.gold.deployment p WHERE p.disturbance_id = d.disturbance_id)", sql)
        self.assertIn("ORDER BY d.detected_at LIMIT :limit", sql)
        self.assertEqual(params, {"epoch": (EPOCH, "TIMESTAMP"), "limit": (5, "INT")})


class Context(unittest.TestCase):
    def test_sql_joins_the_sector_and_filters_by_id(self):
        sql, params = queries.context("prod", "abc")
        self.assertIn("JOIN force.gold.dim_sector s ON s.sector_id = d.sector_id", sql)
        self.assertIn("WHERE d.disturbance_id = :disturbance_id", sql)
        self.assertIn("r.source_type = 'probe'", sql)
        self.assertEqual(params, {"disturbance_id": ("abc", "STRING")})

    def test_selects_every_column_the_context_builder_reads(self):
        sql, _ = queries.context("prod", "abc")
        for column in ("channels_present", "disturbances_last_7d", "deployments_last_7d", "last_signature", "open_deployments",
                       "report_description", "report_relevance", "is_report_sourced", "force_history", "population"):
            with self.subTest(column=column):
                self.assertIn(column, sql)


class Availability(unittest.TestCase):
    def test_jedi_are_unavailable_while_a_deploy_row_names_them(self):
        sql, params = queries.available_jedi("prod")
        self.assertIn("p.decision = 'deploy' AND p.released_at > current_timestamp()", sql)
        self.assertIn("array_contains(p.jedi_ids, j.jedi_id)", sql)
        self.assertIn("LEFT JOIN force.gold.dim_sector s ON s.sector_id = j.homeworld_sector_id", sql)
        self.assertEqual(params, {})

    def test_starships_are_unavailable_while_a_deploy_row_names_them(self):
        sql, params = queries.available_starships("prod")
        self.assertIn("p.decision = 'deploy' AND p.released_at > current_timestamp()", sql)
        self.assertIn("p.starship_id = st.starship_id", sql)
        self.assertIn("FROM force.gold.dim_starship st", sql)
        self.assertEqual(params, {})


class WriteDecision(unittest.TestCase):
    def setUp(self):
        self.sql, self.params = queries.write_decision("dev", ROW)

    def test_merge_inserts_only_when_not_matched(self):
        self.assertTrue(self.sql.startswith("MERGE INTO force.dev_jivejong.deployment t"))
        self.assertIn("ON t.disturbance_id = s.disturbance_id", self.sql)
        self.assertTrue(self.sql.endswith("WHEN NOT MATCHED THEN INSERT *"))
        self.assertNotIn("WHEN MATCHED", self.sql)

    def test_parameter_types(self):
        types = {k: t for k, (_, t) in self.params.items()}
        self.assertEqual(types["eta_hours"], "DOUBLE")
        self.assertEqual(types["released_at"], "TIMESTAMP")
        self.assertEqual(types["decided_at"], "TIMESTAMP")
        self.assertEqual(types["decision_latency_ms"], "BIGINT")
        for key, type_name in types.items():
            if key not in ("eta_hours", "released_at", "decided_at", "decision_latency_ms"):
                self.assertEqual(type_name, "STRING", key)

    def test_json_parameters(self):
        import json
        self.assertEqual(json.loads(self.params["jedi_ids_json"][0]), ["marker_jedi_one", "marker_jedi_two"])
        self.assertEqual(json.loads(self.params["context_json"][0]), {"marker": "marker context value"})
        self.assertEqual(json.loads(self.params["overrides_json"][0]), [{"reason": "marker override"}])

    def test_stand_down_has_an_empty_jedi_array_and_typed_nulls(self):
        row = dict(ROW, decision="stand_down", jedi_ids=None, starship_id=None, eta_hours=None, released_at=None,
                   model=None, decision_latency_ms=None, guardrail_overrides=None)
        _, params = queries.write_decision("dev", row)
        self.assertEqual(params["jedi_ids_json"], ("[]", "STRING"))
        self.assertEqual(params["overrides_json"], ("[]", "STRING"))
        for key in ("starship_id", "eta_hours", "released_at", "model", "decision_latency_ms"):
            self.assertIsNone(params[key][0], key)


class Mark(unittest.TestCase):
    def test_only_an_unprocessed_row_is_updated(self):
        sql, params = queries.mark("prod", "abc")
        self.assertIn("WHERE disturbance_id = :disturbance_id AND agent_processed = false", sql)
        self.assertIn("UPDATE force.gold.gold_disturbance SET agent_processed = true", sql)
        self.assertEqual(params, {"disturbance_id": ("abc", "STRING")})


class DeploymentDdl(unittest.TestCase):
    def ddl_columns(self):
        sql, _ = queries.create_deployment("dev")
        body = sql[sql.index("(") + 1:sql.rindex(")")]
        return [line.strip().split()[0] for line in body.splitlines() if line.strip()]

    def doc_columns(self):
        lines = DOC03.read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("### `gold.deployment`"))
        columns = []
        for line in lines[start + 3:]:
            if not line.startswith("|"):
                break
            columns += re.findall(r"`([a-z_]+)`", line.split("|")[1])
        return columns

    def test_ddl_columns_equal_doc_03_plus_released_at(self):
        self.assertEqual(set(self.ddl_columns()), set(self.doc_columns()) | {"released_at"})

    def test_ddl_has_no_duplicate_columns(self):
        columns = self.ddl_columns()
        self.assertEqual(len(columns), len(set(columns)))

    def test_ddl_creates_the_target_table_if_missing(self):
        sql, _ = queries.create_deployment("prod")
        self.assertTrue(sql.startswith("CREATE TABLE IF NOT EXISTS force.gold.deployment ("))
        self.assertIn("USING DELTA", sql)

    def test_required_columns_are_not_null(self):
        sql, _ = queries.create_deployment("dev")
        for column in ("deployment_id", "disturbance_id", "decided_by", "decision", "rationale", "decided_at"):
            with self.subTest(column=column):
                self.assertRegex(sql, rf"\b{column} \w+ NOT NULL")


if __name__ == "__main__":
    unittest.main()
