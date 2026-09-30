{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

/*
  silver_rejects (doc 03, "silver.rejects"; doc 07 Phase 4 step 3, "the trickiest logic in the phase").

  Contract:
  - Inputs: ref('stg_bronze_events') (rows with `kind IS NULL` and `source_type = 'probe'` only -- housekeeping
    and report rows never reach this model, routed away by silver_probe_event/silver_force_report first).
  - Grain: one row per rejected reading.
  - Materialization: incremental, merge on event_id. Insert-only, earliest arrival wins (doc 03).
  - Unique key: event_id.
  - Reason precedence (doc 03, "silver.rejects"), first match wins, same evaluation style as classify_signature:
      1. unknown_schema_version -- schema_version <> 1
      2. impossible_timestamp   -- event_time more than var('future_tolerance_minutes') ahead of arrival_ts
                                    (COALESCE(_file_modified_ts, _ingest_ts), or synthetic_ingest_ts for backfill
                                    rows), or more than var('max_age_days') behind it
      3. unknown_sector         -- sector_id not in dim_sector
      4. null_required_field    -- a channel is null outside of STEALTH, or payload itself is NULL
      5. out_of_range           -- channel value outside its valid range (doc 02)
  - STEALTH-aware routing (this round's answer to "can faults.py fire on a STEALTH scan?", proposed but not
    built into faults.py -- edge/probe/runtime.py:148 already gates fault injection to CONNECTED only, so this
    is defense in depth, not a reaction to an observed bug): a `mode = 'STEALTH'` row skips reason 4 only for
    midichlorian_ppm and kyber_resonance (expected null there, doc 02); every other check -- 1, 2, 3, and 5 on
    dark_side_activity, the one channel STEALTH actually reports -- still applies. A STEALTH row that passes
    still routes to silver_probe_reading with is_partial = true; a STEALTH row that fails reason 2, 3 or 5 still
    lands here with the correct reason. This is a PENDING DOC EDIT to doc 03 (not yet applied -- see this
    round's report), so this header is the only place it is written down for now.
  - Tests required: not_null(event_id), not_null(reject_reason), accepted_values(reject_reason, the five reasons
    above). A row here must never also appear in silver_probe_reading, silver_probe_event or silver_force_report.
  - Unit-tested (models/silver/_silver__unit_tests.yml): the impossible_timestamp bounds (9/11/1441/2161 minutes
    either side of the two thresholds).

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round.
*/

select null as placeholder
where false
