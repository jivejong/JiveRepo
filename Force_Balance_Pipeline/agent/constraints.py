"""The constraint layer: ordinary code that decides what a deployment may be, with no model involvement.

Implements doc 06 "Constraint layer". The model proposes; check_deploy and forced_stand_down decide what is legal. The same
layer is used for user-initiated deployments (Phase 7). Nothing here raises on bad input: a bad id or a missing value is a
rejection reason.
"""
from dataclasses import dataclass

from agent.signatures import SIGNATURES, SPECIALTY

SEVERITY_FLOOR = 4.40
MASTER_SEVERITY = 12.0
MAX_CONCURRENT_DEPLOYMENTS = 3
MAX_PARTY = 4

RANK_ORDER = {"padawan": 0, "knight": 1, "master": 2, "council_member": 3, "grand_master": 4}
MASTER_RANK = 2
UNKNOWN_RANK = -1

REASONS = (
    "severity_below_threshold",
    "signature_missing",
    "no_jedi",
    "duplicate_jedi",
    "party_too_large",
    "jedi_unavailable",
    "starship_unavailable",
    "deployment_capacity_reached",
    "insufficient_rank",
    "specialty_mismatch",
    "insufficient_capacity",
    "agent_exhausted_retries",
    "agent_no_decision",
)


@dataclass(frozen=True)
class Jedi:
    jedi_id: str
    rank: str
    primary_specialty: str
    secondary_specialty: "str | None"
    homeworld_region: "str | None"


@dataclass(frozen=True)
class Starship:
    starship_id: str
    hyperdrive_rating: float
    crew: int
    passengers: int


@dataclass(frozen=True)
class Incident:
    disturbance_id: str
    severity: "float | None"
    signature: "str | None"
    region: "str | None"


@dataclass(frozen=True)
class FleetState:
    open_jedi_ids: frozenset
    open_starship_ids: frozenset
    open_deployment_count: int


def forced_stand_down(incident):
    """The rejection reason that forces a stand-down for this incident, or None."""
    if incident.severity is None or incident.severity < SEVERITY_FLOOR:
        return "severity_below_threshold"
    if incident.signature is None or incident.signature not in SIGNATURES:
        return "signature_missing"
    return None


def _rank(jedi):
    return RANK_ORDER.get(jedi.rank, UNKNOWN_RANK)


def check_deploy(incident, jedi_ids, starship_id, jedi_by_id, ships_by_id, state):
    """Every reason the proposed deployment is illegal, in a fixed order, each at most once. An empty list means legal."""
    forced = forced_stand_down(incident)
    if forced is not None:
        return [forced]

    reasons = []
    distinct = set(jedi_ids)
    known = [jedi_by_id[j] for j in distinct if j in jedi_by_id]

    if not jedi_ids:
        reasons.append("no_jedi")
    if len(distinct) != len(jedi_ids):
        reasons.append("duplicate_jedi")
    if len(distinct) > MAX_PARTY:
        reasons.append("party_too_large")
    if any(j not in jedi_by_id or j in state.open_jedi_ids for j in distinct):
        reasons.append("jedi_unavailable")
    if starship_id not in ships_by_id or starship_id in state.open_starship_ids:
        reasons.append("starship_unavailable")
    if state.open_deployment_count >= MAX_CONCURRENT_DEPLOYMENTS:
        reasons.append("deployment_capacity_reached")
    if incident.severity > MASTER_SEVERITY and not any(_rank(j) >= MASTER_RANK for j in known):
        reasons.append("insufficient_rank")
    if incident.signature != "unclassified":
        wanted = SPECIALTY[incident.signature]
        if not any(wanted in (j.primary_specialty, j.secondary_specialty) for j in known):
            reasons.append("specialty_mismatch")
    ship = ships_by_id.get(starship_id)
    if ship is not None and len(distinct) > ship.crew + ship.passengers:
        reasons.append("insufficient_capacity")
    return reasons
