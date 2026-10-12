-- doc 03, "gold.disturbance", firing rules: no two incidents for the same sector_id within
-- var('cooldown_hours') of each other, UNLESS the later one is a flagged cooldown_conflict (doc 03,
-- "Late-replayed onset inside an existing cooldown" -- a replay that lands inside an existing incident's
-- cooldown sets cooldown_conflict = true on the EXISTING row and never creates a second one, so a genuine
-- violation here is always a real bug, not this documented exception).
--
-- Phase 5: a probe incident is never suppressed by a report, and a report that falls before a probe onset leaves
-- both rows (doc 03 "Firing rules -- report-sourced", gold_disturbance's header), so a report-sourced incident and a
-- probe-sourced one may sit inside each other's cooldown. ONLY such report-versus-probe pairs are exempt: two probe
-- incidents, or two report incidents, are still checked (a report inside a report incident's cooldown is absorbed).

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
  and a.is_report_sourced = b.is_report_sourced
