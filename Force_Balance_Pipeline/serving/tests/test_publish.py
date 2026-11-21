"""Tests for serving/publish.py (doc 05 "Serving (Phase 7)" and "Monitoring", S5): fake Jobs API, client and pinger; lock files and the log in
a temp folder; Postgres force_serving_test. No network."""
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pg_support
from agent.dbsql import ResultTooLarge
from agent.lock import AlreadyRunning, InstanceLock
from serving import fixtures, jobs, publish, pull, statements
from test_pull import FakeClient, disturbance, probe, reading

NOON = datetime(2026, 10, 19, 12, 17, tzinfo=timezone.utc)
RUN = (500, NOON - timedelta(minutes=40))
ENV_TEXT = ("DATABRICKS_HOST=example.invalid\nDATABRICKS_TOKEN=t\nDATABRICKS_WAREHOUSE_ID=w\nPOSTGRES_PASSWORD=p\n"
            "SERVING_PING_S5_URL=https://hc.example.invalid/ping\n")


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, seconds):
        self.t += seconds

    def monotonic(self):
        return self.t


class PublishTest(unittest.TestCase):
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
        self.dir = Path(tempfile.mkdtemp())
        self.env = self.dir / ".env.serving"
        self.env.write_text(ENV_TEXT, encoding="utf-8")
        self.pings, self.job_calls, self.clock = [], [], Clock()
        self.latest = RUN

    def jobs_fn(self, env, job_id):
        self.job_calls.append(job_id)
        if isinstance(self.latest, Exception):
            raise self.latest
        return self.latest

    def go(self, extra=(), now=NOON, **over):
        kwargs = dict(now_fn=lambda: now, sleep=self.clock.sleep, monotonic=self.clock.monotonic, jobs_fn=self.jobs_fn,
                      client_factory=lambda env: FakeClient({"disturbance": [[disturbance("D1")]]}), conn_factory=lambda env: self.conn,
                      ping=self.pings.append, serve_lock=self.dir / "serve.lock", agent_lock=self.dir / "agent.lock",
                      serve_log=self.dir / "serve.log", owns_conn=False)
        kwargs.update(over)
        return publish.main(["--env", str(self.env), *extra], **kwargs)

    def rows(self):
        return self.conn.execute("SELECT kind, status, error, job1_run_id, statements, row_counts FROM serving.publish_log ORDER BY publish_id").fetchall()

    def log_lines(self):
        path = self.dir / "serve.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class Skips(PublishTest):
    def test_a_held_serve_lock_exits_quietly(self):
        held = InstanceLock(self.dir / "serve.lock").acquire()
        try:
            self.assertEqual(self.go(), 0)
        finally:
            held.release()
        self.assertEqual((self.pings, self.rows(), self.job_calls, self.log_lines()), ([], [], [], []))

    def test_the_quota_hour_skips_without_the_jobs_api(self):
        for minute in (0, 30, 59):
            self.assertEqual(self.go(now=NOON.replace(hour=3, minute=minute)), 0)
        self.assertEqual([r[1:3] for r in self.rows()], [("skipped", "skip_quota_hour")] * 3)
        self.assertEqual(self.pings, ["success"] * 3)
        self.assertEqual(self.job_calls, [])

    def test_no_success_and_no_new_run_are_skips(self):
        self.latest = None
        self.assertEqual(self.go(), 0)
        self.latest = RUN
        self.conn.execute("INSERT INTO serving.publish_log (kind, started_at, status, job1_run_id, job1_run_end) VALUES ('live', %s, 'ok', %s, %s)",
                          (NOON, RUN[0], RUN[1]))
        self.conn.commit()
        self.assertEqual(self.go(), 0)
        skipped = [r for r in self.rows() if r[1] == "skipped"]
        self.assertEqual([r[2] for r in skipped], ["skip_no_success", "skip_no_new_run"])
        self.assertEqual(self.pings, ["success", "success"])


class AgentWait(PublishTest):
    def test_an_agent_that_never_lets_go_gives_agent_busy(self):
        held = InstanceLock(self.dir / "agent.lock").acquire()
        try:
            self.assertEqual(self.go(), 0)
        finally:
            held.release()
        self.assertEqual([r[1:3] for r in self.rows()], [("skipped", "agent_busy")])
        self.assertEqual(self.pings, ["success"])
        self.assertGreaterEqual(self.clock.t, 600)
        self.assertEqual(self.job_calls, [])

    def test_an_agent_that_finishes_after_two_polls_lets_the_pull_go_on(self):
        held = InstanceLock(self.dir / "agent.lock").acquire()
        polls = []

        def sleep(seconds):
            polls.append(seconds)
            self.clock.sleep(seconds)
            if len(polls) == 2:
                held.release()
        try:
            self.assertEqual(self.go(sleep=sleep), 0)
        finally:
            held.release()
        self.assertEqual(len(polls), 2)
        self.assertEqual([r[1] for r in self.rows()], ["ok"])


class Guards(PublishTest):
    def test_fixture_data_stops_everything(self):
        self.conn.execute("INSERT INTO serving.publish_log (kind, started_at, status) VALUES ('fixture', now(), 'ok')")
        self.conn.commit()
        self.assertEqual(self.go(), 2)
        self.assertEqual(self.pings, ["fail"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.job_calls, [])
        self.assertIn("serving.schema --recreate", self.log_lines()[0])

    def test_a_jobs_api_error_is_a_failed_row(self):
        self.latest = jobs.JobsApiError("the Jobs API answered HTTP 500")
        self.assertEqual(self.go(), 1)
        self.assertEqual([r[1] for r in self.rows()], ["failed"])
        self.assertEqual(self.pings, ["fail"])

    def test_a_missing_env_key_exits_2_quietly(self):
        self.env.write_text(ENV_TEXT.replace("POSTGRES_PASSWORD=p\n", ""), encoding="utf-8")
        self.assertEqual(self.go(), 2)
        self.assertEqual((self.pings, self.rows(), self.log_lines()), ([], [], []))


class Publishing(PublishTest):
    def test_a_good_run_writes_the_row_the_data_and_a_success_ping(self):
        self.assertEqual(self.go(pull_fn=pull.run_pulls), 0)
        kind, status, error, run_id, statements, counts = self.rows()[0]
        self.assertEqual((kind, status, error, run_id), ("live", "ok", None, RUN[0]))
        self.assertEqual(statements, 31)
        self.assertEqual(counts["disturbance"], 1)
        self.assertEqual(self.conn.execute("SELECT job1_run_end FROM serving.publish_log").fetchone()[0], RUN[1])
        self.assertEqual(self.conn.execute("SELECT disturbance_id FROM serving.disturbance").fetchall(), [("D1",)])
        self.assertEqual(self.pings, ["success"])
        self.assertEqual(self.job_calls, [692051683030737])

    def test_a_pull_error_is_a_failed_row_with_nothing_written_and_the_url_scrubbed(self):
        factory = lambda env: FakeClient({"disturbance": [[disturbance("D1")]]}, fail_on=5,        # noqa: E731
                                         error=lambda m: RuntimeError("fetch https://dbc-1.example.invalid/api/x?q=1 failed"))
        self.assertEqual(self.go(pull_fn=pull.run_pulls, client_factory=factory), 1)
        row = self.rows()[0]
        self.assertEqual(row[1], "failed")
        self.assertIn("<url>", row[2])
        self.assertNotIn("example.invalid", row[2])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM serving.disturbance").fetchone()[0], 0)
        self.assertEqual(self.pings, ["fail"])
        self.assertNotIn("example.invalid", "".join(self.log_lines()))


class FailureLog(PublishTest):
    """Round 8: a failed publish says which source failed and how many statements it really spent (the 2026-11-17 failures logged statements=0
    and no table)."""

    def test_a_failed_pull_logs_the_real_statement_count_and_the_failing_table(self):
        factory = lambda env: FakeClient({"disturbance": [[disturbance("D1")]]}, fail_on=5, error=RuntimeError)     # noqa: E731
        self.assertEqual(self.go(pull_fn=pull.run_pulls, client_factory=factory), 1)             # dims 3, sector_baseline 4, disturbance 5
        line = self.log_lines()[-1]
        self.assertIn("decision=publish status=failed statements=5 ", line)
        self.assertIn("table=disturbance RuntimeError: boom", line)
        row = self.conn.execute("SELECT status, statements, error FROM serving.publish_log").fetchone()
        self.assertEqual(row[:2], ("failed", 5))
        self.assertIn("table=disturbance", row[2])

    def test_a_row_cap_failure_names_the_table_and_its_statements(self):
        def always(params):
            raise ResultTooLarge("the result has more than 50000 rows")
        self.assertEqual(self.go(pull_fn=pull.run_pulls, client_factory=lambda env: FakeClient({"probe_reading": [[probe(1, NOON)]]})), 0)
        self.latest = (501, NOON + timedelta(hours=2))
        self.assertEqual(self.go(now=NOON + timedelta(hours=3), pull_fn=pull.run_pulls,
                                 client_factory=lambda env: FakeClient({"probe_reading": always})), 1)
        line = self.log_lines()[-1]
        self.assertRegex(line, r"statements=([5-9]|\d\d) ")
        self.assertIn("table=probe_reading ResultTooLarge: probe_reading: the window", line)

    def test_a_pull_function_that_has_no_client_counter_logs_zero(self):
        def boom(client, conn, started, days):
            raise RuntimeError("no counter")
        self.assertEqual(self.go(pull_fn=boom, client_factory=lambda env: object()), 1)
        self.assertIn("statements=0 ", self.log_lines()[-1])


class WindowedPublish(PublishTest):
    def test_a_publish_whose_probe_reading_read_is_over_the_cap_succeeds_and_advances_the_watermark(self):
        first = lambda env: FakeClient({"probe_reading": [[probe(1, NOON - timedelta(hours=1))]]})                  # noqa: E731
        self.assertEqual(self.go(pull_fn=pull.run_pulls, client_factory=first), 0)
        later = NOON + timedelta(days=3, hours=2)
        self.latest = (501, later - timedelta(minutes=40))
        arrivals = [NOON - timedelta(hours=1)] + [NOON + timedelta(hours=7 * n) for n in range(1, 10)]          # P1 again, then nine new rows
        rows = [probe(i, a) for i, a in enumerate(arrivals, 1)]

        def serve(params):
            if "wm" in params:
                raise ResultTooLarge("the result has more than 50000 rows")
            lo, hi = params["lo"][0], params.get("hi", (None,))[0]
            return [r for r in rows if r["arrival_ts"] >= lo and (hi is None or r["arrival_ts"] < hi)]
        client = FakeClient({"probe_reading": serve})
        self.assertEqual(self.go(now=later, pull_fn=pull.run_pulls, client_factory=lambda env: client), 0)
        self.assertEqual([r[1] for r in self.rows()], ["ok", "ok"])
        got = self.conn.execute("SELECT event_id FROM serving.probe_reading ORDER BY event_id").fetchall()
        self.assertEqual(sorted(g[0] for g in got), sorted(r["event_id"] for r in rows))                         # every row once
        self.assertEqual(self.conn.execute("SELECT value FROM serving.watermark WHERE source = 'probe_reading'").fetchone()[0], max(arrivals))
        self.assertEqual(len(client.calls_for("probe_reading")), 1 + 4)                                          # the normal read, 3 days and an open window
        self.assertIn("decision=publish status=ok", self.log_lines()[-1])
        self.assertEqual(self.pings, ["success", "success"])


class NoPing(PublishTest):
    def test_no_ping_sends_nothing_on_any_path(self):
        self.env.write_text(ENV_TEXT.replace("SERVING_PING_S5_URL=https://hc.example.invalid/ping\n", ""), encoding="utf-8")
        with mock.patch.object(publish, "make_pinger", side_effect=AssertionError("a pinger was built")):
            self.assertEqual(self.go(["--no-ping"], ping=None, now=NOON.replace(hour=3)), 0)                  # skip
            self.assertEqual(self.go(["--no-ping"], ping=None, pull_fn=pull.run_pulls), 0)                    # publish
            self.latest = jobs.JobsApiError("x")
            self.assertEqual(self.go(["--no-ping"], ping=None), 1)                                            # failure
        self.assertEqual(self.pings, [])


class Log(PublishTest):
    def test_one_line_per_run_and_no_url(self):
        self.go(now=NOON.replace(hour=3))
        self.go(pull_fn=pull.run_pulls)
        self.latest = jobs.JobsApiError("see https://x.example.invalid/y")
        self.go()
        lines = self.log_lines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("2026-10-19T03:17:00Z decision=skip_quota_hour status=skipped"))
        self.assertIn("decision=publish status=ok statements=31", lines[1])
        self.assertIn("status=failed", lines[2])
        self.assertNotIn("http", "\n".join(lines).lower())


class StartDebounce(PublishTest):
    """Task Scheduler sometimes launches a task twice, about 2 s apart (outage report 2026-10-27): a start under 60 s after the previous
    one does nothing."""

    def setUp(self):
        super().setUp()
        self.stamp = self.dir / "serve_last_start.txt"

    def start(self, seconds=0, **over):
        return self.go(now=NOON + timedelta(seconds=seconds), start_path=self.stamp, **over)

    def test_a_second_start_two_seconds_later_is_debounced(self):
        self.assertEqual(self.start(), 0)
        before = (self.rows(), list(self.pings), list(self.job_calls), len(self.log_lines()))
        self.assertEqual(self.start(2), 0)
        self.assertEqual((self.rows(), self.pings, self.job_calls), before[:3])
        lines = self.log_lines()
        self.assertEqual(len(lines), before[3] + 1)
        self.assertIn("debounced", lines[-1])
        self.assertTrue(lines[-1].startswith("2026-10-19T12:17:02Z "))
        self.assertEqual(self.stamp.read_text(encoding="ascii").strip(), "2026-10-19T12:17:00.000Z")

    def test_a_start_61_seconds_later_runs(self):
        self.start()
        self.start(61)
        self.assertEqual(self.pings, ["success", "success"])
        self.assertFalse(any("debounced" in line for line in self.log_lines()))

    def test_a_corrupt_stamp_runs(self):
        for content in (b"not a time", b"", bytes([255, 254, 0])):
            self.stamp.write_bytes(content)
            self.pings.clear()
            self.assertEqual(self.start(), 0)
            self.assertEqual(self.pings, ["success"], content)

    def test_the_stamp_is_written_atomically(self):
        with mock.patch("agent.run.os.replace", wraps=os.replace) as replace:
            self.start()
        calls = [c for c in replace.call_args_list if Path(c.args[1]) == self.stamp]
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(Path(calls[0].args[0]), self.stamp)
        self.assertFalse(Path(calls[0].args[0]).exists())

    def test_a_held_lock_is_not_reported_as_debounced(self):
        self.stamp.write_text("2026-10-19T12:16:59.000Z" + chr(10), encoding="ascii")
        held = InstanceLock(self.dir / "serve.lock").acquire()
        try:
            self.assertEqual(self.start(), 0)
        finally:
            held.release()
        self.assertEqual((self.pings, self.rows(), self.log_lines()), ([], [], []))

    def test_the_debounce_is_off_without_a_start_path(self):
        self.go()
        self.go(now=NOON + timedelta(seconds=2))
        self.assertEqual(self.pings, ["success", "success"])

    def test_the_command_line_passes_the_real_path(self):
        self.assertEqual(publish.SERVE_START, publish.config.ROOT / "logs" / "serve_last_start.txt")
        self.assertIn("main(start_path=SERVE_START)", Path(publish.__file__).read_text(encoding="utf-8"))


class S5PingLog(PublishTest):
    def failing_opener(self, request, timeout=None, **kwargs):
        raise urllib.error.URLError("certificate verify failed for https://hc.example.invalid/ping/SECRET-PATH")

    def test_a_failed_ping_is_logged_with_its_class_and_no_url(self):
        with mock.patch.object(publish.files_api, "default_opener", self.failing_opener):
            self.assertEqual(self.go(ping=None, now=NOON.replace(hour=3)), 0)
        lines = self.log_lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "2026-10-19T03:17:00Z S5 ping failed (TransportError)")
        self.assertIn("decision=skip_quota_hour", lines[1])
        text = "\n".join(lines)
        for fragment in ("example.invalid", "SECRET-PATH", "http"):
            self.assertNotIn(fragment, text)

    def test_a_working_ping_logs_nothing_extra(self):
        class Ok:
            status = 200

            def read(self):
                return b"OK"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        with mock.patch.object(publish.files_api, "default_opener", lambda request, timeout=None, **kw: Ok()):
            self.go(ping=None, now=NOON.replace(hour=3))
        self.assertEqual(len(self.log_lines()), 1)

    def test_the_pinger_is_built_with_the_s5_name_and_the_project_opener(self):
        with mock.patch.object(publish, "make_pinger", return_value=lambda kind: None) as make:
            self.go(ping=None, now=NOON.replace(hour=3))
        _, kwargs = make.call_args
        self.assertEqual(kwargs["name"], "S5")
        self.assertIs(kwargs["opener"], publish.files_api.default_opener)


class Rotation(PublishTest):
    """Phase 8 review finding 10: serve.log is rotated only after serve.lock is held and the debounce has passed, so two starts that Task
    Scheduler launches together cannot both rotate it."""

    def lock_is_held(self):
        probe = InstanceLock(self.dir / "serve.lock")
        try:
            probe.acquire()
        except AlreadyRunning:
            return True
        probe.release()
        return False

    def run_recording(self, **over):
        calls = []

        def fake(path, **kwargs):
            calls.append((path, self.lock_is_held()))              # was serve.lock held at the moment of the call?
            return False
        with mock.patch("serving.publish.logrotate.rotate_if_large", fake):
            code = self.go(**over)
        return code, calls

    def test_a_run_rotates_serve_log_once_and_while_it_holds_serve_lock(self):
        code, calls = self.run_recording()
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(self.dir / "serve.log", True)])

    def test_a_start_that_finds_serve_lock_held_does_not_rotate(self):
        held = InstanceLock(self.dir / "serve.lock").acquire()
        try:
            code, calls = self.run_recording()
        finally:
            held.release()
        self.assertEqual((code, calls), (0, []))

    def test_a_debounced_start_does_not_rotate(self):
        stamp = self.dir / "serve_last_start.txt"
        first, calls = self.run_recording(start_path=stamp)
        self.assertEqual((first, len(calls)), (0, 1))
        second, more = self.run_recording(now=NOON + timedelta(seconds=2), start_path=stamp)
        self.assertEqual((second, more), (0, []))
        self.assertIn("debounced", self.log_lines()[-1])

    def test_a_configuration_problem_does_not_rotate(self):
        self.env.write_text("DATABRICKS_HOST=example.invalid\n", encoding="utf-8")
        code, calls = self.run_recording()
        self.assertEqual((code, calls), (2, []))


class CappedFake(FakeClient):
    """A FakeClient that refuses the statement that would exceed max_statements, through the same check the real client uses."""

    def __init__(self, cap, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_statements = cap

    def read(self, sql, params=None, **kwargs):
        statements.check_cap(self.statements, self.max_statements)
        return super().read(sql, params, **kwargs)


def over_the_cap(params):
    """A source whose normal (watermark) read is over the row cap; its day pages and windows come back empty."""
    if "wm" in params:
        raise ResultTooLarge("the result has more than 50000 rows")
    return []


class StatementCapPublish(PublishTest):
    """Phase 8 review finding 6: a publish never sends more than MAX_PUBLISH_STATEMENTS statements; the one that would be the 81st is refused
    before it is sent, nothing is written to the serving tables and the failure says statement_cap."""

    def catch_up(self, days, cap=80):
        """A first publish, then a catch-up `days` days later whose sector_reading and probe_reading reads are over the row cap: 3 dims, 4 small
        tables, 1 + 31 sector_reading day pages, 1 + (days + 1) probe_reading windows and 1 rejects read, 42 + days statements in all, so 80 for 38 days."""
        first = lambda env: FakeClient({"sector_reading": [[reading(1, NOON - timedelta(hours=1))]],                 # noqa: E731
                                           "probe_reading": [[probe(1, NOON - timedelta(hours=1))]]})
        self.assertEqual(self.go(pull_fn=pull.run_pulls, client_factory=first), 0)
        later = NOON + timedelta(days=days, hours=2)
        self.latest = (501, later - timedelta(minutes=40))
        client = CappedFake(cap, {"sector_reading": over_the_cap, "probe_reading": over_the_cap})
        code = self.go(now=later, pull_fn=pull.run_pulls, client_factory=lambda env: client)
        return code, client

    def test_a_publish_that_needs_exactly_80_statements_succeeds(self):
        code, client = self.catch_up(38)
        self.assertEqual((code, client.statements), (0, 80))
        self.assertEqual([r[1] for r in self.rows()], ["ok", "ok"])
        self.assertIn("decision=publish status=ok statements=80 ", self.log_lines()[-1])

    def test_a_publish_that_would_need_81_aborts_at_80_with_nothing_written(self):
        before = None
        first = lambda env: FakeClient({"sector_reading": [[reading(1, NOON - timedelta(hours=1))]],                 # noqa: E731
                                           "probe_reading": [[probe(1, NOON - timedelta(hours=1))]]})
        self.assertEqual(self.go(pull_fn=pull.run_pulls, client_factory=first), 0)
        state = lambda: (self.conn.execute("SELECT event_id FROM serving.probe_reading ORDER BY event_id").fetchall(),     # noqa: E731
                         self.conn.execute("SELECT source, value FROM serving.watermark ORDER BY source").fetchall())
        before = state()
        self.conn.execute("TRUNCATE serving.publish_log RESTART IDENTITY")
        self.conn.commit()
        later = NOON + timedelta(days=39, hours=2)
        self.latest = (501, later - timedelta(minutes=40))
        client = CappedFake(80, {"sector_reading": over_the_cap, "probe_reading": over_the_cap})
        self.pings.clear()
        code = self.go(now=later, pull_fn=pull.run_pulls, client_factory=lambda env: client)
        self.assertEqual(code, 1)
        self.assertEqual(client.statements, 80)                                         # the 81st was refused, not sent
        self.assertEqual(state(), before)                                               # no serving table and no watermark changed
        row = self.conn.execute("SELECT status, statements, error FROM serving.publish_log").fetchone()
        self.assertEqual(row[:2], ("failed", 80))
        self.assertTrue(row[2].startswith("statement_cap statements=80 cap=80"), row[2])
        line = self.log_lines()[-1]
        self.assertIn("decision=publish status=failed statements=80 ", line)
        self.assertIn("reason='statement_cap statements=80 cap=80", line)
        self.assertEqual(self.pings, ["fail"])

    def test_the_default_client_is_built_with_the_cap(self):
        env = {"DATABRICKS_HOST": "example.invalid", "DATABRICKS_TOKEN": "t", "DATABRICKS_WAREHOUSE_ID": "w"}
        client = publish.default_client(env)
        self.assertEqual(publish.MAX_PUBLISH_STATEMENTS, 80)
        self.assertEqual(client.max_statements, 80)


if __name__ == "__main__":
    unittest.main()
