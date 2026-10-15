"""Every SQL statement the agent runs, one function each, returning (sql, params).

Implements doc 06 "Runtime" (cycle order: repair, poll, write, mark) and "get_disturbance_context", "list_available_jedi" and
"list_available_starships". The SQL rule: data values are ONLY named parameters (:name). The only text substituted into a
statement is a table name, taken from agent.config.tables(), which validates each name. Parameters are
{name: (value, type_name)}; a None value is a typed NULL.
"""
import json
from pathlib import Path

from agent.config import tables

CREATE_DEPLOYMENT_SQL = Path(__file__).resolve().parent / "sql" / "create_deployment.sql"


def _render(template, target):
    """Replace {disturbance}, {deployment}, ... with the validated table names of the target. Nothing else is substituted."""
    names = tables(target)
    for key, name in names.items():
        template = template.replace("{" + key + "}", name)
    if "{" in template or "}" in template:
        raise ValueError("a placeholder was left in the statement")
    return template


def create_deployment(target):
    return _render(CREATE_DEPLOYMENT_SQL.read_text(encoding="utf-8"), target), {}


def repair(target):
    sql = """UPDATE {disturbance} SET agent_processed = true
WHERE agent_processed = false AND disturbance_id IN (SELECT disturbance_id FROM {deployment})"""
    return _render(sql, target), {}


def poll(target, epoch, limit):
    sql = """SELECT d.disturbance_id FROM {disturbance} d
WHERE d.agent_processed = false AND d.detected_at >= :epoch
  AND NOT EXISTS (SELECT 1 FROM {deployment} p WHERE p.disturbance_id = d.disturbance_id)
ORDER BY d.detected_at LIMIT :limit"""
    return _render(sql, target), {"epoch": (epoch, "TIMESTAMP"), "limit": (limit, "INT")}


def context(target, disturbance_id):
    sql = """SELECT d.disturbance_id, d.sector_id, d.detected_at, d.scan_id, d.imbalance_score, d.z_midi, d.z_kyber, d.z_dark,
       d.signature, d.severity, d.is_report_sourced, d.report_description, d.report_relevance, d.sustained_scans,
       s.sector_name, s.system_name, s.region, s.population, s.description, s.force_history,
       (SELECT max(r.channels_present) FROM {reading} r
         WHERE r.sector_id = d.sector_id AND r.event_time = d.detected_at AND r.source_type = 'probe') AS channels_present,
       (SELECT count(*) FROM {disturbance} h
         WHERE h.sector_id = d.sector_id AND h.detected_at >= d.detected_at - INTERVAL 7 DAYS AND h.detected_at < d.detected_at) AS disturbances_last_7d,
       (SELECT count(*) FROM {deployment} p JOIN {disturbance} h2 ON h2.disturbance_id = p.disturbance_id
         WHERE h2.sector_id = d.sector_id AND p.decision = 'deploy' AND p.decided_at >= d.detected_at - INTERVAL 7 DAYS) AS deployments_last_7d,
       (SELECT max_by(h3.signature, h3.detected_at) FROM {disturbance} h3
         WHERE h3.sector_id = d.sector_id AND h3.detected_at < d.detected_at) AS last_signature,
       (SELECT count(*) FROM {deployment} o WHERE o.decision = 'deploy' AND o.released_at > current_timestamp()) AS open_deployments
FROM {disturbance} d JOIN {sector} s ON s.sector_id = d.sector_id
WHERE d.disturbance_id = :disturbance_id"""
    return _render(sql, target), {"disturbance_id": (disturbance_id, "STRING")}


def available_jedi(target):
    sql = """SELECT j.jedi_id, j.jedi_name, j.rank, j.primary_specialty, j.secondary_specialty, j.power_rating, j.lightsaber_form,
       j.notable_for, s.region AS homeworld_region
FROM {jedi} j LEFT JOIN {sector} s ON s.sector_id = j.homeworld_sector_id
WHERE NOT EXISTS (SELECT 1 FROM {deployment} p WHERE p.decision = 'deploy' AND p.released_at > current_timestamp()
                  AND array_contains(p.jedi_ids, j.jedi_id))"""
    return _render(sql, target), {}


def available_starships(target):
    sql = """SELECT st.starship_id, st.starship_name, st.starship_class, st.hyperdrive_rating, st.mglt, st.crew, st.passengers
FROM {starship} st
WHERE NOT EXISTS (SELECT 1 FROM {deployment} p WHERE p.decision = 'deploy' AND p.released_at > current_timestamp()
                  AND p.starship_id = st.starship_id)"""
    return _render(sql, target), {}


def write_decision(target, row):
    """MERGE one decision, inserting only when the disturbance has no deployment row yet (Delta has no unique keys)."""
    sql = """MERGE INTO {deployment} t
USING (SELECT :deployment_id AS deployment_id, :disturbance_id AS disturbance_id, :decided_by AS decided_by,
              :decision AS decision, from_json(:jedi_ids_json, 'ARRAY<STRING>') AS jedi_ids, :starship_id AS starship_id,
              :eta_hours AS eta_hours, :released_at AS released_at, :rationale AS rationale,
              parse_json(:context_json) AS context_snapshot, :signature_at_decision AS signature_at_decision,
              :model AS model, :decision_latency_ms AS decision_latency_ms,
              parse_json(:overrides_json) AS guardrail_overrides, :decided_at AS decided_at) s
ON t.disturbance_id = s.disturbance_id
WHEN NOT MATCHED THEN INSERT *"""
    params = {
        "deployment_id": (row["deployment_id"], "STRING"),
        "disturbance_id": (row["disturbance_id"], "STRING"),
        "decided_by": (row["decided_by"], "STRING"),
        "decision": (row["decision"], "STRING"),
        "jedi_ids_json": (json.dumps(list(row.get("jedi_ids") or [])), "STRING"),
        "starship_id": (row.get("starship_id"), "STRING"),
        "eta_hours": (row.get("eta_hours"), "DOUBLE"),
        "released_at": (row.get("released_at"), "TIMESTAMP"),
        "rationale": (row["rationale"], "STRING"),
        "context_json": (json.dumps(row.get("context_snapshot"), sort_keys=True), "STRING"),
        "signature_at_decision": (row.get("signature_at_decision"), "STRING"),
        "model": (row.get("model"), "STRING"),
        "decision_latency_ms": (row.get("decision_latency_ms"), "BIGINT"),
        "overrides_json": (json.dumps(list(row.get("guardrail_overrides") or [])), "STRING"),
        "decided_at": (row["decided_at"], "TIMESTAMP"),
    }
    return _render(sql, target), params


def mark(target, disturbance_id):
    sql = """UPDATE {disturbance} SET agent_processed = true WHERE disturbance_id = :disturbance_id AND agent_processed = false"""
    return _render(sql, target), {"disturbance_id": (disturbance_id, "STRING")}
