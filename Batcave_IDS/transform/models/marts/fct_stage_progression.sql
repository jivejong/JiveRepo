{{ config(tags=["ground_truth"]) }}

/* GROUND TRUTH. Session-stage grain, joined to the stage dimension. */

select
    p.session_id,
    p.run_id,
    p.stage,
    s.stage_name,
    s.attack_tactic_id,
    s.attack_tactic_name,
    p.attempts_in_stage,
    p.successes_in_stage,
    p.pivots_in_stage,
    p.retries_in_stage,
    p.first_attempt_at,
    p.stage_entered_at,
    p.stage_cleared_at,
    p.stage_cleared,
    p.dwell_time_s
from {{ ref('int_stage_progression') }} as p
left join {{ ref('dim_stages') }} as s on p.stage = s.stage
