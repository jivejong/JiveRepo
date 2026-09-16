{{ config(tags=["triage_input"]) }}

/* OBSERVED. The clean request stream: quarantined rows are excluded here and
   reported separately by fct_quarantined_events, so nothing is dropped
   silently — the two together account for every landed row. */

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
