"""agent/run.py: one cycle against a fake database, a fake model, a fake clock and a fake ping. One test per case."""
import json
import logging
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import _support as S
from _fixtures import JEDI, SHIPS, StepClock, row
from agent import run
from agent.budget import BudgetExhausted, CallBudget
from agent.dbsql import StatementFailed, StatementTimeout, iso_z
from agent.gemini import GeminiPermanent, GeminiTransient, GeminiUnavailable
from agent.lock import InstanceLock
from agent.loop import Decision, decide
from agent.seen import SeenStore

NOW = datetime(2026, 10, 14, 12, 15, 0, tzinfo=timezone.utc)
TOKEN_MARKER = "dapi-NOT-A-REAL-TOKEN-5521"


def kind_of(sql):
    if sql.startswith("MERGE"):
        return "write"
    if sql.startswith("SELECT count(*) AS n"):
        return "repair_needed"
    if sql.startswith("UPDATE") and "disturbance_id IN" in sql:
        return "repair"
    if sql.startswith("UPDATE"):
        return "mark"
    if sql.startswith("SELECT d.disturbance_id, d.sector_id"):
        return "context"
    if sql.startswith("SELECT d.disturbance_id"):
        return "poll"
    if ".dim_jedi" in sql:
        return "jedi"
    if ".dim_starship" in sql:
        return "ships"
    raise AssertionError("unrecognised statement: " + sql[:60])


class FakeDb:
    def __init__(self, ids=(), fail=None, context_overrides=None, no_context=False, repair_n=0):
        self.ids = list(ids)
        self.repair_n = repair_n
        self.no_context = no_context
        self.fail = fail or {}                      # kind -> exception to raise on every call
        self.context_overrides = context_overrides or {}
        self.kinds = []
        self.calls = []                             # (kind, params)
        self.statements = 0

    def _do(self, sql, params):
        kind = kind_of(sql)
        self.statements += 1
        self.kinds.append(kind)
        self.calls.append((kind, params))
        if kind in self.fail:
            raise self.fail[kind]
        if kind == "repair_needed":
            return [{"n": self.repair_n}]
        if kind == "poll":
            return [{"disturbance_id": i} for i in self.ids]
        if kind == "context":
            if self.no_context:
                return []
            return [row(disturbance_id=params["disturbance_id"][0], **self.context_overrides)]
        if kind == "jedi":
            return [dict(j) for j in JEDI]
        if kind == "ships":
            return [dict(s) for s in SHIPS]
        return []

    read = write = _do

    def params(self, kind):
        return [p for k, p in self.calls if k == kind]


def fake_decision(kind="deploy", **overrides):
    values = dict(decision=kind, jedi_ids=["amy"] if kind == "deploy" else [], starship_id="a_ship" if kind == "deploy" else None,
                  eta_hours=0.5 if kind == "deploy" else None, rationale="Because the telemetry is sustained.", model="test-model",
                  decision_latency_ms=250, model_calls=1, prompt_tokens=1000, output_tokens=100, thought_tokens=50, cost_usd=0.001)
    values.update(overrides)
    return Decision(**values)


class Harness:
    def __init__(self, directory, ids=(), hour=12, job1=(False, "idle"), decide_fn=None, fail=None, monotonic=None, limit=None,
                 context_overrides=None, hold_lock=False, repair_n=0, preseed=True):
        self.dir = Path(directory)
        self.db = FakeDb(ids, fail, context_overrides, repair_n=repair_n)
        self.pings, self.logs, self.decide_calls = [], [], []
        self.budget = CallBudget(20)
        self.lock_path = self.dir / "agent.lock"
        self.decisions_path = self.dir / "agent_decisions.ndjson"
        self.held = InstanceLock(self.lock_path).acquire() if hold_lock else None
        self.hour = hour
        self.decide_fn = decide_fn or self._default_decide
        self.job1 = job1
        self.job1_calls = 0
        self.monotonic = monotonic or StepClock(0.1)
        self.limit = limit
        self.epoch = run.AGENT_EPOCH
        self.sleeps = []
        self.now = NOW
        self.start_path = None                       # set a path to switch the start debounce on
        self.seen_path = self.dir / "agent_seen_dev.json"
        self.seen = SeenStore(self.seen_path)
        if preseed:                                  # most tests are about the cycle itself, so their ids were polled in an earlier cycle
            self.seen.save({i: iso_z(NOW - timedelta(hours=1)) for i in ids})
        self.ulids = iter(f"01ULID{i:020d}" for i in range(100))

    def _default_decide(self, context, gemini, budget, *, monotonic):
        self.decide_calls.append(context)
        return fake_decision()

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) > 100:                   # a wait with no upper bound fails the test instead of hanging it
            raise AssertionError("the Job 1 wait has no upper bound")

    def job1_check(self):
        """job1 is one (active, reason) pair, or a list of pairs consumed one per call (the last repeats)."""
        self.job1_calls += 1
        if isinstance(self.job1, list):
            return self.job1[min(self.job1_calls, len(self.job1)) - 1]
        return self.job1

    def go(self):
        code = run.run_guarded(
            target="dev", limit=self.limit, lock_path=self.lock_path, db=self.db, gemini=object(), budget=self.budget,
            job1_check=self.job1_check, ping=self.pings.append, clock=lambda: self.now.replace(hour=self.hour),
            monotonic=self.monotonic, log=self.logs.append, decisions_path=self.decisions_path,
            decide_fn=self.decide_fn, ulid_fn=lambda: next(self.ulids), epoch=self.epoch, seen=self.seen, sleep=self._sleep,
            start_path=self.start_path)
        if self.held:
            self.held.release()
        return code


def harness(**options):
    directory = tempfile.mkdtemp()
    unittest.addModuleCleanup(shutil.rmtree, directory, ignore_errors=True)
    return Harness(directory, **options)


class Skips(unittest.TestCase):
    def test_a_held_lock_exits_zero_with_no_ping_and_no_statement(self):
        h = harness(ids=["a"], hold_lock=True)
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.pings, [])
        self.assertEqual(h.db.kinds, [])
        self.assertIn("agent: already running; exiting", h.logs)

    def test_the_quota_hour_skips_the_cycle_and_pings_success(self):
        h = harness(ids=["a"], hour=3)
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.pings, ["success"])
        self.assertEqual(h.db.kinds, [])
        self.assertTrue(any("quota window" in line for line in h.logs))

    def test_the_hours_either_side_of_the_quota_hour_run(self):
        for hour in (2, 4):
            with self.subTest(hour=hour):
                h = harness(hour=hour)
                h.go()
                self.assertEqual(h.db.kinds, ["repair_needed", "poll"])

    def test_an_active_job_1_skips_the_cycle_and_pings_success(self):
        h = harness(ids=["a"], job1=(True, "job1_running"))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.pings, ["success"])
        self.assertEqual(h.db.kinds, [])
        self.assertTrue(any("job1_running" in line for line in h.logs))

    def test_a_jobs_api_error_also_skips_the_cycle(self):
        h = harness(ids=["a"], job1=(True, "jobs_api_error"))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds, [])
        self.assertEqual(h.pings, ["fail"])
        self.assertTrue(any("jobs_api_error" in line for line in h.logs))

    def test_the_lock_is_released_after_a_cycle(self):
        h = harness()
        h.go()
        again = InstanceLock(h.lock_path).acquire()
        again.release()


class Cycle(unittest.TestCase):
    def test_no_incidents_costs_exactly_two_statements_and_no_model_call(self):
        h = harness()
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds, ["repair_needed", "poll"])
        self.assertEqual(h.db.statements, 2)
        self.assertEqual(h.decide_calls, [])
        self.assertEqual(h.pings, ["success"])

    def test_one_incident_runs_seven_statements_in_order(self):
        h = harness(ids=["inc-1"])
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds, ["repair_needed", "poll", "context", "jedi", "ships", "write", "mark"])
        self.assertEqual(h.db.statements, 7)
        self.assertEqual(h.pings, ["success"])

    def test_two_incidents_are_handled_in_poll_order(self):
        h = harness(ids=["inc-1", "inc-2"])
        h.go()
        self.assertEqual([p["disturbance_id"][0] for p in h.db.params("mark")], ["inc-1", "inc-2"])

    def test_the_model_being_unavailable_writes_nothing_and_marks_nothing(self):
        def unavailable(context, gemini, budget, *, monotonic):
            raise GeminiUnavailable("down")
        h = harness(ids=["inc-1", "inc-2"], decide_fn=unavailable)
        self.assertEqual(h.go(), 0)
        self.assertNotIn("write", h.db.kinds)
        self.assertNotIn("mark", h.db.kinds)
        self.assertEqual(h.db.kinds.count("context"), 2)          # the cycle went on to the next incident
        self.assertEqual(h.pings, ["success"])

    def test_a_failed_write_is_not_marked_and_the_cycle_goes_on(self):
        h = harness(ids=["inc-1", "inc-2"], fail={"write": StatementFailed("X", "boom", False)})
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds.count("write"), 2)
        self.assertNotIn("mark", h.db.kinds)

    def test_a_timeout_on_a_read_skips_the_incident(self):
        h = harness(ids=["inc-1"], fail={"jedi": StatementTimeout("slow")})
        self.assertEqual(h.go(), 0)
        self.assertNotIn("write", h.db.kinds)
        self.assertNotIn("mark", h.db.kinds)

    def test_a_failed_mark_does_not_stop_the_next_incident(self):
        h = harness(ids=["inc-1", "inc-2"], fail={"mark": StatementFailed("X", "boom", False)})
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds.count("write"), 2)
        self.assertEqual(h.db.kinds.count("mark"), 2)
        self.assertEqual(h.pings, ["success"])
        self.assertTrue(any("mark failed" in line for line in h.logs))

    def test_budget_exhaustion_stops_the_loop(self):
        def spent(context, gemini, budget, *, monotonic):
            raise BudgetExhausted("spent")
        h = harness(ids=["inc-1", "inc-2", "inc-3"], decide_fn=spent)
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds.count("context"), 1)
        self.assertNotIn("write", h.db.kinds)

    def test_thirty_minutes_stop_new_incidents(self):
        h = harness(ids=["inc-1", "inc-2", "inc-3"], monotonic=StepClock(1000.0))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds.count("mark"), 1)
        self.assertTrue(any("no new incident is started" in line for line in h.logs))

    def test_an_incident_with_no_context_row_is_skipped(self):
        h = harness(ids=["inc-1"])
        h.db.no_context = True
        self.assertEqual(h.go(), 0)
        self.assertNotIn("write", h.db.kinds)
        self.assertNotIn("mark", h.db.kinds)
        self.assertTrue(any("no context row" in line for line in h.logs))

    def test_an_unhandled_error_pings_fail_and_exits_one(self):
        h = harness(ids=["inc-1"], fail={"poll": RuntimeError("unexpected")})
        self.assertEqual(h.go(), 1)
        self.assertEqual(h.pings, ["fail"])
        self.assertTrue(any("Traceback" in line and "RuntimeError" in line for line in h.logs))

    def test_a_failed_repair_is_a_crash(self):
        h = harness(repair_n=1, fail={"repair": StatementFailed("X", "boom", False)})
        self.assertEqual(h.go(), 1)
        self.assertEqual(h.pings, ["fail"])

    def test_the_lock_is_released_after_a_crash(self):
        h = harness(fail={"poll": RuntimeError("x")})
        h.go()
        InstanceLock(h.lock_path).acquire().release()


class Rows(unittest.TestCase):
    def params_of_write(self, h):
        return h.db.params("write")[0]

    def test_a_deploy_row(self):
        h = harness(ids=["inc-1"])
        h.go()
        p = self.params_of_write(h)
        self.assertEqual(p["decision"], ("deploy", "STRING"))
        self.assertEqual(p["decided_by"], ("yoda_agent", "STRING"))
        self.assertEqual(p["deployment_id"], ("01ULID" + "0" * 19 + "0", "STRING"))
        self.assertEqual(json.loads(p["jedi_ids_json"][0]), ["amy"])
        self.assertEqual(p["starship_id"][0], "a_ship")
        self.assertEqual(p["eta_hours"], (0.5, "DOUBLE"))
        self.assertEqual(p["decided_at"][0], NOW)
        self.assertEqual(p["released_at"][0], NOW + timedelta(hours=0.5 + 24))
        self.assertEqual(p["rationale"][0], "Because the telemetry is sustained.")
        self.assertEqual(p["model"][0], "test-model")
        self.assertEqual(p["decision_latency_ms"], (250, "BIGINT"))

    def test_the_context_snapshot_round_trips(self):
        h = harness(ids=["inc-1"])
        h.go()
        snapshot = json.loads(self.params_of_write(h)["context_json"][0])
        self.assertEqual(snapshot, json.loads(json.dumps(h.decide_calls[0], sort_keys=True)))
        self.assertEqual(snapshot["disturbance_id"], "inc-1")
        self.assertEqual(self.params_of_write(h)["signature_at_decision"][0], "sith_presence")

    def test_a_stand_down_row(self):
        h = harness(ids=["inc-1"], decide_fn=lambda c, g, b, *, monotonic: fake_decision("stand_down"))
        h.go()
        p = self.params_of_write(h)
        self.assertEqual(p["decision"][0], "stand_down")
        self.assertIsNone(p["eta_hours"][0])
        self.assertIsNone(p["released_at"][0])
        self.assertIsNone(p["starship_id"][0])
        self.assertEqual(p["jedi_ids_json"][0], "[]")

    def test_a_forced_stand_down_row_has_no_model(self):
        h = harness(ids=["inc-1"], decide_fn=decide, context_overrides={"severity": 4.0})
        self.assertEqual(h.go(), 0)
        p = self.params_of_write(h)
        self.assertEqual(p["decision"][0], "stand_down")
        self.assertIsNone(p["model"][0])
        self.assertIsNone(p["decision_latency_ms"][0])
        self.assertEqual(json.loads(p["overrides_json"][0]), [{"proposal": None, "reasons": ["severity_below_threshold"]}])
        self.assertIn("mark", h.db.kinds)

    def test_guardrail_overrides_are_written(self):
        overrides = [{"proposal": {"jedi_ids": ["zed"]}, "reasons": ["specialty_mismatch"]}]
        h = harness(ids=["inc-1"], decide_fn=lambda c, g, b, *, monotonic: fake_decision(guardrail_overrides=overrides))
        h.go()
        self.assertEqual(json.loads(self.params_of_write(h)["overrides_json"][0]), overrides)


class Repair(unittest.TestCase):
    def test_an_idle_cycle_is_two_reads_and_no_write(self):
        h = harness()
        h.go()
        self.assertEqual(h.db.kinds, ["repair_needed", "poll"])
        self.assertEqual(h.db.statements, 2)

    def test_the_update_runs_only_when_something_needs_repair(self):
        h = harness(repair_n=2)
        h.go()
        self.assertEqual(h.db.kinds, ["repair_needed", "repair", "poll"])

    def test_one_incident_with_a_repair_is_eight_statements(self):
        h = harness(ids=["inc-1"], repair_n=1)
        h.go()
        self.assertEqual(h.db.kinds, ["repair_needed", "repair", "poll", "context", "jedi", "ships", "write", "mark"])
        self.assertEqual(h.db.statements, 8)

    def test_zero_needed_makes_no_update_even_with_incidents(self):
        h = harness(ids=["inc-1"], repair_n=0)
        h.go()
        self.assertNotIn("repair", h.db.kinds)


class ModelErrors(unittest.TestCase):
    def raising(self, exc):
        def fn(context, gemini, budget, *, monotonic):
            raise exc
        return fn

    def summary_line(self, h):
        return next(x for x in h.logs if x.startswith("agent: cycle "))

    def test_a_transient_error_is_logged_and_the_cycle_still_pings_success(self):
        h = harness(ids=["inc-1"], decide_fn=self.raising(GeminiTransient("down", kind="transient")))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.pings, ["success"])
        self.assertIn("degraded=false", self.summary_line(h))
        self.assertNotIn("write", h.db.kinds)

    def test_a_permanent_error_writes_nothing_and_pings_fail(self):
        h = harness(ids=["inc-1"], decide_fn=self.raising(GeminiPermanent("HTTP 400: secret-looking-body", kind="http_400")))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.pings, ["fail"])
        self.assertNotIn("write", h.db.kinds)
        self.assertNotIn("mark", h.db.kinds)
        self.assertIn("degraded=true", self.summary_line(h))

    def test_a_permanent_error_logs_the_kind_and_not_the_message(self):
        h = harness(ids=["inc-1"], decide_fn=self.raising(GeminiPermanent("HTTP 400: secret-looking-body", kind="http_400")))
        h.go()
        text = "\n".join(h.logs)
        self.assertIn("http_400", text)
        self.assertNotIn("secret-looking-body", text)

    def test_the_cycle_goes_on_to_the_next_incident_after_a_permanent_error(self):
        calls = []

        def fn(context, gemini, budget, *, monotonic):
            calls.append(context["disturbance_id"])
            if len(calls) == 1:
                raise GeminiPermanent("x", kind="http_400")
            return fake_decision()
        h = harness(ids=["inc-1", "inc-2"], decide_fn=fn)
        h.go()
        self.assertEqual(calls, ["inc-1", "inc-2"])
        self.assertEqual(h.db.kinds.count("write"), 1)
        self.assertEqual(h.pings, ["fail"])

    def test_a_clean_cycle_is_not_degraded(self):
        h = harness(ids=["inc-1"])
        h.go()
        self.assertIn("degraded=false", self.summary_line(h))
        self.assertEqual(h.pings, ["success"])


class JobRecheck(unittest.TestCase):
    def test_job_1_starting_mid_cycle_stops_before_the_write_and_pings_success(self):
        h = harness(ids=["inc-1", "inc-2"], job1=[(False, "idle"), (True, "job1_running")])
        self.assertEqual(h.go(), 0)
        self.assertNotIn("write", h.db.kinds)
        self.assertNotIn("mark", h.db.kinds)
        self.assertEqual(h.db.kinds.count("context"), 1)          # the second incident was never started
        self.assertEqual(h.pings, ["success"])
        self.assertTrue(any("job1_running" in line for line in h.logs))

    def test_a_jobs_api_error_mid_cycle_stops_and_pings_fail(self):
        h = harness(ids=["inc-1", "inc-2"], job1=[(False, "idle"), (True, "jobs_api_error")])
        self.assertEqual(h.go(), 0)
        self.assertNotIn("write", h.db.kinds)
        self.assertEqual(h.pings, ["fail"])

    def test_the_check_runs_once_at_the_start_and_once_per_incident(self):
        h = harness(ids=["inc-1", "inc-2"])
        h.go()
        self.assertEqual(h.job1_calls, 3)

    def test_an_incident_written_before_job_1_started_stays_written(self):
        h = harness(ids=["inc-1", "inc-2"], job1=[(False, "idle"), (False, "idle"), (True, "job1_running")])
        h.go()
        self.assertEqual(h.db.kinds.count("write"), 1)
        self.assertEqual(h.db.kinds.count("mark"), 1)
        self.assertEqual(h.pings, ["success"])


class MainHandler(unittest.TestCase):
    def env_file(self, **overrides):
        values = {"DATABRICKS_HOST": "https://example.invalid", "DATABRICKS_TOKEN": "tok-NOT-A-REAL-ONE-77",
                  "DATABRICKS_WAREHOUSE_ID": "0123456789abcdef", "GEMINI_API_KEY": "key-NOT-A-REAL-ONE-88",
                  "INFERENCE_MODEL": "gemini-3.1-flash-lite", "AGENT_PING_S4_URL": "https://hc.example.invalid/ping/NOT-A-REAL-UUID"}
        values.update(overrides)
        directory = tempfile.mkdtemp()
        unittest.addModuleCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = Path(directory) / ".env.agent"
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        return str(path)

    def run_main(self, argv, opener=None):
        logs = []
        fake_logger = logging.getLogger("agent-test-main")
        fake_logger.info = logs.append
        start_dir = tempfile.mkdtemp()
        unittest.addModuleCleanup(shutil.rmtree, start_dir, ignore_errors=True)
        patches = [mock.patch.object(run, "configure_logging", return_value=fake_logger),
                   mock.patch.object(run, "START_PATH", Path(start_dir) / "agent_last_start.txt")]
        if opener is not None:
            patches.append(mock.patch.object(run.urllib.request, "urlopen", opener))
        for p in patches:
            p.start()
        try:
            try:
                code = run.main(argv)
            except Exception as e:
                self.fail(f"main must handle its own errors, but it raised {e!r}")
        finally:
            for p in patches:
                p.stop()
        return code, logs

    def test_a_bad_model_name_is_logged_and_returns_non_zero(self):
        code, logs = self.run_main(["--target", "dev", "--no-ping", "--env", self.env_file(INFERENCE_MODEL="../bad model")])
        self.assertEqual(code, 1)
        self.assertTrue(any("the run failed" in line and "ValueError" in line for line in logs))

    def test_with_a_ping_url_a_failure_pings_fail(self):
        from _support import FakeOpener
        opener = FakeOpener([(200, "OK")])
        code, _ = self.run_main(["--target", "dev", "--env", self.env_file(INFERENCE_MODEL="../bad model")], opener=opener)
        self.assertEqual(code, 1)
        self.assertEqual([r["url"] for r in opener.requests], ["https://hc.example.invalid/ping/NOT-A-REAL-UUID/fail"])

    def test_the_traceback_has_no_secret(self):
        _, logs = self.run_main(["--target", "dev", "--no-ping", "--env", self.env_file(INFERENCE_MODEL="../bad model")])
        text = "\n".join(logs)
        for secret in ("tok-NOT-A-REAL-ONE-77", "key-NOT-A-REAL-ONE-88", "hc.example.invalid"):
            self.assertNotIn(secret, text)

    def test_a_config_error_exits_2_and_pings_nothing(self):
        from _support import FakeOpener
        opener = FakeOpener([(200, "OK")])
        directory = tempfile.mkdtemp()
        unittest.addModuleCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = Path(directory) / ".env.agent"
        path.write_text("DATABRICKS_HOST=https://example.invalid\n", encoding="utf-8")
        code, logs = self.run_main(["--target", "dev", "--env", str(path)], opener=opener)
        self.assertEqual(code, 2)
        self.assertEqual(opener.requests, [])
        self.assertTrue(any("configuration problem" in line for line in logs))


class Limit(unittest.TestCase):
    IDS = [f"inc-{n}" for n in range(8)]

    def decided(self, limit):
        h = harness(ids=self.IDS, limit=limit)
        h.go()
        return len(h.db.params("write"))

    def test_the_poll_always_reads_the_poll_limit(self):
        for limit in (None, 2, 50):
            h = harness(limit=limit)
            h.go()
            self.assertEqual(h.db.params("poll")[0]["limit"], (50, "INT"), limit)

    def test_no_limit_means_the_maximum_decided(self):
        self.assertEqual(self.decided(None), 5)

    def test_a_limit_above_the_maximum_is_capped(self):
        self.assertEqual(self.decided(50), 5)

    def test_a_smaller_limit_caps_the_decisions(self):
        self.assertEqual(self.decided(2), 2)

    def test_the_poll_uses_the_agent_epoch(self):
        h = harness()
        h.go()
        self.assertEqual(h.db.params("poll")[0]["epoch"], (datetime(2026, 10, 12, tzinfo=timezone.utc), "TIMESTAMP"))


class Job1Wait(unittest.TestCase):
    """The start-of-cycle Job 1 check waits for a running Job 1 (30 s polls, up to 10 minutes) before it skips."""

    def test_an_idle_job_1_means_no_wait(self):
        h = harness()
        h.go()
        self.assertEqual(h.sleeps, [])
        self.assertTrue(any("waited_s=0" in line for line in h.logs))
        self.assertEqual(h.pings, ["success"])

    def test_a_job_1_that_finishes_after_two_polls_lets_the_cycle_go_on(self):
        h = harness(job1=[(True, "job1_running"), (True, "job1_running"), (False, "idle")])
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.sleeps, [30, 30])
        self.assertEqual(h.db.kinds, ["repair_needed", "poll"])
        self.assertTrue(any("waited_s=60" in line for line in h.logs))
        self.assertEqual(h.pings, ["success"])

    def test_a_job_1_that_runs_throughout_skips_after_ten_minutes(self):
        h = harness(ids=["a"], job1=(True, "job1_running"))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.sleeps, [30] * 20)
        self.assertEqual(h.db.kinds, [])
        self.assertEqual(h.pings, ["success"])
        self.assertTrue(any("job1_running" in line and "waited 600 s" in line for line in h.logs))

    def test_an_api_error_during_the_wait_is_a_jobs_api_error(self):
        h = harness(job1=[(True, "job1_running"), (True, "jobs_api_error")])
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.sleeps, [30])
        self.assertEqual(h.db.kinds, [])
        self.assertEqual(h.pings, ["fail"])
        self.assertTrue(any("jobs_api_error" in line for line in h.logs))

    def test_an_api_error_at_once_does_not_wait(self):
        h = harness(job1=(True, "jobs_api_error"))
        h.go()
        self.assertEqual(h.sleeps, [])
        self.assertEqual(h.pings, ["fail"])

    def test_the_recheck_before_a_write_does_not_wait(self):
        h = harness(ids=["a", "b"], job1=[(False, "idle"), (True, "job1_running")])
        h.go()
        self.assertEqual(h.sleeps, [])
        self.assertNotIn("write", h.db.kinds)

    def test_the_quota_hour_skips_before_any_wait(self):
        h = harness(hour=3, job1=(True, "job1_running"))
        h.go()
        self.assertEqual(h.sleeps, [])

    def test_the_wait_constants(self):
        self.assertEqual((run.JOB1_WAIT_SECONDS, run.JOB1_POLL_SECONDS), (600, 30))


class HumanFirst(unittest.TestCase):
    """doc 06 "Human-first window": an incident is decided only in a cycle after the one that first polled it."""

    def test_a_new_id_is_recorded_and_not_decided(self):
        h = harness(ids=["inc-1"], preseed=False)
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds, ["repair_needed", "poll"])
        self.assertEqual(h.decide_calls, [])
        self.assertEqual(h.seen.load(), {"inc-1": iso_z(NOW)})
        self.assertTrue(any("new=1 deferred=1" in line for line in h.logs))

    def test_a_second_cycle_two_seconds_later_does_not_decide_a_new_id(self):
        h = harness(ids=["inc-1"], preseed=False)
        h.go()
        h.now = NOW + timedelta(seconds=2)               # Task Scheduler's duplicate launch
        h.go()
        self.assertEqual(h.decide_calls, [])
        self.assertEqual(h.db.kinds.count("write"), 0)
        self.assertEqual(h.seen.load(), {"inc-1": iso_z(NOW)})

    def test_a_cycle_31_minutes_later_decides_it(self):
        h = harness(ids=["inc-1"], preseed=False)
        h.go()
        h.now = NOW + timedelta(minutes=31)
        h.go()
        self.assertEqual(h.db.kinds.count("write"), 1)

    def test_a_cycle_29_minutes_later_does_not(self):
        h = harness(ids=["inc-1"], preseed=False)
        h.go()
        h.now = NOW + timedelta(minutes=29)
        h.go()
        self.assertEqual(h.db.kinds.count("write"), 0)

    def test_exactly_30_minutes_is_old_enough(self):
        h = harness(ids=["inc-1"], preseed=False)
        h.go()
        h.now = NOW + timedelta(minutes=30)
        h.go()
        self.assertEqual(h.db.kinds.count("write"), 1)

    def test_the_minimum_age_is_30_minutes(self):
        self.assertEqual(run.MIN_FIRST_SEEN_AGE_MINUTES, 30)

    def test_the_next_cycle_decides_it_and_keeps_the_first_seen_time(self):
        h = harness(ids=["inc-1"], preseed=False)
        h.go()
        first = h.seen.load()["inc-1"]
        later = NOW + timedelta(hours=1)
        h.hour = later.hour
        h.go()
        self.assertEqual(h.db.kinds.count("write"), 1)
        self.assertEqual(h.seen.load()["inc-1"], first)

    def test_first_seen_is_never_overwritten(self):
        h = harness(ids=["inc-1"], preseed=False, decide_fn=lambda c, g, b, *, monotonic: (_ for _ in ()).throw(GeminiUnavailable("down")))
        h.go()
        first = h.seen.load()["inc-1"]
        for hour in (13, 14, 15):
            h.hour = hour
            h.go()
            self.assertEqual(h.seen.load()["inc-1"], first)

    def test_a_decided_id_is_pruned_on_the_next_cycle(self):
        h = harness(ids=["inc-1", "inc-2"])
        h.go()                                           # both decided
        h.db.ids = []                                    # the next poll no longer returns them
        h.go()
        self.assertEqual(h.seen.load(), {})

    def test_an_id_that_stays_undecided_stays_in_the_store(self):
        h = harness(ids=["inc-1", "inc-2"], preseed=False)
        h.go()
        h.db.ids = ["inc-2"]
        h.go()
        self.assertEqual(sorted(h.seen.load()), ["inc-2"])

    def test_a_full_poll_prunes_nothing_and_warns(self):
        ids = [f"inc-{n:02d}" for n in range(50)]
        h = harness(ids=ids, preseed=False)
        h.seen.save({"old-gone": iso_z(NOW - timedelta(days=2))})
        h.go()
        self.assertIn("old-gone", h.seen.load())
        self.assertEqual(sum("poll full" in line for line in h.logs), 1)
        self.assertEqual(len(h.seen.load()), 51)

    def test_fewer_than_a_full_poll_prunes_the_ids_not_polled(self):
        h = harness(ids=["a"], preseed=False)
        h.seen.save({"old-gone": iso_z(NOW - timedelta(days=2))})
        h.go()
        self.assertEqual(sorted(h.seen.load()), ["a"])
        self.assertFalse(any("poll full" in line for line in h.logs))

    def test_a_corrupt_file_is_empty_with_one_warning(self):
        h = harness(ids=["inc-1"], preseed=False)
        h.seen_path.write_text("[1, 2, 3]", encoding="utf-8")
        h.go()
        self.assertEqual(h.decide_calls, [])
        self.assertEqual(sum("treated as empty" in line for line in h.logs), 1)
        self.assertEqual(list(h.seen.load()), ["inc-1"])

    def test_dev_and_prod_use_different_files(self):
        self.assertNotEqual(run.SeenStore(run.ROOT / "logs" / "agent_seen_dev.json").path, run.SeenStore(run.ROOT / "logs" / "agent_seen_prod.json").path)
        text = Path(run.__file__).read_text(encoding="utf-8")
        self.assertIn('f"agent_seen_{args.target}.json"', text)

    def test_the_limit_still_caps_the_decisions(self):
        h = harness(ids=[f"inc-{n}" for n in range(8)], limit=3)
        h.go()
        self.assertEqual(len(h.db.params("write")), 3)
        self.assertTrue(any("deferred=5" in line for line in h.logs))

    def test_the_oldest_eligible_ids_are_decided_in_poll_order(self):
        h = harness(ids=[f"inc-{n}" for n in range(8)], limit=None)
        h.go()
        self.assertEqual([p["disturbance_id"][0] for p in h.db.params("mark")], [f"inc-{n}" for n in range(5)])

    def test_new_ids_do_not_use_up_the_limit_of_older_ones(self):
        h = harness(ids=["old-1", "new-1", "old-2"], preseed=False)
        h.seen.save({"old-1": iso_z(NOW - timedelta(hours=1)), "old-2": iso_z(NOW - timedelta(hours=1))})
        h.go()
        self.assertEqual([p["disturbance_id"][0] for p in h.db.params("mark")], ["old-1", "old-2"])

    def test_a_forced_stand_down_also_waits_one_cycle(self):
        h = harness(ids=["inc-1"], preseed=False, decide_fn=decide, context_overrides={"severity": 4.0})
        h.go()
        self.assertNotIn("write", h.db.kinds)
        h.hour = 13
        h.go()
        self.assertIn("write", h.db.kinds)

    def test_the_store_is_saved_before_the_first_decision(self):
        h = harness(ids=["inc-1"])
        seen_at_decision = []
        h.decide_fn = lambda c, g, b, *, monotonic: (seen_at_decision.append(h.seen_path.exists()), fake_decision())[1]
        h.go()
        self.assertEqual(seen_at_decision, [True])

    def test_a_save_that_fails_halfway_leaves_the_old_file(self):
        h = harness(ids=["a"], preseed=False)
        h.seen.save({"keep": iso_z(NOW)})
        with mock.patch("agent.seen.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                h.seen.save({"other": iso_z(NOW)})
        self.assertEqual(h.seen.load(), {"keep": iso_z(NOW)})
        self.assertEqual([p.name for p in h.dir.glob("*.tmp")], [])


class Epoch(unittest.TestCase):
    def test_the_default_epoch_is_agent_epoch(self):
        h = harness()
        h.go()
        self.assertEqual(h.db.params("poll")[0]["epoch"][0], datetime(2026, 10, 12, tzinfo=timezone.utc))

    def test_an_epoch_override_reaches_the_poll(self):
        h = harness()
        h.epoch = datetime(2026, 10, 14, 7, 14, 59, tzinfo=timezone.utc)
        h.go()
        self.assertEqual(h.db.params("poll")[0]["epoch"], (datetime(2026, 10, 14, 7, 14, 59, tzinfo=timezone.utc), "TIMESTAMP"))

    def test_no_override_means_agent_epoch(self):
        self.assertEqual(run.resolve_epoch("dev", None), run.AGENT_EPOCH)
        self.assertEqual(run.resolve_epoch("prod", None), run.AGENT_EPOCH)

    def test_dev_accepts_an_override(self):
        self.assertEqual(run.resolve_epoch("dev", "2026-10-14T07:14:59Z"), datetime(2026, 10, 14, 7, 14, 59, tzinfo=timezone.utc))

    def test_an_override_without_a_zone_is_utc(self):
        self.assertEqual(run.resolve_epoch("dev", "2026-10-14T07:14:59"), datetime(2026, 10, 14, 7, 14, 59, tzinfo=timezone.utc))

    def test_an_override_with_an_offset_is_converted_to_utc(self):
        self.assertEqual(run.resolve_epoch("dev", "2026-10-14T03:14:59-04:00"), datetime(2026, 10, 14, 7, 14, 59, tzinfo=timezone.utc))

    def test_prod_refuses_an_override(self):
        with self.assertRaises(ValueError) as caught:
            run.resolve_epoch("prod", "2026-10-14T07:14:59Z")
        self.assertIn("only with --target dev", str(caught.exception))

    def test_main_exits_2_for_prod_with_an_epoch_before_reading_any_file(self):
        with mock.patch.object(run, "configure_logging", return_value=logging.getLogger("agent-test")):
            self.assertEqual(run.main(["--target", "prod", "--epoch", "2026-10-14T07:14:59Z", "--env", "does-not-exist.env"]), 2)

    def test_main_exits_2_for_a_bad_epoch(self):
        with mock.patch.object(run, "configure_logging", return_value=logging.getLogger("agent-test")):
            self.assertEqual(run.main(["--target", "dev", "--epoch", "not a date", "--env", "does-not-exist.env"]), 2)


class Records(unittest.TestCase):
    def test_one_ndjson_line_per_decision_without_the_context(self):
        h = harness(ids=["inc-1", "inc-2"])
        h.go()
        lines = h.decisions_path.read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 2)
        record = json.loads(lines[0])
        self.assertEqual(record["disturbance_id"], "inc-1")
        self.assertNotIn("context_snapshot", record)
        self.assertNotIn("context_json", record)
        self.assertEqual((record["model_calls"], record["prompt_tokens"], record["output_tokens"], record["thought_tokens"]),
                         (1, 1000, 100, 50))
        self.assertEqual(record["decided_at"], "2026-10-14T12:15:00.000Z")
        self.assertEqual(record["released_at"], "2026-10-15T12:45:00.000Z")

    def test_no_decision_no_line(self):
        h = harness(ids=["inc-1"], fail={"write": StatementFailed("X", "boom", False)})
        h.go()
        self.assertFalse(h.decisions_path.exists())

    def test_the_summary_line(self):
        h = harness(ids=["inc-1", "inc-2"], decide_fn=lambda c, g, b, *, monotonic: fake_decision())
        h.go()
        line = next(x for x in h.logs if x.startswith("agent: cycle "))
        self.assertIn("target=dev polled=2 decided=2 (deploy=2 stand_down=0) left=0 new=0 deferred=0 statements=12 model_calls=2", line)
        self.assertIn("tokens=2000/200/100 cost_usd=0.002000", line)
        self.assertRegex(line, r"duration_s=\d")

    def test_an_incident_that_failed_counts_as_left(self):
        h = harness(ids=["inc-1", "inc-2"], fail={"write": StatementFailed("X", "boom", False)})
        h.go()
        line = next(x for x in h.logs if x.startswith("agent: cycle "))
        self.assertIn("polled=2 decided=0", line)
        self.assertIn("left=2", line)

    def test_no_log_line_has_a_token_or_a_key(self):
        h = harness(ids=["inc-1"], fail={"poll": RuntimeError("x")})
        h.go()
        self.assertNotIn(TOKEN_MARKER, "\n".join(h.logs))


class Pings(unittest.TestCase):
    URL = "https://hc.example.invalid/ping/abc-NOT-A-REAL-UUID"

    def pinger(self, script, url=URL):
        from _support import FakeOpener
        opener = FakeOpener(script)
        logs = []
        return run.make_pinger(url, opener=opener, log=logs.append), opener, logs

    def test_success_is_a_get_of_the_url(self):
        ping, opener, _ = self.pinger([(200, "OK")])
        ping("success")
        self.assertEqual((opener.requests[0]["method"], opener.requests[0]["url"]), ("GET", self.URL))

    def test_fail_adds_the_fail_suffix(self):
        ping, opener, _ = self.pinger([(200, "OK")])
        ping("fail")
        self.assertEqual(opener.requests[0]["url"], self.URL + "/fail")

    def test_a_trailing_slash_is_not_doubled(self):
        ping, opener, _ = self.pinger([(200, "OK")], url=self.URL + "/")
        ping("fail")
        self.assertEqual(opener.requests[0]["url"], self.URL + "/fail")

    def test_the_project_user_agent_is_sent(self):
        import files_api
        ping, opener, _ = self.pinger([(200, "OK")])
        ping("success")
        self.assertEqual(opener.requests[0]["headers"]["user-agent"], files_api.UA)

    def test_a_non_2xx_answer_is_logged_without_the_url_and_does_not_raise(self):
        ping, _, logs = self.pinger([(500, "x")])
        ping("success")
        self.assertEqual(logs, ["agent: S4 ping failed (HTTP 500)"])

    def test_a_network_error_is_logged_without_the_url_and_does_not_raise(self):
        ping, _, logs = self.pinger([S.url_error()])
        ping("success")
        self.assertEqual(len(logs), 1)
        self.assertNotIn("hc.example.invalid", logs[0])

    def test_no_url_means_no_request(self):
        ping, opener, logs = self.pinger([], url=None)
        ping("success")
        ping("fail")
        self.assertEqual((opener.requests, logs), ([], []))

    def test_url_validation(self):
        self.assertTrue(run.valid_ping_url(self.URL))
        for bad in ("http://hc.example.invalid/x", "", None, "not a url", "https://"):
            with self.subTest(bad=bad):
                self.assertFalse(run.valid_ping_url(bad))


class Constants(unittest.TestCase):
    def test_values(self):
        self.assertEqual((run.QUOTA_WINDOW_HOUR_UTC, run.CYCLE_SECONDS), (3, 1800))

    def test_paths(self):
        self.assertEqual(run.LOCK_PATH, S.ROOT / "logs" / "agent.lock")
        self.assertEqual(run.DECISIONS_PATH, S.ROOT / "logs" / "agent_decisions.ndjson")


class StartDebounce(unittest.TestCase):
    """Task Scheduler sometimes launches a task twice, about 2 s apart (outage report 2026-10-27): a start less than 60 s after the
    previous one does nothing."""

    def debounced_harness(self, **options):
        h = harness(**options)
        h.start_path = h.dir / "agent_last_start.txt"
        return h

    def test_a_second_start_two_seconds_later_is_debounced(self):
        h = self.debounced_harness(ids=["inc-1"])
        self.assertEqual(h.go(), 0)
        before = (len(h.pings), h.job1_calls, h.db.statements, len(h.decide_calls))
        h.now = NOW + timedelta(seconds=2)
        self.assertEqual(h.go(), 0)
        self.assertEqual((len(h.pings), h.job1_calls, h.db.statements, len(h.decide_calls)), before)
        self.assertTrue(any("debounced" in line for line in h.logs))
        self.assertEqual(h.start_path.read_text(encoding="ascii").strip(), iso_z(NOW))     # the stamp still names the real start

    def test_a_start_61_seconds_later_runs(self):
        h = self.debounced_harness(ids=["inc-1"])
        h.go()
        h.now = NOW + timedelta(seconds=61)
        h.go()
        self.assertEqual(len(h.pings), 2)
        self.assertEqual(sum("debounced" in line for line in h.logs), 0)
        self.assertEqual(h.start_path.read_text(encoding="ascii").strip(), iso_z(NOW + timedelta(seconds=61)))

    def test_exactly_60_seconds_runs(self):
        h = self.debounced_harness()
        h.go()
        h.now = NOW + timedelta(seconds=60)
        h.go()
        self.assertEqual(len(h.pings), 2)

    def test_a_corrupt_stamp_runs(self):
        for content in (b"not a time", b"", bytes([255, 254, 0]), b"2026-99-99T99:99:99Z"):
            h = self.debounced_harness()
            h.start_path.write_bytes(content)
            h.go()
            self.assertEqual(len(h.pings), 1, content)
            self.assertEqual(h.start_path.read_text(encoding="ascii").strip(), iso_z(NOW))

    def test_a_missing_stamp_runs_and_a_future_stamp_runs(self):
        h = self.debounced_harness()
        h.go()
        self.assertEqual(len(h.pings), 1)
        h2 = self.debounced_harness()
        h2.start_path.write_text(iso_z(NOW + timedelta(minutes=5)) + chr(10), encoding="ascii")
        h2.go()
        self.assertEqual(len(h2.pings), 1)

    def test_the_stamp_is_written_atomically(self):
        h = self.debounced_harness()
        with mock.patch("agent.run.os.replace", wraps=run.os.replace) as replace:
            h.go()
        stamp_calls = [c for c in replace.call_args_list if Path(c.args[1]) == h.start_path]     # the seen store also uses os.replace
        self.assertEqual(len(stamp_calls), 1)
        temp, final = stamp_calls[0].args
        self.assertEqual(Path(final), h.start_path)
        self.assertNotEqual(Path(temp), h.start_path)
        self.assertEqual(Path(temp).parent, h.start_path.parent)
        self.assertFalse(Path(temp).exists())
        self.assertEqual(h.start_path.read_text(encoding="ascii").strip(), iso_z(NOW))

    def test_a_failed_write_never_stops_the_run(self):
        h = self.debounced_harness()
        real = run.os.replace

        def replace(src, dst):
            if Path(dst) == h.start_path:
                raise OSError("disk")
            return real(src, dst)
        with mock.patch("agent.run.os.replace", side_effect=replace):
            self.assertEqual(h.go(), 0)
        self.assertEqual(len(h.pings), 1)
        self.assertFalse(h.start_path.exists())

    def test_a_held_lock_is_reported_as_already_running_not_debounced(self):
        h = self.debounced_harness(hold_lock=True)
        h.start_path.write_text(iso_z(NOW - timedelta(seconds=1)) + chr(10), encoding="ascii")
        h.go()
        self.assertTrue(any("already running" in line for line in h.logs))
        self.assertFalse(any("debounced" in line for line in h.logs))

    def test_the_debounce_is_off_without_a_start_path(self):
        h = harness()
        h.go()
        h.now = NOW + timedelta(seconds=2)
        h.go()
        self.assertEqual(len(h.pings), 2)

    def test_the_command_line_passes_the_real_path(self):
        self.assertEqual(run.START_PATH, S.ROOT / "logs" / "agent_last_start.txt")
        self.assertEqual(run.DEBOUNCE_SECONDS, 60)
        self.assertIn("start_path=START_PATH", Path(run.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
