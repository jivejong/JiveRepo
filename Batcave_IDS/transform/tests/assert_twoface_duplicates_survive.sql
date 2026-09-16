/*
Two-Face's signature is issuing every request twice. Those duplicates carry
DISTINCT event_ids; the duplicate-delivery pathology produces IDENTICAL ones.
Dedupe must remove the second and keep the first, or the villain's defining
behaviour disappears and he becomes unidentifiable (docs/02, docs/03).

Both populations coexist in the same corpus - Phase 4 measured 40 identical-id
duplicates against 39 Two-Face sessions in docs/exercises.md - so this is a real
discrimination, not a hypothetical one.

Fails if any Two-Face session lost its repeated paths in staging.
*/

with twoface_sessions as (
    select distinct session_id
    from {{ ref('stg_attack_runs') }}
    where villain_slug = '678-two-face'
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
