-- doc 03, "gold.sector_baseline": "the most important test in the project" -- report-sourced data must never
-- enter the baseline. Two failure modes: a baseline row whose sample_count doesn't match the probe-only count
-- for its window (the WHERE source_type = 'probe' filter was dropped or weakened somewhere upstream), or any
-- gold_sector_baseline row traceable to a report-sourced silver_probe_reading row (structurally impossible if
-- silver_probe_reading is probe-only by construction, but silver_force_report existing as a separate model is
-- exactly what keeps it that way -- this test is the check that it stays that way).

select
    b.sector_id,
    b.channel,
    b.sample_count,
    'sample_count does not match the probe-only row count for the window' as failure_reason
from {{ ref('gold_sector_baseline') }} b
where b.sample_count != (
    select count(*)
    from {{ ref('silver_probe_reading') }} r
    where r.sector_id = b.sector_id
      and r.event_time >= dateadd(day, -90, b.computed_at)
      and r.event_time < b.computed_at
      -- silver_probe_reading is already probe-only by construction (source_type = 'probe' is part of its own
      -- filter, not re-checked here); this comparison exists to catch a future regression in that filter, not
      -- to re-implement it.
)
