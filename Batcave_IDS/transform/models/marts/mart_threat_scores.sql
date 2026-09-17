{{ config(tags=["triage_input"]) }}

-- noqa: disable=ST06
--
-- ST06 ("select wildcards then simple targets before calculations") wants
-- every calculated column pushed after the simple ones. sqlfluff's autofix
-- for this rule reorders columns WITHOUT moving their preceding comments,
-- which silently detached every explanatory comment from the expression it
-- describes - caught by re-reading the "fixed" file. Disabled for this file:
-- the CTE is deliberately ordered as docs/02 lists the eight components, each
-- with the comment that explains its normalization immediately above it, and
-- that grouping matters more here than the rule.

/*
Composite threat score, 0-100, from observed features only (docs/02). Each
weighted component is its own column so the breakdown is inspectable, not just
the total - the whole point of a component score over a black-box number.

  max_path_tier normalized                              25
  path_enumeration_runs + distinct_paths normalized      15
  injection_pattern_count + traversal_pattern_count      15
  repeated_auth_failure_runs                             10
  error_ratio                                            10
  inverse inter_request_stddev_ms                        10
  evasion (null_ip_ratio + UA rotation)                  10
  requests_per_min (log-scaled, capped)                   5
                                                         ---
                                                         100

Weights are docs/02's, unchanged. Phase 6's ablation re-run (both error_ratio
and wasted_request_ratio now 0/66 load-bearing pairs, confounded between
technique-selection randomization and the corpus's clock change - docs/02,
docs/04) does not touch this mart: error_ratio was never conditioned on that
ablation, and wasted_request_ratio was never a threat-score component (it's a
prompt/baseline-classifier signal only - docs/02, docs/04's Input contract).

Every sub-normalization is capped against the REAL observed distribution on
this corpus (396 sessions), not a guessed range - see the comment on each. A
cap is a ceiling for "no more credit past this point", not a hard clip that
would make an outlier session's score meaningless; every raw feature is
`least(..., cap)`-guarded before scaling into [0, 1] specifically so a future
value beyond today's observed max still degrades gracefully to full credit
rather than producing a score outside [0, 100].
*/

with features as (
    select * from {{ ref('int_session_features_observed') }}
),

scored as (
    select
        session_id,
        run_id,

        -- max_path_tier: hard-bounded 0-4 by the route table (docs/02), no
        -- cap needed.
        (max_path_tier / 4.0) as c_max_path_tier,

        -- path_enumeration_runs: observed 0-2 (min-length-3 streaks are
        -- already rare by construction). distinct_paths: observed 1-12,
        -- median 4 - capped at 10 so only genuinely wide-scanning sessions
        -- saturate.
        (
            0.5 * least(path_enumeration_runs / 2.0, 1.0)
            + 0.5 * least(distinct_paths / 10.0, 1.0)
        ) as c_enumeration,

        -- injection + traversal: observed distribution is dominated by 0
        -- (201 of ~396) and 1 (132); presence matters more than volume past
        -- a handful of hits, so capped at 3 rather than scaled to the
        -- observed max of 8.
        least(
            (injection_pattern_count + traversal_pattern_count) / 3.0, 1.0
        ) as c_exploit_evidence,

        -- repeated_auth_failure_runs: a min-length-3 streak (docs/02) is
        -- already unambiguous brute-force evidence - one qualifying run maxes
        -- this component rather than needing several.
        least(repeated_auth_failure_runs / 1.0, 1.0) as c_auth_failure,

        -- error_ratio is already a 0-1 ratio.
        error_ratio as c_error_ratio,

        -- inverse inter_request_stddev_ms: LOW variance (regular, automated
        -- pacing) is the suspicious direction, hence inverse. Observed
        -- 0-2040ms, median 361ms; capped at 1000ms so a session at or above
        -- roughly the 90th percentile of irregularity gets zero credit here
        -- rather than a vanishingly small one.
        (1.0 - least(inter_request_stddev_ms / 1000.0, 1.0)) as c_regularity,

        -- evasion: null_ip_ratio observed max ~0.083 (the missing-source-ip
        -- pathology, docs/03), capped at 0.1 so the real observed range maps
        -- to close to full credit rather than an arbitrarily distant cap.
        -- UA rotation: distinct_user_agents observed 1-6 (Penguin's
        -- signature, docs/03); 1 (no rotation) scores 0, 6 scores 1.
        (
            0.5 * least(null_ip_ratio / 0.1, 1.0)
            + 0.5 * least((distinct_user_agents - 1) / 5.0, 1.0)
        ) as c_evasion,

        -- requests_per_min: already capped at 600 by the feature model
        -- itself (docs/02's own cap). Log-scaled per docs/02's spec so the
        -- wide observed range (38-600) doesn't let raw volume dominate.
        (ln(1 + requests_per_min) / ln(1 + 600.0)) as c_volume
    from features
)

select
    session_id,
    run_id,

    round(c_max_path_tier * 25, 2) as component_max_path_tier,
    round(c_enumeration * 15, 2) as component_enumeration,
    round(c_exploit_evidence * 15, 2) as component_exploit_evidence,
    round(c_auth_failure * 10, 2) as component_auth_failure,
    round(c_error_ratio * 10, 2) as component_error_ratio,
    round(c_regularity * 10, 2) as component_regularity,
    round(c_evasion * 10, 2) as component_evasion,
    round(c_volume * 5, 2) as component_volume,

    round(
        c_max_path_tier * 25
        + c_enumeration * 15
        + c_exploit_evidence * 15
        + c_auth_failure * 10
        + c_error_ratio * 10
        + c_regularity * 10
        + c_evasion * 10
        + c_volume * 5,
        2
    ) as threat_score,

    (
        c_max_path_tier * 25
        + c_enumeration * 15
        + c_exploit_evidence * 15
        + c_auth_failure * 10
        + c_error_ratio * 10
        + c_regularity * 10
        + c_evasion * 10
        + c_volume * 5
    ) >= {{ var('triage_threshold') }} as above_triage_threshold
from scored
