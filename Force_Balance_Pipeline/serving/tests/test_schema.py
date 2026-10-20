"""Tests for serving/sql/schema.sql and serving/schema.py (doc 05 "Serving (Phase 7)"), against force_serving_test."""
import unittest

import pg_support
from serving import schema

EXPECTED_PK = {
    "dim_sector": ["sector_id"], "dim_jedi": ["jedi_id"], "dim_starship": ["starship_id"],
    "sector_baseline": ["sector_id", "channel"], "sector_reading": ["event_id"], "disturbance": ["disturbance_id"],
    "deployment": ["deployment_id"], "probe_reading": ["event_id"], "rejects": ["event_id"],
    "source_health_snapshot": ["published_at", "source_id"], "publish_log": ["publish_id"], "watermark": ["source"],
}


class Schema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = pg_support.connect_test()
        pg_support.fresh_schema(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def rows(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    def test_applying_it_twice_succeeds_and_keeps_twelve_tables(self):
        self.assertEqual(schema.apply(self.conn), 12)
        self.assertEqual(schema.apply(self.conn), 12)

    def test_every_table_has_its_primary_key(self):
        for table, columns in EXPECTED_PK.items():
            found = [r[0] for r in self.rows(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name "
                "AND kcu.table_schema = tc.table_schema AND kcu.table_name = tc.table_name "
                "WHERE tc.table_schema = 'serving' AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position", table)]
            self.assertEqual(found, columns, table)

    def test_the_json_columns_are_jsonb(self):
        for table, column in (("deployment", "jedi_ids"), ("deployment", "context_snapshot"),
                              ("deployment", "guardrail_overrides"), ("publish_log", "row_counts")):
            kind = self.rows("SELECT data_type FROM information_schema.columns WHERE table_schema = 'serving' "
                             "AND table_name = %s AND column_name = %s", table, column)[0][0]
            self.assertEqual(kind, "jsonb", f"{table}.{column}")

    def test_deployment_has_no_foreign_key(self):
        n = self.rows("SELECT count(*) FROM information_schema.table_constraints WHERE table_schema = 'serving' "
                      "AND table_name = 'deployment' AND constraint_type = 'FOREIGN KEY'")[0][0]
        self.assertEqual(n, 0)

    def test_publish_log_checks_kind_and_status(self):
        for sql in ("INSERT INTO serving.publish_log (kind, started_at, status) VALUES ('other', now(), 'ok')",
                    "INSERT INTO serving.publish_log (kind, started_at, status) VALUES ('live', now(), 'other')"):
            with self.assertRaises(Exception):
                self.conn.execute(sql)
            self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
