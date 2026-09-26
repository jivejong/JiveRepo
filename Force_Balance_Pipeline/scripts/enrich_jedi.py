#!/usr/bin/env python3
"""Jedi enrichment (doc 08): the 17-person roster -> warehouse/dbt/seeds/dim_jedi.csv.

Build-time only. The reviewed output is committed and frozen; reruns do not reproduce it, and
regeneration is a migration event (doc 08), so an existing seed is only overwritten with --force.

A model call NEVER writes the seed (doc 08: seeds are written from the reviewed response, never from
a fresh call). A call saves its raw response outside the repo and reports; only
--from-response FILE --write produces dim_jedi.csv.

Behavior:
  * The roster comes from scripts/jedi_roster.py, which is verified against people.json first.
    Nothing is invented: enrichment only adds attributes to existing SWAPI rows.
  * SWAPI fields (name, species, homeworld) are never model-generated. homeworld_sector_id uses the
    same id mapping as dim_sector, so Yoda and Qui-Gon Jinn map to "uncharted". An empty SWAPI
    species means Human (species/1).
  * The response schema bounds rank, specialties, power_rating and canon_confidence; the exact
    count and id set are enforced in code after the response.
  * Prompt text never names a review anchor (doc 08 rule 5).
  * Refusal gate: --write refuses when the row count is not 17 or any primary_specialty has fewer
    than 3 Jedi (a lopsided roster leaves some signatures without a valid responder).
    --accept-failing-gate overrides and the override is recorded in the sidecar.
  * Every call's raw response is saved outside the repo (--responses-dir) before it is parsed, with
    a .meta.json. --from-response FILE reports offline; with --write it promotes that response,
    taking provenance from the .meta.json and refusing a mismatched prompt hash or roster data.
    The sidecar records the SHA-256 of people.json, planets.json and species.json. No API call.

Usage:
    python scripts/enrich_jedi.py --print-prompts                        # no API call
    python scripts/enrich_jedi.py --thinking-level low                   # one call; saves and reports
    python scripts/enrich_jedi.py --from-response FILE                   # offline re-analysis
    python scripts/enrich_jedi.py --from-response FILE --write           # promote to the seed
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import enrich_common as ec
import gemini_client
import jedi_roster

SEED = "dim_jedi"
PROMPT_VERSION = "jedi-v2"
HUMAN_SPECIES_ID = 1  # SWAPI species/1; an empty SWAPI species list means Human
# 17 entries are roughly 2-3k tokens of answer; thinking tokens count toward the cap too.
MAX_OUTPUT_TOKENS = 16384

FIELDS = ["jedi_id", "jedi_name", "species_id", "homeworld_sector_id", "rank", "primary_specialty",
          "secondary_specialty", "power_rating", "lightsaber_form", "notable_for", "canon_confidence"]

# Doc 08 Jedi prompt. The closing "Return ONLY a JSON array" line is dropped: the response schema
# enforces the shape.
SYSTEM_PROMPT = """You are a Star Wars loremaster. For each Jedi provided, assign gameplay attributes for a
simulation in which Jedi are deployed to respond to disturbances in the Force.

rank: padawan, knight, master, council_member, or grand_master. Use canonical standing during
the Clone Wars era.

primary_specialty and secondary_specialty, each one of:
  combat        - lightsaber mastery, direct confrontation, Sith engagement
  diplomacy     - negotiation, de-escalation, civil and political situations
  investigation - tracking, mystery, sensing disturbances, uncovering causes
  stealth       - infiltration, reconnaissance, covert operations

Assign these based on how the character actually behaves in canon, not on rank. Each of the four
specialties must be the primary_specialty for at least three Jedi. Where canon is thin for a
specialty, choose the Jedi whose canonical behavior best supports it, and lower their
canon_confidence to reflect the uncertainty. Do not default everyone to combat.
secondary_specialty may be null if the character is strongly one-dimensional.

power_rating: 1-10 relative to this roster. Reserve 10 for Yoda.

lightsaber_form: canonical form where known (Form I Shii-Cho through Form VII Juyo/Vaapad),
or the most plausible given their fighting style.

notable_for: 1-2 sentences on what this Jedi is known for.

canon_confidence: 0.0-1.0. Be honest about obscure characters."""

USER_PREAMBLE = (
    "Assign attributes to each Jedi below. Return exactly one entry per Jedi, in the same order, "
    "echoing each jedi_id exactly as given. The listed facts come from SWAPI; do not change them."
)


def batch_schema(ids):
    props = {
        "jedi_id": {"type": "string", "enum": list(ids)},
        "rank": {"type": "string", "enum": list(ec.RANKS)},
        "primary_specialty": {"type": "string", "enum": list(ec.SPECIALTIES)},
        # Nullable; the allowed values are checked in code rather than in the schema.
        "secondary_specialty": {"type": ["string", "null"]},
        "power_rating": {"type": "integer", "minimum": 1, "maximum": 10},
        "lightsaber_form": {"type": "string"},
        "notable_for": {"type": "string"},
        "canon_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    }
    item = {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}
    # No minItems/maxItems: Google rejects an exact array length together with the 17-value id enum
    # (probe steps 14, 17, 18). validate() enforces the exact count and id set instead.
    return {
        "type": "object",
        "properties": {"jedi": {"type": "array", "items": item}},
        "required": ["jedi"],
        "additionalProperties": False,
    }


def build_inputs(records, planets, species, sid_map):
    """One dict per roster person: the model's input facts plus the SWAPI-derived columns."""
    planet_by_id = {ec.url_id(p["url"]): p for p in planets}
    species_by_id = {ec.url_id(s["url"]): s for s in species}
    inputs = []
    for rec in records:
        home_id = ec.url_id(rec["homeworld"])
        # SWAPI leaves species empty for Humans, so an empty list means Human (species/1); doc 08.
        species_key = ec.url_id(rec["species"][0]) if rec.get("species") else HUMAN_SPECIES_ID
        species_name = species_by_id[species_key]["name"]
        inputs.append({
            "jedi_id": ec.slugify(rec["name"]),
            "jedi_name": rec["name"],
            "species_id": ec.slugify(species_name),
            "homeworld_sector_id": sid_map[home_id],
            "_facts": {"jedi_id": ec.slugify(rec["name"]), "name": rec["name"],
                       "birth_year": rec["birth_year"], "species": species_name,
                       "homeworld": planet_by_id[home_id]["name"]},
        })
    ids = [i["jedi_id"] for i in inputs]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"jedi_id collisions after slugging: {ids}")
    return inputs


def user_prompt(inputs):
    return f"{USER_PREAMBLE}\n\nJedi:\n{json.dumps([i['_facts'] for i in inputs], indent=2)}"


def validate(parsed, expected_ids):
    """Hard-check the response. Returns (rows by jedi_id, warnings). Raises ValueError on a hard failure."""
    rows = parsed.get("jedi") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError("response has no `jedi` array")
    ec.check_exact_ids(rows, expected_ids, "jedi_id", "Jedi")
    warnings = []
    for r in rows:
        jid = r["jedi_id"]
        if r.get("rank") not in ec.RANKS:
            raise ValueError(f"{jid}.rank = {r.get('rank')!r} is not allowed")
        if r.get("primary_specialty") not in ec.SPECIALTIES:
            raise ValueError(f"{jid}.primary_specialty = {r.get('primary_specialty')!r} is not allowed")
        sec = r.get("secondary_specialty")
        if sec is not None and sec not in ec.SPECIALTIES:
            raise ValueError(f"{jid}.secondary_specialty = {sec!r} is not allowed")
        power = r.get("power_rating")
        if isinstance(power, bool) or not isinstance(power, int) or not 1 <= power <= 10:
            raise ValueError(f"{jid}.power_rating = {power!r} is not an integer in 1-10")
        conf = r.get("canon_confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
            raise ValueError(f"{jid}.canon_confidence = {conf!r} is outside [0, 1]")
        for field in ("lightsaber_form", "notable_for"):
            if not isinstance(r.get(field), str) or not r[field].strip():
                raise ValueError(f"{jid}.{field} is empty")
        if sec == r["primary_specialty"]:
            warnings.append(f"{jid}: secondary_specialty repeats the primary")
        if power == 10 and jid != "yoda":
            warnings.append(f"{jid}: power_rating 10 is reserved for Yoda")
        if jid == "yoda" and power != 10:
            warnings.append(f"yoda: power_rating is {power}, expected 10")
    return {r["jedi_id"]: r for r in rows}, warnings


EXPECTED_ROWS = len(jedi_roster.ROSTER)  # 17
MIN_PER_SPECIALTY = 3
INPUT_FILES = ("people.json", "planets.json", "species.json")
REQUIRED_META = ("model", "thinking_level", "max_output_tokens", "prompt_version", "prompt_hash",
                 "requested_utc", "call", "calls", "jedi_ids")


def build_rows(inputs, by_id):
    rows = []
    for inp in inputs:
        m = by_id[inp["jedi_id"]]
        rows.append({
            "jedi_id": inp["jedi_id"], "jedi_name": inp["jedi_name"], "species_id": inp["species_id"],
            "homeworld_sector_id": inp["homeworld_sector_id"], "rank": m["rank"],
            "primary_specialty": m["primary_specialty"],
            "secondary_specialty": m["secondary_specialty"] or "",
            "power_rating": m["power_rating"], "lightsaber_form": m["lightsaber_form"].strip(),
            "notable_for": m["notable_for"].strip(), "canon_confidence": round(float(m["canon_confidence"]), 2),
        })
    return rows


def jedi_gate(rows):
    """The refusal gate (doc 08 / analyses check 3): exactly 17 rows and at least 3 Jedi per
    primary_specialty, or the seed is not written."""
    counts = Counter(r["primary_specialty"] for r in rows)
    failures = []
    if len(rows) != EXPECTED_ROWS:
        failures.append(f"row count is {len(rows)}, expected {EXPECTED_ROWS}")
    for s in ec.SPECIALTIES:
        if counts[s] < MIN_PER_SPECIALTY:
            failures.append(f"primary_specialty {s!r} has {counts[s]} Jedi, needs at least {MIN_PER_SPECIALTY}")
    return {"row_count": len(rows), "expected_rows": EXPECTED_ROWS, "min_per_specialty": MIN_PER_SPECIALTY,
            "specialty_counts": {s: counts[s] for s in ec.SPECIALTIES}, "failures": failures,
            "pass": not failures}


def gate_report(gate):
    lines = [f"review gate: {gate['row_count']} rows (must be {gate['expected_rows']}); primary_specialty "
             f"counts (each must be at least {gate['min_per_specialty']}):"]
    for s, n in gate["specialty_counts"].items():
        lines.append(f"    {s:<14} {n:>2}  {'PASS' if n >= gate['min_per_specialty'] else 'FAIL'}")
    lines.append("  OVERALL: PASS" if gate["pass"] else
                 "  OVERALL: FAIL. " + "; ".join(gate["failures"]) + ". A failing gate refuses to write: "
                 "regenerate, or --accept-failing-gate (recorded in the sidecar).")
    return "\n".join(lines)


def rows_table(rows):
    lines = [f"  {'jedi_id':<18} {'rank':<13} {'primary':<14} {'secondary':<14} {'power':>5} {'conf':>4}"]
    for r in rows:
        lines.append(f"  {r['jedi_id']:<18} {r['rank']:<13} {r['primary_specialty']:<14} "
                     f"{(r['secondary_specialty'] or '-'):<14} {r['power_rating']:>5} {r['canon_confidence']:>4.2f}")
    return "\n".join(lines)


def current_prompt_hash():
    return ec.prompt_hash(SYSTEM_PROMPT, USER_PREAMBLE, json.dumps(batch_schema(["<jedi_id>"]), sort_keys=True))


def load_saved_response(path, ids):
    """One saved raw API response (or a plain {"jedi": [...]} file) -> (rows by jedi_id, warnings, usage)."""
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
    elif isinstance(data, dict) and "jedi" in data:
        parsed = data
    else:
        raise SystemExit(f"{path} is neither a raw generateContent response nor a {{\"jedi\": [...]}} file")
    try:
        by_id, warnings = validate(parsed, ids)
    except ValueError as e:
        raise SystemExit(f"{path} failed validation: {e}")
    return by_id, warnings, usage


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


def promote(args, inputs, ids):
    """--from-response --write: turn a reviewed saved response into dim_jedi.csv and its sidecar.

    Provenance comes from the response's .meta.json. Refuses if the saved prompt hash or roster data
    differs from what would be sent today, if the gate fails (row count != 17, or a primary_specialty
    with fewer than 3 Jedi; --accept-failing-gate overrides and is recorded), or if the seed exists.
    """
    out_csv = args.out_dir / f"{SEED}.csv"
    if out_csv.exists() and not args.force:
        raise SystemExit(f"{out_csv} exists. Regeneration is a migration event (doc 08); use --force.")
    if len(args.from_response) != 1:
        raise SystemExit("Jedi enrichment is one call: give exactly one response file")
    path = args.from_response[0]
    meta = load_meta(path)
    if meta["calls"] != 1 or meta["call"] != 1:
        raise SystemExit("this response is not a single-call run")
    if args.thinking_level and args.thinking_level != meta["thinking_level"]:
        raise SystemExit(f"--thinking-level {args.thinking_level} conflicts with the saved response "
                         f"({meta['thinking_level']}); provenance comes from the saved response")
    if meta["prompt_version"] != PROMPT_VERSION:
        raise SystemExit(f"the saved prompt version {meta['prompt_version']!r} is not this script's "
                         f"{PROMPT_VERSION!r}")
    expected = current_prompt_hash()
    if meta["prompt_hash"] != expected:
        raise SystemExit(f"the saved prompt hash {meta['prompt_hash']} does not match the current prompt "
                         f"{expected}: the response came from a different prompt. Regenerate, then promote "
                         "that response.")
    data_checked = "user_prompt_hash" in meta
    if data_checked:
        if ec.prompt_hash(user_prompt(inputs)) != meta["user_prompt_hash"]:
            raise SystemExit("the roster data the model saw differs from the current snapshot. Regenerate, "
                             "then promote that response.")
    else:
        print("WARNING: the meta has no roster-data hash, so the data the model saw was not verified "
              "against the snapshot. The input files' SHA-256 are recorded in the sidecar.")
    by_id, warnings, usage = load_saved_response(path, ids)
    rows = build_rows(inputs, by_id)
    gate = jedi_gate(rows)
    overridden = (not gate["pass"]) and args.accept_failing_gate
    print(f"promoting {Path(path).name}: prompt hash {meta['prompt_hash']} matches the current prompt")
    if not gate["pass"] and not args.accept_failing_gate:
        print(gate_report(gate))
        raise SystemExit("\nThe review gate failed, so nothing was written. Regenerate, or rerun with "
                         "--accept-failing-gate to write anyway; the override is recorded in the sidecar.")

    cycles = ec.regeneration_cycles(args.out_dir, SEED)
    ec.write_csv(out_csv, FIELDS, rows)
    snap = Path(args.snapshot_dir)
    rec = ec.provenance_record(
        SEED, meta["model"], meta["thinking_level"], meta["prompt_version"], meta["prompt_hash"], cycles,
        len(rows), usage, warnings,
        {"primary_specialty_counts": gate["specialty_counts"], "structured_output": ec.STRUCTURED_OUTPUT,
         "max_output_tokens": meta["max_output_tokens"], "review_gate": gate,
         "accepted_failing_gate": overridden,
         "inputs_sha256": {name: ec.sha256_file(snap / name) for name in INPUT_FILES},
         "written_from": "reviewed saved response", "promoted_from": [Path(path).name],
         "promoted_from_sha256": {Path(path).name: ec.sha256_file(path)},
         "promoted_utc": ec.utc_stamp(), "prompt_hash_matched_current_prompt": True,
         "roster_data_matched_snapshot": data_checked,
         "response_run_was_trial": bool(meta.get("trial"))},
        generated_utc=ec.stamp_to_iso(meta["requested_utc"]))
    side = ec.write_sidecar(args.out_dir, SEED, rec)
    prov_md, _ = ec.write_provenance_md(args.out_dir)

    print(f"\nwrote {out_csv} ({len(rows)} rows), {side.name} and {prov_md.name}")
    print()
    print(gate_report(gate))
    if warnings:
        print(f"{len(warnings)} warning(s)")
        for w in warnings:
            print(f"  {w}")
    if overridden:
        print("\nWARNING: written despite a failing review gate (--accept-failing-gate). "
              "The override is recorded in the sidecar; do not accept this output.")
    print(f"\n{prov_md.name} was rebuilt from the sidecars; 'Reviewed by' stays <you> until "
          "rebuild_provenance.py --seed dim_jedi --reviewed-by NAME")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ec.add_common_args(parser, SEED)
    parser.add_argument("--accept-failing-gate", action="store_true",
                        help="with --write: write the seed even though the review gate failed; the "
                             "override is recorded in the provenance sidecar")
    parser.add_argument("--from-response", nargs="+", type=Path, metavar="FILE",
                        help="validate a saved raw response and report the gate; no API call. With "
                             "--write, promote it to the seed")
    parser.add_argument("--write", action="store_true",
                        help="with --from-response: write dim_jedi.csv and its sidecar from the reviewed "
                             "saved response, taking provenance from its .meta.json")
    parser.add_argument("--corrections", type=Path, default=ec.CORRECTIONS_FILE,
                        help="the recorded-corrections file; dim_jedi corrections are not supported, so "
                             "any row for dim_jedi is refused")
    args = parser.parse_args()

    if args.write and not args.from_response:
        raise SystemExit("--write needs --from-response: seeds are written from a reviewed saved "
                         "response (doc 08), never from a fresh call")
    if args.write and args.trial:
        raise SystemExit("--write and --trial cannot be combined")
    if not args.from_response:
        ec.require_thinking_level(args)
    args.max_output_tokens = args.max_output_tokens or MAX_OUTPUT_TOKENS
    if any(r["seed"] == "dim_jedi" for r in ec.read_corrections_file(args.corrections)):
        raise SystemExit("corrections to dim_jedi are not supported: the Jedi roster is checked by count "
                         "and specialty, not corrected by value")

    people = ec.read_snapshot("people", args.snapshot_dir)
    records, problems = jedi_roster.verify(people)
    if problems:
        raise SystemExit("roster does not match people.json:\n  " + "\n  ".join(problems))
    planets = ec.read_snapshot("planets", args.snapshot_dir)
    species = ec.read_snapshot("species", args.snapshot_dir)
    sid_map = ec.sector_ids(planets)
    inputs = build_inputs(records, planets, species, sid_map)
    ids = [i["jedi_id"] for i in inputs]

    ec.assert_no_anchors(**{"system prompt": SYSTEM_PROMPT, "user preamble": USER_PREAMBLE})
    schema = batch_schema(ids)
    hash_ = current_prompt_hash()

    if args.print_prompts:
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        print("\n=== USER PROMPT ===")
        print(user_prompt(inputs))
        print("\n=== RESPONSE SCHEMA ===")
        print(json.dumps(schema, indent=2))
        print()
        ec.print_request_settings(args.model, args.thinking_level, PROMPT_VERSION, hash_,
                                  args.max_output_tokens)
        print(f"roster:          {len(inputs)} people, one call")
        return 0

    if args.from_response and args.write:
        return promote(args, inputs, ids)

    if args.from_response:
        if len(args.from_response) != 1:
            raise SystemExit("Jedi enrichment is one call: give exactly one response file")
        by_id, warnings, _usage = load_saved_response(args.from_response[0], ids)
        rows = build_rows(inputs, by_id)
        print(f"from {Path(args.from_response[0]).name} (no API call, nothing written)\n")
        print(rows_table(rows))
        print()
        print(gate_report(jedi_gate(rows)))
        for w in warnings:
            print(f"  warning: {w}")
        return 0

    api_key = ec.get_api_key()
    responses_dir = ec.resolve_responses_dir(args.responses_dir)
    stamp = ec.utc_stamp()
    user_text = user_prompt(inputs)
    body = gemini_client.build_request(SYSTEM_PROMPT, user_text, schema, args.thinking_level,
                                       args.max_output_tokens)
    raw_path, meta_path = ec.call_paths(responses_dir, "jedi", args.thinking_level, 1, 1, stamp)
    ec.write_call_meta(meta_path, {
        "script": "enrich_jedi.py", "call": 1, "calls": 1, "model": args.model,
        "thinking_level": args.thinking_level, "max_output_tokens": args.max_output_tokens,
        "prompt_version": PROMPT_VERSION, "prompt_hash": hash_, "user_prompt_hash": ec.prompt_hash(user_text),
        "trial": args.trial, "jedi_ids": ids, "requested_utc": stamp,
        "note": "the raw response is the sibling .json file; analyse it with enrich_jedi.py "
                "--from-response, promote it with --from-response --write"})
    print(f"calling the model for {len(inputs)} Jedi ...", flush=True)
    try:
        parsed, usage = gemini_client.generate_json(api_key, args.model, body, raw_path)
    except gemini_client.GeminiError as e:
        hint = (f"\nThe response hit the output limit (--max-output-tokens {args.max_output_tokens}), "
                "and thinking tokens count toward it. Raise --max-output-tokens or lower "
                "--thinking-level, then rerun." if "MAX_TOKENS" in str(e) else "")
        saved = f"\nRaw response saved: {raw_path}" if raw_path.exists() else ""
        raise SystemExit(f"Gemini call failed: {e}{hint}{saved}")
    print(f"raw response saved: {raw_path}")
    try:
        by_id, warnings = validate(parsed, ids)
    except ValueError as e:
        raise SystemExit(f"validation failed: {e}\nRaw response saved: {raw_path}")
    rows = build_rows(inputs, by_id)
    print(f"\nusage {usage}\n")
    print(rows_table(rows))
    print()
    print(gate_report(jedi_gate(rows)))
    for w in warnings:
        print(f"  warning: {w}")
    print("\nnothing was written to the repo: a call never writes the seed (doc 08).")
    print(f're-analyse offline:  python scripts/enrich_jedi.py --from-response "{raw_path}"\n'
          f'once reviewed, promote:  python scripts/enrich_jedi.py --from-response "{raw_path}" --write')
    return 0


if __name__ == "__main__":
    sys.exit(main())
