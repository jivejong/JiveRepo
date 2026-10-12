{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

{% raw %}
/*
  silver_report_rejects -- the validation failures of silver_force_report: the same two-outcome idea as
  silver_rejects / silver_probe_reading, kept in a parallel model rather than by extending silver_rejects.
  Why parallel (decided Phase 5, Round 5): silver_rejects is the other half of the fault-injection reconciliation
  (doc 04: "the count of rejects must equal the count in the fault log") and has no source_type column; mixing
  report rejects into it would put a different population into a count that has to stay 1:1 with the probe fault
  log, and adding the column would be a schema change on a live incremental table. A report is rejected here for
  reasons the dashboard can show the reporter's operator ("why did my report not count?"), never silently dropped.

  Contract:
  - Inputs: ref('stg_bronze_events') (source_type = 'report' and kind is null), ref('dim_sector').
  - Grain: one row per rejected report event_id.
  - Materialization: incremental, merge on event_id, insert-only, earliest arrival wins.
  - Unique key: event_id.
  - Dedupe BEFORE validation: the earliest-arrival copy of an event_id is the only copy that is judged, so a QoS 1
    duplicate (or a crash-retry republish under the same event_id, doc 04) can neither add a row nor flip a verdict
    inside a batch. Across batches an event_id already in this table is never touched again (the anti-join below).
  - Reason precedence, first match wins (doc 03 "silver.rejects" order and vocabulary, applied to reports):
      1. unknown_schema_version -- schema_version <> 1
      2. impossible_timestamp   -- event_time more than var('report_future_tolerance_minutes') ahead of arrival_ts, or
                                    more than var('report_max_age_days') behind it. These are sanity bounds, NOT the
                                    intake's own 7-day window: the intake already enforces that against its own clock
                                    (intake_core.OBSERVED_MAX_AGE), and arrival_ts here is bronze arrival, which trails
                                    the intake by the bridge's flush plus Job 1's cadence, and by days when the bridge
                                    or Databricks is down while the broker holds the message. A report the intake
                                    accepted must never be rejected here for age, so the age bound is the probe's 90
                                    days (doc 03 gives only the probe bounds) and the future bound is the intake's 10
                                    minutes. test_dbt_doc_parity.py asserts both are at least as wide as the intake's.
      3. unknown_sector         -- sector_id null, not in dim_sector, or the 'uncharted' placeholder (is_unknown)
      4. null_required_field    -- description null or blank (whitespace only), or relevance_score or any of the three
                                    inferred channels null. Unlike a probe, no channel is optional: a report always
                                    carries all three (there is no STEALTH for reports). A null payload makes every
                                    extracted field null and is caught here.
      5. out_of_range           -- relevance_score outside 0-1, or a channel outside its doc 02 range (0-30000 ppm,
                                    0-100, 0-100). NaN and infinities compare greater than every bound in Spark and
                                    land here too.
  - description is judged blank by a whitespace-only match (spaces, tabs, newlines) only to decide that; it is never changed --
    silver_force_report carries it verbatim.
  - Tests required: not_null(event_id), unique(event_id), not_null(reject_reason), accepted_values(reject_reason).
    A row here never also appears in silver_probe_reading, silver_rejects or silver_force_report.
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

first_copy as (

    select * from candidates where rn = 1

),

classified as (

    select
        *,
        case
            when schema_version <> 1
                then 'unknown_schema_version'
            when ingest_lag_seconds < (-60 * {{ var('report_future_tolerance_minutes') }})
                then 'impossible_timestamp'
            when ingest_lag_seconds > (86400 * {{ var('report_max_age_days') }})
                then 'impossible_timestamp'
            when sector_id is null
              or sector_id not in (select sector_id from {{ ref('dim_sector') }} where not coalesce(is_unknown, false))
                then 'unknown_sector'
            when description is null or description rlike '^\\s*$'
                then 'null_required_field'
            when relevance_score is null or midichlorian_ppm is null or kyber_resonance is null or dark_side_activity is null
                then 'null_required_field'
            when relevance_score < 0 or relevance_score > 1
                then 'out_of_range'
            when midichlorian_ppm < 0 or midichlorian_ppm > 30000
                then 'out_of_range'
            when kyber_resonance < 0 or kyber_resonance > 100
                then 'out_of_range'
            when dark_side_activity < 0 or dark_side_activity > 100
                then 'out_of_range'
            else null
        end as reject_reason
    from first_copy

)

select
    event_id,
    source_id,
    sector_id,
    event_time,
    arrival_ts,
    reject_reason,
    description,
    relevance_score,
    midichlorian_ppm,
    kyber_resonance,
    dark_side_activity
from classified
where reject_reason is not null
{% if is_incremental() %}
  and event_id not in (select event_id from {{ this }})
{% endif %}
