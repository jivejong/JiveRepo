{{ config(tags=["ground_truth"]) }}

/*
Task 2 - Technique reconstruction (docs/04). One row per order per catalog
technique (23 x every order), predicted vs actual, joined to `observability`
so recall can be reported by tier.

`actual`: the technique was ATTEMPTED in this session's run (stg_attack_attempts),
regardless of outcome - "used" means attempted, not "succeeded". `predicted`:
the order's identified_techniques JSON array names this technique's attack_id.

GROUND TRUTH by construction: reads the attempt log directly. Never reachable
from a triage_input model.

Filtered to `session_source = 'headless'` (Phase 10), the same exclusion and
for the same reason as `fct_triage_evaluations.sql` - see that model's header
for the full rationale and the visibility-counter pointer.
*/

with orders as (
    select
        o.order_id,
        o.session_id,
        o.run_id,
        o.source,
        o.parse_failed,
        o.identified_techniques
    from {{ ref('fct_intervention_orders') }} as o
    inner join {{ ref('fct_attack_runs') }} as r on o.run_id = r.run_id
    where r.session_source = 'headless'
),

predicted_attack_ids as (
    select
        o.order_id,
        unnest(
            from_json(o.identified_techniques, '[{"attack_id": "VARCHAR"}]')
        ).attack_id as attack_id
    from orders as o
    where not o.parse_failed
),

actual_techniques as (
    select distinct
        run_id,
        technique_id
    from {{ ref('stg_attack_attempts') }}
),

evaluated as (
    select
        o.order_id,
        o.session_id,
        o.source,
        t.technique_id,
        t.attack_id,
        t.attack_tactic_id,
        t.observability,

        (p.attack_id is not null) as predicted,
        (a.technique_id is not null) as actual
    from orders as o
    cross join {{ ref('dim_techniques') }} as t
    left join predicted_attack_ids as p on o.order_id = p.order_id and t.attack_id = p.attack_id
    left join actual_techniques as a on o.run_id = a.run_id and t.technique_id = a.technique_id
)

select
    order_id,
    session_id,
    source,
    technique_id,
    attack_id,
    attack_tactic_id,
    observability,
    predicted,
    actual,
    case
        when predicted and actual then 'true_positive'
        when predicted and not actual then 'false_positive'
        when not predicted and actual then 'false_negative'
        else 'true_negative'
    end as outcome_class
from evaluated
