"""Tests for serving/deploy.py (doc 06 "User-initiated deployment"): the manual deployment service on the agent's own code path, with fakes
for the agent client, the serving client, the Jobs check, sleep and the lock folder, and Postgres force_serving_test with the fixtures."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pg_support
from agent import queries
from agent.constraints import check_deploy
from agent.context import build_context, constraint_inputs
from agent.distance import party_eta_hours, released_at
from agent.lock import AlreadyRunning, InstanceLock
from serving import deploy, fixtures, pull, views

FIXTURE_NOW = datetime(2026, 10, 19, 15, 45, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)
ENV = {"DATABRICKS_HOST": "dbc-test.example.invalid", "DATABRICKS_TOKEN": "t", "DATABRICKS_WAREHOUSE_ID": "w"}
RATIONALE = "  Council asked me to act. <b>not bold</b>  "

CTX = dict(disturbance_id="FIXD4", sector_id="bespin", detected_at=FIXTURE_NOW, scan_id="S", imbalance_score=6.9, z_midi=1.0, z_kyber=1.0, z_dark=3.0,
           signature="sith_presence", severity=9.0, is_report_sourced=False, report_description=None, report_relevance=None, sustained_scans=2,
           sector_name="Bespin", system_name="Bespin system", region="Outer Rim", population=6000000.0, description="d", force_history="h",
           channels_present=3, disturbances_last_7d=0, deployments_last_7d=0, last_signature=None, open_deployments=0)
JEDI = [
    dict(jedi_id="windu", jedi_name="Mace Windu", rank="master", primary_specialty="combat", secondary_specialty=None, power_rating=9,
         lightsaber_form="VII", notable_for="n", homeworld_region="Core Worlds"),
    dict(jedi_id="anakin", jedi_name="Anakin", rank="knight", primary_specialty="combat", secondary_specialty=None, power_rating=9,
         lightsaber_form="V", notable_for="n", homeworld_region="Outer Rim"),
    dict(jedi_id="yoda", jedi_name="Yoda", rank="grand_master", primary_specialty="diplomacy", secondary_specialty=None, power_rating=10,
         lightsaber_form="IV", notable_for="n", homeworld_region=None),
]
SHIPS = [dict(starship_id="arc_170", starship_name="ARC-170", starship_class="starfighter", hyperdrive_rating=1.0, mglt=100, crew=3, passengers=0),
         dict(starship_id="solo", starship_name="Solo", starship_class="starfighter", hyperdrive_rating=1.0, mglt=100, crew=1, passengers=0)]
UNSET = object()
REQUEST = {"disturbance_id": "FIXD4", "jedi_ids": ["windu", "anakin"], "starship_id": "arc_170", "rationale": RATIONALE}


class Clock:
    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


class FakeAgentClient:
    """The agent's StatementClient: read and write by statement kind. inserted is the MERGE's num_inserted_rows."""
    def __init__(self, ctx=None, inserted=1, fail_on=None):
        self.ctx, self.inserted, self.fail_on = ctx or {}, inserted, fail_on
        self.kinds, self.params, self.statements = [], [], 0

    def _do(self, sql, params):
        kind = ("merge" if sql.startswith("MERGE") else "mark" if sql.startswith("UPDATE") else
                "context" if sql.startswith("SELECT d.disturbance_id, d.sector_id") else "jedi" if ".dim_jedi" in sql else
                "ships" if ".dim_starship" in sql else "other")
        self.statements += 1
        self.kinds.append(kind)
        self.params.append(params)
        if self.fail_on == kind:
            raise RuntimeError("boom from dbc-test.example.invalid")
        if kind == "context":
            return [dict(CTX, **self.ctx)] if self.ctx is not None else []
        if kind == "jedi":
            return [dict(j) for j in JEDI]
        if kind == "ships":
            return [dict(s) for s in SHIPS]
        if kind == "merge":
            return [{"num_inserted_rows": self.inserted}]
        return []

    read = write = _do


class FakeServingClient:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.statements = rows or [], fail, 0

    def read(self, sql, params=None, *, expected_types=None, max_rows=None):
        self.statements += 1
        if self.fail:
            raise RuntimeError("refresh down")
        return list(self.rows)


def deployment_row(deployment_id, disturbance_id="FIXD4"):
    row = {c: None for c in pull.SPECS["deployment"]["cols"]}
    row.update(deployment_id=deployment_id, disturbance_id=disturbance_id, decided_by="user", decision="deploy", rationale="r", decided_at=NOW,
              jedi_ids=["windu", "anakin"], guardrail_overrides=[])
    return row


class DeployTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = pg_support.connect_test()
        pg_support.fresh_schema(cls.pg)
        views.apply(cls.pg)

    @classmethod
    def tearDownClass(cls):
        cls.pg.close()

    def setUp(self):
        fixtures.load(self.pg, FIXTURE_NOW, reset=True)
        self.dir = Path(tempfile.mkdtemp())
        self.lock_path = self.dir / "agent.lock"
        self.clock, self.logs, self.job_calls = Clock(), [], []
        self.job_script = [(False, "idle")]
        self.client = FakeAgentClient()
        self.serving = FakeServingClient([deployment_row("NEWDEP")])
        self.addCleanup(self.assert_lock_free)

    def lock_is_free(self):
        try:
            again = InstanceLock(self.lock_path).acquire()
        except AlreadyRunning:
            return False
        again.release()
        return True

    def assert_lock_free(self):
        self.assertTrue(self.lock_is_free(), "the agent lock was left held")

    def jobs(self):
        self.job_calls.append(1)
        return self.job_script[min(len(self.job_calls), len(self.job_script)) - 1]

    def go(self, request=UNSET, now=NOW, **over):
        kwargs = dict(env=ENV, now=now, client=self.client, jobs=self.jobs, pg_conn=self.pg, lock_path=self.lock_path, sleep=self.clock.sleep,
                      log=self.logs.append, serving_client=self.serving, ulid_fn=lambda: "01TESTULID0000000000000000")
        kwargs.update(over)
        return deploy.deploy(REQUEST if request is UNSET else request, **kwargs)

    def count(self, table):
        return self.pg.execute(f"SELECT count(*) FROM serving.{table}").fetchone()[0]

    def refused(self, result, *reasons):
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], list(reasons))
        self.assertIsNone(result["deployment_id"])


class Validation(DeployTest):
    def test_each_invalid_request_is_refused_before_the_lock_the_jobs_api_and_any_statement(self):
        held = InstanceLock(self.lock_path).acquire()            # a held lock would show as agent_cycle_in_progress if the lock came first
        try:
            bad = [dict(REQUEST, disturbance_id="bad id!"), dict(REQUEST, disturbance_id=""), dict(REQUEST, disturbance_id="x" * 65),
                   dict(REQUEST, jedi_ids=[]), dict(REQUEST, jedi_ids=["a", "b", "c", "d", "e"]), dict(REQUEST, jedi_ids=["windu", "windu"]),
                   dict(REQUEST, jedi_ids=["Windu"]), dict(REQUEST, jedi_ids="windu"), dict(REQUEST, jedi_ids=[5]),
                   dict(REQUEST, starship_id="Arc_170"), dict(REQUEST, starship_id=None),
                   dict(REQUEST, rationale=""), dict(REQUEST, rationale="   \n\t "), dict(REQUEST, rationale="x" * 2001), dict(REQUEST, rationale=5),
                   "not a dict", None, {}]
            for request in bad:
                result = self.go(request)
                self.refused(result, "invalid_request")
                self.assertEqual(result["statements"], 0, request)
            self.assertEqual((self.job_calls, self.clock.sleeps, self.client.kinds), ([], [], []))
        finally:
            held.release()

    def test_the_limits_themselves_are_accepted(self):
        for request in (dict(REQUEST, jedi_ids=["a", "b", "c", "d"]), dict(REQUEST, rationale="x" * 2000), dict(REQUEST, rationale=" y ")):
            clean, bad = deploy.validate(request)
            self.assertIsNone(bad)
        self.assertEqual(deploy.validate(dict(REQUEST, rationale=" y "))[0]["rationale"], "y")

    def test_the_quota_hour_is_refused_first(self):
        held = InstanceLock(self.lock_path).acquire()
        try:
            for minute in (0, 30, 59):
                result = self.go(now=NOW.replace(hour=3, minute=minute))
                self.refused(result, "quota_hour")
                self.assertEqual(result["statements"], 0)
            self.assertEqual((self.job_calls, self.clock.sleeps), ([], []))
        finally:
            held.release()

    def test_an_incident_that_is_not_open_is_refused_locally(self):
        held = InstanceLock(self.lock_path).acquire()
        try:
            for did, reason in (("FIXD1", "not_open"), ("FIXD5", "not_open"), ("NOPE", "unknown_incident")):
                result = self.go(dict(REQUEST, disturbance_id=did))
                self.refused(result, reason)
                self.assertEqual(result["statements"], 0)
            self.assertEqual((self.job_calls, self.clock.sleeps), ([], []))
        finally:
            held.release()


class Gates(DeployTest):
    def test_a_held_agent_lock_is_refused_after_ten_tries(self):
        held = InstanceLock(self.lock_path).acquire()
        try:
            result = self.go()
        finally:
            held.release()
        self.refused(result, "agent_cycle_in_progress")
        self.assertEqual(self.clock.sleeps, [1] * 9)
        self.assertEqual((result["statements"], self.job_calls), (0, []))

    def test_a_lock_that_frees_up_in_time_lets_the_deployment_through(self):
        held = InstanceLock(self.lock_path).acquire()

        def sleep(seconds):
            self.clock.sleep(seconds)
            if len(self.clock.sleeps) == 3:
                held.release()
        try:
            result = self.go(sleep=sleep)
        finally:
            held.release()
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.clock.sleeps), 3)

    def test_job_1_running_is_refused_without_a_statement(self):
        self.job_script = [(True, "job1_running")]
        result = self.go()
        self.refused(result, "job1_running")
        self.assertEqual((result["statements"], self.client.kinds), (0, []))

    def test_a_jobs_api_error_is_refused(self):
        self.job_script = [(True, "jobs_api_error")]
        self.refused(self.go(), "jobs_api_error")

    def test_job_1_starting_before_the_write_stops_the_deployment(self):
        self.job_script = [(False, "idle"), (True, "job1_running")]
        result = self.go()
        self.refused(result, "job1_running")
        self.assertEqual(self.client.kinds, ["context", "jedi", "ships"])
        self.assertEqual(result["statements"], 3)


class ConstraintLayer(DeployTest):
    def test_the_forced_stand_down_reasons(self):
        for ctx, reason in ((dict(severity=4.0), "severity_below_threshold"), (dict(signature=None), "signature_missing")):
            self.client = FakeAgentClient(ctx=ctx)
            result = self.go()
            self.refused(result, reason)
            self.assertNotIn("merge", self.client.kinds)
            self.assertEqual(result["statements"], 3)

    def test_a_constraint_rejection_shows_the_layers_reasons_and_writes_nothing(self):
        request = dict(REQUEST, jedi_ids=["windu", "ghost"])
        result = self.go(request)
        context = build_context(dict(CTX), JEDI, SHIPS)
        expected = check_deploy(*(lambda inc, j, s, st: (inc, ["windu", "ghost"], "arc_170", j, s, st))(*constraint_inputs(context)))
        self.assertIn("jedi_unavailable", expected)
        self.refused(result, *expected)
        self.assertEqual(self.client.kinds, ["context", "jedi", "ships"])
        self.assertEqual(result["statements"], 3)
        self.assertEqual(self.count("deployment"), 4)

    def test_several_reasons_come_in_the_layers_order(self):
        result = self.go(dict(REQUEST, jedi_ids=["anakin", "ghost"], starship_id="nowhere"))
        self.assertEqual(result["reasons"], ["jedi_unavailable", "starship_unavailable"])

    def test_a_ship_too_small_for_the_party(self):
        result = self.go(dict(REQUEST, starship_id="solo"))
        self.assertEqual(result["reasons"], ["insufficient_capacity"])


class Accepted(DeployTest):
    def test_the_whole_path(self):
        result = self.go()
        self.assertTrue(result["ok"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["deployment_id"], "01TESTULID0000000000000000")
        self.assertEqual(self.client.kinds, ["context", "jedi", "ships", "merge", "mark"])
        self.assertEqual(result["statements"], 6)
        self.assertEqual(self.job_calls, [1, 1])

    def test_the_merge_parameters(self):
        self.go()
        p = self.client.params[self.client.kinds.index("merge")]
        eta = party_eta_hours(["Core Worlds", "Outer Rim"], "Outer Rim", 1.0)
        context = build_context(dict(CTX), JEDI, SHIPS)
        self.assertEqual(p["decided_by"], ("user", "STRING"))
        self.assertEqual(p["decision"], ("deploy", "STRING"))
        self.assertEqual(p["model"], (None, "STRING"))
        self.assertEqual(p["decision_latency_ms"], (None, "BIGINT"))
        self.assertEqual(p["rationale"], ("Council asked me to act. <b>not bold</b>", "STRING"))
        self.assertEqual(p["jedi_ids_json"], (json.dumps(["windu", "anakin"]), "STRING"))
        self.assertEqual(p["starship_id"], ("arc_170", "STRING"))
        self.assertEqual(p["eta_hours"], (eta, "DOUBLE"))
        self.assertEqual(p["released_at"], (released_at(NOW, eta), "TIMESTAMP"))
        self.assertEqual(p["decided_at"], (NOW, "TIMESTAMP"))
        self.assertEqual(p["context_json"], (json.dumps(context, sort_keys=True), "STRING"))
        self.assertEqual(p["signature_at_decision"], ("sith_presence", "STRING"))
        self.assertEqual(p["overrides_json"], ("[]", "STRING"))
        self.assertEqual(p["disturbance_id"], ("FIXD4", "STRING"))
        self.assertEqual(p["deployment_id"], ("01TESTULID0000000000000000", "STRING"))

    def test_the_mark_follows_the_merge_for_the_same_incident(self):
        self.go()
        self.assertEqual(self.client.params[-1], {"disturbance_id": ("FIXD4", "STRING")})

    def test_postgres_is_refreshed_and_the_incident_is_processed(self):
        self.assertFalse(self.pg.execute("SELECT agent_processed FROM serving.disturbance WHERE disturbance_id = 'FIXD4'").fetchone()[0])
        self.go()
        self.assertEqual(self.pg.execute("SELECT deployment_id FROM serving.deployment").fetchall(), [("NEWDEP",)])
        self.assertTrue(self.pg.execute("SELECT agent_processed FROM serving.disturbance WHERE disturbance_id = 'FIXD4'").fetchone()[0])

    def test_a_merge_that_inserted_nothing_is_already_decided_and_not_marked(self):
        self.client = FakeAgentClient(inserted=0)
        result = self.go()
        self.refused(result, "already_decided")
        self.assertEqual(self.client.kinds, ["context", "jedi", "ships", "merge"])
        self.assertEqual(self.count("deployment"), 4)

    def merge_without_count(self, **kwargs):
        client = FakeAgentClient(**kwargs)
        plain = client._do

        def merge_result(sql, params):
            rows = plain(sql, params)
            return [{}] if sql.startswith("MERGE") else rows
        client.read = client.write = merge_result
        return client

    def test_a_merge_result_without_num_inserted_rows_is_unknown_and_nothing_follows(self):
        self.client = self.merge_without_count()
        before = self.pg.execute("SELECT agent_processed FROM serving.disturbance WHERE disturbance_id = 'FIXD4'").fetchone()[0]
        result = self.go()
        self.refused(result, "merge_result_unknown")
        self.assertNotIn("warnings", result)
        self.assertEqual(self.client.kinds[-1], "merge")                 # no mark after it
        self.assertEqual(self.serving.statements, 0)                     # no refresh
        self.assertEqual(self.count("deployment"), 4)
        self.assertEqual(self.pg.execute("SELECT agent_processed FROM serving.disturbance WHERE disturbance_id = 'FIXD4'").fetchone()[0], before)

    def test_an_empty_or_unreadable_merge_result_is_unknown_too(self):
        for rows in ([], [{"num_inserted_rows": None}], [{"num_inserted_rows": "many"}], ["x"]):
            client = FakeAgentClient()
            plain = client._do
            client.read = client.write = (lambda sql, params, plain=plain, rows=rows: rows if sql.startswith("MERGE") else plain(sql, params))
            self.client = client
            self.refused(self.go(), "merge_result_unknown")

    def test_the_row_is_written_as_the_user_through_the_agents_row_builder(self):
        self.go()
        merge = [p for k, p in zip(self.client.kinds, self.client.params) if k == "merge"][0]
        self.assertEqual(merge["decided_by"][0], "user")
        self.assertIsNone(merge["model"][0])

    def test_a_merge_with_a_count_has_no_warning(self):
        result = self.go()
        self.assertNotIn("warnings", result)

    def test_a_failed_mark_still_counts_as_deployed_with_a_warning(self):
        self.client = FakeAgentClient(fail_on="mark")
        result = self.go()
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], ["mark_failed"])

    def test_a_failed_refresh_still_counts_as_deployed_with_a_warning(self):
        self.serving = FakeServingClient(fail=True)
        result = self.go()
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], ["refresh_failed"])
        self.assertEqual(self.count("deployment"), 4)

    def test_the_lock_is_released_after_an_exception(self):
        self.client = FakeAgentClient(fail_on="jedi")
        result = self.go()
        self.refused(result, "internal_error")
        self.assertEqual(result["detail"], "RuntimeError")
        self.assert_lock_free()


class LogLine(DeployTest):
    def test_one_line_per_call_without_the_rationale_a_url_or_a_host(self):
        self.go(dict(REQUEST, jedi_ids=["windu", "ghost"]))
        self.client = FakeAgentClient(fail_on="context")
        self.go()
        self.client = FakeAgentClient()
        self.go()
        self.assertEqual(len(self.logs), 3)
        text = "\n".join(self.logs)
        self.assertNotIn("Council", text)
        self.assertNotIn("not bold", text)
        self.assertNotIn("http", text.lower())
        self.assertNotIn("example.invalid", text)
        self.assertIn("reasons=jedi_unavailable", self.logs[0])
        self.assertIn("reasons=internal_error", self.logs[1])
        self.assertIn("ok=true", self.logs[2])
        self.assertIn(f"rationale_chars={len(RATIONALE)}", self.logs[2])
        self.assertTrue(self.logs[2].startswith("2026-10-19T16:00:00Z disturbance_id=FIXD4 jedi_ids=windu,anakin starship_id=arc_170 ok=true"))
        self.assertIn("jedi_ids=windu,ghost starship_id=arc_170", self.logs[0])

    def test_an_invalid_request_logs_no_party(self):
        self.go(dict(REQUEST, jedi_ids=["windu", "x" * 100], rationale="r"))
        self.assertIn("jedi_ids=- starship_id=-", self.logs[0])
        self.assertNotIn("xxxx", self.logs[0])


if __name__ == "__main__":
    unittest.main()
