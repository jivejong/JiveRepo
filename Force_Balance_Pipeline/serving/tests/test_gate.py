"""Tests for serving/gate.py (doc 05 "Serving (Phase 7)": "Gate"): the pure decision, and last_live_ok against force_serving_test."""
import unittest
from datetime import datetime, timedelta, timezone

import pg_support
from serving import gate

T = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
NOON = T
RUN_A = (100, T)
RUN_B = (101, T + timedelta(hours=1))


class Decide(unittest.TestCase):
    def test_each_branch(self):
        self.assertEqual(gate.decide(NOON, None, None), "skip_no_success")
        self.assertEqual(gate.decide(NOON, RUN_A, None), "publish")
        self.assertEqual(gate.decide(NOON, RUN_B, RUN_A), "publish")
        self.assertEqual(gate.decide(NOON, RUN_A, RUN_A), "skip_no_new_run")
        self.assertEqual(gate.decide(NOON, RUN_A, RUN_B), "skip_no_new_run")

    def test_quota_hour_boundaries(self):
        for stamp, want in ((datetime(2026, 10, 19, 2, 59, 59, tzinfo=timezone.utc), "publish"),
                            (datetime(2026, 10, 19, 3, 0, 0, tzinfo=timezone.utc), "skip_quota_hour"),
                            (datetime(2026, 10, 19, 3, 59, 59, tzinfo=timezone.utc), "skip_quota_hour"),
                            (datetime(2026, 10, 19, 4, 0, 0, tzinfo=timezone.utc), "publish")):
            self.assertEqual(gate.decide(stamp, RUN_B, RUN_A), want, stamp)

    def test_order_of_checks(self):
        three = datetime(2026, 10, 19, 3, 30, tzinfo=timezone.utc)
        self.assertEqual(gate.decide(three, None, None), "skip_quota_hour")
        self.assertEqual(gate.decide(three, RUN_A, RUN_A), "skip_quota_hour")

    def test_equal_ends_and_same_run_id(self):
        self.assertEqual(gate.decide(NOON, (200, T), (100, T)), "skip_no_new_run")          # equal ends, different ids
        self.assertEqual(gate.decide(NOON, (100, T + timedelta(hours=2)), (100, T)), "skip_no_new_run")   # newer end, same id
        self.assertEqual(gate.decide(NOON, (200, T + timedelta(seconds=1)), (100, T)), "publish")


class LastLiveOk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = pg_support.connect_test()
        pg_support.fresh_schema(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.conn.execute("TRUNCATE serving.publish_log RESTART IDENTITY")
        self.conn.commit()

    def add(self, kind, status, run_id, started_hours):
        self.conn.execute("INSERT INTO serving.publish_log (kind, started_at, status, job1_run_id, job1_run_end) VALUES (%s, %s, %s, %s, %s)",
                          (kind, T + timedelta(hours=started_hours), status, run_id, T + timedelta(hours=started_hours)))
        self.conn.commit()

    def test_none_on_an_empty_table(self):
        self.assertIsNone(gate.last_live_ok(self.conn))

    def test_fixture_failed_and_skipped_rows_never_count(self):
        self.add("fixture", "ok", 1, 5)
        self.add("live", "failed", 2, 6)
        self.add("live", "skipped", 3, 7)
        self.assertIsNone(gate.last_live_ok(self.conn))

    def test_the_newest_live_ok_row_wins(self):
        self.add("live", "ok", 10, 1)
        self.add("live", "ok", 11, 3)
        self.add("live", "failed", 12, 4)
        self.add("fixture", "ok", 13, 5)
        self.assertEqual(gate.last_live_ok(self.conn), (11, T + timedelta(hours=3)))


if __name__ == "__main__":
    unittest.main()
