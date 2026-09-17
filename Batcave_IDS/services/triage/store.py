"""Writes triage predictions (LLM or baseline) into the warehouse as a plain
table dbt reads as a source - the bridge between Python (which has to call
Groq / run the baseline classifier) and the SQL evaluation layer.

`raw_triage_predictions` is deliberately NOT a dbt model: dbt can't call an
external API or run Python classification logic, so this table is the one
place in the warehouse written directly rather than derived by `dbt run`.
`fct_intervention_orders` (Phase 6) declares it as a dbt `source` and builds
on top of it like any other landed data.

Both the LLM and the baseline write into the SAME table with a `source`
discriminator column (`llm` / `baseline`), same shape, so the evaluation
marts score them side by side without a schema fork - the whole point of
"run it on every session alongside the LLM, report both side by side"
(docs/04).

Technique predictions are normalized to `attack_id` before writing, even
though the baseline computes `technique_id` internally: the LLM only ever
speaks `attack_id` (docs/04's output contract), and ground truth
(`stg_attack_attempts`) is joined via `dim_techniques` either way, so
`attack_id` is the one common key both prediction sources can be compared on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import duckdb

TABLE_NAME = "raw_triage_predictions"

_SCHEMA = """
create table if not exists raw_triage_predictions (
    order_id varchar primary key,
    session_id varchar,
    run_id varchar,
    source varchar,                  -- 'llm' or 'baseline'
    prompt_version varchar,
    model_name varchar,
    issued_at timestamp,
    threat_level varchar,
    suspected_villain varchar,
    alternate_suspects varchar,      -- JSON array
    suspected_archetype varchar,
    confidence double,
    identified_techniques varchar,   -- JSON array of {attack_id, confidence, evidence}
    identified_tactics varchar,      -- JSON array
    reconstructed_stage_reached integer,
    reasoning varchar,
    in_person_intervention_required boolean,
    recommended_countermeasures varchar,  -- JSON array
    attack_pattern_summary varchar,
    parse_failed boolean,
    hallucinated_villain boolean,
    hallucinated_technique_count integer,
    latency_ms double,
    input_tokens integer,
    output_tokens integer
)
"""


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SCHEMA)


def _order_id(session_id: str, source: str) -> str:
    # Deterministic, not random: re-running the same session through the same
    # source overwrites its prior order rather than accumulating duplicates -
    # `make triage` is safe to re-run.
    return f"{session_id}:{source}"


def technique_id_to_attack_id(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute("select technique_id, attack_id from dim_techniques").fetchall())


def write_llm_order(
    con: duckdb.DuckDBPyConnection,
    session_id: str,
    run_id: str,
    result: Any,  # services.triage.llm.TriageResult
) -> None:
    """Writes one LLM prediction. A parse_failed result still gets a row -
    docs/04's evaluation marts need to see the failure rate, not just the
    successes."""
    ensure_table(con)
    parsed = result.parsed or {}
    con.execute(f"delete from {TABLE_NAME} where order_id = ?", [_order_id(session_id, "llm")])
    con.execute(
        f"""
        insert into {TABLE_NAME} values
        (?, ?, ?, 'llm', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            _order_id(session_id, "llm"),
            session_id,
            run_id,
            result.prompt_version,
            result.model_name,
            datetime.now(UTC),
            parsed.get("threat_level"),
            parsed.get("suspected_villain"),
            json.dumps(parsed.get("alternate_suspects", [])),
            parsed.get("suspected_archetype"),
            parsed.get("confidence"),
            json.dumps(parsed.get("identified_techniques", [])),
            json.dumps(parsed.get("identified_tactics", [])),
            parsed.get("reconstructed_stage_reached"),
            parsed.get("reasoning"),
            parsed.get("in_person_intervention_required"),
            json.dumps(parsed.get("recommended_countermeasures", [])),
            parsed.get("attack_pattern_summary"),
            result.parse_failed,
            result.hallucinated_villain,
            result.hallucinated_technique_count,
            result.latency_ms,
            result.input_tokens,
            result.output_tokens,
        ],
    )


def write_baseline_order(
    con: duckdb.DuckDBPyConnection,
    session_id: str,
    run_id: str,
    *,
    suspected_villain: str,
    alternate_suspects: list[str],
    confidence: float,
    technique_predictions: list[dict],
    id_map: dict[str, str],
    archetype: str | None = None,
) -> None:
    """Writes one baseline prediction in the same shape an LLM order takes,
    so both score through the same evaluation marts. `threat_level` and the
    prose fields (reasoning, countermeasures, summary) are structurally
    absent - the baseline doesn't generate prose, only classifications, and
    those columns stay null rather than being filled with a placeholder."""
    ensure_table(con)
    attack_ids = [
        {
            "attack_id": id_map.get(p["technique_id"], p["technique_id"]),
            "confidence": 1.0,
            "evidence": f"{p['evidence_feature']}={p['evidence_count']}",
        }
        for p in technique_predictions
    ]
    con.execute(f"delete from {TABLE_NAME} where order_id = ?", [_order_id(session_id, "baseline")])
    con.execute(
        f"""
        insert into {TABLE_NAME} values
        (?, ?, ?, 'baseline', 'rule-based-v1', 'nearest-centroid+keyword', ?, null, ?, ?, ?, ?,
         ?, '[]', null, null, null, '[]', null, false, false, 0, null, null, null)
        """,
        [
            _order_id(session_id, "baseline"),
            session_id,
            run_id,
            datetime.now(UTC),
            suspected_villain,
            json.dumps(alternate_suspects),
            archetype,
            confidence,
            json.dumps(attack_ids),
        ],
    )
