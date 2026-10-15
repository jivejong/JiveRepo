#!/usr/bin/env python3
"""Build warehouse/dbt/seeds/dim_starship.csv from the SWAPI snapshot and the roster (doc 03 "dim_starship").

Reads data/swapi_snapshot/starships.json and scripts/starship_roster.py; uses no network. The output is UTF-8 with LF line
endings and is byte-identical on every run.

Parsing rule for crew and passengers: strip commas; "a-b" means b (the upper bound); "n/a", "unknown" or empty means 0.
A ship whose hyperdrive_rating is not a number is excluded, and the exclusion is printed. mglt is empty when it is not a number.

Usage:
    python scripts/build_starship_seed.py
"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import starship_roster as roster  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "warehouse" / "dbt" / "seeds" / "dim_starship.csv"
COLUMNS = ["starship_id", "starship_name", "model", "starship_class", "hyperdrive_rating", "mglt", "crew", "passengers"]


def slug(name):
    """Lowercase; every run of non-alphanumeric characters becomes one underscore; no leading or trailing underscore."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def parse_count(text):
    """A SWAPI crew or passengers string as an int: commas stripped, a range takes its upper bound, no number means 0."""
    cleaned = (text or "").strip().replace(",", "")
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)
    m = re.fullmatch(r"(\d+)-(\d+)", cleaned)
    if m:
        return int(m.group(2))
    return 0


def parse_float(text):
    """A float, or None when the text is not a number."""
    try:
        return float(str(text).strip().replace(",", ""))
    except ValueError:
        return None


def parse_mglt(text):
    """An int, or None when the text is not a whole number."""
    cleaned = str(text).strip().replace(",", "")
    return int(cleaned) if re.fullmatch(r"\d+", cleaned) else None


def build_rows(records):
    """(rows, exclusions): rows are dicts in roster order; exclusions are (name, reason) pairs."""
    rows, exclusions = [], []
    for record in records:
        rating = parse_float(record["hyperdrive_rating"])
        if rating is None:
            exclusions.append((record["name"], f"hyperdrive_rating is {record['hyperdrive_rating']!r}, not a number"))
            continue
        mglt = parse_mglt(record["MGLT"])
        rows.append({
            "starship_id": slug(record["name"]),
            "starship_name": record["name"],
            "model": record["model"],
            "starship_class": record["starship_class"],
            "hyperdrive_rating": repr(rating),
            "mglt": "" if mglt is None else str(mglt),
            "crew": str(parse_count(record["crew"])),
            "passengers": str(parse_count(record["passengers"])),
        })
    return rows, exclusions


def render(rows):
    """The CSV text: UTF-8, LF line endings."""
    import io
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def main():
    records, problems = roster.verify(roster.load_starships())
    if problems:
        for problem in problems:
            print(problem)
        return 1
    rows, exclusions = build_rows(records)
    for name, reason in exclusions:
        print(f"excluded: {name}: {reason}")
    SEED.write_bytes(render(rows).encode("utf-8"))
    print(f"wrote {len(rows)} ships to {SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
