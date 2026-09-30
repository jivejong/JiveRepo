{{ config(materialized='table') }}

/*
  silver_source_health -- Jong writes this one by hand (doc 00; the third of the three). Named source_health per
  doc 03/07 (doc 05's project layout listing originally said silver_device_heartbeat -- fixed to match).

  Contract:
  - Inputs: ref('stg_bronze_events') (for last-seen/mode/scan-completeness), possibly ref('silver_probe_event')
    for buffer-overflow history -- decide when writing.
  - Grain: one row per source_id, latest state only.
  - Materialization: table (full rebuild each run -- "latest state" has no useful incremental story at this
    volume).
  - Unique key: source_id.
  - Columns (doc 03, "silver.source_health"): current mode, last seen (event_time or arrival_ts -- decide),
    buffer depth if reported (doc 03 notes no payload currently reports it -- likely null for now), scan
    completeness (did the last scan_id contain all 60 planets?).
  - Tests required: not_null(source_id), unique(source_id).

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round. No source SQL until Jong writes it.
*/

select null as placeholder
where false
