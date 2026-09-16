/* Negative durations are nonsense in a mart even though they are expected in
   raw data: the clock-skew pathology emits ~1% negative response_time_ms
   (docs/03 row 9), which staging keeps and counts rather than quarantining.

   What must never be negative is a DERIVED duration, because that would mean
   an event ordered before the thing it follows. */

select
    'int_session_features_observed.duration_s' as source,
    session_id as offending_key,
    duration_s as value
from {{ ref('int_session_features_observed') }}
where duration_s < 0

union all

select
    'int_session_features_observed.time_to_tier3_s',
    session_id,
    time_to_tier3_s
from {{ ref('int_session_features_observed') }}
where time_to_tier3_s < 0

union all

select
    'int_stage_progression.dwell_time_s',
    session_id,
    dwell_time_s
from {{ ref('int_stage_progression') }}
where dwell_time_s < 0
