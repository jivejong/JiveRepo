{{ config(tags=["ground_truth"]) }}

/* GROUND TRUTH, and the model that carries villain identity. */

select
    r.event_id,
    r.run_id,
    r.session_id,
    r.villain_slug,
    v.archetype,
    r.started_at,
    r.ended_at,
    r.duration_s,
    r.requests_sent,
    r.attempts_made,
    r.max_stage_reached,
    r.run_outcome,
    r.pathologies_enabled,
    r.timing_compression_factor,
    r.session_source,
    r.dt,
    r.hour
from {{ ref('stg_attack_runs') }} as r
left join {{ ref('stg_villains') }} as v on r.villain_slug = v.slug
