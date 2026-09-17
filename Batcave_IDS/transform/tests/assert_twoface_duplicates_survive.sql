/*
Two-Face's signature is issuing every TRAFFIC-PRODUCING request twice. Those
duplicates carry DISTINCT event_ids; the duplicate-delivery pathology produces
IDENTICAL ones. Dedupe must remove the second and keep the first, or the
villain's defining behaviour disappears and he becomes unidentifiable
(docs/02, docs/03).

**Scoped to sessions with at least one traffic-producing attempt (Phase 6
correction).** The precondition was always "Two-Face made HTTP-visible
requests" - there's nothing to duplicate otherwise. Before Phase 6, technique
selection was deterministic (techniques.csv row order) and stage 1's first row
was always a traffic-producing technique, so every Two-Face session touched
the honeypot at least once and this precondition held silently. Randomizing
selection (docs/06, the pre-committed Phase 6 fix for unreachable techniques)
removed that guarantee: a session can now stall entirely on non-traffic
techniques (net_info_gather, open_source_search, phishing - 3 of stage 1's 5
options don't touch the honeypot) before ever making a request. That's a
legitimate outcome, not a regression - Two-Face's low durability (14) means he
gives up fast, and this is what "fast" can look like. Making the precondition
explicit corrects the test to match the invariant it was always testing,
rather than the broader one it looked like it was testing.

See assert_twoface_test_is_not_vacuous.sql for the second half: this scoping
must not let the test degrade to checking nothing.

Both duplicate populations coexist in the same corpus - Phase 4 measured 40
identical-id duplicates against 39 Two-Face sessions in docs/exercises.md - so
this is a real discrimination, not a hypothetical one.

Fails if any QUALIFYING Two-Face session lost its repeated paths in staging.
*/

with twoface_sessions as (
    select distinct r.session_id
    from {{ ref('stg_attack_runs') }} as r
    inner join {{ ref('stg_attack_attempts') }} as a on r.run_id = a.run_id
    inner join {{ ref('stg_techniques') }} as t on a.technique_id = t.technique_id
    where r.villain_slug = '678-two-face' and t.produces_traffic
),

repeated_paths as (
    select
        e.session_id,
        count(*) as paths_issued_more_than_once
    from (
        select
            session_id,
            path,
            count(distinct event_id) as distinct_events
        from {{ ref('stg_attack_events') }}
        where path is not null
        group by session_id, path
    ) as e
    where e.distinct_events > 1
    group by e.session_id
)

select
    s.session_id,
    coalesce(r.paths_issued_more_than_once, 0) as surviving_duplicate_paths
from twoface_sessions as s
left join repeated_paths as r on s.session_id = r.session_id
where coalesce(r.paths_issued_more_than_once, 0) = 0
