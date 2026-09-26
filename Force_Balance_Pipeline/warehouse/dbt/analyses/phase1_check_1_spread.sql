{#
  Phase 1 review check 1 (doc 08): do the baselines use their documented ranges, without
  clustering at the middle?

  Run after `dbt seed`:
    dbt show --select phase1_check_1_spread --limit 100 --profiles-dir .

  The `uncharted` row (planets/28) is excluded by its is_unknown flag, not by id or name.

  Pass criteria, per channel, on each baseline's position within its documented range (0..1):
    min_pos <= 0.25, max_pos >= 0.75, share of rows in 0.40-0.60 <= 0.35.
  These test range use, not even spread: the prompt's own lore (peaceful worlds low, crystal worlds
  rare) makes the kyber and dark distributions right-skewed by design. p10/p50/p90 are shown for
  context only and are not criteria.

  Mirrored by scripts/enrich_planets.py (RANGE_USE); scripts/check_gate_parity.py verifies they match.
#}

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

use as (
    select
        channel,
        count(*) as n,
        min(pos) as min_pos,
        max(pos) as max_pos,
        avg(case when pos between 0.40 and 0.60 then 1.0 else 0.0 end) as share_in_middle,
        percentile(pos, 0.10) as p10,
        percentile(pos, 0.50) as p50,
        percentile(pos, 0.90) as p90
    from scaled
    group by channel
)

select
    channel,
    n,
    min_pos,
    max_pos,
    share_in_middle,
    p10,
    p50,
    p90,
    (min_pos <= 0.25) as min_ok,
    (max_pos >= 0.75) as max_ok,
    (share_in_middle <= 0.35) as not_clustered,
    (min_pos <= 0.25 and max_pos >= 0.75 and share_in_middle <= 0.35) as pass
from use
order by channel
