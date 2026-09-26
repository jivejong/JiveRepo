{#
  Phase 1 review check 2 (doc 08): are the anchor planets high (or low) on their channel,
  relative to the other planets? BLOCKING: every anchor must pass.

  Run after `dbt seed`:
    dbt show --select phase1_check_2_anchors --limit 100 --profiles-dir .

  The `uncharted` row (planets/28) is excluded by its is_unknown flag, so it neither
  moves the percentile ranks nor appears as an anchor.

  Pass criteria: percent_rank >= 0.85 for 'high' anchors, <= 0.15 for 'low' anchors, and
  all_anchors_ok is true. An anchor missing from dim_sector counts as failing.
  Anchors are the planets named in the doc 07 Phase 1 checkpoint and the doc 08 review checklist.

  Mirrored by scripts/enrich_planets.py (ANCHORS, HIGH_PCT_RANK, LOW_PCT_RANK);
  scripts/check_gate_parity.py verifies they match.
#}

with s as (
    select * from {{ ref('dim_sector') }}
    where not is_unknown
),

ranked as (
    select sector_id, 'midi' as channel, midi_baseline as value,
           percent_rank() over (order by midi_baseline) as pct_rank
    from s
    union all
    select sector_id, 'kyber', kyber_baseline,
           percent_rank() over (order by kyber_baseline)
    from s
    union all
    select sector_id, 'dark', dark_baseline,
           percent_rank() over (order by dark_baseline)
    from s
),

anchors as (
    select * from values
        ('mustafar', 'dark', 'high'),
        ('dathomir', 'dark', 'high'),
        ('geonosis', 'dark', 'high'),
        ('naboo', 'dark', 'low'),
        ('alderaan', 'dark', 'low'),
        ('coruscant', 'midi', 'high'),
        ('utapau', 'kyber', 'high')
    as a(sector_id, channel, expect)
),

checked as (
    select
        a.sector_id,
        a.channel,
        a.expect,
        r.value,
        r.pct_rank,
        coalesce(
            case a.expect
                when 'high' then r.pct_rank >= 0.85
                when 'low' then r.pct_rank <= 0.15
            end,
            false
        ) as ok
    from anchors a
    left join ranked r using (sector_id, channel)
)

select
    sector_id,
    channel,
    expect,
    value,
    pct_rank,
    ok,
    bool_and(ok) over () as all_anchors_ok
from checked
order by channel, sector_id
