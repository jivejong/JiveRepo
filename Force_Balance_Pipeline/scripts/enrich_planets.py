#!/usr/bin/env python3
"""Planet enrichment (doc 08): SWAPI planets -> warehouse/dbt/seeds/dim_sector.csv.

Build-time only. The reviewed output is committed and frozen; reruns do not reproduce it, and
regeneration is a migration event (doc 08), so an existing seed is only overwritten with --force.

Behavior:
  * 59 of the 60 SWAPI planets go to the model, in batches, with a bounded JSON schema for every
    numeric field. Ranges are enforced by the schema and re-checked here.
  * SWAPI planet 28 ("unknown") is never sent to the model. Its row is written with sector_id
    "uncharted", is_unknown = true, and the median of each numeric column of the other 59 rows.
  * Prompt text never names a review anchor (doc 08 rule 5); the script refuses to run if it does.
  * The thinking level is a required flag and is recorded, with the model ID, prompt version and
    hash, in a provenance sidecar next to the CSV.

Usage:
    python scripts/enrich_planets.py --print-prompts          # no API call
    python scripts/enrich_planets.py --thinking-level low --trial   # first batch only, writes nothing
    python scripts/enrich_planets.py --thinking-level low
    python scripts/enrich_planets.py --recompute-unknown      # after hand edits during review
"""
import argparse
import json
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
# 1..100.
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
# sigma / baseline bands from the prompt. Outside them is a review warning, not a hard failure.
RATIO_BANDS = {"midi": (0.02, 0.05), "kyber": (0.08, 0.15), "dark": (0.05, 0.10)}
RANGES = {"midi": (1000.0, 25000.0), "kyber": (0.0, 100.0), "dark": (0.0, 100.0)}

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
    return {"sector_id": sid, "name": p["name"], "climate": p["climate"], "terrain": p["terrain"],
            "population": p["population"], "diameter_km": p["diameter"]}


def user_prompt(batch, sid_map, hint):
    payload = json.dumps([planet_input(p, sid_map[ec.url_id(p["url"])]) for p in batch], indent=2)
    return f"{USER_PREAMBLE}{' ' + FULL_RANGE_HINT if hint else ''}\n\nPlanets:\n{payload}"


def validate_batch(parsed, expected_ids):
    """Hard-check one batch. Returns (rows by sector_id, warnings). Raises ValueError on a hard failure."""
    rows = parsed.get("planets") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError("response has no `planets` array")
    got = [r.get("sector_id") for r in rows]
    if sorted(map(str, got)) != sorted(expected_ids):
        raise ValueError(f"returned ids {got} do not match requested ids {list(expected_ids)}")
    warnings = []
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
        for ch, (lo, hi) in RATIO_BANDS.items():
            ratio = r[f"{ch}_sigma"] / r[f"{ch}_baseline"]
            if not lo - 0.001 <= ratio <= hi + 0.001:
                warnings.append(f"{sid}: {ch}_sigma/{ch}_baseline = {ratio:.3f}, outside {lo:.2f}-{hi:.2f}")
    return {r["sector_id"]: r for r in rows}, warnings


def passthrough(p):
    return {"sector_name": p["name"], "climate": p["climate"], "terrain": p["terrain"],
            "population": p["population"], "diameter_km": p["diameter"]}


def fmt(field, value):
    return round(float(value), 4 if field == "dark_spike_probability" else 2)


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


def spread_summary(rows):
    """A quick clustering hint. The pass criteria are the analyses checks run after dbt seed."""
    out = []
    for ch, (lo, hi) in RANGES.items():
        pos = sorted((float(r[f"{ch}_baseline"]) - lo) / (hi - lo) for r in rows)
        mid = sum(0.40 <= x <= 0.60 for x in pos) / len(pos)
        out.append(f"  {ch:<6} min {pos[0]:.2f}  max {pos[-1]:.2f}  sd {statistics.pstdev(pos):.2f}  "
                   f"share in 0.40-0.60: {mid:.2f}")
    return "\n".join(out)


def print_prompts(args, batches, sid_map, schema, hash_):
    print("=== SYSTEM PROMPT ===")
    print(SYSTEM_PROMPT)
    print("\n=== USER PROMPT (first batch) ===")
    print(user_prompt(batches[0], sid_map, args.full_range_hint))
    print("\n=== RESPONSE SCHEMA (first batch) ===")
    print(json.dumps(schema, indent=2))
    print()
    ec.print_request_settings(args.model, args.thinking_level, PROMPT_VERSION, hash_)
    print(f"batches:         {len(batches)} of up to {args.batch_size} planets "
          f"({sum(map(len, batches))} planets; planets/28 is not sent)")


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
    parser.add_argument("--batch-size", type=int, default=10,
                        help="planets per model call (doc 08: batches of 10, or 1 for one per planet)")
    parser.add_argument("--full-range-hint", action="store_true",
                        help="append the use-the-full-range instruction (for regenerating clustered output)")
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
    batches = [to_enrich[i:i + args.batch_size] for i in range(0, len(to_enrich), args.batch_size)]

    assert_texts = {"system prompt": SYSTEM_PROMPT, "user preamble": USER_PREAMBLE,
                    "full-range hint": FULL_RANGE_HINT}
    ec.assert_no_anchors(**assert_texts)
    version = PROMPT_VERSION + ("+full-range" if args.full_range_hint else "")
    template_schema = batch_schema(["<sector_id>"])
    hash_ = ec.prompt_hash(SYSTEM_PROMPT, USER_PREAMBLE,
                           FULL_RANGE_HINT if args.full_range_hint else "",
                           json.dumps(template_schema, sort_keys=True))

    if args.print_prompts:
        first_ids = [sid_map[ec.url_id(p["url"])] for p in batches[0]]
        print_prompts(args, batches, sid_map, batch_schema(first_ids), hash_)
        return 0

    out_csv = args.out_dir / f"{SEED}.csv"
    if out_csv.exists() and not args.force and not args.trial:
        raise SystemExit(f"{out_csv} exists. Regeneration is a migration event (doc 08); use --force.")

    api_key = ec.get_api_key()
    results, warnings, usages = {}, [], []
    for n, batch in enumerate(batches, 1):
        ids = [sid_map[ec.url_id(p["url"])] for p in batch]
        body = gemini_client.build_request(SYSTEM_PROMPT, user_prompt(batch, sid_map, args.full_range_hint),
                                           batch_schema(ids), args.thinking_level)
        print(f"batch {n}/{len(batches)}: {len(batch)} planets ...", flush=True)
        parsed, usage = gemini_client.generate_json(api_key, args.model, body)
        usages.append(usage)
        try:
            rows, warn = validate_batch(parsed, ids)
        except ValueError as e:
            raise SystemExit(f"batch {n} failed validation: {e}\nNothing was written.")
        results.update(rows)
        warnings += warn
        if args.trial:
            print(json.dumps(list(rows.values()), indent=2))
            print(f"\ntrial: {len(warn)} warning(s); usage {usage}; nothing written")
            for w in warn:
                print(f"  warning: {w}")
            return 0

    rows = []
    for p in to_enrich:
        sid = sid_map[ec.url_id(p["url"])]
        model_row = results[sid]
        row = {"sector_id": sid, **passthrough(p), "system_name": model_row["system_name"].strip(),
               "region": model_row["region"]}
        for f in NUMERIC + ["canon_confidence"]:
            row[f] = fmt(f, model_row[f]) if f != "canon_confidence" else round(float(model_row[f]), 2)
        row.update({"description": model_row["description"].strip(),
                    "force_history": model_row["force_history"].strip(), "is_unknown": False})
        rows.append(row)
    rows.append(unknown_row(unknown_planet, rows))
    rows.sort(key=lambda r: (r["sector_id"] == ec.UNKNOWN_SECTOR_ID, r["sector_id"]))  # stable, unknown last
    ordered = [{k: r[k] for k in FIELDS} for r in rows]

    cycles = ec.regeneration_cycles(args.out_dir, SEED)
    ec.write_csv(out_csv, FIELDS, ordered)
    rec = ec.provenance_record(SEED, args.model, args.thinking_level, version, hash_, cycles,
                               len(ordered), ec.sum_usage(usages), warnings,
                               {"batch_size": args.batch_size, "batches": len(batches),
                                "unknown_row": "medians of the other 59 rows (computed at generation; "
                                               "rerun --recompute-unknown after review edits)"})
    side = ec.write_sidecar(args.out_dir, SEED, rec)

    print(f"\nwrote {out_csv} ({len(ordered)} rows) and {side.name}")
    print("baseline spread (hint only; the pass criteria are the analyses checks after dbt seed):")
    print(spread_summary([r for r in ordered if r["is_unknown"] is not True]))
    print(f"{len(warnings)} sigma-ratio warning(s)")
    for w in warnings[:20]:
        print(f"  {w}")
    print("\nprovenance row for seeds/ENRICHMENT_PROVENANCE.md:")
    print(ec.provenance_table_row(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
