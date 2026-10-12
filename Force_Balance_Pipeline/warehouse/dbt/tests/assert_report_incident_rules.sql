-- doc 03, "Firing rules -- report-sourced", and gold_disturbance's header. Every report-sourced incident met the firing
-- rule (relevance_score >= var('report_relevance_threshold') and imbalance_score > var('emergency_threshold')), is one
-- sighting with no scan, and carries the report's verbatim description. A probe incident carries report fields only
-- when it was escalated by a report that met the same rule, and then carries both or neither.

select
    disturbance_id,
    'report-sourced incident breaks its firing rule or its shape' as failure_reason
from {{ ref('gold_disturbance') }}
where is_report_sourced
  and (   report_relevance is null
       or report_relevance < {{ var('report_relevance_threshold') }}
       or imbalance_score <= {{ var('emergency_threshold') }}
       or report_description is null
       or trim(report_description) = ''
       or scan_id is not null
       or sustained_scans <> 1
       or coalesce(cooldown_conflict, false))

union all

select
    disturbance_id,
    'probe-sourced incident carries a report description without a qualifying relevance, or one without the other'
from {{ ref('gold_disturbance') }}
where not is_report_sourced
  and (   (report_description is null) <> (report_relevance is null)
       or report_relevance < {{ var('report_relevance_threshold') }})
