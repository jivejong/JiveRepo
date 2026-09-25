#!/usr/bin/env python3
"""Hand-maintained Jedi roster (doc 08): Jedi Order members in Episodes I-III who appear in SWAPI.

The roster is an explicit list of SWAPI person ids. Force-sensitivity is never inferred from SWAPI
fields (there is no such field), and nothing is invented: every entry is an existing SWAPI person.

Running this file checks the list against the snapshot in data/swapi_snapshot/people.json: every id
must exist, every name must match what is listed here, and there must be exactly 17 entries.

Usage:
    python scripts/jedi_roster.py
"""
import json
import sys
from pathlib import Path

PEOPLE_URL = "https://swapi.info/api/people/{}"
PEOPLE_JSON = Path(__file__).resolve().parents[1] / "data" / "swapi_snapshot" / "people.json"
EXPECTED_COUNT = 17

# (SWAPI people id, name exactly as SWAPI spells it). SWAPI spells "Ayla Secura"; the canonical
# spelling is "Aayla", and the data keeps SWAPI's spelling (doc 08).
ROSTER = [
    (10, "Obi-Wan Kenobi"),
    (11, "Anakin Skywalker"),
    (20, "Yoda"),
    (32, "Qui-Gon Jinn"),
    (46, "Ayla Secura"),
    (51, "Mace Windu"),
    (52, "Ki-Adi-Mundi"),
    (53, "Kit Fisto"),
    (54, "Eeth Koth"),
    (55, "Adi Gallia"),
    (56, "Saesee Tiin"),
    (57, "Yarael Poof"),
    (58, "Plo Koon"),
    (64, "Luminara Unduli"),
    (65, "Barriss Offee"),
    (74, "Jocasta Nu"),
    (78, "Shaak Ti"),
]

ROSTER_URLS = [PEOPLE_URL.format(i) for i, _ in ROSTER]


def load_people(path=PEOPLE_JSON):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify(people):
    """Return (records in roster order, problems). An empty problems list means the roster is valid."""
    problems = []
    if len(ROSTER) != EXPECTED_COUNT:
        problems.append(f"roster has {len(ROSTER)} entries, expected {EXPECTED_COUNT}")
    ids = [i for i, _ in ROSTER]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate ids in roster: {dupes}")

    by_url = {p["url"].rstrip("/"): p for p in people}
    records = []
    for pid, name in ROSTER:
        rec = by_url.get(PEOPLE_URL.format(pid))
        if rec is None:
            problems.append(f"people/{pid} ({name}) is not in people.json")
            continue
        if rec["name"] != name:
            problems.append(f"people/{pid}: roster says {name!r}, SWAPI says {rec['name']!r}")
        records.append(rec)
    return records, problems


def main():
    records, problems = verify(load_people())
    print(f"{'id':>3}  {'name':<20} homeworld")
    for (pid, _), rec in zip(ROSTER, records):
        note = "  <== planets/28 (uncharted)" if rec["homeworld"].rstrip("/").endswith("/28") else ""
        print(f"{pid:>3}  {rec['name']:<20} {rec['homeworld']}{note}")
    print(f"\n{len(records)} of {len(ROSTER)} roster entries found in people.json")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("roster OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
