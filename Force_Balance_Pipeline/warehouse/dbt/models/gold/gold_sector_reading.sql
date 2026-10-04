{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

{% raw %}
/*
  gold_sector_reading (doc 03, "gold.sector_reading"; doc 07 Phase 4 step 5).

  DBT CONCEPTS USED HERE:
  - unique_key='event_id', plain merge (update on match, dbt-databricks' default): the OPPOSITE choice from
    every silver model, deliberately. Silver is insert-only, earliest-arrival-wins, because doc 03 says a
    duplicate event never overwrites an already-landed one. Gold is the reverse: when a replayed row's
    baseline-relative z-score changes (a late scan recomputed against a since-updated gold_sector_baseline,
    or simply reprocessed inside the recompute window below), the OLD z-score/signature for that same
    event_id must be overwritten, not left stale next to a phantom duplicate -- doc 07's own checkpoint
    wording is "affected historical rows are recomputed, not duplicated." Two layers, two opposite merge
    behaviours, both deliberate.
  - is_incremental() branches the whole CTE below, not just a WHERE clause: the very first build has no
    "this" table yet (nothing to query the running table's own MAX(...) from), so the recompute-window logic
    only runs on later builds; the first build always processes every row in silver_probe_reading, which is
    exactly right for backfilling 90 days of gold history the first time.
  - `_gold_built_at`, a bookkeeping column doc 03 doesn't list: the recompute window formula needs to know
    "which silver rows arrived since the last time THIS model ran," and dbt/Databricks have no built-in
    concept of "the last build's timestamp" -- so this model stamps its own, the same way bronze stamps
    _ingest_ts. A pending doc 03 edit, not silently assumed away.
  - `is_replayed`, added Stage 4a: carried straight through from silver_probe_reading, unchanged. Needed so
    gold_disturbance can tell whether a run's onset scan arrived via replay (doc 03, cooldown_conflict
    "definition B") -- this model is the only place that information can reach gold_disturbance from, since
    gold_disturbance never reads silver directly. SCHEMA CHANGE: this is a new column on an existing
    incremental model with on_schema_change: fail (dbt_project.yml) -- requires a --full-refresh of
    gold_sector_reading after this lands on main, not just a plain incremental run.

  Contract:
  - Inputs: ref('silver_probe_reading'), ref('gold_sector_baseline'), ref('dim_sector') (population, for
    civil_unrest).
  - Grain: one row per planet per scan (doc 03: the scan is the grain). event_id is the merge key in practice
    (1:1 with (sector_id, scan_id) once silver has deduplicated) -- doc 03's own column list for this table
    doesn't list event_id; noted as a pending doc addition, not assumed silently.
  - Materialization: incremental, merge, update on match.
  - Recompute window (doc 05, "Incremental strategy", extended per this round's brief): on the first build,
    every silver_probe_reading row. On later builds,
    `LEAST(now - 48h, MIN(event_time) of silver rows whose arrival_ts is newer than this table's own last
    build)` -- the normal case (nothing unusually late arrived) reduces to the plain trailing-48h window;
    a late replay pulls the window back far enough to recompute that replay's own historical scan too.
  - z-scores: `(reading - mean_90d) / NULLIF(stddev_90d, 0)` per channel, against gold_sector_baseline pivoted
    from long (one row per sector per channel) to wide (one row per sector, three channel columns) via
    conditional aggregation, the same reshape gold_sector_baseline itself does in the other direction.
  - imbalance_score (macros.imbalance_score) and signature (macros.classify_signature) -- both macros, not
    inline SQL, so edge/tests/test_dbt_doc_parity.py can check them against doc 03 independently of this model.
  - Tests required: imbalance_score >= 0, channels_present between 1 and 3, accepted_values(signature, the doc
    03 signature list), not_null(sector_id, scan_id, event_time).
*/
{% endraw %}

{% if is_incremental() %}

with last_build as (

    select coalesce(max(_gold_built_at), timestamp('1900-01-01T00:00:00Z')) as last_built_at
    from {{ this }}

),

recompute_start as (

    select least(
        dateadd(hour, -48, current_timestamp()),
        coalesce(
            (select min(r.event_time)
             from {{ ref('silver_probe_reading') }} r
             cross join last_build l
             where r.arrival_ts > l.last_built_at),
            dateadd(hour, -48, current_timestamp())
        )
    ) as start_ts

),

candidates as (

    select r.*
    from {{ ref('silver_probe_reading') }} r
    cross join recompute_start w
    where r.event_time >= w.start_ts

),

{% else %}

with candidates as (

    select r.*
    from {{ ref('silver_probe_reading') }} r

),

{% endif %}

baseline_wide as (

    select
        sector_id,
        max(case when channel = 'midichlorian' then mean_90d end) as mean_90d_midi,
        max(case when channel = 'midichlorian' then stddev_90d end) as stddev_90d_midi,
        max(case when channel = 'kyber' then mean_90d end) as mean_90d_kyber,
        max(case when channel = 'kyber' then stddev_90d end) as stddev_90d_kyber,
        max(case when channel = 'dark_side' then mean_90d end) as mean_90d_dark,
        max(case when channel = 'dark_side' then stddev_90d end) as stddev_90d_dark
    from {{ ref('gold_sector_baseline') }}
    group by sector_id

),

zscored as (

    select
        c.event_id,
        c.sector_id,
        c.scan_id,
        c.event_time,
        cast('probe' as string) as source_type,  -- always true while gold_sector_reading has only this one input; Phase 5 adds report-sourced rows
        c.midichlorian_ppm,
        c.kyber_resonance,
        c.dark_side_activity,
        c.channels_present,
        c.is_replayed,  -- doc 03, gold.disturbance's cooldown_conflict (definition B, Stage 4a): needs to know
                         -- whether an onset scan arrived via replay, which only silver_probe_reading tracks
        (c.midichlorian_ppm - b.mean_90d_midi) / nullif(b.stddev_90d_midi, 0) as z_midi,
        (c.kyber_resonance - b.mean_90d_kyber) / nullif(b.stddev_90d_kyber, 0) as z_kyber,
        (c.dark_side_activity - b.mean_90d_dark) / nullif(b.stddev_90d_dark, 0) as z_dark,
        d.population
    from candidates c
    left join baseline_wide b on c.sector_id = b.sector_id
    left join {{ ref('dim_sector') }} d on c.sector_id = d.sector_id

)

select
    event_id,
    sector_id,
    scan_id,
    event_time,
    source_type,
    midichlorian_ppm,
    kyber_resonance,
    dark_side_activity,
    z_midi,
    z_kyber,
    z_dark,
    {{ imbalance_score('z_midi', 'z_kyber', 'z_dark', 'channels_present') }} as imbalance_score,
    {{ classify_signature('z_midi', 'z_kyber', 'z_dark', 'population', 'channels_present') }} as signature,
    channels_present,
    is_replayed,
    current_timestamp() as _gold_built_at
from zscored
