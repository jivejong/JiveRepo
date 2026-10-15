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
from agent.dbsql import StatementFailed, StatementTimeout
from agent.gemini import GeminiUnavailable
from agent.lock import InstanceLock
from agent.loop import Decision, decide

NOW = datetime(2026, 10, 14, 12, 15, 0, tzinfo=timezone.utc)
TOKEN_MARKER = "dapi-NOT-A-REAL-TOKEN-5521"


def kind_of(sql):
    if sql.startswith("MERGE"):
        return "write"
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
    def __init__(self, ids=(), fail=None, context_overrides=None, no_context=False):
        self.ids = list(ids)
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
                 context_overrides=None, hold_lock=False):
        self.dir = Path(directory)
        self.db = FakeDb(ids, fail, context_overrides)
        self.pings, self.logs, self.decide_calls = [], [], []
        self.budget = CallBudget(20)
        self.lock_path = self.dir / "agent.lock"
        self.decisions_path = self.dir / "agent_decisions.ndjson"
        self.held = InstanceLock(self.lock_path).acquire() if hold_lock else None
        self.hour = hour
        self.decide_fn = decide_fn or self._default_decide
        self.job1 = job1
        self.monotonic = monotonic or StepClock(0.1)
        self.limit = limit
        self.epoch = run.AGENT_EPOCH
        self.ulids = iter(f"01ULID{i:020d}" for i in range(100))

    def _default_decide(self, context, gemini, budget, *, monotonic):
        self.decide_calls.append(context)
        return fake_decision()

    def go(self):
        code = run.run_guarded(
            target="dev", limit=self.limit, lock_path=self.lock_path, db=self.db, gemini=object(), budget=self.budget,
            job1_check=lambda: self.job1, ping=self.pings.append, clock=lambda: NOW.replace(hour=self.hour),
            monotonic=self.monotonic, log=self.logs.append, decisions_path=self.decisions_path,
            decide_fn=self.decide_fn, ulid_fn=lambda: next(self.ulids), epoch=self.epoch)
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
                self.assertEqual(h.db.kinds, ["repair", "poll"])

    def test_an_active_job_1_skips_the_cycle_and_pings_success(self):
        h = harness(ids=["a"], job1=(True, "job1_running"))
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.pings, ["success"])
        self.assertEqual(h.db.kinds, [])
        self.assertTrue(any("job1_running" in line for line in h.logs))

    def test_a_jobs_api_error_also_skips_the_cycle(self):
        h = harness(ids=["a"], job1=(True, "jobs_api_error"))
        h.go()
        self.assertEqual(h.db.kinds, [])
        self.assertEqual(h.pings, ["success"])

    def test_the_lock_is_released_after_a_cycle(self):
        h = harness()
        h.go()
        again = InstanceLock(h.lock_path).acquire()
        again.release()


class Cycle(unittest.TestCase):
    def test_no_incidents_costs_exactly_two_statements_and_no_model_call(self):
        h = harness()
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds, ["repair", "poll"])
        self.assertEqual(h.db.statements, 2)
        self.assertEqual(h.decide_calls, [])
        self.assertEqual(h.pings, ["success"])

    def test_one_incident_runs_seven_statements_in_order(self):
        h = harness(ids=["inc-1"])
        self.assertEqual(h.go(), 0)
        self.assertEqual(h.db.kinds, ["repair", "poll", "context", "jedi", "ships", "write", "mark"])
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
        h = harness(fail={"repair": StatementFailed("X", "boom", False)})
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


class Limit(unittest.TestCase):
    def poll_limit(self, limit):
        h = harness(limit=limit)
        h.go()
        return h.db.params("poll")[0]["limit"]

    def test_no_limit_means_the_maximum(self):
        self.assertEqual(self.poll_limit(None), (5, "INT"))

    def test_a_limit_above_the_maximum_is_capped(self):
        self.assertEqual(self.poll_limit(50), (5, "INT"))

    def test_a_smaller_limit_is_used(self):
        self.assertEqual(self.poll_limit(2), (2, "INT"))

    def test_the_poll_uses_the_agent_epoch(self):
        h = harness()
        h.go()
        self.assertEqual(h.db.params("poll")[0]["epoch"], (datetime(2026, 10, 12, tzinfo=timezone.utc), "TIMESTAMP"))


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
        self.assertIn("target=dev polled=2 decided=2 (deploy=2 stand_down=0) left=0 statements=12 model_calls=2", line)
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


if __name__ == "__main__":
    unittest.main()
