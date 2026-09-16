{{ config(tags=["triage_input"]) }}

/*
OBSERVED. Structurally empty in Track A: nothing produces `chat_turn` events
until the bat bot exists (Track B, docs/08), so raw_events() yields a typed
empty relation here.

Built now rather than deferred because int_session_features_observed is the
triage model's entire input contract. If the three chat features appeared in
Phase 6 but not Phase 5, every Track A accuracy number would be measured
against a different feature vector than every Track B number, and nothing would
flag it.

`user_text` is carried but is excluded from the committed sample partition
without exception (docs/02, assert_no_user_text_in_sample).
*/

with raw_events as (
    {{ raw_events('chat_turn') }}
)

select
    event_id,
    event_kind,
    run_id,
    session_id,
    received_at,
    client_ts,
    schema_version,
    turn_number,
    speaker,
    objective,
    bot_text,
    user_text,
    extracted_intent_flags,
    refused,
    latency_ms,
    input_tokens,
    output_tokens,
    kafka_partition,
    kafka_offset,
    landed_at,
    dt,
    cast(hour as integer) as hour
from raw_events
