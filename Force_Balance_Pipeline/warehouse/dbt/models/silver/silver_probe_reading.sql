{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

/*
  silver_probe_reading (doc 03, "silver.probe_reading"; doc 07 Phase 4 step 3, "the trickiest logic in the
  phase"). Built together with silver_rejects -- they are the two outcomes of the same validation pass.

  Contract:
  - Inputs: ref('stg_bronze_events') (same pre-filtered population as silver_rejects: `kind IS NULL AND
    source_type = 'probe'`), ref('dim_sector') (sector_id validity).
  - Grain: one row per valid (or STEALTH-partial) probe reading.
  - Materialization: incremental, merge on event_id. Insert-only, earliest arrival wins (doc 03): a later,
    higher-latency copy of an already-landed row never flips is_replayed/was_buffered on it.
  - Unique key: event_id.
  - Added columns (doc 03):
      ingest_lag_seconds = epoch_seconds(arrival_ts) - epoch_seconds(event_time), where arrival_ts is
        synthetic_ingest_ts for is_synthetic rows, else COALESCE(_file_modified_ts, _ingest_ts)
      is_replayed      = ingest_lag_seconds > var('replay_lag_seconds')  -- "arrived >30 min late", a lag
                          threshold, not a direct buffer signal
      was_buffered      = mode = 'DISCONNECTED' -- the direct signal doc 03:104 originally meant
      is_partial        = any of the three channels null (true in STEALTH)
      channels_present  = count of the three channels that are non-null, 1-3
  - Routing: the same STEALTH-aware pass as silver_rejects (see that model's header for the full rule) -- a row
    that clears validation lands here with is_partial/channels_present set; STEALTH's two expected nulls
    (midichlorian_ppm, kyber_resonance) never trigger null_required_field.
  - Tests required: not_null(event_id, sector_id, event_time), relationships(sector_id -> dim_sector.sector_id),
    channels_present between 1 and 3, is_partial = (channels_present < 3), ingest_lag_seconds >=
    var('lag_tolerance_seconds') (doc 03, "Lag precision": -2, not 0 -- _file_modified_ts's 1-second rounding and
    the Phase 2 desktop clock's ~2 s skew both produce small negative lags), synthetic_ingest_ts null iff NOT
    is_synthetic.
  - Unit-tested (models/silver/_silver__unit_tests.yml): the is_replayed 1,799/1,800/1,801-second boundary
    (doc 03: strict `>`, so 1,800 itself is NOT replayed).

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round.
*/

select null as placeholder
where false
