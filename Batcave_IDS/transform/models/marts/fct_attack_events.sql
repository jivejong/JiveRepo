{{ config(tags=["triage_input"]) }}

/* OBSERVED. The clean request stream: quarantined rows are excluded here and
   reported separately by fct_quarantined_events, so nothing is dropped
   silently — the two together account for every landed row.

   `request_body` added in Phase 6: docs/04's triage Input contract needs the
   raw body text (truncated to 200 chars, up to 5 per session -
   services/triage/context.py) as evidence for the LLM and baseline. It's an
   OBSERVED field like any other here - the honeypot logs it raw, same as
   path or headers - simply not selected into this mart until a consumer
   needed it. */

select
    event_id,
    session_id,
    run_id,
    received_at,
    client_ts,
    source_ip,
    user_agent,
    http_method,
    path,
    query_string,
    path_tier,
    request_body,
    body_bytes,
    status_returned,
    response_time_ms,
    body_is_valid_json,
    schema_version,
    tls_fingerprint,
    dt,
    hour
from {{ ref('stg_attack_events') }}
where not is_quarantined
