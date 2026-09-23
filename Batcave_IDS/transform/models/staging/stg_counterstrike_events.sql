/*
Neither triage_input nor ground_truth, deliberately.

Counterstrike attribution comes from the TRIAGE MODEL'S PREDICTION, not from
ground truth (docs/02) — a wrong prediction produces a wrong accusation, which
is the point. So it is not an answer key, and it is not something the model is
fed either.

Deduplicated on event_id, same pattern as stg_attack_events_typed.sql /
stg_attack_runs.sql. Two keys, not three: unlike stg_botchat_turns.sql
(Phase 9), counterstrike has no `_EXCLUDED_COLUMNS` entry in
services/consumer/sample_partition.py - its committed sample copy is a
byte-for-byte duplicate of the real row, so received_at/kafka_offset tying
and picking either copy is invisible; there is no third column that
differs between the two to break the tie on. Added proactively here,
before any real counterstrike data exists, rather than waiting to
discover the gap the way Phase 9 did for chat_turn - the cause (any kind
whose committed sample coexists with its own real landed file in one
directory) is now a known, named pattern, not a surprise to re-derive.
*/

with raw_events as (
    {{ raw_events('counterstrike') }}
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
from deduplicated
where _delivery_seq = 1
