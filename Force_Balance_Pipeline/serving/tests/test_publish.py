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
from agent.lock import InstanceLock
from serving import fixtures, jobs, publish, pull
from test_pull import FakeClient, disturbance

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


if __name__ == "__main__":
    unittest.main()
