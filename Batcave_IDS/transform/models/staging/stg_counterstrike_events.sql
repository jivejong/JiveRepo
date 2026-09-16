/*
Neither triage_input nor ground_truth, deliberately.

Counterstrike attribution comes from the TRIAGE MODEL'S PREDICTION, not from
ground truth (docs/02) — a wrong prediction produces a wrong accusation, which
is the point. So it is not an answer key, and it is not something the model is
fed either.

Structurally empty in Track A (Track B, docs/08).
*/

with raw_events as (
    {{ raw_events('counterstrike') }}
)

select
    event_id,
    event_kind,
    run_id,
    session_id,
    received_at,
    client_ts,
    schema_version,
    sequence,
    readout_line,
    attributed_villain_slug,
    attributed_confidence,
    attack_id,
    kafka_partition,
    kafka_offset,
    landed_at,
    dt,
    cast(hour as integer) as hour
from raw_events
