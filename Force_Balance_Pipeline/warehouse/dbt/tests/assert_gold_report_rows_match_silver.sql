-- Phase 5. Every report row in gold_sector_reading is a valid report from silver_force_report, has no scan_id, and every
-- probe row has one; a report row's values are the report's own inferred values, unchanged.

select g.event_id, 'a report row in gold_sector_reading is not in silver_force_report, or its values differ' as failure_reason
from {{ ref('gold_sector_reading') }} g
left join {{ ref('silver_force_report') }} f on f.event_id = g.event_id
where g.source_type = 'report'
  and (   f.event_id is null
       or g.sector_id <> f.sector_id
       or g.event_time <> f.event_time
       or g.midichlorian_ppm <> f.midichlorian_ppm
       or g.kyber_resonance <> f.kyber_resonance
       or g.dark_side_activity <> f.dark_side_activity)

union all

select g.event_id, 'a report row has a scan_id, or a probe row has none'
from {{ ref('gold_sector_reading') }} g
where (g.source_type = 'report' and g.scan_id is not null)
   or (g.source_type = 'probe' and g.scan_id is null)

union all

-- every valid report older than the table's own last build has been scored (a newer one may simply not be built yet)
select f.event_id, 'a valid report that arrived before the last build is missing from gold_sector_reading'
from {{ ref('silver_force_report') }} f
left join {{ ref('gold_sector_reading') }} g on g.event_id = f.event_id
where g.event_id is null
  and f.arrival_ts < (select max(_gold_built_at) from {{ ref('gold_sector_reading') }})
