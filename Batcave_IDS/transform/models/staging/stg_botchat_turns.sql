{{ config(tags=["triage_input"]) }}

/*
OBSERVED. Structurally empty until the bat bot exists (Track B, docs/08); once
real conversations land, raw_events() reads them the same way every other
kind does.

Built ahead of real data in Phase 5, deliberately, because
int_session_features_observed is the triage model's entire input contract. If
the three chat features appeared in Phase 6 but not Phase 5, every Track A
accuracy number would be measured against a different feature vector than
every Track B number, and nothing would flag it.

`user_text` is carried but is excluded from the mart layer and the committed
sample partition without exception (docs/02, assert_no_user_text_in_sample) -
it stays here, in this one staging model, because it's what the three chat
features are computed from and what a local operator can inspect.

Deduplicated on event_id, same pattern as stg_attack_events_typed.sql /
stg_attack_runs.sql - and load-bearing here for a reason those two don't have:
services/consumer/sample_partition.py's committed sample copies a chosen
session's chat_turn rows verbatim (same event_id, same received_at, same
kafka_offset) into the *same* dt=/hour=/ directory as the real landed file,
redacting only user_text. Once a session is both real corpus data and the
committed sample, raw_events() globs both files, and without dedup here every
aggregate in int_session_features_observed's chat CTE that isn't naturally
duplicate-proof would silently double count - confirmed live: intent_flags_triggered
(a sum()) read 6 instead of 3 the moment the first real bat bot session was
also sampled, while chat_turns_completed (count(distinct turn_number)) and
probe_engagement_ratio (an avg()) happened to be immune. The tie-break prefers
the row with a real (non-null) user_text over the sample's redacted copy - the
two are identical on received_at and kafka_offset (the sample copies both
verbatim), so without this a plain received_at/kafka_offset order would pick
between them arbitrarily, and this is the one model where user_text staying
real, not silently nulled by which copy won a tie, is the documented point of
the model existing at all.
*/

with raw_events as (
    {{ raw_events('chat_turn') }}
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by event_id
            order by received_at asc, kafka_offset asc, (user_text is null) asc
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
from deduplicated
where _delivery_seq = 1
