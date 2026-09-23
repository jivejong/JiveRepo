"""The finale's background pipeline (Phase 10, docs/08).

A finished console run doesn't have a triage prediction the instant it
ends: the consumer's landing flush (default 30s), a `dbt build`, and the
triage call itself all sit between "the player finished" and a real
prediction existing. Rather than a dead stall, this is driven in the
background from the moment a session finishes (`services/console/app.py`
starts it in a thread) and polled by the frontend via a status endpoint,
rendered as its own in-fiction BATCOMPUTER sequence (docs/08).

States: pending -> ingesting -> transforming -> scoring -> attributing ->
ready | failed. Every stage is bounded - an overall deadline this module
enforces stage by stage, not left to the caller to guess from a timeout.
`failed` is a first-class outcome, not an afterthought: it renders the
degraded in-fiction script (`services.console.counterstrike.
build_degraded_script`) rather than a spinner or a stack trace, and the
run still completes. A missing GEMINI_API_KEY is never a failure - the
baseline attributes the finale the same way it stands in for the LLM
everywhere else in this project (`services/triage/console_triage.py`).

DuckDB is single-writer, and a direct cross-process test (docs/05) showed
that on this platform a second process opening the file while this one
holds it open fails immediately, not gracefully. This module never holds
a connection open across the `dbt build` subprocess - it opens and closes
one per phase - specifically to keep that collision window as short as
the pipeline allows, which is what makes the dashboard's short bounded
retry (`dashboard/app.py`) a reasonable mitigation rather than papering
over a long one.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import duckdb
from google import genai

from services.console.counterstrike import (
    build_degraded_script,
    build_script,
    emit_counterstrike,
)
from services.triage.console_triage import ConsoleTriagePrediction, triage_console_session

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
TRANSFORM_DIR = REPO_ROOT / "transform"

# Bounded per stage, not left open-ended - an infinite poll against a
# permanently-missing row is a real failure mode (docs/08), not a
# hypothetical to hand-wave past.
INGEST_TIMEOUT_S = 45.0  # still comfortably past the 30s default flush, for a
# console started without `make console`'s 3s flush override
INGEST_POLL_INTERVAL_S = 1.0  # a glob + one small read - cheap enough to poll tightly
DBT_BUILD_TIMEOUT_S = 60.0

# Only what triage_console_session and build_session_context actually read
# (fct_attack_runs, int_session_features_observed, mart_threat_scores,
# fct_attack_events, fct_botchat_turns, dim_villains, dim_techniques) plus
# their upstream - 17 models instead of the full graph, and `run` not `build`
# because the tests belong to `make transform`/CI, not to a player waiting on
# a finale. Measured: ~5.7s vs ~7.4s for the full `dbt build`.
DBT_SELECT = (
    "+mart_threat_scores +fct_attack_runs +int_session_features_observed "
    "+fct_attack_events +fct_botchat_turns +dim_villains +dim_techniques"
)

FinaleState = Literal[
    "pending", "ingesting", "transforming", "scoring", "attributing", "ready", "failed"
]


@dataclass
class FinaleResult:
    """What `services/console/app.py`'s status endpoint reports. `lines` is
    always populated once `state` reaches a terminal value - the real
    script on `ready`, the degraded one on `failed` - so the frontend never
    has to separately ask "what do I render now."""

    state: FinaleState = "pending"
    reason: str | None = None
    prediction: ConsoleTriagePrediction | None = None
    lines: list[dict] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in ("ready", "failed")


def _session_landed(con: duckdb.DuckDBPyConnection, session_id: str) -> bool:
    """Checks the RAW landed Parquet directly - the same glob-over-Hive-
    layout approach `make landing-check` uses - rather than through any
    dbt model, since nothing has been built yet at this point in the
    pipeline. An attack_run row existing is what "the run's events have
    landed" means; request/attempt rows for an active session land
    continuously, but the attack_run row is written once, at the end, by
    `StageMachine.finish()` - its presence is the actual signal this stage
    is waiting for.

    `glob()` first, same reason `transform/macros/raw_events.sql`'s own
    `raw_events_exist()` does it this way: `read_parquet` on a glob that
    matches zero files is a hard IOException, not an empty result - a real
    case here, not hypothetical, since the very first attack_run this
    process's own consumer flush ever lands could be racing an otherwise-
    empty directory (a session played immediately after `make dev-reset`,
    before anything else has landed)."""
    glob_pattern = str(REPO_ROOT / "data" / "raw" / "attack_run" / "**" / "*.parquet")
    file_count = con.execute(f"select count(*) from glob('{glob_pattern}')").fetchone()[0]
    if file_count == 0:
        return False
    row = con.execute(
        f"select count(*) from read_parquet('{glob_pattern}', union_by_name=true) "
        "where session_id = ?",
        [session_id],
    ).fetchone()
    return row[0] > 0


def _run_dbt_build() -> None:
    """Runs the slice of the dbt graph triage reads (`DBT_SELECT`) as a
    subprocess so the connection it opens is fully closed (and the file
    lock released) before this module opens its own connection again -
    never held open across this call."""
    result = subprocess.run(
        ["uv", "run", "--project", "..", "dbt", "run", "--profiles-dir", "."]
        + ["--select", DBT_SELECT],
        cwd=TRANSFORM_DIR,
        capture_output=True,
        text=True,
        timeout=DBT_BUILD_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(f"dbt run failed (exit {result.returncode}): {result.stderr[-2000:]}")


def _session_scored(con: duckdb.DuckDBPyConnection, session_id: str) -> bool:
    row = con.execute(
        "select count(*) from mart_threat_scores where session_id = ?", [session_id]
    ).fetchone()
    return row[0] > 0


def _villain_display_name(con: duckdb.DuckDBPyConnection, villain_slug: str | None) -> str:
    if villain_slug is None:
        return "UNKNOWN"
    row = con.execute(
        "select villain_name from dim_villains where villain_slug = ?", [villain_slug]
    ).fetchone()
    return row[0] if row else villain_slug


def run_finale_pipeline(
    *,
    session_id: str,
    run_id: str,
    kafka_producer: object,
    kafka_topic: str,
    llm_client: genai.Client | None,
    on_state_change: Callable[[FinaleResult], None],
) -> None:
    """Runs the whole pipeline synchronously - the caller
    (`services/console/app.py`) is responsible for calling this on a
    background thread, not the main request thread. `on_state_change(
    FinaleResult)` is called after every transition, including the final
    one, so the caller can store the latest result wherever its status
    endpoint reads from (a plain attribute write under the GIL is enough
    for this single-operator, single-writer-per-session use - no lock
    needed, the same simplification `services/console/state.py`'s own
    docstring already makes for session state generally)."""
    result = FinaleResult(state="ingesting")
    on_state_change(result)

    deadline = time.monotonic() + INGEST_TIMEOUT_S
    try:
        landed = False
        while time.monotonic() < deadline:
            con = duckdb.connect(str(WAREHOUSE_PATH))
            try:
                landed = _session_landed(con, session_id)
            finally:
                con.close()
            if landed:
                break
            time.sleep(INGEST_POLL_INTERVAL_S)
        if not landed:
            raise TimeoutError(
                f"session {session_id!r} never landed within {INGEST_TIMEOUT_S:.0f}s"
            )

        result = FinaleResult(state="transforming")
        on_state_change(result)
        _run_dbt_build()

        result = FinaleResult(state="scoring")
        on_state_change(result)
        con = duckdb.connect(str(WAREHOUSE_PATH))
        try:
            if not _session_scored(con, session_id):
                raise RuntimeError(
                    f"session {session_id!r} landed but produced no mart_threat_scores row "
                    "after dbt build"
                )
        finally:
            con.close()

        result = FinaleResult(state="attributing")
        on_state_change(result)
        con = duckdb.connect(str(WAREHOUSE_PATH))
        try:
            prediction = triage_console_session(con, session_id, llm_client)
            villain_name = _villain_display_name(con, prediction.suspected_villain)
        finally:
            con.close()

        confidence = prediction.confidence if prediction.confidence is not None else 0.0
        lines = build_script(
            villain_slug=prediction.suspected_villain or "unknown",
            villain_display_name=villain_name,
            confidence=confidence,
            attack_ids=prediction.identified_attack_ids,
        )
        emit_counterstrike(
            session_id=session_id,
            run_id=run_id,
            kafka_producer=kafka_producer,
            kafka_topic=kafka_topic,
            line_dicts=lines,
        )
        result = FinaleResult(state="ready", prediction=prediction, lines=lines)
        on_state_change(result)

    except Exception as exc:  # noqa: BLE001 - a failure at ANY stage must
        # degrade, never propagate to an unhandled background-thread crash
        # (docs/08: "not an infinite poll" applies just as much to a thread
        # that dies silently as to one that hangs).
        result = FinaleResult(state="failed", reason=str(exc), lines=build_degraded_script())
        on_state_change(result)
