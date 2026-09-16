"""Harness/dbt feature cross-check (Phase 5 checkpoint).

docs/06 makes this a rule rather than a nicety: the separability harness
computes its features in SQL *precisely so* Phase 5's dbt models can lift the
same expressions, and "a divergence between the two on the same event corpus is
a bug in one of them, not an acceptable difference."

So this runs the harness's own `_FEATURE_SQL` — imported, not copied, so it
cannot drift — against the same rows the dbt models saw, and compares every
shared feature session by session.

Two things are controlled for, because otherwise the comparison would measure
the wrong difference:

- **Same input rows.** Both sides are fed from `stg_attack_events` (deduped,
  non-quarantined) rather than from the topic. The harness normally reads the
  topic raw, which still contains the ~2% duplicate-delivery copies; comparing
  that against a deduped dbt model would show a difference that is dedupe
  working correctly, not an expression divergence.
- **The 600/min cap.** The harness applies it in Python *after* its SQL
  (`separability._REQ_PER_MIN_CAP`), while the dbt model applies it in SQL. The
  cap is applied to the harness side here so both are capped, which is also why
  the dbt model had to carry it: lifting only the SQL would have diverged on
  exactly the burst sessions the cap exists for.

The truth-side features (`retry_ratio`, `pivot_ratio`,
`attempts_per_stage_reached`) live in `int_session_features_truth` because they
derive from the attempt log, so they are compared against that model rather
than the observed one — the boundary the lift had to be split along.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from services.simulator.separability import _FEATURE_SQL, _REQ_PER_MIN_CAP

# feature name -> which dbt model holds it. The split IS the boundary.
OBSERVED_FEATURES = [
    "request_count",
    "duration_s",
    "requests_per_min",
    "max_path_tier",
    "error_ratio",
    "distinct_source_ips",
    "distinct_user_agents",
    "inter_request_stddev_ms",
    "mean_response_time_ms",
    "riddle_param_count",
    "body_bytes_trend",
    "wasted_request_ratio",
    "path_entropy",
    "exact_duplicate_path_pairs",
]
TRUTH_FEATURES = [
    "retry_ratio",
    "pivot_ratio",
    "attempts_per_stage_reached",
]

TOLERANCE = 1e-9


def build_harness_features(con: duckdb.DuckDBPyConnection) -> None:
    """Populate the `events` table the harness SQL expects, from the warehouse,
    then run the harness's own query over it."""
    con.execute("drop table if exists events")
    con.execute(
        """
        create table events as
        select
            'request' as event_kind,
            session_id,
            received_at,
            path,
            status_returned,
            path_tier,
            source_ip,
            user_agent,
            query_string,
            cast(body_bytes as int) as body_bytes,
            cast(null as varchar) as decision,
            cast(null as int) as stage,
            response_time_ms
        from stg_attack_events
        where not is_quarantined
        union all
        select
            'attempt',
            session_id,
            received_at,
            null, null, null, null, null, null, null,
            decision,
            stage,
            null
        from stg_attack_attempts
        """
    )
    con.execute(f"create or replace table harness_features as {_FEATURE_SQL}")
    # Match the cap the harness applies in Python after its SQL.
    con.execute(
        "update harness_features set requests_per_min = least(requests_per_min, ?)",
        [_REQ_PER_MIN_CAP],
    )


def compare(con: duckdb.DuckDBPyConnection) -> bool:
    ok = True
    print("\n=== harness vs dbt, per session, on the same deduped rows ===")

    sessions = con.sql("select count(*) from harness_features").fetchone()[0]
    print(f"  sessions compared: {sessions}")
    if sessions == 0:
        print("  NOTHING TO COMPARE - build the warehouse first (make transform).")
        return False

    for model, features in (
        ("int_session_features_observed", OBSERVED_FEATURES),
        ("int_session_features_truth", TRUTH_FEATURES),
    ):
        print(f"\n  -- {model}")
        model_columns = {c for c in con.sql(f"select * from {model} limit 0").columns}
        for feature in features:
            if feature not in model_columns:
                print(f"     {feature:28} NOT IN MODEL  <-- feature missing")
                ok = False
                continue
            mismatches, worst = con.execute(
                f"""
                select
                    count(*) filter (
                        where abs(coalesce(h.{feature}, 0) - coalesce(d.{feature}, 0)) > {TOLERANCE}
                    ),
                    coalesce(max(abs(coalesce(h.{feature}, 0) - coalesce(d.{feature}, 0))), 0)
                from harness_features as h
                join {model} as d on h.session_id = d.session_id
                """
            ).fetchone()
            flag = "" if mismatches == 0 else f"   <-- {mismatches} sessions differ"
            print(f"     {feature:28} max_delta={worst:.12g}{flag}")
            if mismatches:
                ok = False

    print(f"\n{'PASS' if ok else 'FAIL'}: harness and dbt agree on every shared feature")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", type=Path, default=Path("data/warehouse.duckdb"))
    args = ap.parse_args()

    if not args.warehouse.exists():
        raise SystemExit(f"no warehouse at {args.warehouse} - run `make transform` first")

    con = duckdb.connect(str(args.warehouse))
    con.execute("SET TimeZone = 'UTC'")
    try:
        build_harness_features(con)
        ok = compare(con)
    finally:
        con.execute("drop table if exists events")
        con.execute("drop table if exists harness_features")
        con.close()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
