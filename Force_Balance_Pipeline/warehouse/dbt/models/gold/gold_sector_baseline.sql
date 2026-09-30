{{ config(materialized='table') }}

{% raw %}
/*
  gold_sector_baseline (doc 03, "gold.sector_baseline"; doc 07 Phase 4 step 4).

  DBT CONCEPTS USED HERE:
  - materialized='table', no incremental config: this model is rebuilt from scratch every run
    (`CREATE OR REPLACE TABLE ... AS`), by design -- doc 05, Job 2, rebuilds it daily, full refresh, and it is
    explicitly excluded from Job 1's 15-minute transform task (doc 05, Job 1 table; Q11). "Rolling 90-day"
    means the WINDOW rolls with each rebuild, not that the table itself is incremental.
  - CTE chain (anchor -> windowed -> sector_counts / stats -> final select): each step named for what it
    computes, so the query reads like the doc 03 spec it implements.
  - Conditional aggregation (three `select ... union all select ...` blocks, one per channel) rather than a
    single GROUP BY: gold_sector_baseline's grain is one row per (sector_id, channel), but the source data
    (silver_probe_reading) has one row per scan with all three channels as separate columns -- this reshapes
    wide (one row, three channel columns) into long (three rows, one channel column each), the same "unpivot"
    pattern SQL has no dedicated keyword for.

  Contract:
  - Inputs: ref('silver_probe_reading'), which is already probe-only by construction (its own `candidates`
    CTE filters `source_type = 'probe'` upstream, doc 03: "WHERE source_type = 'probe' is mandatory here") --
    there is no source_type column left to re-filter on here. assert_baseline_probe_only.sql (already written)
    is the independent check that this guarantee holds.
  - Grain: one row per sector_id per channel (midichlorian | kyber | dark_side).
  - Window anchor -- DECIDED HERE, doc 03 does not say (open question, not silently assumed): `computed_at` is
    captured once per build (`current_timestamp()`, in a CTE), and the 90-day window is
    `[computed_at - 90 days, computed_at)`. The stored `computed_at` and the window that produced it are
    always self-consistent, whatever time the daily job actually runs. The alternative -- anchoring to the
    run's calendar date instead of its exact timestamp -- would be a few hours off `computed_at` depending on
    when in the day the job runs; not distinguishable from doc 03's text, which doesn't say. Flagged for
    confirmation, not asserted as settled.
  - 90-day window: doc 03 says "Rolling 90-day" as prose, not a var -- hardcoded here (`interval 90 days`).
    dbt_project.yml already has `max_age_days: 90` for a different purpose (silver.rejects'
    impossible_timestamp bound, doc 03:138) and reusing it here would conflate two unrelated 90s that just
    happen to match; a dedicated `baseline_window_days` var is a pending edit (dbt_project.yml is locked this
    round -- see this round's report).
  - Unique key: (sector_id, channel).
  - Columns: mean_90d, stddev_90d (both NULL-safe: avg()/stddev() skip NULLs, so a STEALTH-heavy sector's
    midichlorian/kyber stats are computed only from the scans that actually reported them), sample_count
    (doc 03: "the probe-only row count for the window" -- one number per sector, the same across all three of
    that sector's channel rows, matching assert_baseline_probe_only.sql's own comparison exactly, NOT a
    per-channel non-null count), computed_at.
  - Tests required: assert_baseline_probe_only (singular test, already written), not_null on every column.
*/
{% endraw %}

with anchor as (

    select current_timestamp() as computed_at

),

windowed as (

    select r.sector_id, r.midichlorian_ppm, r.kyber_resonance, r.dark_side_activity
    from {{ ref('silver_probe_reading') }} r
    cross join anchor a
    where r.event_time >= dateadd(day, -90, a.computed_at)
      and r.event_time < a.computed_at

),

sector_counts as (

    select sector_id, count(*) as sample_count
    from windowed
    group by sector_id

),

stats as (

    select sector_id, 'midichlorian' as channel, avg(midichlorian_ppm) as mean_90d, stddev(midichlorian_ppm) as stddev_90d
    from windowed
    group by sector_id

    union all

    select sector_id, 'kyber' as channel, avg(kyber_resonance) as mean_90d, stddev(kyber_resonance) as stddev_90d
    from windowed
    group by sector_id

    union all

    select sector_id, 'dark_side' as channel, avg(dark_side_activity) as mean_90d, stddev(dark_side_activity) as stddev_90d
    from windowed
    group by sector_id

)

select
    s.sector_id,
    s.channel,
    s.mean_90d,
    s.stddev_90d,
    c.sample_count,
    a.computed_at
from stats s
join sector_counts c on s.sector_id = c.sector_id
cross join anchor a
