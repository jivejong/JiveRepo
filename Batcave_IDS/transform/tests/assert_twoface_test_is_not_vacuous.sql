/*
Guards assert_twoface_duplicates_survive.sql's scoping (Phase 6 correction).

Scoping that test to "sessions with a traffic-producing attempt" is correct,
but a scope with nothing in it passes trivially - a future bug that made
EVERY Two-Face session dodge traffic-producing techniques (a stat/gating
change, a catalog edit removing his reachable traffic techniques, technique
selection breaking some other way) would leave the duplicate-survival test
green while proving nothing, since it would have zero rows to check.

Requires at least half of the corpus's Two-Face sessions to qualify. A
fraction, not a fixed count, so it scales with corpus size across phases
(Phase 7's larger runs) rather than needing re-tuning. On the seeded corpus
this session (which includes multiple prior runs still landed in the
warehouse), 28 of 33 Two-Face sessions qualified (85%) - well clear of the
50% floor - so the floor is a regression guard, not a tight fit to current
behavior.
*/

with twoface_sessions as (
    select distinct session_id
    from {{ ref('stg_attack_runs') }}
    where villain_slug = '678-two-face'
),

qualifying_sessions as (
    select distinct r.session_id
    from twoface_sessions as r
    inner join {{ ref('stg_attack_attempts') }} as a on r.session_id = a.session_id
    inner join {{ ref('stg_techniques') }} as t on a.technique_id = t.technique_id
    where t.produces_traffic
),

counted as (
    select
        (select count(*) from twoface_sessions) as total_sessions,
        (select count(*) from qualifying_sessions) as qualifying_sessions
)

select
    total_sessions,
    qualifying_sessions,
    qualifying_sessions * 1.0 / nullif(total_sessions, 0) as qualifying_fraction
from counted
where
    total_sessions = 0
    or qualifying_sessions * 1.0 / total_sessions < {{ var('twoface_min_qualifying_fraction') }}
