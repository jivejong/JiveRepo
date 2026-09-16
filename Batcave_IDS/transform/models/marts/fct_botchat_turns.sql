{{ config(tags=["triage_input"]) }}

/* OBSERVED. Structurally empty in Track A — see stg_botchat_turns.
   `user_text` is deliberately NOT selected: docs/02 excludes it from the
   committed sample partition without exception, and the simplest way to
   guarantee that is for it never to reach a mart. */

select
    event_id,
    session_id,
    run_id,
    received_at,
    turn_number,
    speaker,
    objective,
    bot_text,
    extracted_intent_flags,
    refused,
    latency_ms,
    input_tokens,
    output_tokens,
    dt,
    hour
from {{ ref('stg_botchat_turns') }}
