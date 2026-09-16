{{ config(tags=["ground_truth"]) }}

/* Joins attempts to the requests they generated, via attempt_id, and reports
   evidence volume per technique (docs/02).

   This is where the `observability` tiers get validated against actual log
   output rather than asserted — it mirrors what a detection engineer does when
   checking whether a rule would have fired. A technique marked `high` that
   produces no evidence here means either the tier is wrong or the detection
   signature is not being emitted.

   Ground truth by construction: it reads the attempt log and the attempt_id
   link, both of which are the answer key. */

with request_evidence as (
    select
        l.attempt_id,
        count(*) as requests_generated,
        max(e.path_tier) as max_path_tier,
        sum(case when e.status_returned >= 400 then 1 else 0 end) as error_responses,
        sum(case when {{ is_traversal_pattern() }} then 1 else 0 end) as traversal_hits,
        sum(case when {{ is_injection_pattern() }} then 1 else 0 end) as injection_hits,
        sum(case when e.status_returned = 401 then 1 else 0 end) as auth_failures,
        sum(case when e.path_tier >= 3 then 1 else 0 end) as sensitive_accesses
    from {{ ref('int_request_attempt_link') }} as l
    inner join {{ ref('stg_attack_events') }} as e on l.event_id = e.event_id
    where not e.is_quarantined
    group by l.attempt_id
)

select
    a.technique_id,
    t.display_name,
    t.attack_id,
    t.observability,
    t.produces_traffic,
    t.detection_signature,

    count(*) as attempts,
    count(re.attempt_id) as attempts_with_requests,
    coalesce(sum(re.requests_generated), 0) as requests_generated,
    coalesce(sum(re.traversal_hits), 0) as traversal_hits,
    coalesce(sum(re.injection_hits), 0) as injection_hits,
    coalesce(sum(re.auth_failures), 0) as auth_failures,
    coalesce(sum(re.sensitive_accesses), 0) as sensitive_accesses,
    coalesce(max(re.max_path_tier), -1) as max_path_tier_touched,

    /* The headline: did this technique leave ANY machine-detectable trace? */
    coalesce(
        sum(
            re.traversal_hits + re.injection_hits + re.auth_failures + re.sensitive_accesses
        ),
        0
    ) > 0 as left_evidence
from {{ ref('fct_attack_attempts') }} as a
left join request_evidence as re on a.attempt_id = re.attempt_id
left join {{ ref('dim_techniques') }} as t on a.technique_id = t.technique_id
group by
    a.technique_id,
    t.display_name,
    t.attack_id,
    t.observability,
    t.produces_traffic,
    t.detection_signature
