"""Build agent/fixtures/<name>.json from agent/fixtures/spec.py and the seeds (doc 06 "Evaluation fixtures").

Network-free and idempotent: the output is a pure function of the spec and warehouse/dbt/seeds/dim_sector.csv, dim_jedi.csv and
dim_starship.csv. Each context is built with agent.context.build_context from rows assembled here. classify() implements doc 03
"Signature classification" in table order, and a fixture whose z-score template does not classify as its stated signature is
refused (the check is skipped when the signature is None).

Usage:
    python -m agent.fixtures.build            (from the project root)
"""
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.config import ROOT
from agent.context import build_context
from agent.fixtures.spec import FIXTURES

SEEDS = ROOT / "warehouse" / "dbt" / "seeds"
OUT_DIR = Path(__file__).resolve().parent
DETECTED_AT = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
SEVERITY_FLOOR = 4.40
EMERGENCY_MIN_SEVERITY = 6.33       # the smallest severity an emergency incident can have (doc 06, severity bands)

# (z_midi, z_kyber, z_dark, channels_present) per signature; unclassified is also the template for a missing signature
Z_TEMPLATES = {
    "sith_presence": (0.4, -2.8, 5.2, 3),
    "dark_adept": (2.1, 0.3, 4.0, 3),
    "nexus_awakening": (3.0, 3.2, 0.4, 3),
    "force_drain": (-3.1, -2.9, 0.6, 3),
    "kyber_cache": (0.5, 4.1, 0.2, 3),
    "civil_unrest": (0.6, 0.4, 2.2, 3),
    "veiled_presence": (None, None, 3.0, 1),
    "unclassified": (1.2, -1.6, 1.9, 3),
}

RANK_ORDER_NAMES = ("padawan", "knight", "master", "council_member", "grand_master")


def _gt(value, bound):
    return value is not None and value > bound


def _lt(value, bound):
    return value is not None and value < bound


def _abs_lt(value, bound):
    return value is not None and abs(value) < bound


def classify(z_midi, z_kyber, z_dark, population, channels_present):
    """doc 03's signature table, evaluated in order; the first match wins. A missing value never satisfies a condition,
    except the one COALESCE(ABS(z_midi), 0) < 1.0 of veiled_presence."""
    if _gt(z_dark, 2.5) and _lt(z_kyber, -1.0):
        return "sith_presence"
    if _gt(z_dark, 2.0) and _gt(z_midi, 1.5):
        return "dark_adept"
    if _gt(z_midi, 2.0) and _gt(z_kyber, 2.0) and _abs_lt(z_dark, 1.5):
        return "nexus_awakening"
    if _lt(z_midi, -2.0) and _lt(z_kyber, -2.0):
        return "force_drain"
    if _gt(z_kyber, 2.5) and _abs_lt(z_midi, 1.5) and _abs_lt(z_dark, 1.5):
        return "kyber_cache"
    if _gt(z_dark, 1.5) and _gt(population, 1e9) and _abs_lt(z_kyber, 1.5):
        return "civil_unrest"
    if (abs(z_midi) if z_midi is not None else 0) < 1.0 and _gt(z_dark, 2.0) and _lt(channels_present, 3):
        return "veiled_presence"
    return "unclassified"


def severity(imbalance, population):
    """doc 03: imbalance_score * (1 + LOG10(GREATEST(population, 10)) / 10); an empty population counts as 10."""
    return imbalance * (1 + math.log10(max(population or 10, 10)) / 10)


def _read(name):
    with open(SEEDS / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _number(text, kind=float):
    return kind(text) if text.strip() else None


def load_seeds():
    sectors = {r["sector_id"]: r for r in _read("dim_sector.csv")}
    region_of = {sid: r["region"] for sid, r in sectors.items()}
    jedi = []
    for r in _read("dim_jedi.csv"):
        jedi.append({"jedi_id": r["jedi_id"], "jedi_name": r["jedi_name"], "rank": r["rank"],
                     "primary_specialty": r["primary_specialty"], "secondary_specialty": r["secondary_specialty"] or None,
                     "power_rating": int(r["power_rating"]), "lightsaber_form": r["lightsaber_form"],
                     "notable_for": r["notable_for"], "homeworld_region": region_of.get(r["homeworld_sector_id"])})
    ships = []
    for r in _read("dim_starship.csv"):
        ships.append({"starship_id": r["starship_id"], "starship_name": r["starship_name"], "starship_class": r["starship_class"],
                      "hyperdrive_rating": float(r["hyperdrive_rating"]), "mglt": _number(r["mglt"], int),
                      "crew": int(r["crew"]), "passengers": int(r["passengers"])})
    return sectors, jedi, ships


def resolve_sector(name, sectors):
    """The sector_id for a name or a shorthand (LOWPOP, NULLPOP)."""
    if name == "LOWPOP":
        return min((float(r["population"]), sid) for sid, r in sectors.items() if r["population"].strip())[1]
    if name == "NULLPOP":
        return sorted(sid for sid, r in sectors.items() if not r["population"].strip())[0]
    if name not in sectors:
        raise ValueError(f"sector {name!r} is not in dim_sector.csv")
    return name


def _filter_jedi(mode, jedi):
    """(jedi list, note or None)."""
    if mode is None:
        return list(jedi), None
    if mode == "no_combat":
        return [j for j in jedi if "combat" not in (j["primary_specialty"], j["secondary_specialty"])], None
    if mode == "knight_padawan":
        return [j for j in jedi if j["rank"] in ("knight", "padawan")], None
    if mode == "stealth_secondary_only":
        chosen = [j for j in jedi if j["secondary_specialty"] == "stealth" and j["primary_specialty"] != "stealth"]
        if chosen:
            return chosen, "Available Jedi: those with stealth as secondary and not primary."
        return ([j for j in jedi if j["primary_specialty"] == "stealth" and j["secondary_specialty"] != "stealth"],
                "No Jedi has stealth as secondary only; available Jedi: those with stealth as primary only.")
    raise ValueError(f"unknown jedi filter {mode!r}")


def _filter_ships(mode, ships):
    if mode is None:
        return list(ships)
    if mode == "one_seat":
        return [s for s in ships if s["crew"] + s["passengers"] == 1]
    if mode == "none":
        return []
    raise ValueError(f"unknown ships filter {mode!r}")


def build_fixture(spec, seeds):
    sectors, all_jedi, all_ships = seeds
    sector_id = resolve_sector(spec["sector"], sectors)
    sector = sectors[sector_id]
    population = _number(sector["population"])
    signature = spec["signature"]
    z_midi, z_kyber, z_dark, channels = Z_TEMPLATES[signature or "unclassified"]
    if signature is not None and classify(z_midi, z_kyber, z_dark, population, channels) != signature:
        raise ValueError(f"{spec['name']}: the z-score template does not classify as {signature!r} on {sector_id!r} "
                         f"(population {population})")
    sev = severity(spec["imbalance"], population)
    if spec.get("user_path"):
        if not sev < SEVERITY_FLOOR:
            raise ValueError(f"{spec['name']}: severity {sev} is not below {SEVERITY_FLOOR}")
    elif sev < EMERGENCY_MIN_SEVERITY:
        raise ValueError(f"{spec['name']}: severity {sev} is below {EMERGENCY_MIN_SEVERITY}")

    evidence = spec["evidence"]
    is_report = evidence == "report"
    with_description = evidence in ("report", "probe_with_report")
    recent = spec.get("recent", (0, 0, None))
    jedi, note = _filter_jedi(spec.get("jedi"), all_jedi)
    row = {
        "disturbance_id": "fixture-" + spec["name"], "sector_id": sector_id, "detected_at": DETECTED_AT,
        "scan_id": None if is_report else "fixture-scan", "imbalance_score": spec["imbalance"],
        "z_midi": z_midi, "z_kyber": z_kyber, "z_dark": z_dark, "signature": signature, "severity": sev,
        "is_report_sourced": is_report,
        "report_description": spec["description"] if with_description else None,
        "report_relevance": spec["relevance"] if with_description else None,
        "sustained_scans": spec.get("sustained", 1 if is_report else 3),
        "sector_name": sector["sector_name"], "system_name": sector["system_name"], "region": sector["region"],
        "population": population, "description": sector["description"], "force_history": sector["force_history"],
        "channels_present": None if is_report else channels,
        "disturbances_last_7d": recent[0], "deployments_last_7d": recent[1], "last_signature": recent[2],
        "open_deployments": spec.get("open_deployments", 0),
    }
    context = build_context(row, jedi, _filter_ships(spec.get("ships"), all_ships))
    notes = [note] if note else []
    if spec.get("user_path"):
        notes.append("Reachable only through the Phase 7 user path: no emergency incident has a severity this low.")
    return {"name": spec["name"], "expect": spec["expect"], "context": context, "notes": " ".join(notes)}


def render(fixture):
    """The file text: sorted keys, 2-space indent, ASCII, LF."""
    return json.dumps(fixture, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def build_all():
    """{name: file text} for every fixture in the spec."""
    seeds = load_seeds()
    return {spec["name"]: render(build_fixture(spec, seeds)) for spec in FIXTURES}


def write_all(out_dir=OUT_DIR):
    texts = build_all()
    for name, text in texts.items():
        Path(out_dir, name + ".json").write_bytes(text.encode("ascii"))
    return sorted(texts)


def main():
    names = write_all()
    seeds = load_seeds()
    for spec in FIXTURES:
        fixture = build_fixture(spec, seeds)
        c = fixture["context"]
        print(f"{spec['name']}: sector {c['sector']['sector_id']}, severity {c['severity']:.4f}, "
              f"{len(c['available_jedi'])} Jedi, {len(c['available_starships'])} ships")
    print(f"wrote {len(names)} fixtures to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
