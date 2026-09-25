#!/usr/bin/env python3
"""Jedi enrichment (doc 08): the 17-person roster -> warehouse/dbt/seeds/dim_jedi.csv.

Build-time only. The reviewed output is committed and frozen; reruns do not reproduce it, and
regeneration is a migration event (doc 08), so an existing seed is only overwritten with --force.

Behavior:
  * The roster comes from scripts/jedi_roster.py, which is verified against people.json first.
    Nothing is invented: enrichment only adds attributes to existing SWAPI rows.
  * SWAPI fields (name, species, homeworld) are never model-generated. homeworld_sector_id uses the
    same id mapping as dim_sector, so Yoda and Qui-Gon Jinn map to "uncharted".
  * The response schema bounds rank, specialties, power_rating and canon_confidence.
  * Prompt text never names a review anchor (doc 08 rule 5).
  * The thinking level is a required flag and is recorded in a provenance sidecar next to the CSV.
  * The primary_specialty distribution is printed. Fewer than three per specialty is a warning:
    adjust the prompt and regenerate (doc 08), because a lopsided roster leaves some signatures
    without a valid responder.

Usage:
    python scripts/enrich_jedi.py --print-prompts             # no API call
    python scripts/enrich_jedi.py --thinking-level low --trial      # calls the model, writes nothing
    python scripts/enrich_jedi.py --thinking-level low
"""
import argparse
import json
import sys
from collections import Counter

import enrich_common as ec
import gemini_client
import jedi_roster

SEED = "dim_jedi"
PROMPT_VERSION = "jedi-v1"
HUMAN_SPECIES_ID = 1  # SWAPI species/1; an empty SWAPI species list means Human

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

Assign these based on how the character actually behaves in canon, not on rank. Distribute
across all four specialties — do not default everyone to combat. secondary_specialty may be
null if the character is strongly one-dimensional.

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
    return {
        "type": "object",
        "properties": {"jedi": {"type": "array", "items": item,
                                "minItems": len(ids), "maxItems": len(ids)}},
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
    got = [r.get("jedi_id") for r in rows]
    if sorted(map(str, got)) != sorted(expected_ids):
        raise ValueError(f"returned ids {got} do not match requested ids {list(expected_ids)}")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ec.add_common_args(parser, SEED)
    args = parser.parse_args()
    ec.require_thinking_level(args)

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
    hash_ = ec.prompt_hash(SYSTEM_PROMPT, USER_PREAMBLE, json.dumps(batch_schema(["<jedi_id>"]), sort_keys=True))

    if args.print_prompts:
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        print("\n=== USER PROMPT ===")
        print(user_prompt(inputs))
        print("\n=== RESPONSE SCHEMA ===")
        print(json.dumps(schema, indent=2))
        print()
        ec.print_request_settings(args.model, args.thinking_level, PROMPT_VERSION, hash_,
                                  args.structured_output)
        print(f"roster:          {len(inputs)} people, one call")
        return 0

    out_csv = args.out_dir / f"{SEED}.csv"
    if out_csv.exists() and not args.force and not args.trial:
        raise SystemExit(f"{out_csv} exists. Regeneration is a migration event (doc 08); use --force.")

    api_key = ec.get_api_key()
    body = gemini_client.build_request(SYSTEM_PROMPT, user_prompt(inputs), schema, args.thinking_level,
                                       args.structured_output)
    print(f"calling the model for {len(inputs)} Jedi ...", flush=True)
    try:
        parsed, usage = gemini_client.generate_json(api_key, args.model, body)
    except gemini_client.GeminiError as e:
        raise SystemExit(f"Gemini call failed: {e}\nNothing was written.")
    try:
        by_id, warnings = validate(parsed, ids)
    except ValueError as e:
        raise SystemExit(f"validation failed: {e}\nNothing was written.")

    dist = Counter(by_id[i]["primary_specialty"] for i in ids)
    for s in ec.SPECIALTIES:
        if dist[s] < 3:
            warnings.append(f"primary_specialty {s!r} has {dist[s]} Jedi; at least 3 are required (doc 08)")

    if args.trial:
        print(json.dumps(list(by_id.values()), indent=2))
        print(f"\ntrial: {len(warnings)} warning(s); usage {usage}; nothing written")
        for w in warnings:
            print(f"  warning: {w}")
        return 0

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

    cycles = ec.regeneration_cycles(args.out_dir, SEED)
    ec.write_csv(out_csv, FIELDS, rows)
    rec = ec.provenance_record(SEED, args.model, args.thinking_level, PROMPT_VERSION, hash_, cycles,
                               len(rows), usage, warnings,
                               {"primary_specialty_counts": dict(dist),
                                "structured_output": args.structured_output})
    side = ec.write_sidecar(args.out_dir, SEED, rec)

    print(f"\nwrote {out_csv} ({len(rows)} rows) and {side.name}")
    print("primary_specialty distribution (at least 3 each is required):")
    for s in ec.SPECIALTIES:
        print(f"  {s:<14} {dist[s]}")
    print(f"{len(warnings)} warning(s)")
    for w in warnings:
        print(f"  {w}")
    print("\nprovenance row for seeds/ENRICHMENT_PROVENANCE.md:")
    print(ec.provenance_table_row(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
