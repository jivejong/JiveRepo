{{ config(materialized='view') }}

{% raw %}
/*
  stg_bronze_events -- typed pass-through over force.bronze.events (doc 03, "bronze.events").

  DBT CONCEPTS USED HERE (this file, for a reader learning dbt):
  - `{{ source('bronze', 'events') }}`: refers to a table dbt does not build -- declared once in
    models/sources.yml (name, columns, freshness), not hardcoded here. Using source() instead of a literal
    table name is what lets `dbt docs generate` draw this into the lineage graph and `dbt source freshness`
    check it; a raw `FROM force.bronze.events` would do neither.
  - `{{ config(materialized='view') }}`: a config block, evaluated once when dbt builds this model.
    materialized='view' means dbt issues `CREATE VIEW`, not `CREATE TABLE` -- no storage, always current,
    cheap to keep in the DAG purely for the extraction logic below. Every downstream model reads this view
    through `{{ ref('stg_bronze_events') }}`, never the source directly -- ref() is what makes dbt build
    models in the right order and is what the lineage graph is drawn from.
  - `{{ extract_payload(...) }}`: a macro call. A macro (macros/extract_payload.sql) is a reusable
    Jinja+SQL snippet; this one isolates the one place a Databricks build and a (deferred, Phase 8) Postgres
    build would genuinely diverge (doc 05, "Portability macro"), so no model calls raw VARIANT syntax
    directly.
  - `case when is_synthetic then ... else ... end as arrival_ts`: ordinary SQL, but note WHY it's here and
    not left for every downstream model to repeat -- doc 03's `ingest_lag_seconds` definition needs this
    exact CASE, and both silver_rejects and silver_probe_reading need it. Computing it once here, at the
    layer closest to the source, is the same "don't repeat yourself" reasoning that justifies staging models
    in dbt generally.

  Contract:
  - Inputs: source('bronze', 'events') only.
  - Grain: one row per bronze row, unfiltered -- a straight pass-through plus typed extraction. No routing,
    no validation, no dedup happens here; those are silver's job (doc 03, "Silver" section intro).
  - Materialization: view.
  - Unique key: none -- not deduplicated (bronze isn't either); event_id can repeat.
  - Added columns: the three science channels, battery_pct, sensor_temp_c and kind, all via extract_payload();
    arrival_ts and ingest_lag_seconds, doc 03's own definitions (silver.probe_reading), computed once here
    because silver_rejects needs the same lag for its timestamp-bounds check, not just silver_probe_reading.
*/
{% endraw %}

select
    event_id,
    source_id,
    source_type,
    mode,
    scan_id,
    sector_id,
    schema_version,
    event_time,
    is_synthetic,
    synthetic_ingest_ts,
    payload,
    dt,
    hh,
    _source_file,
    _ingest_ts,
    _file_modified_ts,
    _rescued_data,
    case when is_synthetic then synthetic_ingest_ts else coalesce(_file_modified_ts, _ingest_ts) end as arrival_ts,
    {{ epoch_seconds('case when is_synthetic then synthetic_ingest_ts else coalesce(_file_modified_ts, _ingest_ts) end') }}
        - {{ epoch_seconds('event_time') }} as ingest_lag_seconds,
    {{ extract_payload('payload', 'kind', 'string') }} as kind,
    {{ extract_payload('payload', 'midichlorian_ppm', 'double') }} as midichlorian_ppm,
    {{ extract_payload('payload', 'kyber_resonance', 'double') }} as kyber_resonance,
    {{ extract_payload('payload', 'dark_side_activity', 'double') }} as dark_side_activity,
    {{ extract_payload('payload', 'sensor_temp_c', 'double') }} as sensor_temp_c,
    {{ extract_payload('payload', 'battery_pct', 'int') }} as battery_pct
from {{ source('bronze', 'events') }}
