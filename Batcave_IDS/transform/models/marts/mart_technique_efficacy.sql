{{ config(tags=["ground_truth"]) }}

/* Per technique per villain: attempts, successes, observed rate, mean computed
   probability, and the delta between them (docs/02).

   The delta is what assert_probability_calibration checks — it validates the
   SIMULATION, not the pipeline. Scoped to non-detected attempts for the reason
   given there: `detected` overwrites success/failure in the outcome enum, so
   including detected attempts biases the observed rate downward against a
   computed_probability that only ever predicted success. */

select
    a.technique_id,
    t.display_name,
    t.attack_id,
    t.observability,
    r.villain_slug,
    r.archetype,

    count(*) as attempts_total,
    sum(case when a.outcome = 'detected' then 1 else 0 end) as attempts_detected,
    sum(case when a.outcome <> 'detected' then 1 else 0 end) as attempts_scoreable,
    sum(case when a.outcome = 'success' then 1 else 0 end) as successes,

    avg(case when a.outcome = 'success' then 1.0 else 0.0 end) as observed_success_rate,

    /* The calibration pair: both sides over non-detected attempts only. */
    avg(case
        when a.outcome <> 'detected' then
            case when a.outcome = 'success' then 1.0 else 0.0 end
    end) as scoreable_success_rate,
    avg(case when a.outcome <> 'detected' then a.computed_probability end)
        as mean_computed_probability,
    avg(case
        when a.outcome <> 'detected' then
            case when a.outcome = 'success' then 1.0 else 0.0 end
    end) - avg(case when a.outcome <> 'detected' then a.computed_probability end)
        as calibration_delta
from {{ ref('fct_attack_attempts') }} as a
inner join {{ ref('fct_attack_runs') }} as r on a.run_id = r.run_id
left join {{ ref('dim_techniques') }} as t on a.technique_id = t.technique_id
group by a.technique_id, t.display_name, t.attack_id, t.observability, r.villain_slug, r.archetype
