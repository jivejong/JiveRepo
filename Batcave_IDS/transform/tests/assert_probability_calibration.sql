/*
For techniques with enough attempts, the observed success rate must land within
tolerance of the mean computed probability (docs/02). This validates the
SIMULATION, not the pipeline: a drift here means the probability model and the
outcomes it produces have come apart.

**Scoped to non-detected attempts, and that is a correctness fix rather than a
workaround.** `detected` is orthogonal to success/failure but OVERWRITES it in
the outcome enum (docs/07), and it fires on roughly 48% of attempts. Comparing
an all-attempts success rate against a computed_probability that only ever
predicted success would therefore be biased downward by construction — the test
would fail on a perfectly calibrated simulator. Phase 3 measured the gap at
0.006 over non-detected attempts.

The tolerance is a band scaled by the binomial standard error,
sqrt(p(1-p)/n), rather than a flat absolute gap. A flat band is the wrong
shape for this test: at n=41 a 0.10 gap is only ~1.3 standard errors and flags
ordinary sampling noise, while at n=216 it is ~3.2 and would let a real drift
through unnoticed. Measured on the seeded corpus, the worst technique sits at
z=2.08 (remote_services, n=41) and the overall gap across all non-detected
attempts is 0.0038 — so the simulator is well calibrated and the flat band was
mis-flagging noise, not catching a bug.
*/

with by_technique as (
    select
        technique_id,
        count(*) as scoreable_attempts,
        avg(case when outcome = 'success' then 1.0 else 0.0 end) as observed_rate,
        avg(computed_probability) as expected_rate
    from {{ ref('stg_attack_attempts') }}
    where outcome <> 'detected'
    group by technique_id
),

with_band as (
    select
        *,
        sqrt(expected_rate * (1 - expected_rate) / scoreable_attempts) as standard_error,
        abs(observed_rate - expected_rate) as calibration_gap
    from by_technique
    where scoreable_attempts >= {{ var('calibration_min_attempts') }}
)

select
    technique_id,
    scoreable_attempts,
    observed_rate,
    expected_rate,
    calibration_gap,
    calibration_gap / nullif(standard_error, 0) as gap_in_standard_errors
from with_band
where
    /* Beyond k standard errors AND beyond a small absolute floor, so a huge
       sample cannot make a numerically trivial difference look significant. */
    calibration_gap > {{ var('calibration_sigma') }} * standard_error
    and calibration_gap > {{ var('calibration_absolute_floor') }}
