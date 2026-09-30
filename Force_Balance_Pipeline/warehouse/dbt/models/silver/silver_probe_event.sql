{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

{% raw %}
/*
  silver_probe_event -- housekeeping events (doc 02, "Housekeeping events"), routed before the sector/
  validation checks every other silver model applies.

  DBT CONCEPTS USED HERE:
  - `{{ config(materialized='incremental', ...) }}`: this model is a TABLE that dbt only ever adds rows to
    (mostly) -- on the first run it builds the whole thing; on every later run it only processes new source
    rows, which is far cheaper than rebuilding from scratch every time (this table only grows by a handful
    of rows a day, but the pattern is the same one gold.sector_reading and gold.disturbance use at real
    volume). `unique_key='event_id'` plus `incremental_strategy='merge'` tells dbt to generate a SQL
    `MERGE INTO ... USING (this model's SELECT) ON event_id = event_id`.
  - `{% if is_incremental() %} ... {% endif %}`: is_incremental() is a Jinja function that is only true when
    (a) this model is materialized incremental, AND (b) the table already exists (the first-ever run is
    always a full build, since there's nothing to be incremental against yet). The condition inside decides
    what counts as "new" on every run after the first.
  - `{{ this }}`: inside an incremental model, `this` refers to the model's own already-built table --
    used here to check "is this event_id already in the table I'm building?"
  - Why NOT MATCHED only, effectively: dbt's merge strategy by default both updates matching rows and
    inserts new ones. This model needs INSERT-ONLY, earliest-arrival-wins behaviour (doc 03,
    silver.probe_reading's dedup rule, which the same insert-only reasoning applies to every silver model):
    a later, higher-latency duplicate of an already-landed event_id must never overwrite it. Rather than
    fight the merge strategy's default UPDATE behaviour, the SELECT itself excludes any event_id already in
    `{{ this }}`, so the merge's WHEN MATCHED branch is simply never reached for this model's rows -- an
    ordinary MERGE, given a source that never contains an already-matched key.
  - `row_number() over (partition by event_id order by arrival_ts asc)`: a window function, doc-parity
    reasoning applied within dbt: two copies of the SAME event_id can appear in the SAME incremental batch
    (not just across separate runs), because Auto Loader can ingest both the original and a replayed copy of
    a scan in one notebook run. Filtering to rn = 1 picks the earliest arrival_ts of the two, inside this run,
    before the cross-run "already in {{ this }}" check even applies.

  Contract:
  - Inputs: ref('stg_bronze_events').
  - Filter: `kind IS NOT NULL` (doc 02: `payload.kind` is reserved). Checked before the source_type/sector
    routing every other silver model applies (doc 03, "silver.rejects": "An event with a payload.kind goes
    to a probe-events path before the sector check").
  - Grain: one row per housekeeping event (today: buffer_overflow only, doc 02).
  - Materialization: incremental, merge on event_id, insert-only, earliest arrival wins.
  - Unique key: event_id.
*/
{% endraw %}

with deduplicated as (

    select
        event_id,
        source_id,
        sector_id,
        event_time,
        arrival_ts,
        kind,
        {{ extract_payload('payload', 'dropped', 'int') }} as dropped,
        {{ extract_payload('payload', 'cap', 'int') }} as cap,
        row_number() over (partition by event_id order by arrival_ts asc) as rn
    from {{ ref('stg_bronze_events') }}
    where kind is not null

)

select
    event_id,
    source_id,
    sector_id,
    event_time,
    arrival_ts,
    kind,
    dropped,
    cap
from deduplicated
where rn = 1
{% if is_incremental() %}
  and event_id not in (select event_id from {{ this }})
{% endif %}
