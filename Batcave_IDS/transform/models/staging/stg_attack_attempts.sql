{{ config(tags=["ground_truth"]) }}

/*
GROUND TRUTH. What the attacker actually did: technique, stage, roll, outcome.
Never reaches the triage model — it is the answer key the model's
reconstruction is scored against.

No dedupe window is applied beyond event_id: the pathologies corrupt the
OBSERVED stream only (docs/03), never the ground-truth events. A duplicate here
would come from a consumer replay alone, which event_id dedupe still handles.
*/

with raw_events as (
    {{ raw_events('attempt') }}
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by event_id
            order by received_at asc, kafka_offset asc
        ) as _delivery_seq
    from raw_events
)

select
    event_id,
    event_kind,
    run_id,
    session_id,
    attempt_id,
    received_at,
    client_ts,
    schema_version,
    stage,
    technique_id,
    attack_id,
    attempt_seq,
    technique_attempt_seq,
    decision,
    parameters,
    computed_probability,
    roll,
    outcome,
    noise_generated,
    stage_entered_at,
    attempt_at,
    kafka_partition,
    kafka_offset,
    landed_at,
    dt,
    cast(hour as integer) as hour
from deduplicated
where _delivery_seq = 1
