#!/usr/bin/env python3
"""Live fixture runner for the web intake model (doc 04, "This model needs its own fixture suite"). Manual: it calls the real
model, so it is never part of a default suite (`make test-intake` runs only the offline tests in intake/tests/).

For each fixture in intake/fixtures/ it submits the same body the browser would, through intake_core.assess_report, N times, and
checks the outcome against the fixture's `expect` (see intake/fixtures/README.md): the relevance score, the per-channel distance
from the planet's baseline in sigmas, the clamp range and that the stored description is the submitted one. It does NOT compute
the dbt imbalance score; whether a report crosses 5.75 in gold is proven at the Phase 5 checkpoint.

Outcomes are kept apart: scored, rejected (failed validation, no model call), blocked (the model refused the text) and error
(the inference failed). Each run appends a record to intake/eval_results.md. The key is read by intake_core.intake_settings and
handed to the transport; it is never printed, logged or written.

    edge\\.venv\\Scripts\\python.exe intake\\run_fixtures.py --live [--only NAME ...] [--iterations N] [--thinking-level LEVEL]
    edge\\.venv\\Scripts\\python.exe intake\\run_fixtures.py --list

Exit code: 0 every iteration passed; 1 something failed, was blocked or errored; 2 usage or configuration error (no call made).
"""
import argparse
import json
import math
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import intake_core as core  # noqa: E402  (also puts ingest/bridge on the path)
import files_api  # noqa: E402

FIXTURES_DIR = HERE / "fixtures"
RESULTS_DEFAULT = HERE / "eval_results.md"
DEFAULT_ITERATIONS, MAX_ITERATIONS = 3, 20
DEFAULT_PAUSE_S = 1.0
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
SHORT = {"midichlorian_ppm": "midi", "kyber_resonance": "kyber", "dark_side_activity": "dark"}
FIXTURE_KEYS = {"name", "source", "sector_id", "description", "description_repeat", "must_cross_gold", "expect"}
EXPECT_KEYS = {"outcome", "code", "inference_calls", "relevance", "min_sigma", "max_abs_sigma"}
RESULTS_HEADER = ("# Intake fixture results\n\nOne record per run of `intake/run_fixtures.py`, newest last. Outcomes are scored, "
                  "rejected (no model call), blocked (the model refused) and error (the inference failed).\n")


class FixtureError(ValueError):
    """A fixture file is malformed. The message names the file."""


# ---- loading ------------------------------------------------------------------------------------------------
def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_fixture(fx, name, sectors):
    def bad(why):
        raise FixtureError(f"{name}.json: {why}")

    if not isinstance(fx, dict):
        bad("not a JSON object")
    extra = set(fx) - FIXTURE_KEYS
    if extra:
        bad(f"unknown key(s) {sorted(extra)}")
    if fx.get("name") != name:
        bad("name must equal the file name without .json")
    if not isinstance(fx.get("source"), str) or not fx["source"]:
        bad("source must be a non-empty string")
    if fx.get("sector_id") not in sectors:
        bad("sector_id is not a known planet")
    if ("description" in fx) == ("description_repeat" in fx):
        bad("give exactly one of description and description_repeat")
    if "description" in fx and not isinstance(fx["description"], str):
        bad("description must be a string")
    if "description_repeat" in fx:
        rep = fx["description_repeat"]
        if (not isinstance(rep, dict) or set(rep) != {"unit", "chars"} or not isinstance(rep["unit"], str) or not rep["unit"]
                or isinstance(rep["chars"], bool) or not isinstance(rep["chars"], int) or not 1 <= rep["chars"] <= core.MAX_DESCRIPTION_CHARS):
            bad("description_repeat must be {unit: non-empty text, chars: 1..%d}" % core.MAX_DESCRIPTION_CHARS)
    if "must_cross_gold" in fx and not isinstance(fx["must_cross_gold"], bool):
        bad("must_cross_gold must be true or false")
    expect = fx.get("expect")
    if not isinstance(expect, dict):
        bad("expect must be an object")
    if set(expect) - EXPECT_KEYS:
        bad(f"unknown expect key(s) {sorted(set(expect) - EXPECT_KEYS)}")
    if expect.get("outcome") not in ("scored", "rejected"):
        bad("expect.outcome must be scored or rejected")
    if expect["outcome"] == "rejected" and not isinstance(expect.get("code"), str):
        bad("a rejected fixture needs expect.code")
    if expect["outcome"] == "scored" and "code" in expect:
        bad("expect.code belongs to a rejected fixture")
    if "inference_calls" in expect and (isinstance(expect["inference_calls"], bool) or not isinstance(expect["inference_calls"], int)
                                        or expect["inference_calls"] < 0):
        bad("expect.inference_calls must be a non-negative integer")
    if "relevance" in expect:
        rel = expect["relevance"]
        if not isinstance(rel, dict) or not rel or set(rel) - {"gt", "lt"} or not all(_number(v) and 0 <= v <= 1 for v in rel.values()):
            bad("expect.relevance must be {gt: x} and/or {lt: y} with values in 0..1")
        if "gt" in rel and "lt" in rel and rel["gt"] >= rel["lt"]:
            bad("expect.relevance: gt must be below lt")
    if "min_sigma" in expect:
        mins = expect["min_sigma"]
        if not isinstance(mins, dict) or not mins or set(mins) - set(core.CHANNELS) or not all(_number(v) for v in mins.values()):
            bad("expect.min_sigma must map channel names to numbers")
    if "max_abs_sigma" in expect and not (_number(expect["max_abs_sigma"]) and expect["max_abs_sigma"] > 0):
        bad("expect.max_abs_sigma must be a positive number")
    if expect["outcome"] == "rejected" and set(expect) & {"relevance", "min_sigma", "max_abs_sigma"}:
        bad("a rejected fixture has no scores to check")


def load_fixtures(directory, sectors):
    """The fixtures in `directory`, in file-name order. Raises FixtureError for a malformed file."""
    fixtures = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            fx = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise FixtureError(f"{path.name}: not valid JSON ({type(e).__name__})") from None
        validate_fixture(fx, path.stem, sectors)
        fixtures.append(fx)
    if not fixtures:
        raise FixtureError(f"{directory}: no fixtures")
    return fixtures


def description_of(fx):
    if "description" in fx:
        return fx["description"]
    unit, chars = fx["description_repeat"]["unit"], fx["description_repeat"]["chars"]
    return (unit * (chars // len(unit) + 1))[:chars]


# ---- one submission -----------------------------------------------------------------------------------------
def sigma_multiples(sector, values):
    """Signed distance of each channel from the planet's baseline, in the planet's own sigmas."""
    spec = {"midichlorian_ppm": (sector.midi_baseline, sector.midi_sigma),
            "kyber_resonance": (sector.kyber_baseline, sector.kyber_sigma),
            "dark_side_activity": (sector.dark_baseline, sector.dark_sigma)}
    return {name: (values[name] - mean) / sigma for name, (mean, sigma) in spec.items()}


def run_once(fx, sectors, *, api_key, model, thinking_level, http, now, clock=time.monotonic, sleep=time.sleep):
    """Submit the fixture once. Returns an outcome dict: kind (scored/rejected/blocked/error), code, calls (model calls made),
    and for scored: values, sigma, clamped, bounded, verbatim."""
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return http(*args, **kwargs)

    description = description_of(fx)
    stamp = now()
    body = json.dumps({"sector_id": fx["sector_id"], "description": description, "reporter_id": "fxtr",
                       "report_id": core.new_ulid(int(stamp.timestamp() * 1000), secrets.randbits(80))}).encode("utf-8")
    outcome = {"kind": None, "code": None, "calls": 0}
    try:
        envelope, inferred = core.assess_report(body, sectors=sectors, api_key=api_key, model=model, now=stamp,
                                                thinking_level=thinking_level, http=counted, clock=clock, sleep=sleep)
    except core.RequestError as e:
        outcome.update(kind="blocked" if e.code == "blocked_by_model" else "rejected", code=e.code)
    except core.InferenceError as e:
        outcome.update(kind="error", code=e.kind)
    else:
        payload = envelope["payload"]
        values = {name: payload[name] for name in core.RANGES}
        outcome.update(
            kind="scored", values=values, sigma=sigma_multiples(sectors[fx["sector_id"]], values),
            clamped=list(inferred.clamped),
            bounded=all(_number(v) and core.RANGES[n][0] <= v <= core.RANGES[n][1] for n, v in values.items()),
            verbatim=payload["description"] == description)
    outcome["calls"] = len(calls)
    return outcome


def check(fx, outcome):
    """The failures of one outcome against the fixture's expectations; [] means it passed."""
    expect, kind = fx["expect"], outcome["kind"]
    if kind != expect["outcome"]:
        detail = f" ({outcome['code']})" if outcome.get("code") else ""
        return [f"expected {expect['outcome']}, got {kind}{detail}"]
    failures = []
    if kind == "rejected" and outcome["code"] != expect["code"]:
        failures.append(f"rejected with {outcome['code']}, expected {expect['code']}")
    if "inference_calls" in expect and outcome["calls"] != expect["inference_calls"]:
        failures.append(f"{outcome['calls']} model call(s), expected {expect['inference_calls']}")
    if kind == "scored":
        if not outcome["bounded"]:
            failures.append("a value is outside its valid range")
        if not outcome["verbatim"]:
            failures.append("the stored description differs from the submitted one")
        relevance = outcome["values"]["relevance_score"]
        limits = expect.get("relevance", {})
        if "gt" in limits and not relevance > limits["gt"]:
            failures.append(f"relevance {relevance:.3f} is not above {limits['gt']}")
        if "lt" in limits and not relevance < limits["lt"]:
            failures.append(f"relevance {relevance:.3f} is not below {limits['lt']}")
        for channel, minimum in expect.get("min_sigma", {}).items():
            if not outcome["sigma"][channel] >= minimum:
                failures.append(f"{SHORT[channel]} is {outcome['sigma'][channel]:+.1f} sigma, expected at least {minimum:+.1f}")
        if "max_abs_sigma" in expect:
            for channel, distance in outcome["sigma"].items():
                if not abs(distance) <= expect["max_abs_sigma"]:
                    failures.append(f"{SHORT[channel]} is {distance:+.1f} sigma, expected within {expect['max_abs_sigma']}")
    return failures


# ---- reporting ----------------------------------------------------------------------------------------------
def iteration_line(fx, number, total, outcome, failures):
    head = f"{fx['name']:<34} {number}/{total}  {outcome['kind']:<8}"
    if outcome["kind"] == "scored":
        s = outcome["sigma"]
        text = (f"relevance {outcome['values']['relevance_score']:.2f}  sigma midi {s['midichlorian_ppm']:+.1f} "
                f"kyber {s['kyber_resonance']:+.1f} dark {s['dark_side_activity']:+.1f}")
        if outcome["clamped"]:
            text += "  clamped " + ",".join(SHORT[c] for c in outcome["clamped"])
    else:
        text = f"({outcome['code']})  model calls {outcome['calls']}"
    verdict = "PASS" if not failures else "FAIL  " + "; ".join(failures)
    return f"{head}  {text}  {verdict}"


def summarise(fixtures, results):
    """results: {fixture name: [(outcome, failures), ...]} -> one summary dict per fixture, in fixture order."""
    rows = []
    for fx in fixtures:
        runs = results.get(fx["name"], [])
        if not runs:
            continue
        scored = [o for o, _ in runs if o["kind"] == "scored"]
        relevance = [o["values"]["relevance_score"] for o in scored]
        dark = [o["sigma"]["dark_side_activity"] for o in scored]
        rows.append({
            "name": fx["name"], "total": len(runs), "passed": sum(1 for _, f in runs if not f),
            "kinds": {k: sum(1 for o, _ in runs if o["kind"] == k) for k in ("scored", "blocked", "rejected", "error")},
            "relevance": (min(relevance), max(relevance)) if relevance else None,
            "dark": (min(dark), max(dark)) if dark else None,
            "clamped": sum(1 for o in scored if o["clamped"]),
            "failures": sorted({text for _, f in runs for text in f})})
    return rows


def render_record(stamp, model, thinking_level, iterations, calls, rows):
    def span(pair, fmt):
        return "-" if pair is None else (fmt % pair[0] if pair[0] == pair[1] else (fmt + " to " + fmt) % pair)

    lines = [f"\n## {stamp.strftime('%Y-%m-%dT%H:%M:%SZ')} | {model} | thinking {thinking_level} | "
             f"{iterations} iteration(s) | {calls} model call(s)\n",
             "| Fixture | Passed | Scored | Blocked | Rejected | Error | Relevance | Dark sigma | Clamped |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        k = r["kinds"]
        lines.append(f"| {r['name']} | {r['passed']}/{r['total']} | {k['scored']} | {k['blocked']} | {k['rejected']} | {k['error']} | "
                     f"{span(r['relevance'], '%.2f')} | {span(r['dark'], '%+.1f')} | {r['clamped']} |")
    failing = [r for r in rows if r["failures"]]
    lines.append("")
    lines.append("Result: ALL PASS" if not failing else f"Result: {len(failing)} fixture(s) with failures")
    for r in failing:
        lines.append(f"- {r['name']}: " + "; ".join(r["failures"]))
    return "\n".join(lines) + "\n"


def append_results(path, text):
    """Append a record, creating the file with its header on first use. Binary mode: LF on every platform."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("ab") as f:
        if new:
            f.write(RESULTS_HEADER.encode("utf-8"))
        f.write(text.encode("utf-8"))


# ---- command line -------------------------------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--live", action="store_true", help="really call the model (without it nothing is called)")
    p.add_argument("--list", action="store_true", help="list the fixtures and exit")
    p.add_argument("--only", action="append", metavar="NAME", help="run only this fixture (repeatable)")
    p.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS, help="runs per fixture (default %(default)s)")
    p.add_argument("--thinking-level", default=core.DEFAULT_THINKING_LEVEL, choices=core.THINKING_LEVELS)
    p.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S, help="seconds between model calls (default %(default)s)")
    p.add_argument("--results", type=Path, default=RESULTS_DEFAULT, help="where the record is appended (default %(default)s)")
    p.add_argument("--env-file", type=Path, default=core.DEFAULT_ENV_FILE)
    return p.parse_args(argv)


def main(argv=None, *, http=files_api.http_request, environ=None, now=None, sleep=time.sleep, out=None,
         fixtures_dir=FIXTURES_DIR):
    out = out if out is not None else sys.stdout
    environ = os.environ if environ is None else environ
    now = now or (lambda: datetime.now(timezone.utc))

    def say(text=""):
        print(text, file=out, flush=True)

    try:
        args = parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    if not 1 <= args.iterations <= MAX_ITERATIONS or args.pause < 0:
        say(f"--iterations must be 1..{MAX_ITERATIONS} and --pause not negative")
        return EXIT_USAGE
    try:
        sectors = core.load_sectors()
        fixtures = load_fixtures(fixtures_dir, sectors)
    except (core.ConfigError, FixtureError) as e:
        say(str(e))
        return EXIT_USAGE
    if args.only:
        unknown = sorted(set(args.only) - {fx["name"] for fx in fixtures})
        if unknown:
            say("unknown fixture(s): " + ", ".join(unknown))
            return EXIT_USAGE
        fixtures = [fx for fx in fixtures if fx["name"] in args.only]
    if args.list:
        for fx in fixtures:
            say(f"{fx['name']:<34} {fx['sector_id']:<10} expects {fx['expect']['outcome']}")
        return EXIT_OK
    planned = args.iterations * sum(1 for fx in fixtures if fx["expect"]["outcome"] != "rejected")
    if not args.live:
        say(f"Nothing called. This run would make up to {planned} model call(s) ({len(fixtures)} fixture(s) x {args.iterations}); "
            "add --live to do it.")
        return EXIT_USAGE
    try:
        api_key, model = core.intake_settings(environ, args.env_file)
    except core.ConfigError as e:
        say(str(e))
        return EXIT_USAGE

    started = now()
    say(f"model {model}, thinking {args.thinking_level}, {len(fixtures)} fixture(s) x {args.iterations}, up to {planned} model call(s)")
    results, made = {}, 0
    for number in range(1, args.iterations + 1):
        for fx in fixtures:
            outcome = run_once(fx, sectors, api_key=api_key, model=model, thinking_level=args.thinking_level, http=http, now=now,
                               sleep=sleep)
            failures = check(fx, outcome)
            results.setdefault(fx["name"], []).append((outcome, failures))
            say(iteration_line(fx, number, args.iterations, outcome, failures))
            made += outcome["calls"]
            if outcome["calls"] and args.pause:
                sleep(args.pause)
    rows = summarise(fixtures, results)
    append_results(args.results, render_record(started, model, args.thinking_level, args.iterations, made, rows))
    failing = [r["name"] for r in rows if r["failures"]]
    say("")
    say(f"{made} model call(s). " + ("ALL PASS" if not failing else f"{len(failing)} fixture(s) with failures: " + ", ".join(failing)))
    say(f"record appended to {args.results}")
    return EXIT_FAILED if failing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
