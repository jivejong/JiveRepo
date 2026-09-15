"""Layer 1 of the two-layer behavior model (docs/03): the six powerstats,
mapped to continuous traffic parameters. **Derived, never hardcoded per
villain** — the mapping itself is the artifact, so any character in the
dataset works, not just the twelve.

docs/03 Layer 1 mapping:
  intelligence -> targeting precision + evasion (UA rotation, jitter, low waste)
  speed        -> requests per minute
  strength     -> body bytes + repetition on brute-force requests
  durability   -> session duration + persistence after errors
  power        -> payload complexity + growth (body_bytes_trend)
  combat       -> escalation aggressiveness (tier climb, willingness to hit tier 4)

Every parameter here is a Phase 3 calibration surface — the coefficients are
tuned against the separability harness, not derived from first principles.
The tuning history lives in the git log for this file.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.simulator.catalog import Villain


def _unit(stat: int) -> float:
    """A powerstat (0-100) as a [0, 1] fraction."""
    return stat / 100.0


@dataclass(frozen=True)
class BehaviorProfile:
    # Pacing (speed): requests per minute, and the per-request timing jitter
    # (intelligence) that drives inter_request_stddev_ms.
    requests_per_min: float
    jitter: float  # 0 = metronomic, 1 = highly irregular spacing

    # Persistence (durability): how long the session runs and how many
    # failures it tolerates before giving up.
    session_duration_s: float
    failure_tolerance: int

    # Payload (strength + power): body sizes, repetition, and whether size
    # trends upward over the session (body_bytes_trend).
    mean_body_bytes: int
    body_repetition: int
    body_growth: float  # 0 = flat, 1 = strongly increasing

    # Escalation (combat): how aggressively it climbs path tiers.
    tier_aggression: float  # 0 = slow crawl, 1 = straight to tier 4

    # Evasion / precision (intelligence): evasion suppresses detection;
    # targeting_precision inversely drives wasted_request_ratio; the rotation
    # flags feed distinct_source_ips / distinct_user_agents when a signature
    # turns them on.
    evasion: float  # [0, 1], 0 = loud, 1 = quiet
    targeting_precision: float  # [0, 1], 1 = no wasted requests
    rotates_user_agent: bool

    @classmethod
    def from_villain(cls, v: Villain) -> BehaviorProfile:
        intel = _unit(v.intelligence)
        return cls(
            # speed 12 -> ~6/min (slow drip), speed 35 -> ~25/min. Linear-ish
            # with a floor so even the slowest villain makes progress.
            requests_per_min=3.0 + _unit(v.speed) * 60.0,
            # High intelligence adds deliberate timing jitter (docs/03).
            jitter=intel,
            # Durability 14 (Riddler/Two-Face) -> short; 90 (Croc) -> long.
            session_duration_s=20.0 + _unit(v.durability) * 100.0,
            # Failures tolerated before giving up. Quadratic in durability so
            # the very persistent pull far ahead: durability 14 -> ~1
            # (Riddler/Two-Face stop on first error), durability 90 -> ~25
            # (Croc "never stops", producing the highest request count -
            # docs/03). Derived from the stat, not a per-villain override.
            failure_tolerance=1 + int(_unit(v.durability) ** 2 * 30),
            # Strength drives body size + repetition on brute-force.
            mean_body_bytes=64 + int(_unit(v.strength) * 4000),
            body_repetition=1 + int(_unit(v.strength) * 5),
            # Power drives upward body-size trend (Poison Ivy, power 100).
            body_growth=_unit(v.power),
            # Combat drives tier-climb aggression (Bane 95, Ra's 100).
            tier_aggression=_unit(v.combat),
            # Intelligence-driven evasion: this is what makes the most capable
            # villain the hardest to detect true in the data, not just the
            # narrative. Suppresses detection in probability.compute_detection.
            evasion=intel,
            targeting_precision=intel,
            # High-intelligence villains rotate their user agent as evasion.
            rotates_user_agent=v.intelligence >= 80,
        )
