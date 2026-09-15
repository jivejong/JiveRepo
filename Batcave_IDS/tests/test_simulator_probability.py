"""docs/07's probability formula and this phase's detected-outcome logic.
Uses a scripted fake RNG rather than real randomness, so each branch of
resolve_attempt's outcome logic is exercised deterministically."""

import pytest

from services.simulator.catalog import Stage, Technique, Villain, load_stages, load_techniques
from services.simulator.probability import (
    compute_detection_probability,
    compute_noise_generated,
    compute_probability,
    resolve_attempt,
)


class _ScriptedRandom:
    """Returns queued values from .random() in order, one call per value."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0)


@pytest.fixture
def brute_force() -> Technique:
    return next(t for t in load_techniques() if t.technique_id == "brute_force")


@pytest.fixture
def stage2() -> Stage:
    return load_stages()[2]


@pytest.fixture
def bane() -> Villain:
    from services.simulator.catalog import load_villains

    return load_villains()["60-bane"]


def test_probability_clamped_to_bounds(brute_force, stage2, bane):
    p = compute_probability(brute_force, bane, stage2, technique_attempt_seq=1)
    assert 0.02 <= p <= 0.95


def test_retry_penalty_only_applies_from_the_second_attempt(brute_force, stage2, bane):
    # technique_attempt_seq=1 -> exponent 0 -> retry_penalty**0 == 1, no penalty.
    # retry_penalty < 1, so any later attempt must be less or equally likely.
    p1 = compute_probability(brute_force, bane, stage2, technique_attempt_seq=1)
    p2 = compute_probability(brute_force, bane, stage2, technique_attempt_seq=2)
    assert p2 < p1


def test_noise_escalates_with_repetition(brute_force):
    n1 = compute_noise_generated(brute_force, technique_attempt_seq=1)
    n2 = compute_noise_generated(brute_force, technique_attempt_seq=2)
    assert n2 > n1


def test_detection_takes_precedence_over_success(brute_force, stage2, bane):
    # detection_roll=0.0 always fires (detection_probability > 0 for any
    # noise_generated > 0); success roll is irrelevant once detected wins.
    rng = _ScriptedRandom([0.0, 0.0])
    result = resolve_attempt(brute_force, bane, stage2, technique_attempt_seq=3, rng=rng)
    assert result.outcome == "detected"


def test_success_when_not_detected_and_roll_beats_probability(brute_force, stage2, bane):
    # detection_roll=0.99 (unlikely to fire), roll=0.0 (always beats any p > 0)
    rng = _ScriptedRandom([0.99, 0.0])
    result = resolve_attempt(brute_force, bane, stage2, technique_attempt_seq=1, rng=rng)
    assert result.outcome == "success"


def test_failure_when_not_detected_and_roll_loses(brute_force, stage2, bane):
    # detection_roll=0.99 (unlikely to fire), roll=0.99 (beats almost no p)
    rng = _ScriptedRandom([0.99, 0.99])
    result = resolve_attempt(brute_force, bane, stage2, technique_attempt_seq=1, rng=rng)
    assert result.outcome == "failure"


def test_evasion_suppresses_detection():
    # Same noise, more evasion -> lower detection probability.
    loud = compute_detection_probability(noise_generated=5, evasion=0.0)
    quiet = compute_detection_probability(noise_generated=5, evasion=1.0)
    assert quiet < loud
    assert quiet >= 0.0


def test_detection_rises_with_noise():
    low = compute_detection_probability(noise_generated=1, evasion=0.5)
    high = compute_detection_probability(noise_generated=10, evasion=0.5)
    assert high > low


def test_low_noise_single_attempt_is_quiet_baseline():
    # A single low-noise (noise_level=1) attempt by an average villain should
    # be a low baseline, not a coin flip — otherwise everyone is loud.
    p = compute_detection_probability(noise_generated=1, evasion=0.5)
    assert p < 0.10


def test_high_intelligence_villain_stays_quiet_even_on_loud_technique():
    # noise_generated=5 (a loud technique), full evasion: still well under the
    # loud/dumb villain's rate. This is "most capable is hardest to detect."
    quiet = compute_detection_probability(noise_generated=5, evasion=1.0)
    loud = compute_detection_probability(noise_generated=5, evasion=0.19)  # Croc-ish
    assert quiet < loud


def test_resolution_carries_computed_probability_and_noise():
    from services.simulator.catalog import load_villains

    technique = next(t for t in load_techniques() if t.technique_id == "brute_force")
    stage = load_stages()[2]
    villain = load_villains()["60-bane"]
    rng = _ScriptedRandom([0.99, 0.99])
    result = resolve_attempt(technique, villain, stage, technique_attempt_seq=2, rng=rng)
    assert result.computed_probability == compute_probability(technique, villain, stage, 2)
    assert result.noise_generated == compute_noise_generated(technique, 2)
