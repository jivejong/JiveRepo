{{ config(tags=["ground_truth"]) }}

-- noqa: disable=ST06
-- Same reason as mart_threat_scores: the final select's first column is a
-- 4-way coalesce (a calculation) by necessity - it picks whichever CTE
-- actually has this tier's row, since not every tier has rows in every CTE.
-- Pushing it after the simple columns would separate it from the coalesce
-- pattern the other three "coalesce(x, 0)" columns right below it share.

/*
The headline result (docs/04): technique recall grouped by observability
tier. `low` is split into two real detection postures, not one bucket -
8 techniques the honeypot never sees an HTTP request for at all (no evidence
can exist), and 1 (`valid_accounts`) that lands a real request indistinguishable
from legitimate use (camouflaged, not invisible).

**Recall is over REACHABLE techniques only** (docs/04, docs/06 Phase 6's
first item): a technique with zero attempts in the corpus can't be recalled
or missed - there's nothing to score. `reachable` counts techniques with
at least one attempt anywhere in the current corpus; `techniques` is the
full catalog count per tier, so the gap between them is visible rather than
silently baked into the denominator.
*/

with tier as (
    select
        technique_id,
        attack_id,
        case
            when observability = 'low' and technique_id = 'valid_accounts' then 'low_camouflaged'
            when observability = 'low' then 'low_no_evidence'
            else observability
        end as coverage_tier
    from {{ ref('dim_techniques') }}
),

reachable as (
    select distinct technique_id
    from {{ ref('stg_attack_attempts') }}
),

catalog_counts as (
    select
        coverage_tier,
        count(*) as techniques
    from tier
    group by 1
),

reachable_counts as (
    select
        t.coverage_tier,
        count(*) as reachable_techniques
    from tier as t
    inner join reachable as r on t.technique_id = r.technique_id
    group by 1
),

attempts_per_tier as (
    select
        t.coverage_tier,
        count(*) as attempts
    from {{ ref('stg_attack_attempts') }} as a
    inner join tier as t on a.technique_id = t.technique_id
    group by 1
),

per_source as (
    select
        e.source,
        t.coverage_tier,
        sum(case when e.actual then 1 else 0 end) as actual_positives,
        sum(case when e.actual and e.predicted then 1 else 0 end) as recalled
    from {{ ref('fct_technique_evaluations') }} as e
    inner join tier as t on e.technique_id = t.technique_id
    group by 1, 2
)

select
    coalesce(cc.coverage_tier, rc.coverage_tier, ap.coverage_tier, ps.coverage_tier)
        as coverage_tier,
    cc.techniques,
    coalesce(rc.reachable_techniques, 0) as reachable_techniques,
    coalesce(ap.attempts, 0) as attempts,
    ps.source,
    coalesce(ps.recalled, 0) as recalled,
    coalesce(ps.actual_positives, 0) as actual_positives,
    case
        when coalesce(ps.actual_positives, 0) = 0 then null
        else round(ps.recalled * 1.0 / ps.actual_positives, 4)
    end as recall
from catalog_counts as cc
full outer join reachable_counts as rc on cc.coverage_tier = rc.coverage_tier
full outer join attempts_per_tier as ap on cc.coverage_tier = ap.coverage_tier
full outer join per_source as ps on cc.coverage_tier = ps.coverage_tier
order by 1, 5
