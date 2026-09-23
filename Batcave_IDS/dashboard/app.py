"""Batanalytics — the Batcave_IDS dashboard (Phase 10, docs/08).

Presentation over models that already exist. Every panel reads straight
from a dbt mart or fact table; nothing here computes a metric a dbt model
doesn't already own. Run with `streamlit run dashboard/app.py`, port 8501
(docs/01's port table).

Reads `data/warehouse.duckdb` **read-only** — the console backend's finale
pipeline (`services/console/finale.py`) is this warehouse's first other
concurrent user, opening it read-write for a `dbt build` subprocess and a
triage write. A direct cross-process test (docs/05) showed DuckDB's file
lock on this platform is immediate and exclusive, not a graceful queue: a
second process opening the file while the first holds it open fails at
once with `duckdb.IOException`. `_query()` below is the one place every
panel goes through, and it retries specifically on that exception with a
short bounded backoff before giving up — never a bare `except Exception`,
so an unrelated query bug is never mistaken for a lock collision.
"""

from __future__ import annotations

import time
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

WAREHOUSE_PATH = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"

# Short and bounded on purpose (docs/05): the console's own write window is
# kept small by design (finale.py opens/closes a connection per pipeline
# phase, never holding one across the dbt subprocess), so a real collision
# here should be brief and rare - this is a mitigation for that brief
# window, not a substitute for it staying short.
LOCK_RETRY_ATTEMPTS = 5
LOCK_RETRY_BACKOFF_S = 0.5

# The validated categorical palette (dataviz skill, references/palette.md) -
# fixed hue order, never cycled or reassigned per filter.
SERIES_1 = "#2a78d6"  # blue
SERIES_2 = "#eb6834"  # orange
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"


class WarehouseBusyError(RuntimeError):
    """Raised after every retry is exhausted - the dashboard's own terminal
    state for this failure, distinct from `duckdb.IOException` so a caller
    can show a clear message rather than a raw stack trace."""


def _query(sql: str) -> pd.DataFrame:
    last_error: duckdb.IOException | None = None
    for attempt in range(LOCK_RETRY_ATTEMPTS):
        try:
            con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
            try:
                return con.execute(sql).df()
            finally:
                con.close()
        except duckdb.IOException as exc:
            last_error = exc
            if attempt < LOCK_RETRY_ATTEMPTS - 1:
                time.sleep(LOCK_RETRY_BACKOFF_S)
    raise WarehouseBusyError(
        f"warehouse busy after {LOCK_RETRY_ATTEMPTS} attempts - a console session's "
        "background pipeline likely holds it open right now"
    ) from last_error


def _relation_exists(table_name: str) -> bool:
    df = _query(
        f"select count(*) as n from information_schema.tables where table_name = '{table_name}'"
    )
    return df["n"].iloc[0] > 0


st.set_page_config(page_title="Batanalytics", layout="wide")
st.title("Batanalytics")
st.caption(
    "Batcave_IDS detection-engineering dashboard — every panel reads a dbt mart directly, "
    "read-only, from data/warehouse.duckdb."
)

if not WAREHOUSE_PATH.exists():
    st.error(
        f"No warehouse found at {WAREHOUSE_PATH}. Run `make transform` first "
        "(docs/05) — a clean clone has no warehouse until dbt builds one."
    )
    st.stop()

try:
    corpus_size = _query("select count(*) as n from fct_attack_runs")["n"].iloc[0]
except WarehouseBusyError as exc:
    st.warning(f"Warehouse busy, try again in a moment. ({exc})")
    st.stop()

if corpus_size < 50:
    st.info(
        f"Only {corpus_size} sessions in the warehouse right now. On a clean clone before "
        "`make attack-all` has run, only the committed sample partition backs these panels "
        "and most charts below will look sparse — that's expected, not a bug."
    )

try:
    # --- Kill chain funnel -------------------------------------------------
    st.header("Kill chain funnel")
    st.caption("Sessions entering and clearing each stage.")
    funnel = _query(
        """
        select stage, stage_name,
               sum(sessions_entered) as sessions_entered,
               sum(sessions_cleared) as sessions_cleared
        from mart_killchain_funnel
        group by stage, stage_name
        order by stage
        """
    )
    funnel_long = funnel.melt(
        id_vars=["stage", "stage_name"],
        value_vars=["sessions_entered", "sessions_cleared"],
        var_name="metric",
        value_name="sessions",
    )
    funnel_chart = (
        alt.Chart(funnel_long)
        .mark_bar()
        .encode(
            x=alt.X("stage_name:N", sort=None, title="Stage"),
            xOffset="metric:N",
            y=alt.Y("sessions:Q", title="Sessions"),
            color=alt.Color(
                "metric:N",
                scale=alt.Scale(
                    domain=["sessions_entered", "sessions_cleared"], range=[SERIES_1, SERIES_2]
                ),
                title=None,
            ),
            tooltip=["stage_name", "metric", "sessions"],
        )
        .properties(height=320)
    )
    st.altair_chart(funnel_chart, width="stretch")

    # --- Technique efficacy -------------------------------------------------
    st.header("Technique efficacy")
    st.caption("Observed vs. computed success rate per technique, aggregated across villains.")
    efficacy = _query(
        """
        select
            display_name,
            sum(observed_success_rate * attempts_total) / nullif(sum(attempts_total), 0)
                as observed,
            sum(scoreable_success_rate * attempts_scoreable) / nullif(sum(attempts_scoreable), 0)
                as computed
        from mart_technique_efficacy
        group by display_name
        having sum(attempts_total) > 0
        order by display_name
        """
    )
    efficacy_long = efficacy.melt(
        id_vars=["display_name"],
        value_vars=["observed", "computed"],
        var_name="metric",
        value_name="rate",
    )
    efficacy_chart = (
        alt.Chart(efficacy_long)
        .mark_bar()
        .encode(
            x=alt.X("display_name:N", sort=None, title="Technique"),
            xOffset="metric:N",
            y=alt.Y("rate:Q", title="Success rate", axis=alt.Axis(format="%")),
            color=alt.Color(
                "metric:N",
                scale=alt.Scale(domain=["observed", "computed"], range=[SERIES_1, SERIES_2]),
                title=None,
            ),
            tooltip=["display_name", "metric", alt.Tooltip("rate:Q", format=".1%")],
        )
        .properties(height=360)
    )
    st.altair_chart(efficacy_chart, width="stretch")

    # --- Retry vs pivot by archetype (ground truth) -------------------------
    st.header("Retry vs. pivot by archetype (ground truth)")
    st.caption(
        "Reads int_session_features_truth, a ground-truth-tagged model — labelled here "
        "because a dashboard viewer has no other way to see that tagging convention."
    )
    retry_pivot = _query(
        """
        select
            v.archetype,
            avg(f.retry_ratio) as retry_ratio,
            avg(f.pivot_ratio) as pivot_ratio
        from int_session_features_truth as f
        inner join fct_attack_runs as r on f.run_id = r.run_id
        inner join dim_villains as v on r.villain_slug = v.villain_slug
        group by v.archetype
        order by v.archetype
        """
    )
    retry_pivot_long = retry_pivot.melt(
        id_vars=["archetype"],
        value_vars=["retry_ratio", "pivot_ratio"],
        var_name="metric",
        value_name="ratio",
    )
    retry_pivot_chart = (
        alt.Chart(retry_pivot_long)
        .mark_bar()
        .encode(
            x=alt.X("archetype:N", sort=None, title="Archetype"),
            xOffset="metric:N",
            y=alt.Y("ratio:Q", title="Mean ratio"),
            color=alt.Color(
                "metric:N",
                scale=alt.Scale(domain=["retry_ratio", "pivot_ratio"], range=[SERIES_1, SERIES_2]),
                title=None,
            ),
            tooltip=["archetype", "metric", alt.Tooltip("ratio:Q", format=".2f")],
        )
        .properties(height=320)
    )
    st.altair_chart(retry_pivot_chart, width="stretch")

    # --- Detection coverage (headline) --------------------------------------
    st.header("Detection coverage")
    st.caption("Technique recall by observability tier — the headline result of this project.")
    coverage = _query(
        """
        select coverage_tier, source, recall
        from mart_detection_coverage
        where source is not null
        order by coverage_tier, source
        """
    )
    coverage_chart = (
        alt.Chart(coverage)
        .mark_bar()
        .encode(
            x=alt.X("coverage_tier:N", sort=None, title="Observability tier"),
            xOffset="source:N",
            y=alt.Y("recall:Q", title="Recall", axis=alt.Axis(format="%")),
            color=alt.Color(
                "source:N",
                scale=alt.Scale(domain=["baseline", "llm"], range=[SERIES_1, SERIES_2]),
                title=None,
            ),
            tooltip=["coverage_tier", "source", alt.Tooltip("recall:Q", format=".1%")],
        )
        .properties(height=360)
    )
    st.altair_chart(coverage_chart, width="stretch")

    # --- Suspect ranking -----------------------------------------------------
    st.header("Suspect ranking")
    st.caption("The model's prediction and confidence, beside the true villain.")
    suspects = _query(
        """
        select
            source, session_id, true_villain, suspected_villain, confidence,
            exact_match, top_3_match
        from fct_triage_evaluations
        order by confidence desc
        limit 100
        """
    )
    st.dataframe(suspects, width="stretch", hide_index=True)

    # --- Reconstruction comparison -------------------------------------------
    st.header("Reconstruction comparison")
    st.caption("Techniques the model identified vs. techniques actually used.")
    recon = _query(
        """
        select source, outcome_class, count(*) as n
        from fct_technique_evaluations
        where outcome_class != 'true_negative'
        group by source, outcome_class
        order by source, outcome_class
        """
    )
    recon_chart = (
        alt.Chart(recon)
        .mark_bar()
        .encode(
            x=alt.X("source:N", title="Source"),
            y=alt.Y("n:Q", title="Count"),
            color=alt.Color(
                "outcome_class:N",
                scale=alt.Scale(
                    domain=["true_positive", "false_positive", "false_negative"],
                    range=[STATUS_GOOD, STATUS_CRITICAL, STATUS_WARNING],
                ),
                title="Outcome",
            ),
            tooltip=["source", "outcome_class", "n"],
        )
        .properties(height=320)
    )
    st.altair_chart(recon_chart, width="stretch")

    # --- Remediation ----------------------------------------------------------
    st.header("Remediation")
    st.caption(
        "ATT&CK mitigation IDs for the 23 reachable techniques the model has actually "
        "identified — not an exhaustive mitigation catalog. A blank mitigation means the "
        "technique genuinely has none listed on its real ATT&CK page, not a missing lookup."
    )
    if _relation_exists("mart_remediation"):
        remediation = _query(
            """
            select source, attack_id, display_name, mitigation_id, mitigation_name
            from mart_remediation
            order by source, attack_id
            """
        )
        st.dataframe(remediation, width="stretch", hide_index=True)
    else:
        st.info("mart_remediation hasn't been built yet — run `make transform`.")

except WarehouseBusyError as exc:
    st.warning(f"Warehouse busy, try again in a moment. ({exc})")
