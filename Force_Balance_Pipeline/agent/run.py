"""One agent cycle (doc 06 "Runtime" cycle order, "Failure handling", "Monitoring and cost").

    edge\\.venv\\Scripts\\python.exe -m agent.run --target {dev,prod} [--limit N] [--env PATH] [--no-ping]

The ForceAgent scheduled task runs this hourly. A cycle: take logs/agent.lock (exit 0 if held); skip the 03:xx UTC hour and any cycle
in which Job 1 is running (both still ping S4: the agent is alive); repair, then poll; for each incident build the context from three
reads, let the model decide, write the decision with a MERGE and mark the incident processed. A failure on one incident is logged
and the cycle moves on; an unhandled error pings S4 with /fail and exits 1. The S4 ping URL is AGENT_PING_S4_URL in .env.agent and is
never printed or logged. See agent/loop.py for the decision loop and agent/queries.py for the SQL.
"""
import argparse
import json
import logging
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from agent import jobs, queries
from agent.budget import BudgetExhausted, CallBudget
from agent.config import AGENT_EPOCH, ENV_KEYS, MAX_INCIDENTS_PER_RUN, PING_KEY, ROOT, RUN_ENV_KEYS, load_env
from agent.context import build_context
from agent.dbsql import StatementClient, StatementError, iso_z
from agent.distance import released_at
from agent.gemini import GeminiClient, GeminiUnavailable
from agent.lock import AlreadyRunning, InstanceLock
from agent.loop import decide
from agent.ulid import ulid_now
sys.path.insert(0, str(ROOT / "ingest" / "bridge"))      # files_api: the one outbound HTTP path

import files_api  # noqa: E402

LOG_PATH = ROOT / "logs" / "agent.log"
LOCK_PATH = ROOT / "logs" / "agent.lock"
DECISIONS_PATH = ROOT / "logs" / "agent_decisions.ndjson"
QUOTA_WINDOW_HOUR_UTC = 3
CYCLE_SECONDS = 30 * 60
PING_TIMEOUT_SECONDS = 10
LOG_MAX_BYTES, LOG_BACKUPS = 1_000_000, 3


# ---- pings ----------------------------------------------------------------------------------------------------------
def make_pinger(url, *, opener, log):
    """ping(kind) for kind "success" or "fail". A failed ping is logged (without the URL) and never raises. url None disables."""
    def ping(kind):
        if not url:
            return
        target = url if kind == "success" else url.rstrip("/") + "/fail"
        try:
            status, _ = files_api.http_request("GET", target, timeout=PING_TIMEOUT_SECONDS, opener=opener)
            if not 200 <= status < 300:
                log(f"agent: S4 ping failed (HTTP {status})")
        except Exception as e:
            log(f"agent: S4 ping failed ({type(e).__name__})")
    return ping


def valid_ping_url(value):
    parsed = urlparse((value or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


# ---- one cycle ------------------------------------------------------------------------------------------------------
def build_row(context, decision, decided_at, deployment_id):
    """The dict queries.write_decision takes. released_at is set only for a deploy."""
    eta = decision.eta_hours
    return {
        "deployment_id": deployment_id, "disturbance_id": context["disturbance_id"], "decided_by": "yoda_agent",
        "decision": decision.decision, "jedi_ids": list(decision.jedi_ids), "starship_id": decision.starship_id,
        "eta_hours": eta, "released_at": released_at(decided_at, eta) if decision.decision == "deploy" else None,
        "rationale": decision.rationale, "context_snapshot": context, "signature_at_decision": context.get("signature"),
        "model": decision.model, "decision_latency_ms": decision.decision_latency_ms,
        "guardrail_overrides": decision.guardrail_overrides, "decided_at": decided_at,
    }


def decision_record(row, decision):
    """One line for logs/agent_decisions.ndjson: the row without the context, plus the usage."""
    record = {k: v for k, v in row.items() if k != "context_snapshot"}
    for key in ("released_at", "decided_at"):
        if record[key] is not None:
            record[key] = iso_z(record[key])
    record.update(model_calls=decision.model_calls, prompt_tokens=decision.prompt_tokens, output_tokens=decision.output_tokens,
                  thought_tokens=decision.thought_tokens, cost_usd=decision.cost_usd)
    return record


def append_ndjson(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write((json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))


def run_cycle(*, target, limit, db, gemini, budget, clock, monotonic, log, decisions_path, decide_fn=decide, ulid_fn=ulid_now):
    """Steps d to f of the cycle. Returns the summary dict (it is also logged)."""
    started = monotonic()
    statements_before = db.statements
    db.write(*queries.repair(target))
    polled = db.read(*queries.poll(target, AGENT_EPOCH, min(limit or MAX_INCIDENTS_PER_RUN, MAX_INCIDENTS_PER_RUN)))
    ids = [r["disturbance_id"] for r in polled]
    totals = {"deploy": 0, "stand_down": 0, "model_calls": 0, "prompt": 0, "output": 0, "thought": 0, "cost": 0.0}

    for disturbance_id in ids:
        if monotonic() - started >= CYCLE_SECONDS:
            log(f"agent: the cycle has run {CYCLE_SECONDS // 60} minutes; no new incident is started")
            break
        try:
            context_rows = db.read(*queries.context(target, disturbance_id))
            jedi_rows = db.read(*queries.available_jedi(target))
            ship_rows = db.read(*queries.available_starships(target))
            if not context_rows:
                log(f"agent: incident {disturbance_id} has no context row; skipped")
                continue
            context = build_context(context_rows[0], jedi_rows, ship_rows)
            decision = decide_fn(context, gemini, budget, monotonic=monotonic)
            decided_at = clock()
            row = build_row(context, decision, decided_at, ulid_fn())
            db.write(*queries.write_decision(target, row))
        except GeminiUnavailable as e:
            log(f"agent: incident {disturbance_id}: the model was unavailable ({e}); nothing written")
            continue
        except BudgetExhausted as e:
            log(f"agent: {e}; stopping the cycle")
            break
        except StatementError as e:
            log(f"agent: incident {disturbance_id}: a statement failed ({type(e).__name__}); not marked")
            continue
        totals[decision.decision] += 1
        totals["model_calls"] += decision.model_calls
        totals["prompt"] += decision.prompt_tokens
        totals["output"] += decision.output_tokens
        totals["thought"] += decision.thought_tokens
        totals["cost"] += decision.cost_usd
        append_ndjson(decisions_path, decision_record(row, decision))
        log(f"agent: incident {disturbance_id}: {decision.decision}" +
            (f" ({', '.join(decision.jedi_ids)} on {decision.starship_id})" if decision.decision == "deploy" else ""))
        try:
            db.write(*queries.mark(target, disturbance_id))
        except StatementError as e:
            log(f"agent: incident {disturbance_id}: mark failed ({type(e).__name__}); the next cycle's repair will fix it")

    decided = totals["deploy"] + totals["stand_down"]
    summary = {
        "target": target, "polled": len(ids), "decided": decided, "deploy": totals["deploy"], "stand_down": totals["stand_down"],
        "left": len(ids) - decided, "statements": db.statements - statements_before, "model_calls": totals["model_calls"],
        "http_attempts": budget.used, "prompt_tokens": totals["prompt"], "output_tokens": totals["output"],
        "thought_tokens": totals["thought"], "cost_usd": totals["cost"], "duration_s": round(monotonic() - started, 1),
    }
    log("agent: cycle target={target} polled={polled} decided={decided} (deploy={deploy} stand_down={stand_down}) left={left} "
        "statements={statements} model_calls={model_calls} http_attempts={http_attempts} "
        "tokens={prompt_tokens}/{output_tokens}/{thought_tokens} cost_usd={cost_usd:.6f} duration_s={duration_s}".format(**summary))
    return summary


def run_guarded(*, target, limit, lock_path, db, gemini, budget, job1_check, ping, clock, monotonic, log, decisions_path,
                decide_fn=decide, ulid_fn=ulid_now):
    """Steps a to c and g: the lock, the quota window, the Job 1 check, and the S4 ping. Returns the exit code."""
    lock = InstanceLock(lock_path)
    try:
        lock.acquire()
    except AlreadyRunning:
        log("agent: already running; exiting")
        return 0
    try:
        if clock().hour == QUOTA_WINDOW_HOUR_UTC:
            log("agent: quota window (03:xx UTC); skipping the cycle")
            ping("success")
            return 0
        active, reason = job1_check()
        if active:
            log(f"agent: skipping the cycle: {reason}")
            ping("success")
            return 0
        run_cycle(target=target, limit=limit, db=db, gemini=gemini, budget=budget, clock=clock, monotonic=monotonic, log=log,
                  decisions_path=decisions_path, decide_fn=decide_fn, ulid_fn=ulid_fn)
        ping("success")
        return 0
    except Exception:
        log("agent: the cycle failed:\n" + traceback.format_exc())
        ping("fail")
        return 1
    finally:
        lock.release()


# ---- command line ---------------------------------------------------------------------------------------------------
def configure_logging(path=LOG_PATH):
    logger = logging.getLogger("agent")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)sZ %(message)s", "%Y-%m-%dT%H:%M:%S"))
        handler.formatter.converter = time.gmtime
        logger.addHandler(handler)
    return logger


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent.run")
    parser.add_argument("--target", required=True, choices=("dev", "prod"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--env", default=str(ROOT / ".env.agent"))
    parser.add_argument("--no-ping", action="store_true")
    args = parser.parse_args(argv)

    logger = configure_logging()
    log = logger.info
    try:
        env = load_env(args.env, required=ENV_KEYS if args.no_ping else RUN_ENV_KEYS)
        url = None if args.no_ping else env[PING_KEY]
        if url is not None and not valid_ping_url(url):
            raise ValueError(f"{PING_KEY} is not an https URL")
    except Exception as e:
        log(f"agent: configuration problem: {e}")
        return 2

    opener = urllib.request.urlopen
    budget = CallBudget(20)
    db = StatementClient(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], env["DATABRICKS_WAREHOUSE_ID"], opener=opener,
                         sleep=time.sleep, monotonic=time.monotonic)
    gemini = GeminiClient(env["GEMINI_API_KEY"], env["INFERENCE_MODEL"], opener=opener, sleep=time.sleep, monotonic=time.monotonic,
                          budget=budget)
    return run_guarded(
        target=args.target, limit=args.limit, lock_path=LOCK_PATH, db=db, gemini=gemini, budget=budget,
        job1_check=lambda: jobs.job1_active(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], opener=opener),
        ping=make_pinger(url, opener=opener, log=log), clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
        log=log, decisions_path=DECISIONS_PATH)


if __name__ == "__main__":
    sys.exit(main())
