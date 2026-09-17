"""Assembles the per-session observed context docs/04's Input contract lists
- shared by the LLM path and the rule-based baseline, since both need exactly
the same evidence and neither should see anything the other doesn't.

Assembled **exclusively** from `int_session_features_observed`,
`mart_threat_scores`, and raw request/chat data (docs/04). Never reads
`stg_attack_attempts`, `stg_attack_runs`, `int_stage_progression`, or
`int_session_features_truth` - the same boundary `assert_no_ground_truth_leakage`
enforces at the model layer, held here too at the query layer so a context bug
can't leak ground truth even if the dbt tag were ever wrong.
"""

from __future__ import annotations

from typing import Any

import duckdb

# Every observed feature the model/baseline may see. Explicit list rather than
# `select *` on int_session_features_observed, so a new column added there
# doesn't silently start reaching the LLM before a decision is made about it.
FEATURE_COLUMNS = [
    "request_count",
    "duration_s",
    "requests_per_min",
    "max_path_tier",
    "error_ratio",
    "distinct_source_ips",
    "distinct_user_agents",
    "null_ip_ratio",
    "inter_request_stddev_ms",
    "mean_response_time_ms",
    "mean_body_bytes",
    "max_body_bytes",
    "invalid_body_ratio",
    "riddle_param_count",
    "traversal_pattern_count",
    "injection_pattern_count",
    "late_arrival_count",
    "body_bytes_trend",
    "distinct_paths",
    "path_entropy",
    "exact_duplicate_path_pairs",
    "wasted_request_ratio",
    "time_to_tier3_s",
    "had_error",
    "requests_after_first_error",
    "repeated_auth_failure_runs",
    "path_enumeration_runs",
    "sensitive_data_access_runs",
    "chat_turns_completed",
    "probe_engagement_ratio",
    "intent_flags_triggered",
]

MAX_PATH_SEQUENCE = 40
MAX_BODIES = 5
BODY_TRUNCATE_CHARS = 200


def _truncate_ip(source_ip: str | None) -> str | None:
    """First three octets only (docs/04) - enough to see rotation
    (distinct_source_ips already carries the count), not enough to be a full
    identifier."""
    if source_ip is None:
        return None
    parts = source_ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".x"
    return source_ip


def build_session_context(con: duckdb.DuckDBPyConnection, session_id: str) -> dict[str, Any]:
    """Everything docs/04's Input contract lists for one session, as a plain
    JSON-serializable dict. Raises if the session has no threat score row
    (mart_threat_scores) or no feature row - both are triage_input models
    built for every session, so a missing row means the caller passed a bad
    session_id, not a legitimate empty case."""
    feature_row = con.execute(
        f"select {', '.join(FEATURE_COLUMNS)} from int_session_features_observed "
        "where session_id = ?",
        [session_id],
    ).fetchone()
    if feature_row is None:
        raise ValueError(f"no observed features for session_id={session_id!r}")
    features = dict(zip(FEATURE_COLUMNS, feature_row, strict=True))

    score_row = con.execute(
        """
        select threat_score, component_max_path_tier, component_enumeration,
               component_exploit_evidence, component_auth_failure, component_error_ratio,
               component_regularity, component_evasion, component_volume
        from mart_threat_scores where session_id = ?
        """,
        [session_id],
    ).fetchone()
    if score_row is None:
        raise ValueError(f"no threat score for session_id={session_id!r}")
    threat_score = {
        "total": score_row[0],
        "components": {
            "max_path_tier": score_row[1],
            "enumeration": score_row[2],
            "exploit_evidence": score_row[3],
            "auth_failure": score_row[4],
            "error_ratio": score_row[5],
            "regularity": score_row[6],
            "evasion": score_row[7],
            "volume": score_row[8],
        },
    }

    # Path sequence: first 20, last 20 if longer than 40 (docs/04).
    all_paths = [
        r[0]
        for r in con.execute(
            "select path from fct_attack_events where session_id = ? order by received_at",
            [session_id],
        ).fetchall()
    ]
    if len(all_paths) > MAX_PATH_SEQUENCE:
        path_sequence = all_paths[:20] + ["...(truncated)..."] + all_paths[-20:]
    else:
        path_sequence = all_paths

    status_codes = con.execute(
        """
        select status_returned, count(*) from fct_attack_events
        where session_id = ? group by 1 order by 1
        """,
        [session_id],
    ).fetchall()

    user_agents = [
        r[0]
        for r in con.execute(
            "select distinct user_agent from fct_attack_events "
            "where session_id = ? and user_agent is not null",
            [session_id],
        ).fetchall()
    ]

    bodies = [
        r[0][:BODY_TRUNCATE_CHARS]
        for r in con.execute(
            """
            select request_body from fct_attack_events
            where session_id = ? and request_body is not null
            order by received_at limit ?
            """,
            [session_id, MAX_BODIES],
        ).fetchall()
    ]

    source_ip_row = con.execute(
        "select source_ip from fct_attack_events where session_id = ? "
        "and source_ip is not null limit 1",
        [session_id],
    ).fetchone()

    chat_turns = con.execute(
        """
        select turn_number, speaker, objective, refused, extracted_intent_flags
        from fct_botchat_turns where session_id = ? order by turn_number
        """,
        [session_id],
    ).fetchall()

    return {
        "session_id": session_id,
        "features": features,
        "threat_score": threat_score,
        "path_sequence": path_sequence,
        "status_code_distribution": {str(code): count for code, count in status_codes},
        "distinct_user_agents": user_agents,
        "request_bodies_sample": bodies,
        "source_ip_prefix": _truncate_ip(source_ip_row[0] if source_ip_row else None),
        "chat_turns": [
            {
                "turn_number": t,
                "speaker": speaker,
                "objective": objective,
                "refused": refused,
                "intent_flags": flags,
            }
            for t, speaker, objective, refused, flags in chat_turns
        ],
    }
