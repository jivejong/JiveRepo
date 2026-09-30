-- doc 04, "STEALTH": partial readings route to silver_probe_reading with is_partial = true, never to
-- silver_rejects (doc 03: "genuine piece of pipeline logic, not boilerplate"). A STEALTH-mode row must never
-- appear in silver_rejects for null_required_field on its two expected-null channels (midichlorian_ppm,
-- kyber_resonance) -- it may still legitimately appear there for an actual fault on the one channel it does
-- report (dark_side_activity out of range, doc 03's STEALTH-aware routing rule, this round's answer (a)).

select
    r.event_id,
    r.reject_reason,
    'a STEALTH row was rejected for null_required_field, which should have routed to silver_probe_reading instead' as failure_reason
from {{ ref('silver_rejects') }} r
join {{ ref('stg_bronze_events') }} e using (event_id)
where e.mode = 'STEALTH'
  and r.reject_reason = 'null_required_field'
