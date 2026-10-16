"""Region rings, distance units, ETA and the release time of a deployment.

Implements doc 06 "ETA": distance_units = abs(ring(a) - ring(b)) + 1, eta_hours = units * hyperdrive_rating / 10.0,
released_at = decided_at + eta_hours + MISSION_HOURS.
"""
from datetime import timedelta

RINGS = {
    "Core Worlds": 0,
    "Colonies": 1,
    "Inner Rim": 2,
    "Expansion Region": 2,
    "Mid Rim": 3,
    "Outer Rim": 4,
    "Wild Space": 5,
    "Unknown Regions": 5,
}
MISSING_REGION_RING = 5
MISSION_HOURS = 24


def ring(region):
    """The ring of a region; an unknown or missing region counts as MISSING_REGION_RING."""
    return RINGS.get(region, MISSING_REGION_RING)


def distance_units(region_a, region_b):
    return abs(ring(region_a) - ring(region_b)) + 1


def eta_hours(units, hyperdrive_rating):
    """Not rounded."""
    return units * hyperdrive_rating / 10.0


def released_at(decided_at, eta):
    """When the Jedi and the starship of a deploy decision become available again."""
    return decided_at + timedelta(hours=eta + MISSION_HOURS)
