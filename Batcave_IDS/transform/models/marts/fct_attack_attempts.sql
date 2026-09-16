{{ config(tags=["ground_truth"]) }}

/* GROUND TRUTH. One row per attempt, joined to its technique for reporting. */

select
    a.event_id,
    a.attempt_id,
    a.session_id,
    a.run_id,
    a.received_at,
    a.stage,
    a.technique_id,
    a.attack_id,
    a.attempt_seq,
    a.technique_attempt_seq,
    a.decision,
    a.computed_probability,
    a.roll,
    a.outcome,
    a.noise_generated,
    a.stage_entered_at,
    a.attempt_at,
    t.observability,
    t.produces_traffic,
    t.min_intelligence,
    a.dt,
    a.hour
from {{ ref('stg_attack_attempts') }} as a
left join {{ ref('stg_techniques') }} as t on a.technique_id = t.technique_id
