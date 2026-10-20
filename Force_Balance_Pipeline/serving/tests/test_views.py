"""Tests for serving/sql/views.sql (doc 07 Phase 7 "Panels" and "Display rules"; doc 06 "Re-escalation"), against force_serving_test.
Each test truncates the serving tables and inserts only the rows it needs; one smoke test loads the fixtures instead."""
import ast
import itertools
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pg_support
from agent import constraints
from agent.config import AGENT_EPOCH
from psycopg.types.json import Jsonb
from serving import fixtures, views

ROOT = Path(__file__).resolve().parents[2]
VIEWS_SQL = (ROOT / "serving" / "sql" / "views.sql").read_text(encoding="utf-8")
T0 = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
ALL_VIEWS = ("v_galaxy", "v_baseline_bands", "v_incidents", "v_signature_breakdown", "v_scan_completeness", "v_source_health",
             "v_ingest_lag_histogram", "v_rejection_rate", "v_rejects_by_reason", "v_deployment_log")


class ViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = pg_support.connect_test()
        pg_support.fresh_schema(cls.conn)
        views.apply(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.conn.execute("TRUNCATE " + ", ".join(f"serving.{t}" for t in fixtures.TABLES) + " RESTART IDENTITY")
        self.conn.commit()
        self.ids = itertools.count(1)

    def q(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    # -- row helpers
    def sector(self, sid, region="Core Worlds"):
        self.conn.execute("INSERT INTO serving.dim_sector (sector_id, sector_name, region, is_unknown) VALUES (%s, %s, %s, false)",
                          (sid, sid.title(), region))

    def jedi(self, jid, rank):
        self.conn.execute("INSERT INTO serving.dim_jedi (jedi_id, rank) VALUES (%s, %s)", (jid, rank))

    def reading(self, sid, when, score, source_type="probe"):
        n = next(self.ids)
        self.conn.execute("INSERT INTO serving.sector_reading (event_id, sector_id, event_time, source_type, imbalance_score, "
                          "_gold_built_at) VALUES (%s, %s, %s, %s, %s, %s)", (f"R{n}", sid, when, source_type, score, T0))

    def incident(self, did, severity=7.0, detected=T0, sector="s1", signature="sith_presence", report=False):
        self.conn.execute("INSERT INTO serving.disturbance (disturbance_id, sector_id, detected_at, severity, signature, "
                          "is_report_sourced) VALUES (%s, %s, %s, %s, %s, %s)", (did, sector, detected, severity, signature, report))

    def deployment(self, depid, did, decision="deploy", decided=T0, eta=0.5, jedi=(), snapshot=None, overrides=None):
        ctx = None if snapshot is None else Jsonb({"severity": snapshot})
        self.conn.execute(
            "INSERT INTO serving.deployment (deployment_id, disturbance_id, decided_by, decision, jedi_ids, eta_hours, rationale, "
            "context_snapshot, guardrail_overrides, decided_at) VALUES (%s, %s, 'yoda_agent', %s, %s, %s, 'r', %s, %s, %s)",
            (depid, did, decision, Jsonb(list(jedi)), eta, ctx, None if overrides is None else Jsonb(overrides), decided))

    def probe(self, event_time, arrival, lag, scan="S1", mode="CONNECTED", replayed=False, buffered=False):
        n = next(self.ids)
        self.conn.execute("INSERT INTO serving.probe_reading (event_id, source_id, sector_id, scan_id, event_time, arrival_ts, mode, "
                          "ingest_lag_seconds, is_replayed, was_buffered) VALUES (%s, 'probe-01', %s, %s, %s, %s, %s, %s, %s, %s)",
                          (f"P{n}", f"s{n}", scan, event_time, arrival, mode, lag, replayed, buffered))

    def reject(self, arrival, reason="out_of_range"):
        n = next(self.ids)
        self.conn.execute("INSERT INTO serving.rejects (event_id, arrival_ts, reject_reason) VALUES (%s, %s, %s)",
                          (f"J{n}", arrival, reason))


class Galaxy(ViewTest):
    def test_band_edges(self):
        for sid, score in (("a", 4.0), ("b", 4.0001), ("c", 5.75), ("d", 5.7501), ("e", None)):
            self.sector(sid)
            self.reading(sid, T0, score)
        self.sector("f")                                       # no reading at all
        bands = dict(self.q("SELECT sector_id, band FROM serving.v_galaxy"))
        self.assertEqual(bands, {"a": "normal", "b": "anomaly", "c": "anomaly", "d": "emergency", "e": "no_data", "f": "no_data"})

    def test_newest_probe_row_wins_and_a_report_row_is_ignored(self):
        self.sector("n")
        self.reading("n", T0 - timedelta(hours=1), 7.0)
        self.reading("n", T0, 1.0)
        self.sector("r")
        self.reading("r", T0 - timedelta(hours=1), 1.0)
        self.reading("r", T0, 9.0, source_type="report")
        got = {s: (score, band) for s, score, band in self.q("SELECT sector_id, imbalance_score, band FROM serving.v_galaxy")}
        self.assertEqual(got["n"], (1.0, "normal"))
        self.assertEqual(got["r"], (1.0, "normal"))

    def test_open_disturbance_id_is_the_newest_open_incident(self):
        self.sector("s1")
        self.incident("OLD", detected=T0 - timedelta(hours=2))
        self.incident("NEW", detected=T0)
        self.assertEqual(self.q("SELECT open_disturbance_id FROM serving.v_galaxy")[0][0], "NEW")
        self.deployment("D1", "NEW")
        self.assertEqual(self.q("SELECT open_disturbance_id FROM serving.v_galaxy")[0][0], "OLD")


class Incidents(ViewTest):
    def test_status_at_the_epoch(self):
        self.sector("s1")
        self.incident("AT", detected=AGENT_EPOCH)
        self.incident("BEFORE", detected=AGENT_EPOCH - timedelta(seconds=1))
        self.incident("DEC", detected=AGENT_EPOCH - timedelta(days=3))
        self.deployment("D1", "DEC")
        status = dict(self.q("SELECT disturbance_id, status FROM serving.v_incidents"))
        self.assertEqual(status, {"AT": "open", "BEFORE": "historical", "DEC": "decided"})

    def test_drift_rules(self):
        for jid, rank in (("jm", "master"), ("jc", "council_member"), ("jg", "grand_master"), ("jk", "knight")):
            self.jedi(jid, rank)
        cases = {                                      # id: (decision, jedi, snapshot, severity, expected)
            "S1": ("stand_down", [], 4.3, 6.5, True),
            "S2": ("stand_down", [], 4.5, 6.5, False),
            "S3": ("stand_down", [], 4.3, 4.39, False),
            "D1": ("deploy", ["jk"], 11.9, 12.5, True),
            "D2": ("deploy", ["jk", "jm"], 11.9, 12.5, False),
            "D3": ("deploy", ["jc"], 11.9, 12.5, False),
            "D4": ("deploy", ["jg"], 11.9, 12.5, False),
            "D5": ("deploy", ["jk"], 11.9, 12.0, False),
            "D6": ("deploy", ["jk"], 12.5, 14.0, False),
            "N1": ("deploy", ["jk"], None, 14.0, False),
            "N2": ("stand_down", [], None, 6.5, False),
        }
        for did, (decision, jedi, snap, sev, _) in cases.items():
            self.incident(did, severity=sev)
            self.deployment("X" + did, did, decision=decision, jedi=jedi, snapshot=snap)
        self.incident("NODEP", severity=30.0)
        got = dict(self.q("SELECT disturbance_id, severity_rose_after_decision FROM serving.v_incidents"))
        for did, case in cases.items():
            self.assertIs(got[did], case[4], did)
        self.assertIs(got["NODEP"], False)

    def test_two_deployments_give_one_row_with_the_newer(self):
        self.sector("s1")
        self.incident("I1")
        self.deployment("OLDER", "I1", decided=T0 - timedelta(hours=1), decision="stand_down")
        self.deployment("NEWER", "I1", decided=T0)
        rows = self.q("SELECT deployment_id, decision FROM serving.v_incidents")
        self.assertEqual(rows, [("NEWER", "deploy")])

    def test_signature_breakdown_counts(self):
        self.sector("s1")
        self.incident("A", signature="x", report=True)
        self.incident("B", signature="x", detected=AGENT_EPOCH - timedelta(days=1))
        self.deployment("D", "A")
        row = self.q("SELECT incidents_total, incidents_open, incidents_decided, incidents_report_sourced FROM "
                     "serving.v_signature_breakdown WHERE signature = 'x'")[0]
        self.assertEqual(row, (2, 0, 1, 1))


class DeploymentLog(ViewTest):
    def test_orphan_arrival_and_overrides(self):
        self.sector("s1")
        self.incident("I1")
        cap = [{"proposal": None, "reasons": ["jedi_unavailable", "deployment_capacity_reached"]}]
        self.deployment("MATCH", "I1", decided=T0, eta=1.5, overrides=cap)
        self.deployment("ORPH", "GONE", decided=T0, eta=None, overrides=None)
        self.deployment("OTHER", "I1", decided=T0, overrides=[{"proposal": None, "reasons": ["jedi_unavailable"]}])
        rows = {r[0]: r[1:] for r in self.q("SELECT deployment_id, orphaned, arrival_at, override_count, capacity_reached, sector_id "
                                             "FROM serving.v_deployment_log")}
        self.assertEqual(sorted(rows), ["MATCH", "ORPH", "OTHER"])
        self.assertEqual(rows["MATCH"],(False, T0 + timedelta(hours=1.5), 1, True, "s1"))
        self.assertEqual(rows["ORPH"], (True, None, 0, False, None))
        self.assertEqual(rows["OTHER"][3], False)


class Histogram(ViewTest):
    def bins(self):
        return {r[0]: r[1:] for r in self.q("SELECT bin_order, rows, replayed_rows, buffered_rows FROM serving.v_ingest_lag_histogram")}

    def test_all_ten_bins_exist_on_an_empty_table(self):
        got = self.bins()
        self.assertEqual(sorted(got), list(range(1, 11)))
        self.assertTrue(all(v == (0, 0, 0) for v in got.values()))

    def test_edges_and_exclusions(self):
        self.probe(T0, T0, 3)                                                       # the anchor row: bin 2
        self.probe(T0 - timedelta(hours=1), T0 - timedelta(hours=1), -1)            # bin 1
        self.probe(T0 - timedelta(hours=2), T0 - timedelta(hours=1), 1800, replayed=False, buffered=True)   # bin 8
        self.probe(T0 + timedelta(hours=1), T0, -3600)                              # future event_time: excluded
        self.probe(T0 - timedelta(days=9), T0 - timedelta(days=8), 3)               # older than anchor - 7 days: excluded
        got = self.bins()
        self.assertEqual(got[1], (1, 0, 0))
        self.assertEqual(got[2], (1, 0, 0))
        self.assertEqual(got[8], (1, 0, 1))
        self.assertEqual(sum(v[0] for v in got.values()), 3)


class Rates(ViewTest):
    def test_rate_per_hour(self):
        h = lambda n: T0.replace(hour=n, minute=10)  # noqa: E731
        self.reject(h(10)); self.reject(h(10))                                       # only rejects
        self.probe(h(11), h(11), 3); self.probe(h(11), h(11), 3)                    # only readings
        self.reject(h(13)); [self.probe(h(13), h(13), 3) for _ in range(3)]       # 1 of 4
        rows = {r[0].hour: r[1:] for r in self.q("SELECT hour, rejects, accepted, rate FROM serving.v_rejection_rate")}
        self.assertEqual(sorted(rows), [10, 11, 13])                                 # hour 12 is absent
        self.assertEqual(rows[10], (2, 0, 1.0))
        self.assertEqual(rows[11], (0, 2, 0.0))
        self.assertEqual(rows[13], (1, 3, 0.25))

    def test_rejects_by_reason(self):
        self.reject(T0, "out_of_range"); self.reject(T0, "out_of_range"); self.reject(T0, "unknown_sector")
        got = dict(self.q("SELECT reject_reason, rejects FROM serving.v_rejects_by_reason"))
        self.assertEqual(got, {"out_of_range": 2, "unknown_sector": 1})


class SourceHealth(ViewTest):
    def test_scan_completeness(self):
        for _ in range(55):
            self.probe(T0, T0, 3, scan="SHORT")
        for _ in range(60):
            self.probe(T0, T0, 3, scan="FULL")
        got = dict(self.q("SELECT scan_id, complete FROM serving.v_scan_completeness"))
        self.assertEqual(got, {"SHORT": False, "FULL": True})

    def test_newest_snapshot_and_newest_scan(self):
        for when, mode in ((T0 - timedelta(hours=1), "DISCONNECTED"), (T0, "CONNECTED")):
            self.conn.execute("INSERT INTO serving.source_health_snapshot VALUES (%s, 'probe-01', %s, %s, 'x', true)", (when, mode, when))
        for n in range(60):
            self.probe(T0 - timedelta(minutes=15), T0, 3, scan="OLD")
            self.probe(T0, T0, 3, scan="NEW")
        row = self.q("SELECT current_mode, last_scan_id, last_scan_rows, last_scan_complete FROM serving.v_source_health")
        self.assertEqual(row, [("CONNECTED", "NEW", 60, True)])


class Smoke(ViewTest):
    def test_every_view_returns_on_the_fixtures(self):
        fixtures.load(self.conn, T0, reset=True)
        for name in ALL_VIEWS:
            self.assertGreater(len(self.q(f"SELECT * FROM serving.{name}")), 0, name)


class Parity(unittest.TestCase):
    def test_thresholds_match_forcesim(self):
        tree = ast.parse((ROOT / "edge" / "forcesim" / "signatures.py").read_text(encoding="utf-8"))
        consts = {n.targets[0].id: n.value.value for n in tree.body
                  if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
        self.assertIn(f"> {consts['EMERGENCY_THRESHOLD']} THEN 'emergency'", VIEWS_SQL)
        self.assertIn(f"> {consts['ANOMALY_THRESHOLD']} THEN 'anomaly'", VIEWS_SQL)

    def test_epoch_floor_master_line_and_ranks_match_the_agent(self):
        m = re.search(r"timestamptz '([^']+)'", VIEWS_SQL)
        self.assertEqual(datetime.fromisoformat(m.group(1).replace("+00", "+00:00")), AGENT_EPOCH)
        self.assertIn(f"snapshot_severity < {constraints.SEVERITY_FLOOR:.2f}", VIEWS_SQL)
        self.assertIn(f"severity >= {constraints.SEVERITY_FLOOR:.2f}", VIEWS_SQL)
        self.assertIn(f"snapshot_severity <= {constraints.MASTER_SEVERITY}", VIEWS_SQL)
        self.assertIn(f"severity > {constraints.MASTER_SEVERITY}", VIEWS_SQL)
        ranks = {r for r, v in constraints.RANK_ORDER.items() if v >= constraints.MASTER_RANK}
        found = set(re.findall(r"'(\w+)'", re.search(r"dj\.rank IN \(([^)]*)\)", VIEWS_SQL).group(1)))
        self.assertEqual(found, ranks)


if __name__ == "__main__":
    unittest.main()
