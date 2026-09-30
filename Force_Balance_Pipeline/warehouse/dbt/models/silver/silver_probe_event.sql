{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

/*
  silver_probe_event -- Jong writes this one by hand (doc 00; the second of the three).

  Contract:
  - Inputs: ref('stg_bronze_events').
  - Filter: `kind IS NOT NULL` (doc 02, "Housekeeping events": `payload.kind` is reserved). Checked before the
    source_type/sector routing that feeds every other silver model (doc 03, "silver.rejects": "An event with a
    payload.kind goes to a probe-events path before the sector check").
  - Grain: one row per housekeeping event (today: buffer_overflow only, doc 02).
  - Materialization: incremental, merge on event_id. Insert-only, earliest arrival wins (doc 03,
    silver.probe_reading's dedup rule, which applies here too) -- a later duplicate of an already-landed
    housekeeping event never overwrites it.
  - Unique key: event_id.
  - Tests required: not_null(event_id), not_null(kind), accepted_values(kind, ['buffer_overflow']). A row that
    lands here must never also appear in silver_rejects or silver_probe_reading (doc 02) -- see
    _staging__unit_tests.yml's housekeeping-routing case for the proof that the routing itself is correct.

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round. No source SQL until Jong writes it.
*/

select null as placeholder
where false
