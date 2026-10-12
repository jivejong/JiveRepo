{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge', on_schema_change='append_new_columns') }}

{% raw %}
/*
  silver_force_report (doc 03, "silver.force_report"; doc 07 Phase 5). Valid report events: the complement of
  silver_report_rejects, the same two-outcome design as silver_probe_reading / silver_rejects.

  DBT CONCEPTS USED HERE: nothing new beyond silver_probe_event / silver_probe_reading (incremental, merge,
  insert-only, and a model built as the complement of its reject twin so the two can never disagree).
  `on_schema_change='append_new_columns'` (this model only; the project default is 'fail'): the Phase 4 stub was
  already built in prod, empty and without sector_id, which gold needs. A full refresh of a silver table is not
  allowed, and the table has no rows to lose, so the one new column is appended instead. Any further schema change
  here goes back to needing a deliberate decision, as with every other model: delete this line once the column is
  live everywhere.

  Contract:
  - Inputs: ref('stg_bronze_events') (source_type = 'report' and kind is null), ref('silver_report_rejects').
  - Grain: one row per valid report event_id.
  - Materialization: incremental, merge on event_id, insert-only, earliest arrival wins.
  - Unique key: event_id.
  - Dedupe: the earliest-arrival copy of an event_id within the batch is the only one considered (the same copy
    silver_report_rejects judged, so the two models agree); across batches an event_id already in this table is never
    touched, so a QoS 1 duplicate or a crash-retry republish (doc 04) never adds a row or overwrites the first.
  - Validity: the complement of silver_report_rejects (non-null, in range, known sector, event_time window, description
    non-blank -- see that model). `description` is carried VERBATIM (no trim, no case change, no normalisation).
  - Columns: the Phase 4 stub's (event_id, source_id, event_time, arrival_ts, description, relevance_score, the three
    inferred channels, inference_model, inference_rationale, reporter_id) plus sector_id, appended last.
  - Read by gold_sector_reading (scored against the PROBE-ONLY baseline) and gold_disturbance (relevance and
    description of a report-sourced or escalating incident). Never by gold_sector_baseline.
  - Tests required: not_null(event_id, sector_id, event_time, description), unique(event_id), relationships(sector_id ->
    dim_sector), relevance_score and the channels in range.
*/
{% endraw %}

with candidates as (

    select
        *,
        row_number() over (partition by event_id order by arrival_ts asc) as rn
    from {{ ref('stg_bronze_events') }}
    where source_type = 'report'
      and kind is null

),

valid as (

    -- the complement of silver_report_rejects, on the earliest copy only (see the header)
    select c.*
    from candidates c
    left join {{ ref('silver_report_rejects') }} r on c.event_id = r.event_id
    where c.rn = 1
      and r.event_id is null

)

select
    event_id,
    source_id,
    event_time,
    arrival_ts,
    description,
    relevance_score,
    midichlorian_ppm,
    kyber_resonance,
    dark_side_activity,
    inference_model,
    inference_rationale,
    {{ extract_payload('payload', 'reporter_id', 'string') }} as reporter_id,
    sector_id
from valid
{% if is_incremental() %}
where event_id not in (select event_id from {{ this }})
{% endif %}
