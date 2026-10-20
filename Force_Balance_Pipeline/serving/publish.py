"""The ForceServe publish command (doc 05 "Serving (Phase 7)" and "Monitoring", S5).

    serving\\.venv\\Scripts\\python.exe -m serving.publish [--env PATH] [--no-ping] [--backfill-days N]

One run: take logs\\serve.lock (held: exit 0, nothing else); skip the 03:xx UTC hour; wait up to 10 minutes for logs\\agent.lock to be
free, so the pull follows the agent's cycle while the warehouse is still on; refuse if fixture data is in the database; ask the Jobs API
for Job 1's latest successful run and let serving.gate decide; pull with serving.pull; write one serving.publish_log row; ping S5
(SERVING_PING_S5_URL, success or /fail, the same pinger agent/run.py uses for S4); append one line to logs\\serve.log.
Exit codes: 0 published or skipped on purpose, 1 failed, 2 configuration problem or fixture data present.
Nothing here prints or logs a URL, key, token or host: error text is scrubbed of anything that looks like a URL.
"""
import argparse
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from agent.config import JOB1_ID
from agent.lock import AlreadyRunning, InstanceLock
from agent.run import make_pinger, valid_ping_url
from serving import config, gate, pull
from serving.jobs import JobsApiError, latest_successful_run
from serving.statements import ServingStatementClient

SERVE_LOCK = config.ROOT / "logs" / "serve.lock"
AGENT_LOCK = config.ROOT / "logs" / "agent.lock"
SERVE_LOG = config.ROOT / "logs" / "serve.log"
QUOTA_WINDOW_HOUR_UTC = 3
AGENT_WAIT_SECONDS = 600
AGENT_POLL_SECONDS = 5
BASE_KEYS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID", "POSTGRES_PASSWORD")


def scrub(text):
    """The text with every URL replaced by <url>, cut to 500 characters."""
    return re.sub(r"https?://\S+", "<url>", str(text))[:500]


def agent_is_free(agent_lock):
    """True if logs\\agent.lock can be taken right now (it is released again at once)."""
    lock = InstanceLock(agent_lock)
    try:
        lock.acquire()
    except AlreadyRunning:
        return False
    lock.release()
    return True


def wait_for_agent(agent_lock, *, sleep, monotonic, limit=AGENT_WAIT_SECONDS, poll=AGENT_POLL_SECONDS):
    start = monotonic()
    while True:
        if agent_is_free(agent_lock):
            return True
        if monotonic() - start >= limit:
            return False
        sleep(poll)


def write_row(conn, status, started, finished, error=None, run=None, statements=None, row_counts=None):
    conn.execute("INSERT INTO serving.publish_log (kind, started_at, finished_at, job1_run_id, job1_run_end, status, statements, "
                 "row_counts, error) VALUES ('live', %s, %s, %s, %s, %s, %s, %s, %s)",
                 (started, finished, run[0] if run else None, run[1] if run else None, status, statements,
                  None if row_counts is None else Jsonb(row_counts), error))
    conn.commit()


def default_client(env):
    return ServingStatementClient(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], env["DATABRICKS_WAREHOUSE_ID"],
                                  opener=urllib.request.urlopen, sleep=time.sleep, monotonic=time.monotonic)


def main(argv=None, *, now_fn=None, sleep=time.sleep, monotonic=time.monotonic, jobs_fn=latest_successful_run, pull_fn=pull.run_pulls,
         client_factory=default_client, conn_factory=config.connect, ping=None, serve_lock=SERVE_LOCK, agent_lock=AGENT_LOCK,
         serve_log=SERVE_LOG, wait_limit=AGENT_WAIT_SECONDS, poll=AGENT_POLL_SECONDS, owns_conn=True):
    parser = argparse.ArgumentParser(prog="serving.publish")
    parser.add_argument("--env", default=str(config.DEFAULT_ENV_FILE))
    parser.add_argument("--no-ping", action="store_true")
    parser.add_argument("--backfill-days", type=int, default=pull.DEFAULT_BACKFILL_DAYS)
    args = parser.parse_args(argv)
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    try:
        env = config.load_env(args.env, required=BASE_KEYS + (() if args.no_ping else ("SERVING_PING_S5_URL",)))
        if not args.no_ping and not valid_ping_url(env["SERVING_PING_S5_URL"]):
            raise config.ConfigError("SERVING_PING_S5_URL is not an https URL")
    except (config.ConfigError, OSError) as e:
        print(f"serving.publish: configuration problem: {scrub(e)}")
        return 2
    if ping is None:
        ping = (lambda kind: None) if args.no_ping else make_pinger(env["SERVING_PING_S5_URL"], opener=urllib.request.urlopen,
                                                                  log=lambda m: None)

    lock = InstanceLock(serve_lock)
    try:
        lock.acquire()
    except AlreadyRunning:
        return 0
    started, t0 = now_fn(), monotonic()
    outcome = {"decision": "error", "status": "failed", "statements": 0, "rows": 0, "reason": ""}
    held, code = {}, 1                                       # held["conn"] is the open connection, if any, for the finally below
    try:
        code = _run(env, args, outcome, started, now_fn, sleep, monotonic, jobs_fn, pull_fn, client_factory, conn_factory, ping,
                    agent_lock, wait_limit, poll, held)
    except BaseException as e:                              # a failure before a row could be written (for example no database)
        outcome.update(decision="error", status="failed", reason=f"{type(e).__name__}: {scrub(e)}")
        ping("fail")
        code = 1
    finally:
        if held.get("conn") is not None and owns_conn:
            held["conn"].close()
        _log_line(serve_log, now_fn(), outcome, monotonic() - t0)
        lock.release()
    return code


def _run(env, args, o, started, now_fn, sleep, monotonic, jobs_fn, pull_fn, client_factory, conn_factory, ping, agent_lock, wait_limit,
         poll, held):
    conn = held["conn"] = conn_factory(env)

    def finish(status, decision, code, kind, error=None, run=None, statements=None, row_counts=None, write=True):
        if write:
            write_row(conn, status, started, now_fn(), error, run, statements, row_counts)
        o.update(decision=decision, status=status, reason=error or "", statements=statements or 0,
                 rows=sum((row_counts or {}).values()))
        ping(kind)
        return code

    if started.hour == QUOTA_WINDOW_HOUR_UTC:
        return finish("skipped", "skip_quota_hour", 0, "success", "skip_quota_hour")
    if not wait_for_agent(agent_lock, sleep=sleep, monotonic=monotonic, limit=wait_limit, poll=poll):
        return finish("skipped", "agent_busy", 0, "success", "agent_busy")
    if conn.execute("SELECT EXISTS (SELECT 1 FROM serving.publish_log WHERE kind = 'fixture')").fetchone()[0]:
        return finish("failed", "fixture_guard", 2, "fail", "fixture data present: run python -m serving.schema --recreate", write=False)
    try:
        latest = jobs_fn(env, JOB1_ID)
    except JobsApiError as e:
        return finish("failed", "jobs_error", 1, "fail", scrub(f"JobsApiError: {e}"))
    decision = gate.decide(started, latest, gate.last_live_ok(conn))
    if decision != "publish":
        return finish("skipped", decision, 0, "success", decision)
    try:
        result = pull_fn(client_factory(env), conn, started, args.backfill_days)
    except BaseException as e:
        conn.rollback()
        return finish("failed", "publish", 1, "fail", scrub(f"{type(e).__name__}: {e}"))
    return finish("ok", "publish", 0, "success", None, latest, result["statements"], result["rows"])


def _log_line(path, when, o, duration):
    line = (f"{when:%Y-%m-%dT%H:%M:%S}Z decision={o['decision']} status={o['status']} statements={o['statements']} rows={o['rows']} "
            f"duration_s={duration:.1f}" + (f" reason={scrub(o['reason'])!r}" if o["reason"] else ""))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
