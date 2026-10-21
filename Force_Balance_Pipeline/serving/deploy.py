"""The manual deployment service (doc 06 "User-initiated deployment", "Manual deployment path"; doc 01 D9: one constraint layer).

deploy() runs the agent's own per-incident path for a user's choice of Jedi and starship: the same reads (agent.queries), the same
build_context, forced_stand_down and check_deploy (agent.constraints), the same ETA and release time (agent.distance), the same row
(agent.run.build_row, with decided_by 'user' and no model) and the same MERGE and mark. Nothing here copies agent logic. Steps, each of
which refuses without writing anything: validate the request; refuse in the 03:xx UTC hour; check the local Postgres that the incident
is open; take logs\\agent.lock (once a second for up to 10 s); ask the Jobs API whether Job 1 is active; read the context and the
availability; forced stand-down; check_deploy; re-check Job 1; MERGE; mark; refresh serving.deployment and set the incident's
agent_processed in the local Postgres. One line goes to logs\\deploy.log: never the rationale text, a URL or a host.
Result: {"ok", "reasons", "deployment_id", "statements"} (plus "warnings" when the warehouse write succeeded but a later step failed).
"""
import re
import sys
import time
import urllib.request
from datetime import timezone

from agent import queries
from agent.config import JOB1_ID, ROOT as AGENT_ROOT
from agent.constraints import check_deploy, forced_stand_down
from agent.context import build_context, constraint_inputs
from agent.dbsql import StatementClient
from agent.distance import party_eta_hours
from agent.jobs import job1_active
from agent.lock import AlreadyRunning, InstanceLock
from agent.loop import Decision
from agent.run import build_row
from agent.ulid import ulid_now
from serving import config, pull
from serving.statements import ServingStatementClient

TARGET = "prod"
QUOTA_WINDOW_HOUR_UTC = 3
LOCK_TRIES = 10
LOCK_WAIT_SECONDS = 1
MAX_PARTY = 4
MAX_RATIONALE = 2000
DEPLOY_LOG = config.ROOT / "logs" / "deploy.log"
AGENT_LOCK = AGENT_ROOT / "logs" / "agent.lock"
DISTURBANCE_ID = re.compile(r"[0-9A-Za-z_]{1,64}")
SLUG = re.compile(r"[a-z0-9_]{1,64}")


def default_clients(env):
    """(the agent's writing client, the read-only serving client) from the .env.serving values."""
    opener = urllib.request.urlopen
    agent_client = StatementClient(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], env["DATABRICKS_WAREHOUSE_ID"],
                                   opener=opener, sleep=time.sleep, monotonic=time.monotonic)
    serving_client = ServingStatementClient(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], env["DATABRICKS_WAREHOUSE_ID"],
                                            opener=opener, sleep=time.sleep, monotonic=time.monotonic)
    return agent_client, serving_client


def default_job1_check(env):
    return lambda: job1_active(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], opener=urllib.request.urlopen)


def validate(request):
    """(clean request, None) or (None, "invalid_request")."""
    if not isinstance(request, dict):
        return None, "invalid_request"
    did, jedi, ship, rationale = (request.get(k) for k in ("disturbance_id", "jedi_ids", "starship_id", "rationale"))
    if not (isinstance(did, str) and DISTURBANCE_ID.fullmatch(did)):
        return None, "invalid_request"
    if not (isinstance(jedi, list) and 1 <= len(jedi) <= MAX_PARTY and len(set(jedi)) == len(jedi)
            and all(isinstance(j, str) and SLUG.fullmatch(j) for j in jedi)):
        return None, "invalid_request"
    if not (isinstance(ship, str) and SLUG.fullmatch(ship)):
        return None, "invalid_request"
    if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= MAX_RATIONALE:
        return None, "invalid_request"
    return {"disturbance_id": did, "jedi_ids": list(jedi), "starship_id": ship, "rationale": rationale.strip()}, None


def take_agent_lock(lock_path, sleep):
    """The agent's lock, tried LOCK_TRIES times one second apart; None if it stays held."""
    lock = InstanceLock(lock_path)
    for attempt in range(LOCK_TRIES):
        try:
            return lock.acquire()
        except AlreadyRunning:
            if attempt < LOCK_TRIES - 1:
                sleep(LOCK_WAIT_SECONDS)
    return None


def write_log(log, now, disturbance_id, result, rationale_chars):
    line = (f"{now.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S}Z disturbance_id={disturbance_id or '-'} ok={str(result['ok']).lower()} "
            f"reasons={','.join(result['reasons']) or '-'} statements={result['statements']} rationale_chars={rationale_chars}")
    log(line)


def default_log(line):
    DEPLOY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DEPLOY_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def deploy(request, *, env, now, client, jobs, pg_conn, lock_path=AGENT_LOCK, sleep=time.sleep, log=default_log, serving_client=None,
           ulid_fn=ulid_now):
    """Deploy the user's party to an open incident through the agent's code path. `client` is the agent's StatementClient, `jobs` a callable
    returning (active, reason) like agent.jobs.job1_active, `serving_client` the read-only client used for the refresh."""
    if serving_client is None:
        serving_client = ServingStatementClient(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], env["DATABRICKS_WAREHOUSE_ID"],
                                                opener=urllib.request.urlopen, sleep=time.sleep, monotonic=time.monotonic)
    before = (client.statements, serving_client.statements)
    result = {"ok": False, "reasons": [], "deployment_id": None, "statements": 0}
    clean, bad = validate(request)
    chars = len(request.get("rationale", "")) if isinstance(request, dict) and isinstance(request.get("rationale"), str) else 0
    lock = None
    try:
        if bad:
            result["reasons"] = [bad]
            return result
        if now.astimezone(timezone.utc).hour == QUOTA_WINDOW_HOUR_UTC:
            result["reasons"] = ["quota_hour"]
            return result
        status = pg_conn.execute("SELECT status FROM serving.v_incidents WHERE disturbance_id = %s", (clean["disturbance_id"],)).fetchone()
        pg_conn.rollback()
        if status is None:
            result["reasons"] = ["unknown_incident"]
            return result
        if status[0] != "open":
            result["reasons"] = ["not_open"]
            return result
        lock = take_agent_lock(lock_path, sleep)
        if lock is None:
            result["reasons"] = ["agent_cycle_in_progress"]
            return result
        active, reason = jobs()
        if active:
            result["reasons"] = [reason]
            return result

        context_rows = client.read(*queries.context(TARGET, clean["disturbance_id"]))
        jedi_rows = client.read(*queries.available_jedi(TARGET))
        ship_rows = client.read(*queries.available_starships(TARGET))
        if not context_rows:
            result["reasons"] = ["unknown_incident"]
            return result
        context = build_context(context_rows[0], jedi_rows, ship_rows)
        incident, jedi_by_id, ships_by_id, state = constraint_inputs(context)
        forced = forced_stand_down(incident)
        if forced is not None:
            result["reasons"] = [forced]
            return result
        reasons = check_deploy(incident, clean["jedi_ids"], clean["starship_id"], jedi_by_id, ships_by_id, state)
        if reasons:
            result["reasons"] = list(reasons)
            return result

        active, reason = jobs()                                   # the agent's re-check before its write
        if active:
            result["reasons"] = [reason]
            return result

        eta = party_eta_hours([jedi_by_id[j].homeworld_region for j in clean["jedi_ids"]], incident.region,
                              ships_by_id[clean["starship_id"]].hyperdrive_rating)
        decision = Decision("deploy", jedi_ids=clean["jedi_ids"], starship_id=clean["starship_id"], eta_hours=eta,
                            rationale=clean["rationale"], guardrail_overrides=[], model=None, decision_latency_ms=None)
        row = build_row(context, decision, now, ulid_fn())
        row["decided_by"] = "user"
        merged = client.write(*queries.write_decision(TARGET, row))
        inserted = merged[0].get("num_inserted_rows") if merged and isinstance(merged[0], dict) else None
        if inserted is not None and int(inserted) == 0:
            result["reasons"] = ["already_decided"]
            return result
        result["ok"], result["deployment_id"] = True, row["deployment_id"]

        warnings = []
        if inserted is None:                                       # the MERGE result has no num_inserted_rows: not verified live yet
            warnings.append("merge_result_unverified")
        try:
            client.write(*queries.mark(TARGET, clean["disturbance_id"]))
        except Exception:
            warnings.append("mark_failed")                         # the agent's next repair marks it
        try:
            pull.refresh_source(serving_client, pg_conn, "deployment")
            pg_conn.execute("UPDATE serving.disturbance SET agent_processed = true WHERE disturbance_id = %s", (clean["disturbance_id"],))
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()
            warnings.append("refresh_failed")                      # the next ForceServe pull replaces the table
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        result.update(ok=False, reasons=["internal_error"], deployment_id=None)
        result["detail"] = type(e).__name__                        # the class only: never a message, which might carry a host
        return result
    finally:
        if lock is not None:
            lock.release()
        result["statements"] = (client.statements - before[0]) + (serving_client.statements - before[1])
        write_log(log, now, clean["disturbance_id"] if clean else None, result, chars)
