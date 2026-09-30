{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

{% raw %}
/*
  silver_force_report -- stub until Phase 5 (doc 07, Phase 4 step 1: "force_report (stub until Phase 5)").

  DBT CONCEPTS USED HERE: nothing new beyond silver_probe_event's incremental/merge/insert-only pattern.
  Worth noting as a *stub* specifically: this model has real SQL (not the header-only placeholder the
  scaffolding round used), because report events already exist in bronze -- the web intake endpoint isn't
  built until Phase 5, but nothing stops a `source_type = 'report'` row from existing in bronze today (there
  are none yet; a query check for that is part of this round's real-data checks). The three inferred channel
  values and relevance_score are left as the raw extracted values now, rather than validated or clamped --
  Phase 5 owns that, not this stub.

  Contract:
  - Inputs: ref('stg_bronze_events').
  - Filter: `source_type = 'report'`.
  - Grain: one row per report event.
  - Materialization: incremental, merge on event_id, insert-only, earliest arrival wins.
  - Unique key: event_id.
  - Columns (doc 03, "silver.force_report"): description (verbatim), relevance_score, inference_model,
    inference_rationale, the three inferred channel values. Never read by anything built in Phase 4 --
    gold.disturbance's report-sourced branch is Phase 5.
  - Tests required: none meaningful until Phase 5 populates and uses these columns for real.
*/
{% endraw %}

with deduplicated as (

    select
        event_id,
        source_id,
        event_time,
        arrival_ts,
        {{ extract_payload('payload', 'description', 'string') }} as description,
        {{ extract_payload('payload', 'relevance_score', 'double') }} as relevance_score,
        {{ extract_payload('payload', 'midichlorian_ppm', 'double') }} as midichlorian_ppm,
        {{ extract_payload('payload', 'kyber_resonance', 'double') }} as kyber_resonance,
        {{ extract_payload('payload', 'dark_side_activity', 'double') }} as dark_side_activity,
        {{ extract_payload('payload', 'inference_model', 'string') }} as inference_model,
        {{ extract_payload('payload', 'inference_rationale', 'string') }} as inference_rationale,
        {{ extract_payload('payload', 'reporter_id', 'string') }} as reporter_id,
        row_number() over (partition by event_id order by arrival_ts asc) as rn
    from {{ ref('stg_bronze_events') }}
    where source_type = 'report'

)

select
    event_id, source_id, event_time, arrival_ts, description, relevance_score,
    midichlorian_ppm, kyber_resonance, dark_side_activity,
    inference_model, inference_rationale, reporter_id
from deduplicated
where rn = 1
{% if is_incremental() %}
  and event_id not in (select event_id from {{ this }})
{% endif %}
