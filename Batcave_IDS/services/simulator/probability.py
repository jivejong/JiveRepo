"""Success probability model and outcome resolution. Pure functions, no
I/O — docs/07's formula exactly, plus the `detected` outcome this phase
defines (see docs/07, "What makes an attempt detected?").

Schema note: docs/07's attempt event has exactly one `roll` field, not
two. The `detected` check uses its own internal roll (not persisted
separately) — detection evidence is instead carried by `noise_generated`,
which is already in the schema. `roll` on the persisted event is always
the success-probability roll.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from services.simulator.catalog import Stage, Technique, Villain

Outcome = Literal["success", "failure", "detected"]


def _normalized_stat_factor(technique: Technique, villain: Villain) -> float:
    """Weighted stat sum, stretched from its natural [0, 1] range (weights
    sum to 1.0, each stat/100 is in [0, 1]) to docs/07's [0.5, 1.5]."""
    raw = (
        technique.w_intelligence * villain.intelligence
        + technique.w_strength * villain.strength
        + technique.w_speed * villain.speed
        + technique.w_durability * villain.durability
        + technique.w_power * villain.power
        + technique.w_combat * villain.combat
    ) / 100
    return 0.5 + raw


def compute_probability(
    technique: Technique, villain: Villain, stage: Stage, technique_attempt_seq: int
) -> float:
    """docs/07's formula. `technique_attempt_seq` is 1 on the first attempt
    at this technique, so the retry_penalty exponent (technique_attempt_seq
    - 1) is 0 on that first attempt — no penalty before any repetition."""
    p = technique.base_success_rate
    p *= _normalized_stat_factor(technique, villain)
    p *= technique.retry_penalty ** (technique_attempt_seq - 1)
    p *= stage.difficulty_multiplier
    return max(0.02, min(0.95, p))


def compute_noise_generated(technique: Technique, technique_attempt_seq: int) -> int:
    """Escalates with repetition on the same technique — brute_force's own
    signature is literally "repeated" POST /login."""
    return technique.noise_level * technique_attempt_seq


# Detection calibration (Phase 3). The Phase 2 formula (noise_generated / 10)
# detected ~48% of all attempts — so loud that noise and the evasion features
# were nearly meaningless (everyone's caught). This version makes detection
# intelligence-aware, which is what makes "the most capable villain is the
# hardest to detect" true in the data rather than only in the narrative:
#   base ~5% per unit of noise, suppressed by up to EVASION_STRENGTH by a
#   high-intelligence villain's evasion, capped so nothing is ever certain.
# Tuned against the Monte Carlo in the git log for this file.
_DETECTION_PER_NOISE = 0.05
_EVASION_STRENGTH = 0.7
_DETECTION_CAP = 0.85


def compute_detection_probability(noise_generated: int, evasion: float) -> float:
    """`evasion` in [0, 1] (BehaviorProfile.evasion, from intelligence): 0 =
    loud, 1 = quiet. A careful high-INT villain moves quietly; a loud low-INT
    one grinding a noisy technique gets caught."""
    raw = _DETECTION_PER_NOISE * noise_generated
    return max(0.0, min(_DETECTION_CAP, raw * (1.0 - _EVASION_STRENGTH * evasion)))


@dataclass(frozen=True)
class AttemptResolution:
    computed_probability: float
    roll: float
    noise_generated: int
    outcome: Outcome


def resolve_attempt(
    technique: Technique,
    villain: Villain,
    stage: Stage,
    technique_attempt_seq: int,
    evasion: float = 0.0,
    rng: random.Random | None = None,
) -> AttemptResolution:
    """`detected` is orthogonal to success/failure, not a subtype of either
    — a loud attempt can succeed and still be flagged detected, matching how
    a real security team catches loud failures and loud successes alike.

    `evasion` (BehaviorProfile.evasion, from intelligence) suppresses
    detection; defaults to 0 (no evasion) for callers that don't supply it."""
    rng = rng or random.Random()

    computed_probability = compute_probability(technique, villain, stage, technique_attempt_seq)
    noise_generated = compute_noise_generated(technique, technique_attempt_seq)
    detection_probability = compute_detection_probability(noise_generated, evasion)

    detection_roll = rng.random()
    roll = rng.random()

    if detection_roll < detection_probability:
        outcome: Outcome = "detected"
    elif roll < computed_probability:
        outcome = "success"
    else:
        outcome = "failure"

    return AttemptResolution(
        computed_probability=computed_probability,
        roll=roll,
        noise_generated=noise_generated,
        outcome=outcome,
    )
