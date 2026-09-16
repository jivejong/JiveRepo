/*
No session enters stage N+1 without clearing stage N (docs/02).

Stage 1 (Network Intrusion) is the entry point, not a gated stage: stage 0 is
Reconnaissance, which Lex Luthor performs offstage and which produces no
attempts at all (verified — the attempt log contains stages 1-4 only, and all
144 sessions start at stage 1). Requiring stage 0 to be cleared before stage 1
would fail every session for a reason that has nothing to do with the stage
machine, so the gate check starts at stage 2.

A real violation means the stage machine let an attacker skip a stage, which
would invalidate mart_killchain_funnel's conversion rates.
*/

with entered as (
    select distinct
        session_id,
        stage
    from {{ ref('int_stage_progression') }}
),

cleared as (
    select
        session_id,
        stage
    from {{ ref('int_stage_progression') }}
    where stage_cleared
)

select
    e.session_id,
    e.stage as entered_stage,
    e.stage - 1 as unmet_prerequisite
from entered as e
left join cleared as c
    on e.session_id = c.session_id and c.stage = e.stage - 1
where
    e.stage > 1
    and c.stage is null
