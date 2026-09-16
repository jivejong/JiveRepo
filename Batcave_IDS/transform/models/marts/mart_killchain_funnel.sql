{{ config(tags=["ground_truth"]) }}

/* Sessions entering and clearing each stage, by villain and archetype
   (docs/02). Ground truth: it reports which villain ran each session and how
   far they actually got.

   Printed at the Phase 5 checkpoint alongside mart_reconciliation. */

with entered as (
    select
        p.stage,
        r.villain_slug,
        r.archetype,
        count(distinct p.session_id) as sessions_entered,
        count(distinct case when p.stage_cleared then p.session_id end) as sessions_cleared,
        avg(p.attempts_in_stage) as mean_attempts_in_stage,
        avg(p.dwell_time_s) as mean_dwell_time_s
    from {{ ref('int_stage_progression') }} as p
    inner join {{ ref('fct_attack_runs') }} as r on p.run_id = r.run_id
    group by p.stage, r.villain_slug, r.archetype
)

select
    e.stage,
    s.stage_name,
    s.attack_tactic_name,
    e.villain_slug,
    e.archetype,
    e.sessions_entered,
    e.sessions_cleared,
    e.mean_attempts_in_stage,
    e.mean_dwell_time_s,
    e.sessions_cleared * 1.0 / nullif(e.sessions_entered, 0) as clear_rate
from entered as e
left join {{ ref('dim_stages') }} as s on e.stage = s.stage
