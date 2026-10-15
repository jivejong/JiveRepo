#!/usr/bin/env python3
"""Hand-maintained starship roster for dim_starship (doc 03 "dim_species, dim_starship"; doc 06 "list_available_starships").

The roster is an explicit list of SWAPI starship ids: ships that appear in films 4, 5 or 6 (Episodes I-III) and that are
Republic, Jedi or neutral civilian ships. Separatist, Sith and Trade Federation ships are never listed. Nothing is invented:
every entry is an existing SWAPI starship. The comment on each entry gives the reason it is on the list.

Running this file checks the list against data/swapi_snapshot/starships.json: every id must exist, every name must match
what is listed here, every ship must appear in film 4, 5 or 6, and there must be no duplicate.

Usage:
    python scripts/starship_roster.py
"""
import json
import sys
from pathlib import Path

STARSHIP_URL = "https://swapi.info/api/starships/{}"
STARSHIPS_JSON = Path(__file__).resolve().parents[1] / "data" / "swapi_snapshot" / "starships.json"
EPISODE_I_TO_III_FILMS = {4, 5, 6}

# (SWAPI starships id, name exactly as SWAPI spells it).
ROSTER = [
    (31, "Republic Cruiser"),              # Consular-class cruiser: the Republic ambassadors' ship (Episode I)
    (39, "Naboo fighter"),                 # N-1 starfighter: Naboo royal security, neutral (Episodes I, II)
    (40, "Naboo Royal Starship"),          # J-type 327 Nubian: the Queen's ship, neutral (Episode I)
    (43, "J-type diplomatic barge"),       # Naboo diplomatic transport, neutral (Episode II)
    (48, "Jedi starfighter"),              # Delta-7 Aethersprite: the Jedi Order's fighter (Episodes II, III)
    (49, "H-type Nubian yacht"),           # Naboo civilian yacht, neutral (Episode II)
    (52, "Republic Assault ship"),         # Acclamator I-class: Republic troop carrier (Episode II)
    (61, "Theta-class T-2c shuttle"),      # Republic shuttle (Episode III)
    (63, "Republic attack cruiser"),       # Venator-era Republic warship (Episode III)
    (64, "Naboo star skiff"),              # J-type star skiff: Naboo and Senate transport, neutral (Episode III)
    (65, "Jedi Interceptor"),              # Eta-2 Actis: the Jedi Order's interceptor (Episode III)
    (66, "arc-170"),                       # Republic reconnaissance starfighter (Episode III)
    (75, "V-wing"),                        # Alpha-3 Nimbus: Republic starfighter (Episode III)
]

ROSTER_URLS = [STARSHIP_URL.format(i) for i, _ in ROSTER]


def load_starships(path=STARSHIPS_JSON):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify(starships):
    """Return (records in roster order, problems). An empty problems list means the roster is valid."""
    problems = []
    ids = [i for i, _ in ROSTER]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate ids in roster: {dupes}")
    by_url = {s["url"].rstrip("/"): s for s in starships}
    records = []
    for ship_id, name in ROSTER:
        record = by_url.get(STARSHIP_URL.format(ship_id))
        if record is None:
            problems.append(f"starships/{ship_id} ({name}) is not in the snapshot")
            continue
        if record["name"] != name:
            problems.append(f"starships/{ship_id} is {record['name']!r} in the snapshot, not {name!r}")
        films = {int(f.rstrip("/").rsplit("/", 1)[-1]) for f in record.get("films", [])}
        if not films & EPISODE_I_TO_III_FILMS:
            problems.append(f"starships/{ship_id} ({name}) is in none of films 4, 5, 6")
        records.append(record)
    return records, problems


def main():
    records, problems = verify(load_starships())
    for problem in problems:
        print(problem)
    print(f"{len(records)} of {len(ROSTER)} roster entries found")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
