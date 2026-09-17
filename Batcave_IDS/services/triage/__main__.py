"""CLI for `make triage` and `make eval` (docs/04, docs/06 Phase 6).

`triage`: selects sessions at or above the triage threshold from
`mart_threat_scores`, runs the rule-based baseline on every one of them, and
additionally runs the LLM when `GROQ_API_KEY` is set - the documented
zero-credential path (docs/04's Configuration section) means baseline-only is
never an error, just what happens without a key.

`eval`: prints the tiered evaluation tables docs/04 asks for, straight from
the dbt evaluation marts. Read-only - run `triage` (and `dbt build`) first.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import duckdb

from services.triage.baseline import (
    build_reference_profiles,
    classify_techniques,
    classify_villain,
    villain_archetype_map,
)
from services.triage.context import FEATURE_COLUMNS, build_session_context
from services.triage.prompt import PROMPT_VERSION, build_system_prompt
from services.triage.store import (
    ensure_table,
    technique_id_to_attack_id,
    write_baseline_order,
    write_llm_order,
)

log = logging.getLogger("triage")

DEFAULT_WAREHOUSE = Path("data/warehouse.duckdb")
DEFAULT_LIMIT = 60


def _select_sessions(con: duckdb.DuckDBPyConnection, limit: int) -> list[tuple[str, str]]:
    """Sessions at or above the triage threshold (mart_threat_scores already
    applies docs/02's triage_threshold var), highest score first. This is the
    PRODUCTION trigger (docs/04, Phase 7's Dagster asset uses the same
    predicate) - deliberately narrow, because that's the point of a
    threshold."""
    return con.execute(
        """
        select session_id, run_id from mart_threat_scores
        where above_triage_threshold
        order by threat_score desc
        limit ?
        """,
        [limit],
    ).fetchall()


def _select_stratified_sessions(
    con: duckdb.DuckDBPyConnection, per_villain: int
) -> list[tuple[str, str]]:
    """`per_villain` sessions from EACH of the twelve villains, regardless of
    threat_score - for evaluation breadth, not the production trigger.

    Needed because the two purposes conflict: only 7 of 12 villains ever
    produce a session above the triage threshold on this corpus (docs/02's
    calibration note materializing at scale - Catwoman's mean threat_score is
    the lowest of all twelve and never crosses it). The production trigger is
    correctly narrow; scoring attribution and technique reconstruction fairly
    across the whole roster needs the other five villains too, or the
    evaluation would silently inherit the same blind spot the threshold has.
    """
    return con.execute(
        """
        select session_id, run_id from (
            select
                t.session_id, t.run_id,
                row_number() over (
                    partition by r.villain_slug order by t.threat_score desc
                ) as rn
            from mart_threat_scores as t
            inner join fct_attack_runs as r on t.run_id = r.run_id
        )
        where rn <= ?
        """,
        [per_villain],
    ).fetchall()


def _feature_dict(con: duckdb.DuckDBPyConnection, session_id: str) -> dict:
    row = con.execute(
        f"select {', '.join(FEATURE_COLUMNS)} from int_session_features_observed "
        "where session_id = ?",
        [session_id],
    ).fetchone()
    return dict(zip(FEATURE_COLUMNS, row, strict=True))


def run_triage(
    con: duckdb.DuckDBPyConnection, limit: int, stratified_per_villain: int | None = None
) -> None:
    ensure_table(con)
    if stratified_per_villain:
        sessions = _select_stratified_sessions(con, stratified_per_villain)
        print(f"stratified selection: up to {stratified_per_villain} sessions per villain")
    else:
        sessions = _select_sessions(con, limit)
    if not sessions:
        print("no sessions selected - nothing to do")
        return

    profiles = build_reference_profiles(con)
    id_map = technique_id_to_attack_id(con)
    archetypes = villain_archetype_map(con)

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    llm_client = None
    system_prompt = None
    known_villains: set[str] = set()
    known_attack_ids: set[str] = set()
    if groq_key:
        from groq import Groq

        llm_client = Groq(api_key=groq_key)
        system_prompt = build_system_prompt(con)
        known_villains = {
            r[0] for r in con.execute("select villain_slug from dim_villains").fetchall()
        }
        known_attack_ids = {
            r[0] for r in con.execute("select attack_id from dim_techniques").fetchall()
        }
        print(f"GROQ_API_KEY present - running LLM (prompt {PROMPT_VERSION}) + baseline")
    else:
        print("no GROQ_API_KEY - running baseline only (docs/04's zero-credential path)")

    for i, (session_id, run_id) in enumerate(sessions, 1):
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

        status = f"[{i}/{len(sessions)}] {session_id[:8]} baseline={predicted}"

        if llm_client is not None:
            from services.triage.llm import triage_session

            context = build_session_context(con, session_id)
            result = triage_session(
                llm_client,
                system_prompt,
                context,
                known_villains,
                known_attack_ids,
                prompt_version=PROMPT_VERSION,
            )
            write_llm_order(con, session_id, run_id, result)
            llm_guess = (
                "parse_failed"
                if result.parse_failed
                else (result.parsed or {}).get("suspected_villain")
            )
            status += f" llm={llm_guess} ({result.latency_ms:.0f}ms)"

        print(status)

    con.commit()
    print(f"\nwrote orders for {len(sessions)} sessions")


def _print_attribution_table(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== Task 1: Attribution ===")
    print(f"{'source':10} {'n':>5} {'exact':>8} {'top_3':>8} {'archetype':>10} {'parse_fail':>11}")
    for row in con.execute(
        """
        select
            o.source,
            count(*) as n,
            avg(case when e.exact_match then 1.0 else 0.0 end) as exact_rate,
            avg(case when e.top_3_match then 1.0 else 0.0 end) as top3_rate,
            avg(case when e.archetype_match then 1.0 else 0.0 end) as archetype_rate,
            avg(case when o.parse_failed then 1.0 else 0.0 end) as parse_fail_rate
        from fct_triage_evaluations as e
        inner join fct_intervention_orders as o on e.order_id = o.order_id
        group by o.source
        order by o.source
        """
    ).fetchall():
        source, n, exact, top3, arch, pf = row
        print(f"{source:10} {n:>5} {exact:>7.1%} {top3:>7.1%} {arch:>9.1%} {pf:>10.1%}")
    print("(random baselines: exact 8.3%, top_3 25.0%, archetype ~20%)")


def _print_confusion_matrix(con: duckdb.DuckDBPyConnection, source: str) -> None:
    print(f"\n=== Confusion matrix ({source}): true_villain -> suspected_villain (count) ===")
    rows = con.execute(
        """
        select e.true_villain, e.suspected_villain, count(*)
        from fct_triage_evaluations as e
        inner join fct_intervention_orders as o on e.order_id = o.order_id
        where o.source = ? and not o.parse_failed
        group by 1, 2 order by 1, 3 desc
        """,
        [source],
    ).fetchall()
    by_true: dict[str, list] = {}
    for true_v, pred_v, n in rows:
        by_true.setdefault(true_v, []).append((pred_v, n))
    for true_v in sorted(by_true):
        preds = ", ".join(f"{p}={n}" for p, n in sorted(by_true[true_v], key=lambda x: -x[1]))
        print(f"  {true_v:20} -> {preds}")


def _print_technique_table(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== Task 2: Technique reconstruction ===")
    print(f"{'source':10} {'precision':>10} {'recall':>8} {'f1':>6} {'hallucination_rate':>19}")
    for row in con.execute(
        """
        select
            o.source,
            sum(case when e.outcome_class = 'true_positive' then 1 else 0 end) as tp,
            sum(case when e.outcome_class = 'false_positive' then 1 else 0 end) as fp,
            sum(case when e.outcome_class = 'false_negative' then 1 else 0 end) as fn,
            avg(o.hallucinated_technique_count) as mean_hallucinations
        from fct_technique_evaluations as e
        inner join fct_intervention_orders as o on e.order_id = o.order_id
        group by o.source
        order by o.source
        """
    ).fetchall():
        source, tp, fp, fn, halluc = row
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        print(f"{source:10} {precision:>9.1%} {recall:>7.1%} {f1:>5.2f} {halluc:>19.2f}")


def _print_detection_coverage(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== mart_detection_coverage: recall by observability tier (the headline result) ===")
    header = (
        f"{'tier':18} {'techniques':>10} {'reachable':>9} {'attempts':>8} "
        f"{'source':>10} {'recall':>7}"
    )
    print(header)
    for row in con.execute(
        """
        select coverage_tier, techniques, reachable_techniques, attempts, source, recall
        from mart_detection_coverage
        where source is not null
        order by coverage_tier, source
        """
    ).fetchall():
        tier, techniques, reachable, attempts, source, recall = row
        recall_s = f"{recall:.1%}" if recall is not None else "n/a"
        print(f"{tier:18} {techniques:>10} {reachable:>9} {attempts:>8} {source:>10} {recall_s:>7}")


def run_eval(con: duckdb.DuckDBPyConnection) -> None:
    n_orders = con.execute("select count(*) from fct_intervention_orders").fetchone()[0]
    if n_orders == 0:
        print("no intervention orders - run `make triage` first")
        return
    _print_attribution_table(con)
    for source in ("baseline", "llm"):
        exists = con.execute(
            "select count(*) from fct_intervention_orders where source = ?", [source]
        ).fetchone()[0]
        if exists:
            _print_confusion_matrix(con, source)
    _print_technique_table(con)
    _print_detection_coverage(con)


def run_coverage(con: duckdb.DuckDBPyConnection) -> None:
    """`make coverage` - mart_detection_coverage on its own, since the tier/
    reachable/attempts columns are meaningful before any session has been
    triaged (recall by source is simply absent until orders exist)."""
    _print_detection_coverage(con)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["triage", "eval", "coverage"])
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--stratified-per-villain",
        type=int,
        default=None,
        help=(
            "ignore the triage threshold; take up to N sessions from EACH "
            "villain instead. For evaluation breadth (docs/06 Phase 6 "
            "checkpoint: 30+ sessions across all twelve villains) - the "
            "threshold alone only ever reaches 7 of 12 (docs/02's "
            "calibration note). Not the production trigger."
        ),
    )
    args = parser.parse_args()

    if not args.warehouse.exists():
        print(f"no warehouse at {args.warehouse} - run `make transform` first", file=sys.stderr)
        raise SystemExit(1)

    con = duckdb.connect(str(args.warehouse))
    con.execute("SET TimeZone = 'UTC'")
    try:
        if args.command == "triage":
            run_triage(con, args.limit, args.stratified_per_villain)
        elif args.command == "eval":
            run_eval(con)
        else:
            run_coverage(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
