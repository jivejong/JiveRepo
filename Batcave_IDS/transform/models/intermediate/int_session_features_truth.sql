{{ config(tags=["ground_truth"]) }}

/*
GROUND TRUTH — evaluation only. Never an input to triage.

This is the other half of the separability harness's feature query. The harness
(services/simulator/separability.py) computes observed and attempt-derived
features together in one SELECT, which is fine for measuring separability
offline but would be a leak here: retry_ratio, pivot_ratio and
attempts_per_stage_reached are all derived from the attempt log, which is the
answer key. So the lift is split at the boundary and this side is tagged
ground_truth. The expressions themselves are lifted verbatim so the harness/dbt
cross-check compares like with like.

docs/02 field list: max_stage_reached, stages_cleared, total_attempts,
total_successes, overall_success_rate, distinct_techniques_used,
technique_id_set, attack_id_set, tactic_set, retry_ratio, pivot_ratio,
mean_technique_min_intelligence, total_noise_generated, time_to_max_stage_s,
abandoned_after_failures.
*/

with attempts as (
    select
        a.*,
        t.min_intelligence,
        t.attack_tactic_id
    from {{ ref('stg_attack_attempts') }} as a
    left join {{ ref('stg_techniques') }} as t on a.technique_id = t.technique_id
),

cleared_stages as (
    select
        session_id,
        stage
    from attempts
    where outcome = 'success'
    group by session_id, stage
),

stage_clears as (
    select
        session_id,
        count(*) as stages_cleared
    from cleared_stages
    group by session_id
),

trailing_failures as (
    /* Did the run end on a streak of failures rather than a success? The
       session's last attempt tells us whether it gave up or finished. */
    select
        session_id,
        max(attempt_seq) as last_attempt_seq,
        max(case when outcome = 'success' then attempt_seq end) as last_success_seq
    from attempts
    group by session_id
)

select
    a.session_id,
    min(a.run_id) as run_id,

    max(a.stage) as max_stage_reached,
    coalesce(max(sc.stages_cleared), 0) as stages_cleared,
    count(*) as total_attempts,
    sum(case when a.outcome = 'success' then 1 else 0 end) as total_successes,
    avg(case when a.outcome = 'success' then 1.0 else 0.0 end) as overall_success_rate,

    count(distinct a.technique_id) as distinct_techniques_used,
    list_sort(list(distinct a.technique_id)) as technique_id_set,
    list_sort(list(distinct a.attack_id)) as attack_id_set,
    list_sort(list(distinct a.attack_tactic_id)) as tactic_set,

    /* Lifted from the harness verbatim — these are the two features docs/03's
       narrative was corrected against in Phase 3 (Joker leads pivot_ratio,
       Croc leads retry_ratio). */
    avg(case when a.decision = 'retry' then 1.0 else 0.0 end) as retry_ratio,
    avg(case when a.decision = 'pivot' then 1.0 else 0.0 end) as pivot_ratio,
    count(*) * 1.0 / greatest(max(a.stage), 1) as attempts_per_stage_reached,

    avg(a.min_intelligence) as mean_technique_min_intelligence,
    sum(a.noise_generated) as total_noise_generated,
    sum(case when a.outcome = 'detected' then 1 else 0 end) as detected_attempts,

    epoch(max(a.attempt_at)) - epoch(min(a.attempt_at)) as time_to_max_stage_s,

    /* Ended on failures with no later success — gave up rather than finished. */
    coalesce(max(tf.last_success_seq), 0) < max(tf.last_attempt_seq) as abandoned_after_failures
from attempts as a
left join stage_clears as sc on a.session_id = sc.session_id
left join trailing_failures as tf on a.session_id = tf.session_id
group by a.session_id
