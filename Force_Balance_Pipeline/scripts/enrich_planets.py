#!/usr/bin/env python3
"""Planet enrichment (doc 08): SWAPI planets -> warehouse/dbt/seeds/dim_sector.csv.

Build-time only. The reviewed output is committed and frozen; reruns do not reproduce it, and
regeneration is a migration event (doc 08), so an existing seed is only overwritten with --force.

Behavior:
  * The 59 planets other than SWAPI planet 28 go to the model in ONE call by default, with a
    bounded JSON schema for every numeric field. --batch-size N splits the call if a single
    response is truncated (finishReason MAX_TOKENS).
  * Each sigma is enforced as a fraction of that row's own baseline (midi 2-5%, kyber 8-15%, dark
    5-10%). Out-of-band values are clamped in code and every adjustment is recorded in the
    provenance sidecar; --strict-sigma fails instead.
  * A review gate is computed offline on the 59 rows and printed, and recorded in the sidecar: at
    most 10% of rows clamped on any channel, and the check-1 spread thresholds (doc 08, analyses).
    A FAIL means regenerate, do not accept: a real run refuses to write on FAIL, and
    --accept-failing-gate overrides that and is recorded in the sidecar. --trial only reports.
  * SWAPI planet 28 ("unknown") is never sent to the model. Its row is written with sector_id
    "uncharted", is_unknown = true, and the median of each numeric column of the other 59 rows.
  * population and diameter_km are blank in the CSV when SWAPI's value is not a number, and a
    diameter_km of 0 is blank too (SWAPI's 0 diameter means unknown).
  * Prompt instruction text never names a review anchor (doc 08 rule 5); the script refuses to run
    if it does. The planet data entries the model must describe are inherent to the request.
  * The thinking level is a required flag and is recorded, with the model ID, prompt version and
    hash, in a provenance sidecar next to the CSV.

Usage:
    python scripts/enrich_planets.py --print-prompts          # no API call
    python scripts/enrich_planets.py --thinking-level low --trial   # one call, writes nothing
    python scripts/enrich_planets.py --thinking-level low
    python scripts/enrich_planets.py --recompute-unknown      # after hand edits during review
"""
import argparse
import json
import math
import re
import statistics
import sys

import enrich_common as ec
import gemini_client

SEED = "dim_sector"
PROMPT_VERSION = "planets-v1"

FIELDS = ["sector_id", "sector_name", "climate", "terrain", "population", "diameter_km",
          "system_name", "region", "midi_baseline", "midi_sigma", "kyber_baseline", "kyber_sigma",
          "dark_baseline", "dark_sigma", "dark_spike_probability", "description", "force_history",
          "canon_confidence", "is_unknown"]

NUMERIC = ["midi_baseline", "midi_sigma", "kyber_baseline", "kyber_sigma", "dark_baseline",
           "dark_sigma", "dark_spike_probability"]
MEDIAN_COLUMNS = NUMERIC  # canon_confidence is fixed at 0.0 for the unknown row

# Hard bounds, in the schema and re-checked in code (doc 08 output schema). Baselines stay at or
# above 1 because the review checklist forbids zeros. Sigma bounds are the prompt's percentage
# bands taken to their extremes: midi 2-5% of 1000..25000, kyber 8-15% of 1..100, dark 5-10% of
# 1..100. The schema cannot express "a percentage of this row's baseline"; enforce_sigma() does.
BOUNDS = {
    "midi_baseline": (1000.0, 25000.0),
    "midi_sigma": (20.0, 1250.0),
    "kyber_baseline": (1.0, 100.0),
    "kyber_sigma": (0.08, 15.0),
    "dark_baseline": (1.0, 100.0),
    "dark_sigma": (0.05, 10.0),
    "dark_spike_probability": (0.0, 0.05),
    "canon_confidence": (0.0, 1.0),
}
# sigma / baseline bands from the prompt, enforced per row.
RATIO_BANDS = {"midi": (0.02, 0.05), "kyber": (0.08, 0.15), "dark": (0.05, 0.10)}
SIGMA_TOL = 1e-4      # slack on the ratio before a sigma counts as out of band
FINAL_TOL = 2e-4      # slack for the post-rounding self-check
RANGES = {"midi": (1000.0, 25000.0), "kyber": (0.0, 100.0), "dark": (0.0, 100.0)}
NUMERIC_TEXT = re.compile(r"-?\d+(\.\d+)?")

# Review gate. SPREAD mirrors warehouse/dbt/analyses/phase1_check_1_spread.sql; keep them in sync.
# MAX_CLAMPED_SHARE is the doc 08 checklist rule: more than 10% of rows clamped on any channel is a
# prompt problem, so regenerate rather than accept.
SPREAD = {"min_span": 0.50, "min_sd": 0.20, "middle_lo": 0.40, "middle_hi": 0.60,
          "max_middle_share": 0.35, "n_bins": 10, "min_bins": 7}
MAX_CLAMPED_SHARE = 0.10

# Doc 08 planet prompt. The closing "Return ONLY a JSON array" line is dropped: the response schema
# enforces the shape. Review anchors are not named here (doc 08 rule 5).
SYSTEM_PROMPT = """You are a Star Wars loremaster with encyclopedic knowledge of canon and Legends material,
assisting with a data engineering simulation. For each planet you will produce Force-related
sensor parameters and descriptive context.

Three measured channels:

MIDICHLORIAN DENSITY (ppm, range 1000-25000)
Ambient midichlorian concentration. Driven by population density, sentient life, and
connection to the Force. Densely populated worlds run high. Barren or lifeless
worlds run low. This channel is STABLE — sigma should be 2-5% of baseline.

KYBER RESONANCE (0-100)
Ambient crystalline Force resonance. Driven by geology, crystal deposits, and Force-attuned
locations. Worlds with major crystal deposits run very high. Gas giants and artificial worlds run low.
MODERATELY VARIABLE — sigma should be 8-15% of baseline.

DARK SIDE ACTIVITY (0-100)
Ambient dark side presence. Driven by Sith history, atrocity, suffering, and dark side nexuses.
Sith strongholds, sites of atrocity, and war-scarred worlds run high. Peaceful worlds run low.
Mostly quiet with RARE SPIKES — sigma 5-10% of baseline, and dark_spike_probability between
0.0 and 0.05 representing the per-scan chance of a dark side surge.

Also assign:
- system_name: the star system, using canonical naming where known
- region: exactly one of Core Worlds, Colonies, Inner Rim, Expansion Region, Mid Rim,
  Outer Rim, Wild Space, Unknown Regions
- description: 2-3 sentences on the planet's character
- force_history: 2-3 sentences on canonical Force-related events there
- canon_confidence: 0.0-1.0, how confident you are this reflects actual canon rather than
  your own reasonable extrapolation. Be honest. Obscure planets should score low.

Every planet must receive non-zero, plausible values on all three channels. There are no
Force-dead worlds in this simulation."""

USER_PREAMBLE = (
    "Produce parameters for each planet below. Return exactly one entry per planet, in the same "
    "order, echoing each planet's sector_id exactly as given. A SWAPI value of \"unknown\" means "
    "SWAPI has no data for that field."
)

# Appended only with --full-range-hint, for the regeneration doc 08 describes when baselines
# cluster at the midpoint. It changes the prompt hash and version label.
FULL_RANGE_HINT = (
    "Use the full documented range of each channel across these planets. Do not cluster "
    "baselines near the midpoint of a range."
)


def batch_schema(ids):
    def num(field):
        lo, hi = BOUNDS[field]
        return {"type": "number", "minimum": lo, "maximum": hi}

    props = {
        "sector_id": {"type": "string", "enum": list(ids)},
        "system_name": {"type": "string"},
        "region": {"type": "string", "enum": list(ec.REGIONS)},
        **{f: num(f) for f in NUMERIC},
        "description": {"type": "string"},
        "force_history": {"type": "string"},
        "canon_confidence": num("canon_confidence"),
    }
    item = {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}
    return {
        "type": "object",
        "properties": {"planets": {"type": "array", "items": item,
                                   "minItems": len(ids), "maxItems": len(ids)}},
        "required": ["planets"],
        "additionalProperties": False,
    }


def planet_input(p, sid):
    # The model sees SWAPI's raw values, including "unknown"; blanking is for the CSV only.
    return {"sector_id": sid, "name": p["name"], "climate": p["climate"], "terrain": p["terrain"],
            "population": p["population"], "diameter_km": p["diameter"]}


def user_prompt(batch, sid_map, hint):
    payload = json.dumps([planet_input(p, sid_map[ec.url_id(p["url"])]) for p in batch], indent=2)
    return f"{USER_PREAMBLE}{' ' + FULL_RANGE_HINT if hint else ''}\n\nPlanets:\n{payload}"


def validate_batch(parsed, expected_ids):
    """Hard-check one response. Returns rows by sector_id. Raises ValueError on a hard failure."""
    rows = parsed.get("planets") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError("response has no `planets` array")
    got = [r.get("sector_id") for r in rows]
    if sorted(map(str, got)) != sorted(expected_ids):
        raise ValueError(f"returned ids {got} do not match requested ids {list(expected_ids)}")
    for r in rows:
        sid = r["sector_id"]
        for field, (lo, hi) in BOUNDS.items():
            v = r.get(field)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ValueError(f"{sid}.{field} = {v!r} is outside [{lo}, {hi}]")
        if r.get("region") not in ec.REGIONS:
            raise ValueError(f"{sid}.region = {r.get('region')!r} is not an allowed region")
        for field in ("system_name", "description", "force_history"):
            if not isinstance(r.get(field), str) or not r[field].strip():
                raise ValueError(f"{sid}.{field} is empty")
    return {r["sector_id"]: r for r in rows}


def enforce_sigma(sid, m, strict):
    """Round each baseline, then hold its sigma to the channel's band as a fraction of the row's
    own (rounded) baseline. Returns (rounded numbers, adjustments). Out-of-band sigmas are clamped,
    or raise ValueError when strict."""
    out, adjustments = {}, []
    for ch, (lo, hi) in RATIO_BANDS.items():
        base = round(float(m[f"{ch}_baseline"]), 2)
        sigma = float(m[f"{ch}_sigma"])
        ratio = sigma / base
        if ratio < lo - SIGMA_TOL or ratio > hi + SIGMA_TOL:
            if strict:
                raise ValueError(f"{sid}.{ch}_sigma / {ch}_baseline = {ratio:.4f} is outside "
                                 f"{lo:.2f}-{hi:.2f}")
            clamped = min(max(sigma, base * lo), base * hi)
            adjustments.append({"sector_id": sid, "channel": ch, "baseline": base,
                                "model_sigma": sigma, "model_ratio": round(ratio, 4),
                                "clamped_sigma": round(clamped, 4)})
            sigma = clamped
        out[f"{ch}_baseline"] = base
        out[f"{ch}_sigma"] = round(sigma, 4)
    out["dark_spike_probability"] = round(float(m["dark_spike_probability"]), 4)
    out["canon_confidence"] = round(float(m["canon_confidence"]), 2)
    return out, adjustments


def sigma_band_violations(rows):
    """Self-check on the final rounded rows (the unknown row is exempt: its values are medians)."""
    bad = []
    for r in rows:
        if r["is_unknown"] is True or r["is_unknown"] == "true":
            continue
        for ch, (lo, hi) in RATIO_BANDS.items():
            ratio = float(r[f"{ch}_sigma"]) / float(r[f"{ch}_baseline"])
            if ratio < lo - FINAL_TOL or ratio > hi + FINAL_TOL:
                bad.append(f"{r['sector_id']}.{ch}: {ratio:.4f} outside {lo:.2f}-{hi:.2f}")
    return bad


def numeric_or_blank(value, zero_is_blank=False):
    """SWAPI's "unknown" (or any non-number) becomes empty, so the seed column can be numeric.
    With zero_is_blank, a numeric zero also becomes empty: SWAPI's 0 diameter means unknown."""
    text = str(value)
    if not NUMERIC_TEXT.fullmatch(text):
        return ""
    if zero_is_blank and float(text) == 0:
        return ""
    return value


def passthrough(p):
    return {"sector_name": p["name"], "climate": p["climate"], "terrain": p["terrain"],
            "population": numeric_or_blank(p["population"]),
            "diameter_km": numeric_or_blank(p["diameter"], zero_is_blank=True)}


def fmt(field, value):
    digits = 4 if field == "dark_spike_probability" or field.endswith("_sigma") else 2
    return round(float(value), digits)


def unknown_row(p, others):
    """The planets/28 row: fixed text, and the median of each numeric column of the other rows."""
    row = {"sector_id": ec.UNKNOWN_SECTOR_ID, **passthrough(p),
           "system_name": "Unknown", "region": "Unknown Regions"}
    for f in MEDIAN_COLUMNS:
        row[f] = fmt(f, statistics.median(float(r[f]) for r in others))
    row.update({
        "description": "Location unknown; SWAPI has no data for this planet. Baseline values are "
                       "the medians of the other sectors.",
        "force_history": "No canonical Force history is recorded for this location.",
        "canon_confidence": 0.0,
        "is_unknown": True,
    })
    return row


def percentile(sorted_values, p):
    """Linear interpolation between closest ranks, as Databricks percentile() does."""
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def spread_stats(rows):
    """Check 1 of warehouse/dbt/analyses/phase1_check_1_spread.sql, computed offline on `rows`."""
    out = {}
    for ch, (lo, hi) in RANGES.items():
        pos = sorted((float(r[f"{ch}_baseline"]) - lo) / (hi - lo) for r in rows)
        p10, p90 = percentile(pos, 0.10), percentile(pos, 0.90)
        sd = statistics.pstdev(pos)
        share = sum(SPREAD["middle_lo"] <= x <= SPREAD["middle_hi"] for x in pos) / len(pos)
        bins = len({min(max(math.floor(x * SPREAD["n_bins"]), 0), SPREAD["n_bins"] - 1) for x in pos})
        checks = {"span": p90 - p10 >= SPREAD["min_span"], "sd": sd >= SPREAD["min_sd"],
                  "middle": share <= SPREAD["max_middle_share"], "bins": bins >= SPREAD["min_bins"]}
        out[ch] = {"p10": round(p10, 4), "p90": round(p90, 4), "span": round(p90 - p10, 4),
                   "sd": round(sd, 4), "middle_share": round(share, 4), "bins": bins,
                   "checks": checks, "pass": all(checks.values())}
    return out


def clamp_stats(adjustments, n_rows):
    """Per channel: how many rows had a sigma clamped, against the 10% review gate (doc 08)."""
    out = {}
    for ch in RATIO_BANDS:
        count = sum(1 for a in adjustments if a["channel"] == ch)
        share = count / n_rows if n_rows else 0.0
        out[ch] = {"clamped": count, "share": round(share, 4), "pass": share <= MAX_CLAMPED_SHARE}
    return out


def review_gate(rows, adjustments):
    clamps = clamp_stats(adjustments, len(rows))
    spread = spread_stats(rows)
    passed = all(c["pass"] for c in clamps.values()) and all(s["pass"] for s in spread.values())
    return {"rows_evaluated": len(rows), "clamps": clamps, "spread": spread, "pass": passed}


def gate_report(gate):
    n = gate["rows_evaluated"]
    partial = "" if n == 59 else f"  (only {n} of 59 rows: the thresholds are meant for all 59)"
    lines = [f"review gate (offline math on {n} rows; the SQL checks run after dbt seed){partial}",
             f"  sigmas clamped per channel (gate: at most {MAX_CLAMPED_SHARE:.0%} of rows):"]
    for ch, c in gate["clamps"].items():
        lines.append(f"    {ch:<6} {c['clamped']:>3} of {n}  ({c['share']:.1%})  "
                     f"{'PASS' if c['pass'] else 'FAIL'}")
    lines.append("  baseline spread within each documented range (position 0..1):")
    lines.append(f"    {'':<6} {'p10-p90 span':>17} {'sd':>13} {'middle share':>17} {'bins of 10':>14}   result")
    lines.append(f"    {'':<6} {'(>= ' + format(SPREAD['min_span'], '.2f') + ')':>17} "
                 f"{'(>= ' + format(SPREAD['min_sd'], '.2f') + ')':>13} "
                 f"{'(<= ' + format(SPREAD['max_middle_share'], '.2f') + ')':>17} "
                 f"{'(>= ' + str(SPREAD['min_bins']) + ')':>14}")
    for ch, s in gate["spread"].items():
        cell = lambda v, key, fmt_: f"{format(v, fmt_)}{'' if s['checks'][key] else ' FAIL'}"
        lines.append(f"    {ch:<6} {cell(s['span'], 'span', '.2f'):>17} {cell(s['sd'], 'sd', '.2f'):>13} "
                     f"{cell(s['middle_share'], 'middle', '.2f'):>17} {cell(s['bins'], 'bins', 'd'):>14}   "
                     f"{'PASS' if s['pass'] else 'FAIL'}")
    lines.append("  OVERALL: PASS" if gate["pass"] else
                 "  OVERALL: FAIL. A clamp or spread failure is a prompt problem: regenerate, do not accept.")
    return "\n".join(lines)


def trial_table(rows):
    lines = [f"  {'sector_id':<18} {'region':<16} {'midi':>8} {'m%':>5} {'kyber':>6} {'k%':>5} "
             f"{'dark':>6} {'d%':>5} {'spike':>6} {'conf':>4}"]
    for r in rows:
        pct = lambda ch: 100 * r[f"{ch}_sigma"] / r[f"{ch}_baseline"]
        lines.append(f"  {r['sector_id']:<18} {r['region']:<16} {r['midi_baseline']:>8.0f} "
                     f"{pct('midi'):>5.1f} {r['kyber_baseline']:>6.1f} {pct('kyber'):>5.1f} "
                     f"{r['dark_baseline']:>6.1f} {pct('dark'):>5.1f} {r['dark_spike_probability']:>6.3f} "
                     f"{r['canon_confidence']:>4.2f}")
    return "\n".join(lines)


def print_prompts(args, batches, sid_map, schema, hash_):
    print("=== SYSTEM PROMPT ===")
    print(SYSTEM_PROMPT)
    print("\n=== USER PROMPT (first call) ===")
    print(user_prompt(batches[0], sid_map, args.full_range_hint))
    print("\n=== RESPONSE SCHEMA (first call) ===")
    print(json.dumps(schema, indent=2))
    print()
    ec.print_request_settings(args.model, args.thinking_level, PROMPT_VERSION, hash_,
                              args.structured_output)
    print(f"calls:           {len(batches)} ({sum(map(len, batches))} planets; "
          f"planets/28 is not sent)")


def recompute_unknown(args):
    path = args.out_dir / f"{SEED}.csv"
    rows = ec.read_csv(path)
    others = [r for r in rows if r["is_unknown"] != "true"]
    unknown = [r for r in rows if r["is_unknown"] == "true"]
    if len(unknown) != 1:
        raise SystemExit(f"{path} must contain exactly one is_unknown row, found {len(unknown)}")
    for f in MEDIAN_COLUMNS:
        unknown[0][f] = fmt(f, statistics.median(float(r[f]) for r in others))
    ec.write_csv(path, FIELDS, rows)
    print(f"recomputed the median row from {len(others)} other rows in {path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ec.add_common_args(parser, SEED)
    parser.add_argument("--batch-size", type=int, default=0,
                        help="planets per model call; 0 (default) sends all 59 in one call. Use "
                             "e.g. 20 if a single response is truncated (MAX_TOKENS)")
    parser.add_argument("--strict-sigma", action="store_true",
                        help="fail when a sigma is outside its percentage band, instead of clamping it")
    parser.add_argument("--full-range-hint", action="store_true",
                        help="append the use-the-full-range instruction (for regenerating clustered output)")
    parser.add_argument("--accept-failing-gate", action="store_true",
                        help="write the seed even though the review gate failed; the override is "
                             "recorded in the provenance sidecar. Real runs otherwise refuse to write")
    parser.add_argument("--recompute-unknown", action="store_true",
                        help="recompute the is_unknown row's medians in the existing CSV; no model call")
    args = parser.parse_args()

    if args.recompute_unknown:  # no model call, so no thinking level is needed
        return recompute_unknown(args)
    ec.require_thinking_level(args)

    planets = ec.read_snapshot("planets", args.snapshot_dir)
    sid_map = ec.sector_ids(planets)
    unknown_planet = next(p for p in planets if ec.url_id(p["url"]) == ec.UNKNOWN_PLANET_ID)
    to_enrich = sorted((p for p in planets if ec.url_id(p["url"]) != ec.UNKNOWN_PLANET_ID),
                       key=lambda p: ec.url_id(p["url"]))
    args.batch_size = args.batch_size or len(to_enrich)
    batches = [to_enrich[i:i + args.batch_size] for i in range(0, len(to_enrich), args.batch_size)]

    ec.assert_no_anchors(**{"system prompt": SYSTEM_PROMPT, "user preamble": USER_PREAMBLE,
                            "full-range hint": FULL_RANGE_HINT})
    version = PROMPT_VERSION + ("+full-range" if args.full_range_hint else "")
    hash_ = ec.prompt_hash(SYSTEM_PROMPT, USER_PREAMBLE,
                           FULL_RANGE_HINT if args.full_range_hint else "",
                           json.dumps(batch_schema(["<sector_id>"]), sort_keys=True))

    if args.print_prompts:
        first_ids = [sid_map[ec.url_id(p["url"])] for p in batches[0]]
        print_prompts(args, batches, sid_map, batch_schema(first_ids), hash_)
        return 0

    out_csv = args.out_dir / f"{SEED}.csv"
    if out_csv.exists() and not args.force and not args.trial:
        raise SystemExit(f"{out_csv} exists. Regeneration is a migration event (doc 08); use --force.")

    api_key = ec.get_api_key()
    results, usages = {}, []
    for n, batch in enumerate(batches, 1):
        ids = [sid_map[ec.url_id(p["url"])] for p in batch]
        body = gemini_client.build_request(SYSTEM_PROMPT, user_prompt(batch, sid_map, args.full_range_hint),
                                           batch_schema(ids), args.thinking_level, args.structured_output)
        print(f"call {n}/{len(batches)}: {len(batch)} planets ...", flush=True)
        try:
            parsed, usage = gemini_client.generate_json(api_key, args.model, body)
        except gemini_client.GeminiError as e:
            hint = ("\nThe response was truncated. Rerun with --batch-size 20 (or smaller)."
                    if "MAX_TOKENS" in str(e) else "")
            raise SystemExit(f"Gemini call {n} failed: {e}{hint}\nNothing was written.")
        usages.append(usage)
        try:
            results.update(validate_batch(parsed, ids))
        except ValueError as e:
            raise SystemExit(f"call {n} failed validation: {e}\nNothing was written.")
        if args.trial:
            break

    adjustments, rows = [], []
    for p in to_enrich:
        sid = sid_map[ec.url_id(p["url"])]
        if sid not in results:  # only possible in --trial, which stops after the first call
            continue
        m = results[sid]
        try:
            numbers, adj = enforce_sigma(sid, m, args.strict_sigma)
        except ValueError as e:
            raise SystemExit(f"{e}\nNothing was written (--strict-sigma).")
        adjustments += adj
        rows.append({"sector_id": sid, **passthrough(p), "system_name": m["system_name"].strip(),
                     "region": m["region"], **numbers, "description": m["description"].strip(),
                     "force_history": m["force_history"].strip(), "is_unknown": False})
    warnings = [f"{a['sector_id']} {a['channel']}: sigma {a['model_sigma']:.4f} was "
                f"{a['model_ratio']:.3f} of baseline; clamped to {a['clamped_sigma']:.4f}"
                for a in adjustments]

    if args.trial:
        print("\ntrial (nothing written): per-row summary; m%/k%/d% are sigma as a percent of baseline, "
              "after enforcement")
        print(trial_table(rows))
        print(f"\nfirst two rows as returned by the model:\n{json.dumps(list(results.values())[:2], indent=2)}")
        print(f"\nusage {ec.sum_usage(usages)}")
        print(f"{len(adjustments)} sigma adjustment(s):")
        for w in warnings:
            print(f"  {w}")
        print()
        print(gate_report(review_gate(rows, adjustments)))
        return 0

    rows.append(unknown_row(unknown_planet, rows))
    rows.sort(key=lambda r: (r["sector_id"] == ec.UNKNOWN_SECTOR_ID, r["sector_id"]))  # unknown last
    ordered = [{k: r[k] for k in FIELDS} for r in rows]
    bad = sigma_band_violations(ordered)
    if bad:
        raise SystemExit("internal check failed after rounding:\n  " + "\n  ".join(bad))
    gate = review_gate([r for r in ordered if r["is_unknown"] is not True], adjustments)
    overridden = (not gate["pass"]) and args.accept_failing_gate
    if not gate["pass"] and not args.accept_failing_gate:
        print(gate_report(gate))
        raise SystemExit("\nThe review gate failed, so nothing was written. Regenerate (doc 08: this is a "
                         "prompt problem), or rerun with --accept-failing-gate to write anyway; the "
                         "override is recorded in the sidecar.")

    cycles = ec.regeneration_cycles(args.out_dir, SEED)
    ec.write_csv(out_csv, FIELDS, ordered)
    rec = ec.provenance_record(
        SEED, args.model, args.thinking_level, version, hash_, cycles, len(ordered),
        ec.sum_usage(usages), warnings,
        {"structured_output": args.structured_output, "calls": len(batches),
         "batch_size": args.batch_size,
         "sigma_enforcement": "strict" if args.strict_sigma else "clamped to the band; see sigma_adjustments",
         "sigma_adjustments": adjustments,
         "review_gate": gate,
         "accepted_failing_gate": overridden,
         "unknown_row": "medians of the other 59 rows (computed at generation; "
                        "rerun --recompute-unknown after review edits)"})
    side = ec.write_sidecar(args.out_dir, SEED, rec)

    print(f"\nwrote {out_csv} ({len(ordered)} rows) and {side.name}")
    print(f"{len(adjustments)} sigma adjustment(s) (all recorded in the sidecar)")
    for w in warnings[:20]:
        print(f"  {w}")
    print()
    print(gate_report(gate))
    if overridden:
        print("\nWARNING: written despite a failing review gate (--accept-failing-gate). "
              "The override is recorded in the sidecar; do not accept this output.")
    print("\nprovenance row for seeds/ENRICHMENT_PROVENANCE.md:")
    print(ec.provenance_table_row(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
