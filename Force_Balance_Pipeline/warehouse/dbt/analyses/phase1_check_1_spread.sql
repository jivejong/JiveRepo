{#
  Phase 1 review check 1 (doc 08): are baselines spread across their documented ranges?

  Run after `dbt seed`, once the Phase 1 seeds exist:
    dbt show --select phase1_check_1_spread --limit 100 --vars "{run_phase1_checks: true}" --profiles-dir .

  Disabled by default. ref('dim_sector') does not resolve until the seeds exist, and an
  unresolved ref in an enabled node makes every dbt command fail, including a plain `dbt build`.

  The `uncharted` row (planets/28) is excluded by its is_unknown flag, not by id or name.

  Pass criteria, per channel, on each baseline's position within its documented range (0..1):
    p90 - p10 >= 0.50, stddev >= 0.20, share of rows in 0.40-0.60 <= 0.35,
    at least 7 of 10 equal-width bins occupied.
#}
{{ config(enabled=var('run_phase1_checks', false) | as_bool) }}

with s as (
    select * from {{ ref('dim_sector') }}
    where not is_unknown
),

ranges as (
    select * from values
        ('midi', 1000.0, 25000.0),
        ('kyber', 0.0, 100.0),
        ('dark', 0.0, 100.0)
    as r(channel, range_min, range_max)
),

vals as (
    select 'midi' as channel, midi_baseline as baseline from s
    union all select 'kyber', kyber_baseline from s
    union all select 'dark', dark_baseline from s
),

scaled as (
    select
        v.channel,
        (v.baseline - r.range_min) / (r.range_max - r.range_min) as pos
    from vals v
    join ranges r using (channel)
),

spread as (
    select
        channel,
        count(*) as n,
        min(pos) as min_pos,
        max(pos) as max_pos,
        stddev_pop(pos) as sd_pos,
        percentile(pos, 0.10) as p10,
        percentile(pos, 0.90) as p90,
        avg(case when pos between 0.40 and 0.60 then 1.0 else 0.0 end) as share_in_middle,
        count(distinct least(greatest(floor(pos * 10), 0), 9)) as bins_occupied
    from scaled
    group by channel
)

select
    channel,
    n,
    min_pos,
    max_pos,
    sd_pos,
    p10,
    p90,
    share_in_middle,
    bins_occupied,
    (p90 - p10 >= 0.50) as span_ok,
    (sd_pos >= 0.20) as sd_ok,
    (share_in_middle <= 0.35) as not_clustered,
    (bins_occupied >= 7) as bins_ok,
    (p90 - p10 >= 0.50 and sd_pos >= 0.20 and share_in_middle <= 0.35 and bins_occupied >= 7) as pass
from spread
order by channel
