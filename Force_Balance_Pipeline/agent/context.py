"""Build the disturbance context the model sees, and the inputs the constraint layer needs. Pure functions; no I/O.

Implements doc 06 "get_disturbance_context", "list_available_jedi" and "list_available_starships": the three tool results are
preloaded into the first user message as one JSON object (doc 06 "Runtime", context preload). The output round-trips through
json.dumps and json.loads unchanged.
"""
from agent.constraints import RANK_ORDER, UNKNOWN_RANK, FleetState, Incident, Jedi, Starship
from agent.dbsql import iso_z
from agent.distance import distance_units
from agent.signatures import SPECIALTY, evidence_kind

REPORT_CHANNELS = 3         # a report always carries all three inferred channels (gold_sector_reading)


def _stamp(value):
    return iso_z(value) if hasattr(value, "astimezone") else value


def _matches(jedi, specialty):
    return specialty is not None and specialty in (jedi.get("primary_specialty"), jedi.get("secondary_specialty"))


def build_context(ctx_row, jedi_rows, ship_rows):
    """The context dict for one incident: ctx_row is the row of queries.context, the others the availability queries' rows."""
    signature = ctx_row.get("signature")
    specialty = SPECIALTY.get(signature)
    is_report = bool(ctx_row.get("is_report_sourced"))
    region = ctx_row.get("region")

    jedi = []
    for row in jedi_rows:
        entry = {k: _stamp(v) for k, v in row.items()}
        entry["distance_from_sector"] = distance_units(row.get("homeworld_region"), region)
        jedi.append(entry)
    jedi.sort(key=lambda j: (not _matches(j, specialty), -RANK_ORDER.get(j.get("rank"), UNKNOWN_RANK), j["jedi_id"]))

    return {
        "disturbance_id": ctx_row["disturbance_id"],
        "detected_at": _stamp(ctx_row.get("detected_at")),
        "sector": {
            "sector_id": ctx_row.get("sector_id"),
            "sector_name": ctx_row.get("sector_name"),
            "system_name": ctx_row.get("system_name"),
            "region": region,
            "population": ctx_row.get("population"),
            "description": ctx_row.get("description"),
            "force_history": ctx_row.get("force_history"),
        },
        "signature": signature,
        "signature_specialty": specialty,
        "imbalance_score": ctx_row.get("imbalance_score"),
        "severity": ctx_row.get("severity"),
        "z_scores": {"midi": ctx_row.get("z_midi"), "kyber": ctx_row.get("z_kyber"), "dark": ctx_row.get("z_dark")},
        "sustained_scans": ctx_row.get("sustained_scans"),
        "channels_present": REPORT_CHANNELS if is_report else ctx_row.get("channels_present"),
        "is_report_sourced": is_report,
        "evidence_kind": evidence_kind(is_report, ctx_row.get("report_description")),
        "report_description": ctx_row.get("report_description"),
        "report_relevance": ctx_row.get("report_relevance"),
        "recent_history": {
            "disturbances_last_7d": ctx_row.get("disturbances_last_7d"),
            "deployments_last_7d": ctx_row.get("deployments_last_7d"),
            "last_signature": ctx_row.get("last_signature"),
        },
        "open_deployments": ctx_row.get("open_deployments"),
        "available_jedi": jedi,
        "available_starships": sorted((dict(s) for s in ship_rows), key=lambda s: s["starship_id"]),
    }


def constraint_inputs(context):
    """(Incident, jedi_by_id, ships_by_id, FleetState) for agent.constraints.check_deploy.

    jedi_by_id and ships_by_id hold ONLY the rows the availability queries returned, so a Jedi or starship that is unavailable
    (or unknown) is not in them and check_deploy reports it as jedi_unavailable or starship_unavailable. That is why the open
    id sets in FleetState are empty: availability is already applied by the queries, so only the count of open deployments
    is needed, for the galaxy-wide cap.
    """
    incident = Incident(context["disturbance_id"], context.get("severity"), context.get("signature"),
                        (context.get("sector") or {}).get("region"))
    jedi_by_id = {
        j["jedi_id"]: Jedi(j["jedi_id"], j.get("rank"), j.get("primary_specialty"), j.get("secondary_specialty"),
                           j.get("homeworld_region"))
        for j in context["available_jedi"]
    }
    ships_by_id = {
        s["starship_id"]: Starship(s["starship_id"], float(s["hyperdrive_rating"]), int(s["crew"]), int(s["passengers"]))
        for s in context["available_starships"]
    }
    state = FleetState(frozenset(), frozenset(), int(context.get("open_deployments") or 0))
    return incident, jedi_by_id, ships_by_id, state
