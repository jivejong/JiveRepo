"""Evaluation runner: every fixture, several runs each, against the real model (doc 06 "Evaluation fixtures").

    edge\\.venv\\Scripts\\python.exe -m agent.eval --runs 3 [--only NAME ...] [--env PATH] [--budget 300]

For each fixture and run it calls loop.decide() with one shared CallBudget (one unit per HTTP attempt) and checks the decision for
compliance (a deploy must pass the constraint layer and use only available Jedi and ships) and for the fixture's expectation. A
fixture PASSES only if all its runs pass, and is FLAKY if its runs disagree on the decision class. Per-run records go to
logs/agent_eval/<UTC timestamp>.ndjson (gitignored); a summary section is appended to agent/eval_results.md. The key and the model
name are never printed.
"""
import argparse
import dataclasses
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from agent.budget import BudgetExhausted, CallBudget
from agent.config import ROOT, load_env
from agent.constraints import RANK_ORDER, check_deploy
from agent.context import constraint_inputs
from agent.fixtures.spec import FIXTURES
from agent.gemini import GeminiClient, GeminiUnavailable
from agent.loop import decide

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
RESULTS_FILE = Path(__file__).resolve().parent / "eval_results.md"
LOG_DIR = ROOT / "logs" / "agent_eval"
EVAL_ENV_KEYS = ("GEMINI_API_KEY", "INFERENCE_MODEL")
DEFAULT_BUDGET = 300


def load_fixtures(directory=FIXTURE_DIR, only=None):
    """The fixtures in spec order; `only` is a list of names."""
    wanted = list(only) if only else None
    names = [spec["name"] for spec in FIXTURES]
    if wanted:
        unknown = [n for n in wanted if n not in names]
        if unknown:
            raise ValueError(f"unknown fixture(s): {', '.join(unknown)}")
        names = [n for n in names if n in wanted]
    return [json.loads(Path(directory, n + ".json").read_text(encoding="utf-8")) for n in names]


# ---- checking a decision --------------------------------------------------------------------------------------------
def check_run(fixture, decision):
    """The failures of one decision against its fixture: compliance first, then the expectation. Empty means it passed."""
    failures = []
    context, expect = fixture["context"], fixture["expect"]
    incident, jedi_by_id, ships_by_id, state = constraint_inputs(context)

    if decision.decision == "deploy":
        reasons = check_deploy(incident, decision.jedi_ids, decision.starship_id, jedi_by_id, ships_by_id, state)
        if reasons:
            failures.append("compliance: the deploy breaks " + ", ".join(reasons))
        available_jedi = {j["jedi_id"] for j in context["available_jedi"]}
        available_ships = {s["starship_id"] for s in context["available_starships"]}
        if not set(decision.jedi_ids) <= available_jedi:
            failures.append("compliance: a deployed Jedi is not in the available list")
        if decision.starship_id not in available_ships:
            failures.append("compliance: the starship is not in the available list")

    wanted = expect.get("decision", "any")
    if wanted != "any" and decision.decision != wanted:
        failures.append(f"expected {wanted}, got {decision.decision}")

    party = [jedi_by_id[j] for j in decision.jedi_ids if j in jedi_by_id] if decision.decision == "deploy" else []
    if "min_rank" in expect and decision.decision == "deploy":
        if not any(RANK_ORDER.get(j.rank, -1) >= RANK_ORDER[expect["min_rank"]] for j in party):
            failures.append(f"no deployed Jedi has rank {expect['min_rank']} or above")
    if "specialty" in expect and decision.decision == "deploy":
        if not any(expect["specialty"] in (j.primary_specialty, j.secondary_specialty) for j in party):
            failures.append(f"no deployed Jedi has specialty {expect['specialty']}")
    if "rationale_mentions_any" in expect:
        text = decision.rationale.lower()
        if not any(word.lower() in text for word in expect["rationale_mentions_any"]):
            failures.append("the rationale mentions none of " + ", ".join(expect["rationale_mentions_any"]))
    if "max_jedi" in expect and len(decision.jedi_ids) > expect["max_jedi"]:
        failures.append(f"{len(decision.jedi_ids)} Jedi deployed, at most {expect['max_jedi']} allowed")
    if "forced" in expect:
        reasons = [r for o in decision.guardrail_overrides for r in o.get("reasons", [])]
        if expect["forced"] not in reasons:
            failures.append(f"the forced reason {expect['forced']} is not in the overrides")
    if "model_calls" in expect and decision.model_calls != expect["model_calls"]:
        failures.append(f"{decision.model_calls} model calls, expected {expect['model_calls']}")
    return failures


def fixture_status(runs):
    """PASS, FAIL, FLAKY (the runs disagree on the decision class), or not run."""
    if not runs:
        return "not run"
    if len({r["decision"] for r in runs if r.get("decision")}) > 1:
        return "FLAKY"
    return "PASS" if all(not r["failures"] and not r.get("error") for r in runs) else "FAIL"


# ---- running the suite ----------------------------------------------------------------------------------------------
def run_record(fixture, run_number, decision, failures, error=None):
    record = {"fixture": fixture["name"], "run": run_number, "failures": failures, "error": error}
    if decision is not None:
        record.update(dataclasses.asdict(decision))
    return record


def run_suite(fixtures, runs, decide_fn):
    """Run every fixture `runs` times. Returns (results, exhausted): results is [{fixture, runs}] in order, and exhausted is True
    if the call budget ran out (the remaining runs are not made)."""
    results = []
    for index, fixture in enumerate(fixtures):
        entry = {"fixture": fixture, "runs": []}
        results.append(entry)
        for number in range(1, runs + 1):
            try:
                decision = decide_fn(fixture["context"])
            except BudgetExhausted:
                results.extend({"fixture": f, "runs": []} for f in fixtures[index + 1:])
                return results, True
            except GeminiUnavailable as e:
                entry["runs"].append(run_record(fixture, number, None, [], error="GeminiUnavailable: " + str(e)))
                continue
            entry["runs"].append(run_record(fixture, number, decision, check_run(fixture, decision)))
    return results, False


# ---- reporting ------------------------------------------------------------------------------------------------------
def describe_expect(expect):
    parts = [expect.get("decision", "any")]
    for key in ("min_rank", "specialty", "max_jedi", "forced", "model_calls"):
        if key in expect:
            parts.append(f"{key} {expect[key]}")
    if "rationale_mentions_any" in expect:
        parts.append("mentions any of " + "/".join(expect["rationale_mentions_any"]))
    return ", ".join(parts)


def totals(results, budget):
    records = [r for e in results for r in e["runs"]]
    statuses = [fixture_status(e["runs"]) for e in results]
    return {
        "pass": statuses.count("PASS"), "flaky": statuses.count("FLAKY"), "fail": statuses.count("FAIL"),
        "not_run": statuses.count("not run"), "fixtures": len(results), "runs": len(records),
        "model_calls": sum(r.get("model_calls", 0) for r in records), "http_attempts": budget.used,
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) for r in records),
        "thought_tokens": sum(r.get("thought_tokens", 0) for r in records),
        "cost_usd": sum(r.get("cost_usd", 0.0) for r in records),
    }


def render_section(results, budget, meta):
    """The markdown section appended to eval_results.md."""
    t = totals(results, budget)
    lines = [f"## Run {meta['date']}", "",
             f"- Model: `{meta['model']}`, thinking level `{meta['thinking_level']}`, {meta['runs']} runs per fixture, "
             f"call budget {meta['budget']} HTTP attempts" + (" (exhausted: the remaining fixtures were not run)" if meta["exhausted"] else ""),
             "", "| Fixture | Expect | Run decisions | Result |", "|---|---|---|---|"]
    for e in results:
        decisions = "/".join(r.get("decision") or "error" for r in e["runs"]) or "-"
        lines.append(f"| {e['fixture']['name']} | {describe_expect(e['fixture']['expect'])} | {decisions} | {fixture_status(e['runs'])} |")
    lines += ["", f"- Passed: {t['pass']} of {t['fixtures']}; flaky: {t['flaky']}; failed: {t['fail']}; not run: {t['not_run']}",
              f"- Model calls: {t['model_calls']}; HTTP attempts: {t['http_attempts']}",
              f"- Tokens: {t['prompt_tokens']} prompt, {t['output_tokens']} output, {t['thought_tokens']} thought",
              f"- Cost (from usageMetadata): ${t['cost_usd']:.6f}", ""]
    return "\n".join(lines)


def append_results(path, section):
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else (
        "# Agent evaluation results\n\nOne section per run of `python -m agent.eval` (doc 06, \"Evaluation fixtures\"). "
        "Costs are measured from `usageMetadata`.\n\n")
    path.write_bytes((text.rstrip("\n") + "\n\n" + section).encode("utf-8"))


def write_ndjson(path, results):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, sort_keys=True, ensure_ascii=True) for e in results for r in e["runs"]]
    path.write_bytes(("\n".join(lines) + ("\n" if lines else "")).encode("ascii"))


# ---- command line ---------------------------------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent.eval")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--env", default=str(ROOT / ".env.agent"))
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    args = parser.parse_args(argv)

    env = load_env(args.env, required=EVAL_ENV_KEYS)
    fixtures = load_fixtures(only=args.only)
    budget = CallBudget(args.budget)
    client = GeminiClient(env["GEMINI_API_KEY"], env["INFERENCE_MODEL"], opener=urllib.request.urlopen, sleep=time.sleep,
                          monotonic=time.monotonic, budget=budget)
    results, exhausted = run_suite(fixtures, args.runs, lambda ctx: decide(ctx, client, budget, monotonic=time.monotonic))

    now = datetime.now(timezone.utc)
    meta = {"date": now.strftime("%Y-%m-%d %H:%M UTC"), "model": client.model, "thinking_level": client.thinking_level,
            "runs": args.runs, "budget": args.budget, "exhausted": exhausted}
    log_path = LOG_DIR / (now.strftime("%Y%m%dT%H%M%SZ") + ".ndjson")
    write_ndjson(log_path, results)
    section = render_section(results, budget, meta)
    append_results(RESULTS_FILE, section)
    print(section)
    print(f"per-run records: {log_path}")
    return 1 if exhausted else 0


if __name__ == "__main__":
    sys.exit(main())
