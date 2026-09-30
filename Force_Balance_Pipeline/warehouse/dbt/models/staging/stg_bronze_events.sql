{{ config(materialized='view') }}

/*
  stg_bronze_events -- Jong writes this one by hand (doc 00, "write the first three silver models by hand";
  this is the fundamentals model that precedes them).

  Contract:
  - Inputs: source('bronze', 'events') only.
  - Grain: one row per bronze row, unfiltered -- a straight pass-through plus typed extraction. No routing,
    no validation, no dedup happens here; those are silver's job (doc 03, "Silver" section intro).
  - Materialization: view. Bronze is already a table; this just adds typed columns over it, cheaply.
  - Unique key: none -- not deduplicated (bronze isn't either). event_id is not asserted unique at this layer.
  - Adds: the three extracted science channels + battery_pct + sensor_temp_c via extract_payload(); `kind` =
    extract_payload(payload, 'kind', 'string') for housekeeping routing (doc 02); `arrival_ts` =
    COALESCE(_file_modified_ts, _ingest_ts) for doc 03's ingest_lag_seconds definition.
  - Tests required: none at this layer (see _staging__models.yml) -- correctness here is exercised through the
    models built on top of it (silver_probe_event's housekeeping routing, silver_probe_reading's validation).
  - Unit-tested (models/staging/_staging__unit_tests.yml): the housekeeping-routing rule (a housekeeping event's
    `kind` is not null) and extract_payload's own JSON-null/missing-field/numeric-string behavior.

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round. No source SQL until Jong writes it.
*/

select null as placeholder
where false
