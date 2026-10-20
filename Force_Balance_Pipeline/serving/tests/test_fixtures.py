"""Tests for serving/fixtures.py (doc 05 "Serving (Phase 7)"): determinism, the designed cases and the loader's guards.
Run against force_serving_test; nothing here writes to force_serving."""
import unittest
from datetime import datetime, timedelta, timezone

import pg_support
from serving import fixtures, views

NOW = datetime(2026, 10, 19, 15, 45, tzinfo=timezone.utc)
EPOCH = datetime(2026, 10, 12, tzinfo=timezone.utc)


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = pg_support.connect_test()
        pg_support.fresh_schema(cls.conn)
        views.apply(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.counts = fixtures.load(self.conn, NOW, reset=True)

    def q(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    def digest(self):
        return self.q("SELECT md5(string_agg(event_id || ':' || imbalance_score::text, ',' ORDER BY event_id)) "
                      "FROM serving.sector_reading")[0][0]

    def test_two_loads_with_the_same_now_are_identical(self):
        first, digest = dict(self.counts), self.digest()
        second = fixtures.load(self.conn, NOW, reset=True)
        self.assertEqual(first, second)
        self.assertEqual(digest, self.digest())

    def test_row_counts(self):
        self.assertEqual(self.counts["sector_reading"], 288 * 60)
        self.assertEqual(self.counts["probe_reading"], 288 * 60 - 5)
        self.assertEqual((self.counts["dim_sector"], self.counts["dim_jedi"], self.counts["dim_starship"]), (60, 17, 13))
        self.assertEqual((self.counts["sector_baseline"], self.counts["disturbance"], self.counts["deployment"]), (180, 5, 4))

    def test_d4_is_the_only_open_undecided_incident(self):
        rows = self.q("SELECT d.disturbance_id FROM serving.disturbance d WHERE NOT d.agent_processed AND d.detected_at >= %s "
                      "AND NOT EXISTS (SELECT 1 FROM serving.deployment p WHERE p.disturbance_id = d.disturbance_id)", EPOCH)
        self.assertEqual(rows, [("FIXD4",)])

    def test_the_designed_lag_cases_exist(self):
        self.assertEqual(self.q("SELECT count(*) FROM serving.probe_reading WHERE ingest_lag_seconds = -1")[0][0], 5)
        self.assertEqual(self.q("SELECT count(*) FROM serving.probe_reading WHERE is_replayed")[0][0], 120)   # 2 scans x 60 sectors
        self.assertEqual(self.q("SELECT count(*) FROM serving.probe_reading WHERE was_buffered AND mode = 'DISCONNECTED'")[0][0], 240)
        short = self.q("SELECT scan_id, count(*) FROM serving.probe_reading GROUP BY scan_id HAVING count(*) < 60")
        self.assertEqual([n for _, n in short], [55])

    def test_the_reject_spike_hour_has_twenty(self):
        start = NOW - timedelta(hours=12)
        n = self.q("SELECT count(*) FROM serving.rejects WHERE arrival_ts >= %s AND arrival_ts < %s", start, start + timedelta(hours=1))
        self.assertEqual(n[0][0], 20)
        self.assertEqual(self.q("SELECT count(*) FROM serving.rejects")[0][0], 40)

    def test_the_orphan_matches_no_disturbance(self):
        n = self.q("SELECT count(*) FROM serving.deployment p WHERE p.disturbance_id = 'FIXORPHAN' AND EXISTS "
                   "(SELECT 1 FROM serving.disturbance d WHERE d.disturbance_id = p.disturbance_id)")
        self.assertEqual(n[0][0], 0)
        self.assertEqual(self.q("SELECT count(*) FROM serving.deployment WHERE disturbance_id = 'FIXORPHAN'")[0][0], 1)

    def test_deployment_cases(self):
        user = self.q("SELECT model, decision_latency_ms, rationale FROM serving.deployment WHERE decided_by = 'user'")
        self.assertEqual(user, [(None, None, "Council asked me to act. <b>not bold</b>")])
        stand = self.q("SELECT jedi_ids FROM serving.deployment WHERE decision = 'stand_down'")
        self.assertTrue(stand and all(r[0] == [] for r in stand))
        self.assertEqual(self.q("SELECT count(*) FROM serving.deployment WHERE rationale IS NULL OR rationale = ''")[0][0], 0)

    def test_the_capacity_stand_down_has_the_agents_override_shape(self):
        rows = self.q("SELECT capacity_reached, guardrail_overrides FROM serving.v_deployment_log WHERE deployment_id = 'FIXDEP2'")
        self.assertTrue(rows[0][0])
        element = rows[0][1][0]
        self.assertEqual(sorted(element), ["proposal", "reasons"])
        self.assertEqual(sorted(element["proposal"]), ["jedi_ids", "rationale", "starship_id"])
        self.assertEqual(self.q("SELECT count(*) FROM serving.v_deployment_log WHERE capacity_reached")[0][0], 1)

    def test_the_loader_refuses_when_a_live_publish_row_exists(self):
        self.conn.execute("INSERT INTO serving.publish_log (kind, started_at, status) VALUES ('live', now(), 'ok')")
        self.conn.commit()
        self.addCleanup(lambda: (self.conn.execute("DELETE FROM serving.publish_log WHERE kind = 'live'"), self.conn.commit()))
        before = self.q("SELECT count(*) FROM serving.sector_reading")[0][0]
        with self.assertRaises(fixtures.Refused):
            fixtures.load(self.conn, NOW, reset=True)
        self.assertEqual(self.q("SELECT count(*) FROM serving.sector_reading")[0][0], before)
        self.assertEqual(fixtures.main(["--database", pg_support.TEST_DB, "--reset", "--now", "2026-10-19T15:45:00Z"]), 2)

    def test_the_loader_refuses_without_reset_when_a_table_is_not_empty(self):
        with self.assertRaises(fixtures.Refused):
            fixtures.load(self.conn, NOW, reset=False)
        self.assertEqual(fixtures.main(["--database", pg_support.TEST_DB, "--now", "2026-10-19T15:45:00Z"]), 2)


if __name__ == "__main__":
    unittest.main()
