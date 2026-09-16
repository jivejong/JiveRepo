{{ config(tags=["triage_input"]) }}

/*
The observed request stream: what the honeypot's HTTP sensor saw, and the root
of everything the triage model is allowed to reason over.

`attempt_id` is deliberately absent. It exists on the raw event so an attempt
can be correlated with the requests it generated, but it is a join key for
evaluation, not a feature (docs/02) — keeping it here would hand the model the
attempt boundaries it is supposed to reconstruct. It survives only in
int_request_attempt_link, which is tagged ground_truth.
Guarded by assert_no_attempt_id_in_triage_input, and structurally by
assert_no_ground_truth_leakage walking this model's ancestry.
*/

select
    event_id,
    event_kind,
    run_id,
    session_id,
    received_at,
    client_ts,
    schema_version,
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
    headers,
    tls_fingerprint,
    body_is_valid_json,
    is_quarantined,
    quarantine_reason,
    kafka_partition,
    kafka_offset,
    landed_at,
    dt,
    hour
from {{ ref('stg_attack_events_typed') }}
