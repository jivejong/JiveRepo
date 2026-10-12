{{ config(severity='error') }}

-- Phase 5. gold_disturbance walks each sector's candidates with two recursive CTEs (`walked` over probe runs, `report_walk` over
-- report incidents). A recursive CTE stops the whole build with RECURSION_LEVEL_LIMIT_EXCEEDED once a chain is longer than its limit,
-- var('walk_recursion_limit'), and costs about 0.95 s per level, so a chain that keeps growing first slows Job 1 and then breaks it.
-- This test fails -- severity ERROR, so Job 1 fails, its success ping stops and the alert fires -- when any sector's chain in either walk
-- reaches 80% of the limit (400 of 500), while there is still headroom. It returns one row per sector and walk that has.
--
-- A chain is NOT shortened by the cooldown: an absorbed run or report is still a link. So the depth is:
--   probe walk:  the number of sustained runs the sector has ever had (the rows of `runs_seq` in gold_disturbance)
--   report walk: the number of qualifying reports the sector has ever had that no probe incident's window covers (`standalone_reports`)
-- Both are recomputed here from the same inputs, because a model's CTEs cannot be read from a test. KEEP THESE IN STEP with
-- gold_disturbance.sql (flagged .. runs, qualifying_reports, escalating); test_dbt_lineage.py pins the limit var, and
-- the mutation check of this test pins the arithmetic.

{% set alarm_depth = (var('walk_recursion_limit') * 8) // 10 %}

with probe_islands as (

    select
        sector_id,
        is_above,
        seq - seq_within_flag as run_id
    from (
        select
            sector_id,
            is_above,
            row_number() over (partition by sector_id order by event_time) as seq,
            row_number() over (partition by sector_id, is_above order by event_time) as seq_within_flag
        from (
            select
                sector_id,
                event_time,
                imbalance_score > {{ var('emergency_threshold') }} as is_above
            from {{ ref('gold_sector_reading') }}
            where coalesce(source_type, 'probe') = 'probe'
        )
    )

),

probe_runs as (

    select sector_id, run_id
    from probe_islands
    where is_above
    group by sector_id, run_id
    having count(*) >= {{ var('sustained_scans') }}

),

report_source as (

    select event_id, relevance_score
    from (
        select
            event_id,
            relevance_score,
            row_number() over (partition by event_id order by arrival_ts asc) as rn
        from {{ ref('silver_force_report') }}
    )
    where rn = 1

),

qualifying_reports as (

    select r.event_id, r.sector_id, r.event_time
    from {{ ref('gold_sector_reading') }} r
    join report_source f on f.event_id = r.event_id
    where r.source_type = 'report'
      and r.imbalance_score > {{ var('emergency_threshold') }}
      and f.relevance_score >= {{ var('report_relevance_threshold') }}

),

probe_windows as (

    -- the window gold_disturbance escalates inside: from the incident's onset to detected_at plus the cooldown
    select
        p.sector_id,
        o.event_time as onset_event_time,
        dateadd(hour, {{ var('cooldown_hours') }}, p.detected_at) as window_end
    from {{ ref('gold_disturbance') }} p
    join {{ ref('gold_sector_reading') }} o
      on o.sector_id = p.sector_id
     and o.scan_id = p.scan_id
     and o.source_type = 'probe'
    where not p.is_report_sourced

),

standalone_reports as (

    select q.sector_id
    from qualifying_reports q
    where not exists (
        select 1
        from probe_windows w
        where w.sector_id = q.sector_id
          and q.event_time >= w.onset_event_time
          and q.event_time < w.window_end
    )

)

select
    'probe walk' as walk,
    sector_id,
    count(*) as chain_depth,
    {{ alarm_depth }} as alarm_depth
from probe_runs
group by sector_id
having count(*) >= {{ alarm_depth }}

union all

select
    'report walk' as walk,
    sector_id,
    count(*) as chain_depth,
    {{ alarm_depth }} as alarm_depth
from standalone_reports
group by sector_id
having count(*) >= {{ alarm_depth }}
