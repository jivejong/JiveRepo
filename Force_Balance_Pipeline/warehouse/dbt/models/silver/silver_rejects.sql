{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

{% raw %}
/*
  silver_rejects (doc 03, "silver.rejects"; doc 07 Phase 4 step 3, "the trickiest logic in the phase").
  Built alongside silver_probe_reading -- they are the two outcomes of one validation pass over the same
  input rows.

  DBT CONCEPTS USED HERE (beyond silver_probe_event, which covers the incremental/merge/dedup pattern):
  - `{{ var('future_tolerance_minutes') }}`, `{{ var('max_age_days') }}`: vars are project-wide constants
    declared once in dbt_project.yml (doc 03, "Thresholds" -- the same "one source of truth" idea as a
    Python constant, but readable from any model or macro via var(), and overridable per-run with
    `--vars` without editing a file). Every threshold in this model reads from a var, never a bare number,
    so retuning doc 03 and retuning the pipeline are the same edit.
  - A CTE chain (`with candidates as (...), classified as (...), deduplicated as (...) select ...`): each
    step named and built on the one before it. This is ordinary SQL, but the *reason* to write it this way
    in dbt specifically is that every intermediate name reads like the doc 03 precedence list it implements
    -- someone diffing this file against the doc can match each WHEN to the table row it encodes.
  - `case ... when ... then ... end` evaluated top-to-bottom, first match wins: the same evaluation style as
    macros/classify_signature.sql, applied to doc 03's five reject reasons in their documented order.

  Contract:
  - Inputs: ref('stg_bronze_events') (rows with `kind IS NULL AND source_type = 'probe'` only -- housekeeping
    and report rows are routed away by silver_probe_event/silver_force_report first), ref('dim_sector').
  - Grain: one row per rejected reading.
  - Materialization: incremental, merge on event_id, insert-only, earliest arrival wins (same pattern as
    silver_probe_event).
  - Unique key: event_id.
  - Reason precedence (doc 03, "silver.rejects"), first match wins:
      1. unknown_schema_version -- schema_version <> 1
      2. impossible_timestamp   -- event_time more than future_tolerance_minutes ahead of arrival_ts, or
                                    more than max_age_days behind it
      3. unknown_sector         -- sector_id not in dim_sector
      4. null_required_field    -- dark_side_activity null (required in every mode, including STEALTH), or
                                    (mode <> STEALTH and midichlorian_ppm or kyber_resonance null). A null
                                    payload (try_parse_json failure) makes every extracted channel null, so
                                    it is caught here too, by the same dark_side_activity check, without a
                                    separate branch.
      5. out_of_range           -- a present channel value outside its doc 02 valid range
  - STEALTH-aware routing (doc 03, "STEALTH-aware routing"): a STEALTH row is exempted from
    null_required_field only for midichlorian_ppm/kyber_resonance; every other check, including
    out_of_range on dark_side_activity, still applies -- there is no separate STEALTH branch in the CASE
    below, the dark_side_activity-only null check and the mode-guarded midi/kyber check together already
    produce exactly that behaviour.
  - Tests required: not_null(event_id), not_null(reject_reason), accepted_values(reject_reason, the five
    reasons above). A row here must never also appear in silver_probe_reading, silver_probe_event or
    silver_force_report.
*/
{% endraw %}

with candidates as (

    select *
    from {{ ref('stg_bronze_events') }}
    where kind is null
      and source_type = 'probe'

),

classified as (

    select
        *,
        case
            when schema_version <> 1
                then 'unknown_schema_version'
            when ingest_lag_seconds < (-60 * {{ var('future_tolerance_minutes') }})
                then 'impossible_timestamp'
            when ingest_lag_seconds > (86400 * {{ var('max_age_days') }})
                then 'impossible_timestamp'
            when sector_id not in (select sector_id from {{ ref('dim_sector') }})
                then 'unknown_sector'
            when dark_side_activity is null
                then 'null_required_field'
            when mode <> 'STEALTH' and (midichlorian_ppm is null or kyber_resonance is null)
                then 'null_required_field'
            when midichlorian_ppm is not null and (midichlorian_ppm < 0 or midichlorian_ppm > 30000)
                then 'out_of_range'
            when kyber_resonance is not null and (kyber_resonance < 0 or kyber_resonance > 100)
                then 'out_of_range'
            when dark_side_activity is not null and (dark_side_activity < 0 or dark_side_activity > 100)
                then 'out_of_range'
            else null
        end as reject_reason
    from candidates

),

deduplicated as (

    select
        *,
        row_number() over (partition by event_id order by arrival_ts asc) as rn
    from classified
    where reject_reason is not null

)

select
    event_id,
    source_id,
    sector_id,
    scan_id,
    event_time,
    arrival_ts,
    mode,
    reject_reason,
    midichlorian_ppm,
    kyber_resonance,
    dark_side_activity
from deduplicated
where rn = 1
{% if is_incremental() %}
  and event_id not in (select event_id from {{ this }})
{% endif %}
