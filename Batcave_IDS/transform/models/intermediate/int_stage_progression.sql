{{ config(tags=["ground_truth"]) }}

/*
GROUND TRUTH. One row per session-stage (docs/02): how long the attacker spent
in each stage of the kill chain and whether they cleared it.

Feeds mart_killchain_funnel and assert_stage_monotonic. Named explicitly in
assert_no_ground_truth_leakage's forbidden list, so no triage_input model may
reach it even if its tag were dropped.
*/

select
    session_id,
    stage,
    min(run_id) as run_id,

    count(*) as attempts_in_stage,
    sum(case when outcome = 'success' then 1 else 0 end) as successes_in_stage,
    sum(case when decision = 'pivot' then 1 else 0 end) as pivots_in_stage,
    sum(case when decision = 'retry' then 1 else 0 end) as retries_in_stage,

    min(attempt_at) as first_attempt_at,
    min(stage_entered_at) as stage_entered_at,

    /* The moment the stage was cleared: the first successful attempt in it.
       Null for a stage the session entered but never cleared — the stall. */
    min(case when outcome = 'success' then attempt_at end) as stage_cleared_at,
    max(coalesce(outcome = 'success', false)) as stage_cleared,

    /* Dwell time: entering the stage until clearing it, or until the last
       attempt in it for a stage that was never cleared. */
    epoch(
        coalesce(
            min(case when outcome = 'success' then attempt_at end),
            max(attempt_at)
        )
    ) - epoch(min(stage_entered_at)) as dwell_time_s
from {{ ref('stg_attack_attempts') }}
group by session_id, stage
