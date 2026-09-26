#!/usr/bin/env python3
"""Planet enrichment (doc 08): SWAPI planets -> warehouse/dbt/seeds/dim_sector.csv.

Build-time only. The reviewed output is committed and frozen; reruns do not reproduce it, and
regeneration is a migration event (doc 08), so an existing seed is only overwritten with --force.

A model call NEVER writes the seed (doc 08: seeds are written from the reviewed response, never from
a fresh call). A call saves its raw response outside the repo and reports; only
--from-response FILE --write produces dim_sector.csv.

Behavior:
  * The 59 planets other than SWAPI planet 28 go to the model in ONE call by default, with a
    bounded JSON schema for every numeric field. --batch-size N splits the call if a single
    response is truncated (finishReason MAX_TOKENS); --trial sends only the first call.
  * Each sigma is enforced as a fraction of that row's own baseline (midi 2-5%, kyber 8-15%, dark
    5-10%). Out-of-band values are clamped in code and every adjustment is recorded in the
    provenance sidecar; --strict-sigma fails instead.
  * The review gate, per channel: at most 10% of rows with a clamped sigma; middle-band (0.40-0.60)
    share at most 0.35; range use (lowest baseline at position <= 0.25, highest >= 0.75); and all
    check-2 anchors passing (blocking). Printed for every call and recorded in the sidecar.
  * Recorded human corrections (data/enrichment_corrections.csv, --corrections) are applied at
    promote time BEFORE the gate: baseline columns of listed rows only, never the uncharted row. A
    corrected baseline rescales its sigma to the same percentage of baseline and the band is
    re-checked. Reports show the pre- and post-correction gates, and the sidecar records every
    correction (original, corrected, reason, sigma rescale). --suggest-corrections prints, for each
    failing anchor, the value that just reaches its threshold and the channel's p90/p10.
  * SWAPI planet 28 ("unknown") is never sent to the model. Its row is written with sector_id
    "uncharted", is_unknown = true, and the median of each numeric column of the other 59 rows.
  * population and diameter_km are blank in the CSV when SWAPI's value is not a number, and a
    diameter_km of 0 is blank too (SWAPI's 0 diameter means unknown).
  * Prompt instruction text never names a review anchor (doc 08 rule 5); the script refuses to run
    if it does. The planet data entries the model must describe are inherent to the request.
  * Every call's raw response is saved outside the repo (--responses-dir, default
    ~/.force_balance_pipeline/responses) before it is parsed, with a .meta.json.
  * --from-response FILE --write promotes a reviewed saved response. Provenance comes from the
    response's .meta.json. It refuses if the saved prompt hash or planet data differs from today's
    (an older meta without the planet-data hash promotes with a warning), if the responses are not
    one complete run, if the gate fails (--accept-failing-gate overrides and is recorded), or if the
    seed exists (--force). The sidecar always records planets.json's SHA-256. No API call, no key.

Usage:
    python scripts/enrich_planets.py --print-prompts                     # no API call
    python scripts/enrich_planets.py --thinking-level low --trial        # first call only; saves and reports
    python scripts/enrich_planets.py --thinking-level low                # all calls; saves and reports
    python scripts/enrich_planets.py --from-response FILE [FILE ...]     # offline re-analysis
    python scripts/enrich_planets.py --from-response FILE --suggest-corrections
    python scripts/enrich_planets.py --from-response FILE [FILE ...] --write   # promote to the seed
    python scripts/enrich_planets.py --recompute-unknown                 # after hand edits during review
"""
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path

import enrich_common as ec
import gemini_client

SEED = "dim_sector"
PROMPT_VERSION = "planets-v1"
# One call of 59 planets is roughly 13-19k tokens of answer, and thinking tokens count toward the
# cap. 65536 was accepted by the API (probe step 20); unused headroom costs nothing.
MAX_OUTPUT_TOKENS = 65536

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

# Review gate (doc 08), per channel. RANGE_USE mirrors warehouse/dbt/analyses/phase1_check_1_spread.sql
# and ANCHORS/HIGH_PCT_RANK/LOW_PCT_RANK mirror phase1_check_2_anchors.sql; scripts/check_gate_parity.py
# verifies they stay in sync. The gate deliberately does not test even spread (P10-P90 span, SD, bins):
# the prompt's own lore (peaceful worlds low, crystal worlds rare) makes the kyber and dark
# distributions right-skewed by design, so it tests range use instead.
#   * at most 10% of rows have a sigma clamped (MAX_CLAMPED_SHARE)
#   * middle-band share (0.40-0.60 of the range) is at most 0.35
#   * range use: the lowest baseline is at position <= 0.25 and the highest at >= 0.75
#   * all anchors pass check 2 (blocking)
RANGE_USE = {"max_min_position": 0.25, "min_max_position": 0.75, "middle_lo": 0.40,
             "middle_hi": 0.60, "max_middle_share": 0.35}
MAX_CLAMPED_SHARE = 0.10
BASELINE_COLUMNS = ("midi_baseline", "kyber_baseline", "dark_baseline")

# Check 2 (blocking; mirrors warehouse/dbt/analyses/phase1_check_2_anchors.sql). The anchors are the
# planets named in the doc 07 checkpoint and the doc 08 review checklist.
ANCHORS = (("mustafar", "dark", "high"), ("dathomir", "dark", "high"), ("geonosis", "dark", "high"),
           ("naboo", "dark", "low"), ("alderaan", "dark", "low"),
           ("coruscant", "midi", "high"), ("utapau", "kyber", "high"))
HIGH_PCT_RANK = 0.85
LOW_PCT_RANK = 0.15

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
    # No minItems/maxItems: Google rejects exact array lengths on this schema (probe steps 12 and
    # 15-18). The enum keeps every id valid; validate_batch enforces the exact count and id set.
    return {
        "type": "object",
        "properties": {"planets": {"type": "array", "items": item}},
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
    ec.check_exact_ids(rows, expected_ids, "sector_id", "planet")
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


def _positions(rows, ch):
    """(position within the documented range, native value, sector_id), ascending by position."""
    lo, hi = RANGES[ch]
    return sorted(((float(r[f"{ch}_baseline"]) - lo) / (hi - lo), float(r[f"{ch}_baseline"]),
                   r["sector_id"]) for r in rows)


def percent_rank(values, v):
    """SQL percent_rank(): (rank - 1) / (n - 1), where ties share the lowest rank."""
    rank = 1 + sum(1 for x in values if x < v)
    return (rank - 1) / (len(values) - 1)


def range_use_stats(rows):
    """Check 1 of warehouse/dbt/analyses/phase1_check_1_spread.sql, computed offline on `rows`."""
    out = {}
    for ch in RANGES:
        pos = [p[0] for p in _positions(rows, ch)]
        share = sum(RANGE_USE["middle_lo"] <= x <= RANGE_USE["middle_hi"] for x in pos) / len(pos)
        checks = {"min": pos[0] <= RANGE_USE["max_min_position"],
                  "max": pos[-1] >= RANGE_USE["min_max_position"],
                  "middle": share <= RANGE_USE["max_middle_share"]}
        out[ch] = {"min_position": round(pos[0], 4), "max_position": round(pos[-1], 4),
                   "middle_share": round(share, 4), "checks": checks, "pass": all(checks.values())}
    return out


def clamp_stats(adjustments, n_rows):
    """Per channel: how many rows had a sigma clamped, against the 10% review gate (doc 08)."""
    out = {}
    for ch in RATIO_BANDS:
        count = sum(1 for a in adjustments if a["channel"] == ch)
        share = count / n_rows if n_rows else 0.0
        out[ch] = {"clamped": count, "share": round(share, 4), "pass": share <= MAX_CLAMPED_SHARE}
    return out


def anchor_results(rows):
    """Check 2 of warehouse/dbt/analyses/phase1_check_2_anchors.sql, computed offline on `rows`."""
    by_id = {r["sector_id"]: r for r in rows}
    out = []
    for sid, ch, expect in ANCHORS:
        row = by_id.get(sid)
        if row is None:
            out.append({"sector_id": sid, "channel": ch, "expect": expect, "present": False, "ok": False})
            continue
        col = f"{ch}_baseline"
        v = float(row[col])
        pr = percent_rank([float(r[col]) for r in rows], v)
        ok = pr >= HIGH_PCT_RANK if expect == "high" else pr <= LOW_PCT_RANK
        out.append({"sector_id": sid, "channel": ch, "expect": expect, "present": True,
                    "value": round(v, 4), "pct_rank": round(pr, 4), "ok": bool(ok)})
    return out


def review_gate(rows, adjustments):
    clamps = clamp_stats(adjustments, len(rows))
    use = range_use_stats(rows)
    results = anchor_results(rows)
    anchors = {"results": results, "passed": sum(a["ok"] for a in results), "total": len(results),
               "pass": all(a["ok"] for a in results)}
    passed = all(c["pass"] for c in clamps.values()) and all(s["pass"] for s in use.values()) \
        and anchors["pass"]
    return {"rows_evaluated": len(rows), "clamps": clamps, "range_use": use, "anchors": anchors,
            "pass": passed}


def gate_report(gate, title="review gate"):
    n = gate["rows_evaluated"]
    partial = "" if n == 59 else f"  (only {n} of 59 rows: the thresholds are meant for all 59)"
    lines = [f"{title} (offline math on {n} rows; the SQL checks run after dbt seed){partial}",
             f"  sigmas clamped per channel (gate: at most {MAX_CLAMPED_SHARE:.0%} of rows):"]
    for ch, c in gate["clamps"].items():
        lines.append(f"    {ch:<6} {c['clamped']:>3} of {n}  ({c['share']:.1%})  "
                     f"{'PASS' if c['pass'] else 'FAIL'}")
    lines.append("  baseline range use, position within each documented range (0 = minimum, 1 = maximum):")
    lines.append(f"    {'':<6} {'min position':>14} {'max position':>14} {'middle share':>14}   result")
    lines.append(f"    {'':<6} {'(<= ' + format(RANGE_USE['max_min_position'], '.2f') + ')':>14} "
                 f"{'(>= ' + format(RANGE_USE['min_max_position'], '.2f') + ')':>14} "
                 f"{'(<= ' + format(RANGE_USE['max_middle_share'], '.2f') + ')':>14}")
    for ch, s in gate["range_use"].items():
        cell = lambda v, key: f"{v:.2f}{'' if s['checks'][key] else ' FAIL'}"
        lines.append(f"    {ch:<6} {cell(s['min_position'], 'min'):>14} {cell(s['max_position'], 'max'):>14} "
                     f"{cell(s['middle_share'], 'middle'):>14}   {'PASS' if s['pass'] else 'FAIL'}")
    a = gate["anchors"]
    lines.append(f"  check 2, anchors (blocking; percent_rank across the {n} planets, "
                 f"high >= {HIGH_PCT_RANK}, low <= {LOW_PCT_RANK}):")
    lines.append(f"    {'sector':<10} {'channel':<7} {'expect':<6} {'value':>9} {'pct_rank':>9}   result")
    for r in a["results"]:
        if not r["present"]:
            lines.append(f"    {r['sector_id']:<10} {r['channel']:<7} {r['expect']:<6} {'(not in this response)':>19}   FAIL")
        else:
            lines.append(f"    {r['sector_id']:<10} {r['channel']:<7} {r['expect']:<6} {r['value']:>9.2f} "
                         f"{r['pct_rank']:>9.2f}   {'PASS' if r['ok'] else 'FAIL'}")
    lines.append(f"  anchors passing: {a['passed']} of {a['total']}")
    lines.append("  OVERALL: PASS" if gate["pass"] else
                 "  OVERALL: FAIL. A clamp, range-use or anchor failure blocks the seed: regenerate, record a "
                 "correction (data/enrichment_corrections.csv), or --accept-failing-gate.")
    return "\n".join(lines)


def distribution_report(rows):
    lines = ["baseline distribution: position within each documented range (0 = minimum, 1 = maximum)",
             f"    {'':<6} {'min':>6} {'p10':>6} {'p50':>6} {'p90':>6} {'max':>6}"]
    for ch in RANGES:
        pos = [p[0] for p in _positions(rows, ch)]
        vals = [pos[0], percentile(pos, 0.10), percentile(pos, 0.50), percentile(pos, 0.90), pos[-1]]
        lines.append(f"    {ch:<6} " + " ".join(f"{v:>6.2f}" for v in vals))
    return "\n".join(lines)


def sorted_baselines_report(rows, names):
    lines = []
    for ch in RANGES:
        lines.append(f"{ch}_baseline, ascending ({len(rows)} planets):")
        for i, (pos, val, sid) in enumerate(_positions(rows, ch), 1):
            lines.append(f"  {i:>2}  pos {pos:.2f}  {val:>10.2f}  {names.get(sid, sid)}")
        lines.append("")
    return "\n".join(lines)


def suggest_corrections_text(rows, gate):
    """For each failing anchor: the value that just reaches its threshold, and the channel's p90
    (p10 for a low anchor), so a corrected value can be chosen with both in view. Offline."""
    lines = ["suggestions for failing anchors (pre-correction data; baselines in each channel's own units)"]
    failing = [a for a in gate["anchors"]["results"] if a["present"] and not a["ok"]]
    if not failing:
        lines.append("  no failing anchors")
        return "\n".join(lines)
    for a in failing:
        col = f"{a['channel']}_baseline"
        everyone = sorted(float(r[col]) for r in rows)
        others = sorted(float(r[col]) for r in rows if r["sector_id"] != a["sector_id"])
        m = len(others)
        ref_p = 0.90 if a["expect"] == "high" else 0.10
        ref = percentile(everyone, ref_p)
        lines.append(f"  {a['sector_id']} {a['channel']} (expect {a['expect']}, "
                     f"needs pct_rank {'>=' if a['expect'] == 'high' else '<='} "
                     f"{HIGH_PCT_RANK if a['expect'] == 'high' else LOW_PCT_RANK}): "
                     f"currently {a['value']:.2f} (pct_rank {a['pct_rank']:.2f})")
        if a["expect"] == "high":
            k = next(k for k in range(m + 1) if k / m >= HIGH_PCT_RANK)
            v = round(others[k - 1] + 0.01, 2)
            lines.append(f"    minimum value that reaches the threshold: {v:.2f} "
                         f"(pct_rank {percent_rank(others + [v], v):.2f}; must exceed the {k}th lowest of the other {m})")
        else:
            k = max(k for k in range(m + 1) if k / m <= LOW_PCT_RANK)
            v = round(others[k], 2)
            lines.append(f"    maximum value that reaches the threshold: {v:.2f} "
                         f"(pct_rank {percent_rank(others + [v], v):.2f}; must not exceed the {k + 1}th lowest of the other {m})")
        lines.append(f"    channel p{int(ref_p * 100)} for reference: {ref:.2f} (of all {len(everyone)} planets)")
        # If this channel's range-use check also fails on the same side, say what would fix both.
        lo, hi = RANGES[a["channel"]]
        use = gate["range_use"][a["channel"]]
        if a["expect"] == "high" and not use["checks"]["max"]:
            need = lo + RANGE_USE["min_max_position"] * (hi - lo)
            lines.append(f"    range use also fails on {a['channel']}: the highest baseline is "
                         f"{lo + use['max_position'] * (hi - lo):.2f} (position {use['max_position']:.2f}); it needs "
                         f">= {need:.2f} (position {RANGE_USE['min_max_position']:.2f}). A corrected value "
                         f">= {need:.2f} for this planet would satisfy the anchor and the range check together.")
        if a["expect"] == "low" and not use["checks"]["min"]:
            need = lo + RANGE_USE["max_min_position"] * (hi - lo)
            lines.append(f"    range use also fails on {a['channel']}: the lowest baseline is "
                         f"{lo + use['min_position'] * (hi - lo):.2f} (position {use['min_position']:.2f}); it needs "
                         f"<= {need:.2f} (position {RANGE_USE['max_min_position']:.2f}). A corrected value "
                         f"<= {need:.2f} for this planet would satisfy the anchor and the range check together.")
    lines.append("  A correction goes in data/enrichment_corrections.csv; its sigma is rescaled to the same "
                 "percentage of the new baseline.")
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


def planet_corrections(file_rows, valid_ids):
    """The dim_sector corrections, validated. Refuses: the uncharted row, keys that are not planets
    sent to the model, any column other than a *_baseline, and values outside the column's bounds."""
    out = []
    for r in file_rows:
        if r["seed"] != SEED:
            continue  # another seed's corrections are validated by that seed's script
        if r["key"] == ec.UNKNOWN_SECTOR_ID:
            raise SystemExit(f"correction for {r['key']!r} refused: that row is derived from the other "
                             "59 (medians), not enriched, so it cannot be corrected")
        if r["key"] not in valid_ids:
            raise SystemExit(f"correction key {r['key']!r} is not a planet that was sent to the model")
        if r["column"] not in BASELINE_COLUMNS:
            raise SystemExit(f"correction column {r['column']!r} refused: only {BASELINE_COLUMNS} can be "
                             "corrected (a sigma follows its baseline)")
        try:
            value = float(r["corrected_value"])
        except ValueError:
            raise SystemExit(f"correction for {r['key']}.{r['column']}: {r['corrected_value']!r} is not a number")
        lo, hi = BOUNDS[r["column"]]
        if not lo <= value <= hi:
            raise SystemExit(f"correction for {r['key']}.{r['column']}: {value} is outside [{lo}, {hi}]")
        out.append({"key": r["key"], "column": r["column"], "value": value, "reason": r["reason"]})
    return out


def enforce_sigma_rows(results, to_enrich, sid_map, strict, corrections):
    """Apply corrections (if any), enforce sigma bands, and build the rounded rows for every planet
    present in `results`. Returns (rows, adjustments, correction records).

    A corrected baseline rescales its sigma to the same percentage of baseline (relative to what
    the model gave), then the band is enforced as usual, so a rescaled sigma is re-checked."""
    by_key = {}
    for c in corrections:
        by_key.setdefault(c["key"], []).append(c)
    adjustments, rows, records = [], [], []
    for p in to_enrich:
        sid = sid_map[ec.url_id(p["url"])]
        if sid not in results:  # a partial response (--trial, or --from-response on one batch)
            continue
        m = dict(results[sid])
        pending = []
        for c in by_key.get(sid, []):
            sigma_col = c["column"].replace("_baseline", "_sigma")
            orig_b, orig_s = float(m[c["column"]]), float(m[sigma_col])
            m[c["column"]] = c["value"]
            m[sigma_col] = orig_s * (c["value"] / orig_b)
            pending.append((c, sigma_col, orig_b, orig_s, m[sigma_col]))
        try:
            numbers, adj = enforce_sigma(sid, m, strict)
        except ValueError as e:
            raise SystemExit(f"{e}\nNothing was written (--strict-sigma).")
        adjustments += adj
        for c, sigma_col, orig_b, orig_s, rescaled in pending:
            ch = c["column"].split("_")[0]
            records.append({"sector_id": sid, "column": c["column"], "reason": c["reason"],
                            "original_baseline": round(orig_b, 2), "corrected_baseline": c["value"],
                            "sigma_column": sigma_col, "sigma_before": round(orig_s, 4),
                            "sigma_rescaled": round(rescaled, 4), "sigma_final": numbers[sigma_col],
                            "band_adjusted": any(a["channel"] == ch for a in adj)})
        rows.append({"sector_id": sid, **passthrough(p), "system_name": m["system_name"].strip(),
                     "region": m["region"], **numbers, "description": m["description"].strip(),
                     "force_history": m["force_history"].strip(), "is_unknown": False})
    return rows, adjustments, records


def evaluate(results, to_enrich, sid_map, strict, corrections):
    """Pre-correction and post-correction rows and gates. Without corrections they are the same."""
    pre_rows, pre_adj, _ = enforce_sigma_rows(results, to_enrich, sid_map, strict, ())
    pre_gate = review_gate(pre_rows, pre_adj)
    if not corrections:
        return {"pre_rows": pre_rows, "pre_adj": pre_adj, "pre_gate": pre_gate, "rows": pre_rows,
                "adj": pre_adj, "gate": pre_gate, "records": [], "corrected": False}
    rows, adj, records = enforce_sigma_rows(results, to_enrich, sid_map, strict, corrections)
    return {"pre_rows": pre_rows, "pre_adj": pre_adj, "pre_gate": pre_gate, "rows": rows, "adj": adj,
            "gate": review_gate(rows, adj), "records": records, "corrected": True}


def adjustment_lines(adjustments):
    return [f"{a['sector_id']} {a['channel']}: sigma {a['model_sigma']:.4f} was "
            f"{a['model_ratio']:.3f} of baseline; clamped to {a['clamped_sigma']:.4f}"
            for a in adjustments]


def correction_lines(records):
    lines = []
    for r in records:
        lines.append(f"  {r['sector_id']}.{r['column']}: {r['original_baseline']:.2f} -> "
                     f"{r['corrected_baseline']:.2f}; sigma {r['sigma_before']:.4f} -> "
                     f"{r['sigma_rescaled']:.4f} (rescaled)" +
                     (f" -> {r['sigma_final']:.4f} (band re-check clamped it)" if r["band_adjusted"] else "") +
                     f"\n      reason: {r['reason']}")
    return lines


def print_evaluation(ev, names, suggest=False):
    """The gate (pre- and post-correction when corrections apply) and the detail behind it."""
    print(f"{len(ev['pre_adj'])} sigma adjustment(s) before corrections:")
    for line in adjustment_lines(ev["pre_adj"]):
        print(f"  {line}")
    print()
    if ev["corrected"]:
        print(gate_report(ev["pre_gate"], "PRE-correction review gate"))
        print(f"\n{len(ev['records'])} recorded correction(s) applied:")
        for line in correction_lines(ev["records"]):
            print(line)
        print()
        print(gate_report(ev["gate"], "POST-correction review gate"))
    else:
        print(gate_report(ev["gate"]))
    print()
    print(distribution_report(ev["rows"]))
    print()
    if suggest:
        print(suggest_corrections_text(ev["pre_rows"], ev["pre_gate"]))
        print()
    print(sorted_baselines_report(ev["rows"], names))


def load_saved_response(path, all_ids):
    """One saved raw API response (or a plain {"planets": [...]} file) -> validated rows by sector_id."""
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SystemExit(f"{path} is not JSON: {e}")
    usage = {}
    if isinstance(data, dict) and "candidates" in data:
        try:
            parsed, usage = gemini_client.parse_response(raw)
        except gemini_client.GeminiError as e:
            raise SystemExit(f"{path}: {e}")
    elif isinstance(data, dict) and "planets" in data:
        parsed = data
    else:
        raise SystemExit(f"{path} is neither a raw generateContent response nor a {{\"planets\": [...]}} file")
    rows = parsed["planets"] if isinstance(parsed.get("planets"), list) else []
    ids = [str(r.get("sector_id")) if isinstance(r, dict) else "<not an object>" for r in rows]
    stray = sorted(set(ids) - set(all_ids))
    if stray:
        raise SystemExit(f"{path}: ids that are not planets to enrich: {stray}")
    try:
        return validate_batch(parsed, ids), usage
    except ValueError as e:
        raise SystemExit(f"{path} failed validation: {e}")


def print_prompts(args, batches, sid_map, schema, hash_):
    print("=== SYSTEM PROMPT ===")
    print(SYSTEM_PROMPT)
    print("\n=== USER PROMPT (first call) ===")
    print(user_prompt(batches[0], sid_map, args.full_range_hint))
    print("\n=== RESPONSE SCHEMA (first call) ===")
    print(json.dumps(schema, indent=2))
    print()
    ec.print_request_settings(args.model, args.thinking_level, PROMPT_VERSION, hash_,
                              args.max_output_tokens)
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


def current_prompt_hash(full_range_hint):
    """Hash of everything that defines the prompt: system prompt, preamble, hint, schema template."""
    return ec.prompt_hash(SYSTEM_PROMPT, USER_PREAMBLE, FULL_RANGE_HINT if full_range_hint else "",
                          json.dumps(batch_schema(["<sector_id>"]), sort_keys=True))


def finalize_and_write(args, ev, unknown_planet, info):
    """Add the unknown row, apply the (post-correction) review gate, and write the seed and sidecar.

    Refuses to overwrite without --force, and refuses to write when the gate fails
    (--accept-failing-gate overrides and is recorded). `info` carries the provenance.
    """
    out_csv = args.out_dir / f"{SEED}.csv"
    if out_csv.exists() and not args.force:
        raise SystemExit(f"{out_csv} exists. Regeneration is a migration event (doc 08); use --force.")
    rows = list(ev["rows"])
    rows = rows + [unknown_row(unknown_planet, rows)]
    rows.sort(key=lambda r: (r["sector_id"] == ec.UNKNOWN_SECTOR_ID, r["sector_id"]))  # unknown last
    ordered = [{k: r[k] for k in FIELDS} for r in rows]
    bad = sigma_band_violations(ordered)
    if bad:
        raise SystemExit("internal check failed after rounding:\n  " + "\n  ".join(bad))
    gate = ev["gate"]
    overridden = (not gate["pass"]) and args.accept_failing_gate
    if not gate["pass"] and not args.accept_failing_gate:
        print(gate_report(gate, "POST-correction review gate" if ev["corrected"] else "review gate"))
        raise SystemExit("\nThe review gate failed, so nothing was written. Regenerate (doc 08: a prompt "
                         "problem), record a correction in data/enrichment_corrections.csv, or rerun with "
                         "--accept-failing-gate to write anyway; the override is recorded in the sidecar.")

    warnings = adjustment_lines(ev["adj"])
    cycles = ec.regeneration_cycles(args.out_dir, SEED)
    ec.write_csv(out_csv, FIELDS, ordered)
    rec = ec.provenance_record(
        SEED, info["model"], info["thinking_level"], info["version"], info["prompt_hash"], cycles,
        len(ordered), info["usage"], warnings,
        {"structured_output": ec.STRUCTURED_OUTPUT, "calls": info["calls"],
         "batch_size": info["batch_size"], "max_output_tokens": info["max_output_tokens"],
         "sigma_enforcement": "strict" if args.strict_sigma else "clamped to the band; see sigma_adjustments",
         "sigma_adjustments": ev["adj"],
         "review_gate": gate,
         "review_gate_pre_correction": ev["pre_gate"] if ev["corrected"] else None,
         "corrections": ev["records"],
         "corrections_file_sha256": info["corrections_sha256"],
         "inputs_sha256": info["inputs_sha256"],
         "accepted_failing_gate": overridden,
         "unknown_row": "medians of the other 59 rows (computed at generation; "
                        "rerun --recompute-unknown after review edits)",
         **info["extra"]},
        generated_utc=info.get("generated_utc"))
    side = ec.write_sidecar(args.out_dir, SEED, rec)
    prov_md, _ = ec.write_provenance_md(args.out_dir)

    print(f"\nwrote {out_csv} ({len(ordered)} rows), {side.name} and {prov_md.name}")
    print(f"{len(ev['adj'])} sigma adjustment(s) and {len(ev['records'])} correction(s) "
          "(all recorded in the sidecar)")
    print()
    print(gate_report(gate, "POST-correction review gate" if ev["corrected"] else "review gate"))
    if overridden:
        print("\nWARNING: written despite a failing review gate (--accept-failing-gate). "
              "The override is recorded in the sidecar; do not accept this output.")
    print(f"\n{prov_md.name} was rebuilt from the sidecars; 'Reviewed by' stays <you> until "
          "rebuild_provenance.py --seed dim_sector --reviewed-by NAME")
    return 0


REQUIRED_META = ("model", "thinking_level", "max_output_tokens", "prompt_version", "prompt_hash",
                 "requested_utc", "call", "calls", "sector_ids")


def load_meta(raw_path):
    path = ec.meta_path_for(raw_path)
    if not path.exists():
        raise SystemExit(f"{Path(raw_path).name}: there is no {path.name} next to it, so its provenance "
                         "cannot be taken and it cannot be promoted")
    meta = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise SystemExit(f"{path.name} is missing {missing}; it cannot be promoted")
    return meta


def promote(args, to_enrich, sid_map, unknown_planet, corrections):
    """--from-response --write: turn reviewed saved response(s) into dim_sector.csv and its sidecar.

    Provenance (model, thinking level, prompt hash, output-token cap, timestamp) comes from each
    response's .meta.json, not from this run's flags. Refuses if the responses are not one complete
    run, if the saved prompt hash or planet data differs from what would be sent today (the
    response must come from the prompt being committed), or if the gate fails or the seed exists.
    """
    out_csv = args.out_dir / f"{SEED}.csv"
    if out_csv.exists() and not args.force:
        raise SystemExit(f"{out_csv} exists. Regeneration is a migration event (doc 08); use --force.")
    files = list(args.from_response)
    metas = [load_meta(f) for f in files]
    first = metas[0]

    for key in ("model", "thinking_level", "max_output_tokens", "prompt_version", "prompt_hash",
                "requested_utc", "calls"):
        if len({m[key] for m in metas}) != 1:
            raise SystemExit(f"the response files disagree on {key!r}; they must be one run")
    if first["calls"] != len(files) or sorted(m["call"] for m in metas) != list(range(1, first["calls"] + 1)):
        raise SystemExit(f"this run made {first['calls']} call(s) but {len(files)} response file(s) were "
                         "given, or their call numbers do not match; give every call of one run")
    if args.thinking_level and args.thinking_level != first["thinking_level"]:
        raise SystemExit(f"--thinking-level {args.thinking_level} conflicts with the saved response "
                         f"({first['thinking_level']}); provenance comes from the saved response")

    # The response must come from the prompt being committed.
    base_version, _, variant = first["prompt_version"].partition("+")
    if base_version != PROMPT_VERSION or variant not in ("", "full-range"):
        raise SystemExit(f"the saved prompt version {first['prompt_version']!r} is not this script's "
                         f"{PROMPT_VERSION!r}")
    hint = variant == "full-range"
    expected = current_prompt_hash(hint)
    if first["prompt_hash"] != expected:
        raise SystemExit(f"the saved prompt hash {first['prompt_hash']} does not match the current prompt "
                         f"{expected}: the response came from a different prompt. Regenerate, then promote "
                         "that response.")
    by_sid = {sid_map[ec.url_id(p["url"])]: p for p in to_enrich}
    data_checked = True
    for f, m in zip(files, metas):
        if "user_prompt_hash" not in m:
            data_checked = False
            continue
        try:
            batch = [by_sid[s] for s in m["sector_ids"]]
        except KeyError as e:
            raise SystemExit(f"{Path(f).name} lists a planet that is not in the current snapshot: {e}")
        if ec.prompt_hash(user_prompt(batch, sid_map, hint)) != m["user_prompt_hash"]:
            raise SystemExit(f"{Path(f).name}: the planet data the model saw differs from the current "
                             "snapshot. Regenerate, then promote that response.")
    if not data_checked:
        print("WARNING: an older meta has no planet-data hash, so the planet data the model saw was not "
              "verified against the snapshot. planets.json's SHA-256 is recorded in the sidecar.")

    all_ids = [sid_map[ec.url_id(p["url"])] for p in to_enrich]
    results, usages = {}, []
    for f in files:
        rows_by_id, usage = load_saved_response(f, all_ids)
        clash = sorted(set(results) & set(rows_by_id))
        if clash:
            raise SystemExit(f"{Path(f).name}: planets already present in an earlier file: {clash}")
        results.update(rows_by_id)
        usages.append(usage)
    if set(results) != set(all_ids) or len(results) != len(all_ids):
        raise SystemExit(f"the saved responses cover {len(results)} of {len(all_ids)} planets; a seed needs "
                         "all of them")

    ev = evaluate(results, to_enrich, sid_map, args.strict_sigma, corrections)
    print(f"promoting {len(files)} saved response file(s): prompt hash {first['prompt_hash']} matches the "
          f"current prompt; {len(corrections)} recorded correction(s)")
    if ev["corrected"]:
        print(gate_report(ev["pre_gate"], "PRE-correction review gate"))
        print(f"\n{len(ev['records'])} recorded correction(s) applied:")
        for line in correction_lines(ev["records"]):
            print(line)
        print()
    return finalize_and_write(args, ev, unknown_planet, {
        "model": first["model"], "thinking_level": first["thinking_level"], "version": first["prompt_version"],
        "prompt_hash": first["prompt_hash"], "max_output_tokens": first["max_output_tokens"],
        "usage": ec.sum_usage(usages), "calls": first["calls"],
        "batch_size": max(len(m["sector_ids"]) for m in metas),
        "generated_utc": ec.stamp_to_iso(first["requested_utc"]),
        "corrections_sha256": ec.sha256_file(args.corrections) if Path(args.corrections).exists() else None,
        "inputs_sha256": {"planets.json": ec.sha256_file(Path(args.snapshot_dir) / "planets.json")},
        "extra": {"written_from": "reviewed saved response",
                  "promoted_from": [Path(f).name for f in files],
                  "promoted_from_sha256": {Path(f).name: ec.sha256_file(f) for f in files},
                  "promoted_utc": ec.utc_stamp(),
                  "prompt_hash_matched_current_prompt": True,
                  "planet_data_matched_snapshot": data_checked,
                  "response_run_was_trial": bool(first.get("trial"))}})


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
                        help="with --write: write the seed even though the review gate failed; the "
                             "override is recorded in the provenance sidecar")
    parser.add_argument("--recompute-unknown", action="store_true",
                        help="recompute the is_unknown row's medians in the existing CSV; no model call")
    parser.add_argument("--from-response", nargs="+", type=Path, metavar="FILE",
                        help="recompute the review gate and the detail reports from saved raw "
                             "response file(s) (see --responses-dir); no API call. With --write, "
                             "promote them to the seed")
    parser.add_argument("--write", action="store_true",
                        help="with --from-response: write dim_sector.csv and its sidecar from the "
                             "reviewed saved response(s), taking provenance from their .meta.json")
    parser.add_argument("--corrections", type=Path, default=ec.CORRECTIONS_FILE,
                        help="recorded human corrections, applied at promote time before the gate "
                             f"(default: {ec.CORRECTIONS_FILE.relative_to(ec.REPO_ROOT)})")
    parser.add_argument("--suggest-corrections", action="store_true",
                        help="with --from-response: for each failing anchor, print the value that just "
                             "reaches its threshold and the channel's p90 (p10 for a low anchor)")
    args = parser.parse_args()

    if args.write and not args.from_response:
        raise SystemExit("--write needs --from-response: seeds are written from a reviewed saved "
                         "response (doc 08), never from a fresh call")
    if args.write and args.trial:
        raise SystemExit("--write and --trial cannot be combined")
    if args.suggest_corrections and not args.from_response:
        raise SystemExit("--suggest-corrections needs --from-response (it reads a saved response)")
    if args.suggest_corrections and args.write:
        raise SystemExit("--suggest-corrections is for review; run it before --write")
    if args.recompute_unknown:  # no model call, so no thinking level is needed
        return recompute_unknown(args)
    if not args.from_response:
        ec.require_thinking_level(args)

    planets = ec.read_snapshot("planets", args.snapshot_dir)
    sid_map = ec.sector_ids(planets)
    names = {sid_map[ec.url_id(p["url"])]: p["name"] for p in planets}
    unknown_planet = next(p for p in planets if ec.url_id(p["url"]) == ec.UNKNOWN_PLANET_ID)
    to_enrich = sorted((p for p in planets if ec.url_id(p["url"]) != ec.UNKNOWN_PLANET_ID),
                       key=lambda p: ec.url_id(p["url"]))
    all_ids = [sid_map[ec.url_id(p["url"])] for p in to_enrich]
    # dim_jedi rows in the corrections file belong to enrich_jedi.py, which refuses them itself.
    corrections = planet_corrections(ec.read_corrections_file(args.corrections), set(all_ids))  # validated before any API call

    if args.from_response and args.write:
        return promote(args, to_enrich, sid_map, unknown_planet, corrections)

    if args.from_response:
        results = {}
        for path in args.from_response:
            rows_by_id, _usage = load_saved_response(path, all_ids)
            clash = sorted(set(results) & set(rows_by_id))
            if clash:
                raise SystemExit(f"{path}: planets already present in an earlier file: {clash}")
            results.update(rows_by_id)
        ev = evaluate(results, to_enrich, sid_map, args.strict_sigma, corrections)
        print(f"from {len(args.from_response)} saved response file(s): {len(ev['rows'])} of {len(all_ids)} "
              f"planets (no API call, nothing written)")
        print_evaluation(ev, names, args.suggest_corrections)
        return 0

    args.batch_size = args.batch_size or len(to_enrich)
    args.max_output_tokens = args.max_output_tokens or MAX_OUTPUT_TOKENS
    batches = [to_enrich[i:i + args.batch_size] for i in range(0, len(to_enrich), args.batch_size)]

    ec.assert_no_anchors(**{"system prompt": SYSTEM_PROMPT, "user preamble": USER_PREAMBLE,
                            "full-range hint": FULL_RANGE_HINT})
    version = PROMPT_VERSION + ("+full-range" if args.full_range_hint else "")
    hash_ = current_prompt_hash(args.full_range_hint)

    if args.print_prompts:
        first_ids = [sid_map[ec.url_id(p["url"])] for p in batches[0]]
        print_prompts(args, batches, sid_map, batch_schema(first_ids), hash_)
        return 0

    api_key = ec.get_api_key()
    responses_dir = ec.resolve_responses_dir(args.responses_dir)
    stamp = ec.utc_stamp()
    results, usages, saved_paths = {}, [], []
    for n, batch in enumerate(batches, 1):
        ids = [sid_map[ec.url_id(p["url"])] for p in batch]
        user_text = user_prompt(batch, sid_map, args.full_range_hint)
        body = gemini_client.build_request(SYSTEM_PROMPT, user_text, batch_schema(ids),
                                           args.thinking_level, args.max_output_tokens)
        raw_path, meta_path = ec.call_paths(responses_dir, "planets", args.thinking_level, n,
                                            len(batches), stamp)
        ec.write_call_meta(meta_path, {
            "script": "enrich_planets.py", "call": n, "calls": len(batches), "model": args.model,
            "thinking_level": args.thinking_level, "max_output_tokens": args.max_output_tokens,
            "prompt_version": version, "prompt_hash": hash_, "user_prompt_hash": ec.prompt_hash(user_text),
            "trial": args.trial, "sector_ids": ids, "requested_utc": stamp,
            "note": "the raw response is the sibling .json file; analyse it with "
                    "enrich_planets.py --from-response, promote it with --from-response --write"})
        print(f"call {n}/{len(batches)}: {len(batch)} planets ...", flush=True)
        try:
            parsed, usage = gemini_client.generate_json(api_key, args.model, body, raw_path)
        except gemini_client.GeminiError as e:
            hint = (f"\nThe response hit the output limit (--max-output-tokens {args.max_output_tokens}), "
                    "and thinking tokens count toward it. Raise --max-output-tokens, lower "
                    "--thinking-level, or rerun with --batch-size 20 (or smaller)."
                    if "MAX_TOKENS" in str(e) else "")
            saved = f"\nRaw response saved: {raw_path}" if raw_path.exists() else ""
            raise SystemExit(f"Gemini call {n} failed: {e}{hint}{saved}")
        print(f"  raw response saved: {raw_path}")
        saved_paths.append(raw_path)
        usages.append(usage)
        try:
            results.update(validate_batch(parsed, ids))
        except ValueError as e:
            raise SystemExit(f"call {n} failed validation: {e}\nRaw response saved: {raw_path}")
        if args.trial:
            break

    ev = evaluate(results, to_enrich, sid_map, args.strict_sigma, corrections)
    print("\nper-row summary; m%/k%/d% are sigma as a percent of baseline, after enforcement "
          "(pre-correction)")
    print(trial_table(ev["pre_rows"]))
    print(f"\nfirst two rows as returned by the model:\n{json.dumps(list(results.values())[:2], indent=2)}")
    print(f"\nusage {ec.sum_usage(usages)}\n")
    print_evaluation(ev, names)
    files = " ".join(f'"{p}"' for p in saved_paths)
    print("nothing was written to the repo: a call never writes the seed (doc 08).")
    print(f"raw response(s) saved under {responses_dir}\nre-analyse offline:  python scripts/enrich_planets.py "
          f"--from-response {files}\nonce reviewed, promote:  python scripts/enrich_planets.py "
          f"--from-response {files} --write")
    if args.trial:
        print("(--trial sent only the first call, so this response is partial and cannot be promoted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
