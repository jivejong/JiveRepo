"""Tests for serving/pull.py (doc 05 "Serving (Phase 7)": "Reads"): a fake warehouse client and force_serving_test. No network."""
import json
import unittest
from datetime import datetime, timedelta, timezone

import pg_support
from agent.dbsql import TransientHTTPError
from fake_http import FakeOpener
from serving import config, fixtures, pull, statements

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
SOURCE_KEYS = {name: key for key, name in config.SOURCES.items()}
REQUIRED = {"sector_reading": ("sector_id", "event_time", "source_type", "_gold_built_at"), "disturbance": ("sector_id", "detected_at"),
            "deployment": ("disturbance_id", "decided_by", "decision", "rationale", "decided_at"),
            "probe_reading": ("event_time", "arrival_ts"), "rejects": ("arrival_ts", "reject_reason")}


class Boom(Exception):
    pass


def row(key, **over):
    """A warehouse row for a source: None everywhere except what is given."""
    out = {c: None for c in pull.SPECS[key]["cols"]}
    out.update(over)
    return out


def reading(n, built, event=None, score=1.0):
    return row("sector_reading", event_id=f"E{n}", sector_id="s1", event_time=event or built, source_type="probe",
               imbalance_score=score, _gold_built_at=built)


def probe(n, arrival, mode="CONNECTED"):
    return row("probe_reading", event_id=f"P{n}", sector_id="s1", event_time=arrival, arrival_ts=arrival, mode=mode)


def reject(n, arrival, event=None):
    return row("rejects", event_id=f"J{n}", event_time=event or arrival, arrival_ts=arrival, reject_reason="out_of_range")


def disturbance(did):
    return row("disturbance", disturbance_id=did, sector_id="s1", detected_at=NOW, severity=7.0)


def deployment(depid, did, **over):
    return row("deployment", deployment_id=depid, disturbance_id=did, decided_by="yoda_agent", decision="deploy", rationale="r",
               decided_at=NOW, **over)


class FakeClient:
    """script[key] is a list of per-call result lists (missing means []), or a callable(params) returning rows."""
    def __init__(self, script=None, fail_on=None, error=Boom):
        self.script, self.fail_on, self.error = {k: (v if callable(v) else list(v)) for k, v in (script or {}).items()}, fail_on, error
        self.calls, self.statements = [], 0

    def read(self, sql, params=None, *, expected_types=None, max_rows=None):
        self.statements += 1
        self.calls.append((sql, params, expected_types))
        if self.fail_on == self.statements:
            raise self.error("boom")
        key = next(k for name, k in SOURCE_KEYS.items() if f" FROM {name}" in sql)
        script = self.script.get(key, [])
        return script(params) if callable(script) else (script.pop(0) if script else [])

    def calls_for(self, key):
        return [c for c in self.calls if f" FROM {config.SOURCES[key]}" in c[0]]


class PullTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = pg_support.connect_test()
        pg_support.fresh_schema(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.conn.execute("TRUNCATE " + ", ".join(f"serving.{t}" for t in fixtures.TABLES) + " RESTART IDENTITY")
        self.conn.commit()

    def q(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    def count(self, table):
        return self.q(f"SELECT count(*) FROM serving.{table}")[0][0]

    def state(self):
        return {t: self.q(f"SELECT * FROM serving.{t} ORDER BY 1, 2") for t in
                ("disturbance", "deployment", "sector_baseline", "sector_reading", "probe_reading", "rejects", "watermark",
                 "source_health_snapshot", "dim_sector")}

    def first_run(self, script=None, now=NOW):
        script = dict(script or {})
        for key, one in (("dim_sector", {"sector_id": "s1"}), ("dim_jedi", {"jedi_id": "j1"}), ("dim_starship", {"starship_id": "x1"})):
            script.setdefault(key, [[row(key, **one)]])                  # real dims are never empty; empty ones would stay "due"
        return pull.run_pulls(FakeClient(script), self.conn, now)


class Backfill(PullTest):
    def test_first_run_backfills_one_page_per_day_and_sets_watermarks(self):
        built = [NOW - timedelta(minutes=30), NOW - timedelta(minutes=10)]
        client = FakeClient({"sector_reading": [[reading(1, built[0]), reading(2, built[1])]],
                             "probe_reading": [[probe(1, NOW - timedelta(days=2)), probe(2, NOW - HOUR)]],
                             "rejects": [[reject(1, NOW - timedelta(days=3))]]})
        result = pull.run_pulls(client, self.conn, NOW)
        for key in pull.INCREMENTAL_KEYS:
            self.assertEqual(len(client.calls_for(key)), 8, key)
        pages = client.calls_for("probe_reading")
        self.assertEqual(pages[0][1]["start"][0], datetime(2026, 10, 12, tzinfo=timezone.utc))
        self.assertEqual(pages[-1][1]["end"][0], NOW + HOUR)
        self.assertIn("arrival_ts >= :start", pages[0][0])
        self.assertIn("event_time >= :start", client.calls_for("sector_reading")[0][0])
        self.assertEqual(result["statements"], 3 + 4 + 3 * 8)
        wm = dict(self.q("SELECT source, value FROM serving.watermark"))
        self.assertEqual(wm, {"sector_reading": built[1], "probe_reading": NOW - HOUR, "rejects": NOW - timedelta(days=3), "dims": NOW})
        self.assertEqual(result["rows"]["sector_reading"], 2)


class Incremental(PullTest):
    def test_second_run_uses_the_stored_watermarks(self):
        built = NOW - timedelta(minutes=10)
        self.first_run({"sector_reading": [[reading(1, built)]], "probe_reading": [[probe(1, NOW - HOUR)]], "rejects": [[reject(1, NOW - HOUR)]]})
        client = FakeClient()
        result = pull.run_pulls(client, self.conn, NOW + HOUR)
        self.assertEqual(result["statements"], 7)
        sql, params, _ = client.calls_for("sector_reading")[0]
        self.assertIn("_gold_built_at > :wm", sql)
        self.assertEqual(params["wm"][0], built)
        self.assertEqual(client.calls_for("probe_reading")[0][1]["wm"][0], NOW - HOUR - HOUR)
        self.assertEqual(client.calls_for("rejects")[0][1]["wm"][0], NOW - HOUR - HOUR)

    def test_a_changed_reading_replaces_and_a_duplicate_probe_row_is_ignored(self):
        t = NOW - timedelta(minutes=10)
        self.first_run({"sector_reading": [[reading(1, t, score=1.0)]], "probe_reading": [[probe(1, NOW - HOUR, "CONNECTED")]]})
        pull.run_pulls(FakeClient({"sector_reading": [[reading(1, t + HOUR, event=t, score=9.5)]],
                                   "probe_reading": [[probe(1, NOW - HOUR, "DISCONNECTED"), probe(2, NOW)]]}), self.conn, NOW + HOUR)
        self.assertEqual(self.q("SELECT imbalance_score, _gold_built_at FROM serving.sector_reading"), [(9.5, t + HOUR)])
        self.assertEqual(self.q("SELECT event_id, mode FROM serving.probe_reading ORDER BY 1"), [("P1", "CONNECTED"), ("P2", "CONNECTED")])

    def test_an_empty_incremental_result_leaves_the_watermark(self):
        self.first_run({"rejects": [[reject(1, NOW - HOUR)]]})
        before = self.q("SELECT value FROM serving.watermark WHERE source = 'rejects'")
        pull.run_pulls(FakeClient(), self.conn, NOW + HOUR)
        self.assertEqual(self.q("SELECT value FROM serving.watermark WHERE source = 'rejects'"), before)


class FullTables(PullTest):
    def test_a_vanished_disturbance_goes_and_an_orphan_deployment_stays(self):
        self.first_run({"disturbance": [[disturbance("D1"), disturbance("D2")]], "deployment": [[deployment("X1", "D1")]]})
        self.assertEqual(self.count("disturbance"), 2)
        pull.run_pulls(FakeClient({"disturbance": [[disturbance("D3")]], "deployment": [[deployment("X1", "D1"), deployment("X2", "GONE")]]}),
                       self.conn, NOW + HOUR)
        self.assertEqual(self.q("SELECT disturbance_id FROM serving.disturbance"), [("D3",)])
        self.assertEqual(sorted(r[0] for r in self.q("SELECT deployment_id FROM serving.deployment")), ["X1", "X2"])

    def test_one_snapshot_per_run(self):
        shr = row("source_health", source_id="probe-01", current_mode="CONNECTED", last_seen_at=NOW, last_scan_id="S", scan_completeness=True)
        self.first_run({"source_health": [[shr]]})
        pull.run_pulls(FakeClient({"source_health": [[shr]]}), self.conn, NOW + HOUR)
        self.assertEqual(self.q("SELECT published_at FROM serving.source_health_snapshot ORDER BY 1"), [(NOW,), (NOW + HOUR,)])

    def test_json_columns_are_jsonb_and_a_null_variant_is_null(self):
        over = [{"proposal": None, "reasons": ["deployment_capacity_reached"]}]
        self.first_run({"deployment": [[deployment("X1", "D1", jedi_ids=["a", "b"], guardrail_overrides=over, context_snapshot=None)]]})
        r = self.q("SELECT jsonb_typeof(jedi_ids), jedi_ids, jsonb_typeof(guardrail_overrides), context_snapshot IS NULL FROM serving.deployment")[0]
        self.assertEqual(r, ("array", ["a", "b"], "array", True))


class Dims(PullTest):
    def fill_dims(self):
        self.conn.execute("INSERT INTO serving.dim_sector (sector_id) VALUES ('s1')")
        self.conn.execute("INSERT INTO serving.dim_jedi (jedi_id) VALUES ('j1')")
        self.conn.execute("INSERT INTO serving.dim_starship (starship_id) VALUES ('x1')")
        self.conn.commit()

    def test_dims_due(self):
        self.assertTrue(pull.dims_due(self.conn, NOW))                                  # empty
        self.fill_dims()
        self.assertTrue(pull.dims_due(self.conn, NOW))                                  # no watermark
        for age, want in ((timedelta(hours=23), False), (timedelta(hours=25), True)):
            self.conn.execute("INSERT INTO serving.watermark VALUES ('dims', %s) ON CONFLICT (source) DO UPDATE SET value = EXCLUDED.value",
                              (NOW - age,))
            self.conn.commit()
            self.assertEqual(pull.dims_due(self.conn, NOW), want, age)

    def test_dims_are_pulled_when_due_and_not_otherwise(self):
        self.fill_dims()
        self.conn.execute("INSERT INTO serving.watermark VALUES ('dims', %s)", (NOW - timedelta(hours=2),))
        self.conn.commit()
        client = FakeClient()
        pull.run_pulls(client, self.conn, NOW)
        self.assertFalse(client.calls_for("dim_sector"))
        self.assertEqual(self.count("dim_sector"), 1)
        stmts = pull.run_pulls(FakeClient(), self.conn, NOW + timedelta(hours=24))["statements"]
        self.assertGreaterEqual(stmts, 10)


class Statements(PullTest):
    def test_steady_and_backfill_counts(self):
        incremental = {"sector_reading": [[reading(1, NOW - HOUR)]], "probe_reading": [[probe(1, NOW - HOUR)]], "rejects": [[reject(1, NOW - HOUR)]]}
        self.assertEqual(self.first_run(incremental)["statements"], 31)                # dims 3 + 4 + 3 sources x 8 pages
        self.assertEqual(pull.run_pulls(FakeClient(), self.conn, NOW + HOUR)["statements"], 7)
        self.assertEqual(pull.run_pulls(FakeClient(), self.conn, NOW + timedelta(hours=26))["statements"], 10)   # dims due again

    def test_every_generated_sql_is_read_only_and_has_no_star(self):
        client = FakeClient()
        pull.run_pulls(client, self.conn, NOW)
        pull.run_pulls(client, self.conn, NOW + timedelta(hours=26))
        self.assertTrue(client.calls)
        for sql, _, types in client.calls:
            statements.check_read_only(sql)
            self.assertNotIn("*", sql)
            self.assertNotIn("ORDER BY", sql.upper())
            self.assertTrue(types)


class Retention(PullTest):
    def test_only_rows_older_than_30_days_go_on_the_right_column(self):
        old, recent = NOW - timedelta(days=31), NOW - timedelta(days=1)
        for i, (event, built) in enumerate(((old, recent), (recent, old))):
            self.conn.execute("INSERT INTO serving.sector_reading (event_id, sector_id, event_time, source_type, _gold_built_at) "
                              "VALUES (%s, 's', %s, 'probe', %s)", (f"R{i}", event, built))
        for i, (event, arrival) in enumerate(((old, recent), (recent, old))):
            self.conn.execute("INSERT INTO serving.probe_reading (event_id, event_time, arrival_ts) VALUES (%s, %s, %s)", (f"P{i}", event, arrival))
            self.conn.execute("INSERT INTO serving.rejects (event_id, event_time, arrival_ts, reject_reason) VALUES (%s, %s, %s, 'x')", (f"J{i}", event, arrival))
        self.conn.execute("INSERT INTO serving.source_health_snapshot (published_at, source_id) VALUES (%s, 'a'), (%s, 'b')", (old, recent))
        self.conn.commit()
        self.first_run()
        self.assertEqual(self.q("SELECT event_id FROM serving.sector_reading"), [("R1",)])    # event_time decides
        self.assertEqual(self.q("SELECT event_id FROM serving.probe_reading"), [("P0",)])     # arrival_ts decides
        self.assertEqual(self.q("SELECT event_id FROM serving.rejects"), [("J0",)])           # arrival_ts decides
        self.assertEqual(self.q("SELECT source_id FROM serving.source_health_snapshot ORDER BY 1"), [("b",)])


def wire(key, row):
    """A warehouse row as the Statement API sends it: every cell a string (or null)."""
    def cell(v):
        if v is None:
            return None
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(v, (list, dict)):
            return json.dumps(v)
        return str(v)
    return [cell(row[c]) for c in pull.SPECS[key]["cols"]]


class Retries(PullTest):
    """The real serving client over a fake transport: a transient error is retried once, a second one fails the run."""

    def client(self, transient):
        sent = {"posts": 0}

        def handler(method, url, body):
            if method != "POST":
                return 500, {}
            sent["posts"] += 1
            if sent["posts"] <= transient:
                return 503, {}
            key = next(k for name, k in SOURCE_KEYS.items() if f" FROM {name}" in body["statement"])
            cols = [{"name": c, "type_name": t} for c, t in pull.SPECS[key]["types"].items()]
            data = [wire(key, disturbance("D1"))] if key == "disturbance" else []
            return 200, {"statement_id": "S", "status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": cols}},
                         "result": {"data_array": data}}
        return statements.ServingStatementClient("example.invalid", "t", "w", opener=FakeOpener(handler), sleep=lambda s: None,
                                                 monotonic=lambda: 0.0), sent

    def warm(self, extra=None):
        script = {"sector_reading": [[reading(1, NOW - HOUR)]], "probe_reading": [[probe(1, NOW - HOUR)]], "rejects": [[reject(1, NOW - HOUR)]]}
        script.update(extra or {})
        self.first_run(script)

    def test_one_transient_error_then_success_writes_the_data(self):
        self.warm()
        client, sent = self.client(1)
        try:
            pull.run_pulls(client, self.conn, NOW + HOUR)
        except TransientHTTPError:
            self.fail("one transient error must be retried")
        self.assertEqual(self.q("SELECT disturbance_id FROM serving.disturbance"), [("D1",)])
        self.assertEqual(sent["posts"], 8)                       # 7 reads + 1 retry

    def test_two_transient_errors_fail_the_run_and_write_nothing(self):
        self.warm({"disturbance": [[disturbance("OLD")]]})
        before = self.state()
        client, _ = self.client(2)
        with self.assertRaises(TransientHTTPError):
            pull.run_pulls(client, self.conn, NOW + HOUR)
        self.assertEqual(self.state(), before)


class Atomicity(PullTest):
    def seed(self):
        self.first_run({"disturbance": [[disturbance("OLD")]], "sector_reading": [[reading(1, NOW - HOUR)]],
                        "probe_reading": [[probe(1, NOW - HOUR)]]})
        return self.state()

    def test_a_client_error_on_the_fifth_read_changes_nothing(self):
        before = self.seed()
        client = FakeClient({"disturbance": [[disturbance("NEW")]], "sector_reading": [[reading(2, NOW)]]}, fail_on=5)
        with self.assertRaises(Boom):
            pull.run_pulls(client, self.conn, NOW + HOUR)
        self.assertEqual(self.state(), before)

    def test_a_schema_mismatch_changes_nothing(self):
        before = self.seed()
        client = FakeClient({"disturbance": [[disturbance("NEW")]]}, fail_on=3, error=statements.SchemaMismatch)
        with self.assertRaises(statements.SchemaMismatch):
            pull.run_pulls(client, self.conn, NOW + HOUR)
        self.assertEqual(self.state(), before)

    def test_a_failure_while_writing_rolls_back_the_earlier_tables(self):
        before = self.seed()
        bad = probe(9, None)                                    # arrival_ts NOT NULL fails after the disturbance table was replaced
        client = FakeClient({"disturbance": [[disturbance("NEW")]], "probe_reading": [[bad]]})
        with self.assertRaises(Exception):
            pull.run_pulls(client, self.conn, NOW + HOUR)
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
