"""Single-session triage path for a console session's finale (Phase 10),
bypassing the production threshold selector (`_select_sessions`,
`services/triage/__main__.py`) on purpose: no console session can ever
clear it. The threshold was calibrated against `BehaviorProfile`-paced
traffic; human pacing produces a structurally lower `threat_score` -
every console session observed so far scores 10.5-50.2 against a
threshold of 60 (docs/08). This bypass reads as "every console playthrough
deserves a finale regardless of score," not a general loosening of triage
eligibility - it refuses outright on a non-console session rather than
silently triaging one, so nothing here can change what bare `make triage`
selects.

Reuses every piece `run_triage()` (`services/triage/__main__.py`) already
assembles per session - `build_reference_profiles`, `technique_id_to_
attack_id`, `villain_archetype_map`, `classify_villain`, `classify_
techniques`, `build_session_context`, `triage_session`, `write_baseline_
order`, `write_llm_order` - a thin wrapper, not new machinery, so the
console's one-off case can never drift from what every other session gets.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
from google import genai

from services.triage.baseline import (
    build_reference_profiles,
    classify_techniques,
    classify_villain,
    villain_archetype_map,
)
from services.triage.context import FEATURE_COLUMNS, build_session_context
from services.triage.llm import triage_session
from services.triage.prompt import PROMPT_VERSION, build_system_prompt
from services.triage.store import (
    ensure_table,
    technique_id_to_attack_id,
    write_baseline_order,
    write_llm_order,
)


@dataclass(frozen=True)
class ConsoleTriagePrediction:
    """What the finale needs to render the counterstrike readout - the
    prediction actually used to attribute it, whichever source produced it
    (docs/08: "the suspect comes from the model, not from ground truth")."""

    source: str  # 'llm' or 'baseline'
    suspected_villain: str | None
    confidence: float | None
    identified_attack_ids: list[str]


class NotAConsoleSessionError(ValueError):
    """Raised when the bypass is asked to triage a session that either
    doesn't exist yet in fct_attack_runs (the caller's dbt build hasn't
    landed it) or exists but isn't session_source='console' - refusing is
    the point, not an edge case to tolerate silently."""


def _feature_dict(con: duckdb.DuckDBPyConnection, session_id: str) -> dict:
    row = con.execute(
        f"select {', '.join(FEATURE_COLUMNS)} from int_session_features_observed "
        "where session_id = ?",
        [session_id],
    ).fetchone()
    return dict(zip(FEATURE_COLUMNS, row, strict=True))


def triage_console_session(
    con: duckdb.DuckDBPyConnection,
    session_id: str,
    llm_client: genai.Client | None,
) -> ConsoleTriagePrediction:
    """Triages exactly one session, bypassing the threshold. Always writes
    the baseline order; additionally calls the LLM and writes its order
    when `llm_client` is not None (the same zero-credential contract
    `run_triage` applies - docs/04's Configuration section). Returns the
    prediction the finale should attribute from: the LLM's when it ran,
    the baseline's otherwise - the same precedence docs/04 documents for
    "without GEMINI_API_KEY, triage runs the baseline instead"."""
    row = con.execute(
        "select session_source, run_id from fct_attack_runs where session_id = ?",
        [session_id],
    ).fetchone()
    if row is None:
        raise NotAConsoleSessionError(
            f"session_id={session_id!r} has no fct_attack_runs row yet - run dbt build first"
        )
    session_source, run_id = row
    if session_source != "console":
        raise NotAConsoleSessionError(
            f"session_id={session_id!r} is session_source={session_source!r}, "
            "not 'console' - the threshold bypass is scoped to console "
            "sessions only and refuses everything else on purpose"
        )

    ensure_table(con)
    profiles = build_reference_profiles(con)
    id_map = technique_id_to_attack_id(con)
    archetypes = villain_archetype_map(con)
    features = _feature_dict(con, session_id)

    predicted, alternates, confidence = classify_villain(features, profiles)
    techniques = classify_techniques(features)
    write_baseline_order(
        con,
        session_id,
        run_id,
        suspected_villain=predicted,
        alternate_suspects=alternates,
        confidence=confidence,
        technique_predictions=techniques,
        id_map=id_map,
        archetype=archetypes.get(predicted),
    )
    result = ConsoleTriagePrediction(
        source="baseline",
        suspected_villain=predicted,
        confidence=confidence,
        identified_attack_ids=[
            id_map.get(t["technique_id"], t["technique_id"]) for t in techniques
        ],
    )

    if llm_client is not None:
        known_villains = {
            r[0] for r in con.execute("select villain_slug from dim_villains").fetchall()
        }
        known_attack_ids = {
            r[0] for r in con.execute("select attack_id from dim_techniques").fetchall()
        }
        system_prompt = build_system_prompt(con)
        context = build_session_context(con, session_id)
        llm_result = triage_session(
            llm_client,
            system_prompt,
            context,
            known_villains,
            known_attack_ids,
            prompt_version=PROMPT_VERSION,
        )
        write_llm_order(con, session_id, run_id, llm_result)
        if not llm_result.parse_failed:
            parsed = llm_result.parsed or {}
            result = ConsoleTriagePrediction(
                source="llm",
                suspected_villain=parsed.get("suspected_villain"),
                confidence=parsed.get("confidence"),
                identified_attack_ids=[
                    t.get("attack_id")
                    for t in parsed.get("identified_techniques", [])
                    if isinstance(t, dict) and t.get("attack_id")
                ],
            )

    con.commit()
    return result
