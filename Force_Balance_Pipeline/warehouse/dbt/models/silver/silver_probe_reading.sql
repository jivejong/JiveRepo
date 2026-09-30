{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

{% raw %}
/*
  silver_probe_reading (doc 03, "silver.probe_reading"; doc 07 Phase 4 step 3, "the trickiest logic in the
  phase"). The other outcome of the same validation pass as silver_rejects -- a row lands in exactly one of
  the two, never both, because both models read the same candidates CTE shape and apply complementary halves
  of the same CASE logic (see silver_rejects.sql's header for the dbt concepts shared between them).

  Contract:
  - Inputs: ref('stg_bronze_events') (same pre-filtered population as silver_rejects: `kind IS NULL AND
    source_type = 'probe'`), ref('dim_sector') (sector_id validity).
  - Grain: one row per valid (or STEALTH-partial) probe reading.
  - Materialization: incremental, merge on event_id, insert-only, earliest arrival wins -- a later,
    higher-latency copy of an already-landed row never flips is_replayed/was_buffered on it (doc 03).
  - Unique key: event_id.
  - Added columns (doc 03):
      ingest_lag_seconds -- computed once in stg_bronze_events, carried through unchanged
      is_replayed      = ingest_lag_seconds > var('replay_lag_seconds')  -- "arrived >30 min late", a lag
                          threshold, not a direct buffer signal (doc 03, "Lag precision")
      was_buffered      = mode = 'DISCONNECTED' -- the direct signal doc 03:104 originally meant
      is_partial        = any of the three channels null (true in STEALTH)
      channels_present  = count of the three channels that are non-null, 1-3
  - Routing: a row reaches here only if it passed every silver_rejects check -- this model does not
    re-implement the CASE, it simply selects the complement (LEFT JOIN silver_rejects, keep unmatched) so the
    two models can never disagree about which rows landed where by drifting out of sync with each other.
  - Tests required: not_null(event_id, sector_id, event_time), relationships(sector_id -> dim_sector.sector_id),
    channels_present between 1 and 3, is_partial = (channels_present < 3), ingest_lag_seconds >=
    var('lag_tolerance_seconds'), synthetic_ingest_ts null iff NOT is_synthetic.
*/
{% endraw %}

with candidates as (

    select *
    from {{ ref('stg_bronze_events') }}
    where kind is null
      and source_type = 'probe'

),

valid as (

    -- The complement of silver_rejects: a candidate row that does NOT appear there passed every check.
    -- ref('silver_rejects') here is the standard dbt way two models that partition the same input stay in
    -- sync -- if silver_rejects' CASE changes, this LEFT JOIN's population changes with it automatically,
    -- with no duplicated validation logic to keep in step by hand.
    select c.*
    from candidates c
    left join {{ ref('silver_rejects') }} r on c.event_id = r.event_id
    where r.event_id is null

),

enriched as (

    select
        *,
        ingest_lag_seconds > {{ var('replay_lag_seconds') }} as is_replayed,
        mode = 'DISCONNECTED' as was_buffered,
        (midichlorian_ppm is null or kyber_resonance is null or dark_side_activity is null) as is_partial,
        (case when midichlorian_ppm is not null then 1 else 0 end
       + case when kyber_resonance is not null then 1 else 0 end
       + case when dark_side_activity is not null then 1 else 0 end) as channels_present,
        row_number() over (partition by event_id order by arrival_ts asc) as rn
    from valid

)

select
    event_id,
    source_id,
    sector_id,
    scan_id,
    event_time,
    arrival_ts,
    mode,
    is_synthetic,
    synthetic_ingest_ts,
    ingest_lag_seconds,
    is_replayed,
    was_buffered,
    is_partial,
    channels_present,
    midichlorian_ppm,
    kyber_resonance,
    dark_side_activity,
    sensor_temp_c,
    battery_pct,
    _rescued_data
from enriched
where rn = 1
{% if is_incremental() %}
  and event_id not in (select event_id from {{ this }})
{% endif %}
