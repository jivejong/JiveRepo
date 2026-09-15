"""Loads the villain roster, technique catalog, and stage table from the seed
CSVs, and derives per-villain gating.

Gating is derived entirely from the seed rows (docs/07: "never hardcoded per
villain") — nothing in this module names a specific villain or technique.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "transform" / "seeds"


@dataclass(frozen=True)
class Villain:
    slug: str
    name: str
    archetype: str
    intelligence: int
    strength: int
    speed: int
    durability: int
    power: int
    combat: int


@dataclass(frozen=True)
class Technique:
    technique_id: str
    attack_id: str
    attack_name: str
    attack_tactic_id: str
    stage: int
    display_name: str
    min_intelligence: int
    min_power: int
    min_strength: int
    base_success_rate: float
    w_intelligence: float
    w_strength: float
    w_speed: float
    w_durability: float
    w_power: float
    w_combat: float
    noise_level: int
    retry_penalty: float
    observability: str
    detection_signature: str
    produces_traffic: bool


@dataclass(frozen=True)
class Stage:
    stage: int
    name: str
    attack_tactic_id: str
    attack_tactic_name: str
    difficulty_multiplier: float


def _to_bool(value: str) -> bool:
    return value.strip().lower() == "true"


@lru_cache(maxsize=1)
def load_villains() -> dict[str, Villain]:
    villains: dict[str, Villain] = {}
    with open(SEEDS_DIR / "villains.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            villains[row["slug"]] = Villain(
                slug=row["slug"],
                name=row["name"],
                archetype=row["archetype"],
                intelligence=int(row["intelligence"]),
                strength=int(row["strength"]),
                speed=int(row["speed"]),
                durability=int(row["durability"]),
                power=int(row["power"]),
                combat=int(row["combat"]),
            )
    return villains


@lru_cache(maxsize=1)
def load_techniques() -> tuple[Technique, ...]:
    techniques: list[Technique] = []
    with open(SEEDS_DIR / "techniques.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            techniques.append(
                Technique(
                    technique_id=row["technique_id"],
                    attack_id=row["attack_id"],
                    attack_name=row["attack_name"],
                    attack_tactic_id=row["attack_tactic_id"],
                    stage=int(row["stage"]),
                    display_name=row["display_name"],
                    min_intelligence=int(row["min_intelligence"]),
                    min_power=int(row["min_power"]),
                    min_strength=int(row["min_strength"]),
                    base_success_rate=float(row["base_success_rate"]),
                    w_intelligence=float(row["w_intelligence"]),
                    w_strength=float(row["w_strength"]),
                    w_speed=float(row["w_speed"]),
                    w_durability=float(row["w_durability"]),
                    w_power=float(row["w_power"]),
                    w_combat=float(row["w_combat"]),
                    noise_level=int(row["noise_level"]),
                    retry_penalty=float(row["retry_penalty"]),
                    observability=row["observability"],
                    detection_signature=row["detection_signature"],
                    produces_traffic=_to_bool(row["produces_traffic"]),
                )
            )
    return tuple(techniques)


@lru_cache(maxsize=1)
def load_stages() -> dict[int, Stage]:
    stages: dict[int, Stage] = {}
    with open(SEEDS_DIR / "stages.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stage_num = int(row["stage"])
            stages[stage_num] = Stage(
                stage=stage_num,
                name=row["name"],
                attack_tactic_id=row["attack_tactic_id"],
                attack_tactic_name=row["attack_tactic_name"],
                difficulty_multiplier=float(row["difficulty_multiplier"]),
            )
    return stages


def gated_techniques(villain: Villain, stage: int) -> list[Technique]:
    """Every technique in `stage` whose gates `villain`'s stats satisfy.

    `min_intelligence` is the primary gate. `min_power`/`min_strength` are
    secondary gates — 0 means unused, which every non-negative stat trivially
    satisfies. Stage-reachability (whether the villain can ever get to this
    stage) is the stage machine's concern, not this function's: this only
    answers "if standing at this stage, which techniques are available."
    """
    return [
        t
        for t in load_techniques()
        if t.stage == stage
        and villain.intelligence >= t.min_intelligence
        and villain.power >= t.min_power
        and villain.strength >= t.min_strength
    ]
