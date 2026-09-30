{{ config(materialized='table') }}

{% raw %}
/*
  silver_source_health (doc 03, "silver.source_health"). Named source_health per doc 03/07 (doc 05's project
  layout originally said silver_device_heartbeat -- fixed to match).

  DBT CONCEPTS USED HERE:
  - `{{ config(materialized='table') }}`, no incremental config: this model asks "what is the latest state
    right now?", which has no natural incremental story -- every run needs to look at the whole history
    again to know what "latest" currently means. Full rebuild every run (`CREATE OR REPLACE TABLE ... AS`)
    is the correct, simple choice here, not a missed optimization; contrast with silver_probe_event and
    silver_rejects/silver_probe_reading, which are append-only history and incremental for exactly the
    opposite reason.
  - `qualify row_number() over (...) = 1`: `QUALIFY` filters on a window function's result without wrapping
    the query in an extra CTE just to reference it in a WHERE clause -- a Databricks/Spark SQL extension
    (not standard ANSI SQL, which is why the incremental models above use a CTE + WHERE instead: this
    project also builds against Postgres eventually, doc 07, and QUALIFY has no Postgres equivalent, so it's
    reserved for models -- like this one -- that don't participate in that portability effort. Silver here
    still runs through extract_payload/epoch_seconds either way).

  Contract:
  - Inputs: ref('stg_bronze_events') (last-seen event per source_id and mode), ref('silver_probe_event')
    (buffer_overflow history, for a future overflow-count column -- not built this round).
  - Grain: one row per source_id, latest state only.
  - Materialization: table, full rebuild every run.
  - Unique key: source_id.
  - Columns (doc 03): current mode, last_seen_at (event_time of the latest row), last_scan_id,
    scan_completeness (did the last scan_id contain all 60 planets?). buffer depth is left null -- doc 03
    itself notes no payload currently reports it.
  - Tests required: not_null(source_id), unique(source_id).
*/
{% endraw %}

with latest_event as (

    select *
    from {{ ref('stg_bronze_events') }}
    where source_type = 'probe'
    qualify row_number() over (partition by source_id order by event_time desc, arrival_ts desc) = 1

),

last_scan_size as (

    select
        source_id,
        scan_id,
        count(*) as sector_count
    from {{ ref('stg_bronze_events') }}
    where source_type = 'probe' and scan_id is not null
    group by source_id, scan_id

)

select
    e.source_id,
    e.mode as current_mode,
    e.event_time as last_seen_at,
    e.scan_id as last_scan_id,
    s.sector_count = 60 as scan_completeness,
    cast(null as int) as buffer_depth  -- doc 03: no payload currently reports this
from latest_event e
left join last_scan_size s on e.source_id = s.source_id and e.scan_id = s.scan_id
