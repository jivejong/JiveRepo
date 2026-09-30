-- doc 03, "gold.disturbance", firing rules: no two incidents for the same sector_id within
-- var('cooldown_hours') of each other, UNLESS the later one is a flagged cooldown_conflict (doc 03,
-- "Late-replayed onset inside an existing cooldown" -- a replay that lands inside an existing incident's
-- cooldown sets cooldown_conflict = true on the EXISTING row and never creates a second one, so a genuine
-- violation here is always a real bug, not this documented exception).

select
    a.sector_id,
    a.disturbance_id as earlier_id,
    b.disturbance_id as later_id,
    a.detected_at as earlier_detected_at,
    b.detected_at as later_detected_at
from {{ ref('gold_disturbance') }} a
join {{ ref('gold_disturbance') }} b
  on a.sector_id = b.sector_id
 and b.detected_at > a.detected_at
 and b.detected_at < dateadd(hour, {{ var('cooldown_hours') }}, a.detected_at)
where coalesce(a.cooldown_conflict, false) = false
