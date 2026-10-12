-- Phase 5. A report event lands in silver_force_report or silver_report_rejects and nowhere else; a probe event never
-- lands in either; and silver_force_report / silver_report_rejects never hold the same event_id. This is the data-side
-- half of "the baseline is probe-only" (gold_sector_baseline reads silver_probe_reading alone): if a report event_id
-- ever turned up in a probe table, the baseline would be using it. The report ids are the small side of every join.

with report_events as (

    select distinct event_id
    from {{ ref('stg_bronze_events') }}
    where source_type = 'report'

),

probe_event_ids_in_report_tables as (

    select f.event_id, 'silver_force_report' as found_in
    from {{ ref('silver_force_report') }} f
    left join report_events e on e.event_id = f.event_id
    where e.event_id is null

    union all

    select r.event_id, 'silver_report_rejects'
    from {{ ref('silver_report_rejects') }} r
    left join report_events e on e.event_id = r.event_id
    where e.event_id is null

)

select event_id, found_in as failure_reason from probe_event_ids_in_report_tables

union all

select e.event_id, 'a report event is in silver_probe_reading'
from report_events e
join {{ ref('silver_probe_reading') }} r on r.event_id = e.event_id

union all

select e.event_id, 'a report event is in silver_rejects'
from report_events e
join {{ ref('silver_rejects') }} r on r.event_id = e.event_id

union all

select e.event_id, 'a report event is in silver_probe_event'
from report_events e
join {{ ref('silver_probe_event') }} r on r.event_id = e.event_id

union all

select f.event_id, 'in both silver_force_report and silver_report_rejects'
from {{ ref('silver_force_report') }} f
join {{ ref('silver_report_rejects') }} r on r.event_id = f.event_id
