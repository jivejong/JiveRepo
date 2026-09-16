{{ config(materialized="ephemeral") }}

/*
Typing, deduplication and quarantine flagging for the observed request stream —
everything stg_attack_events does except dropping `attempt_id`.

Ephemeral and untagged on purpose. `attempt_id` is a correlation key, not a
feature (docs/02): it must never reach a triage_input model, but
mart_detection_correlation genuinely needs it to join attempts to the requests
they generated. Rather than read and dedupe the lakehouse twice, that logic
lives here once and exactly two children consume it:

  stg_attack_events        [triage_input]  - drops attempt_id
  int_request_attempt_link [ground_truth]  - keeps only event_id -> attempt_id

Being ephemeral it is inlined as a CTE rather than materialized, so attempt_id
is never durably stored next to the observed stream.
*/

with raw_events as (
    {{ raw_events('request') }}
),

deduplicated as (
    select
        *,
        /* At-least-once delivery means the same event_id legitimately arrives
           more than once: the duplicate-delivery pathology (~2%, docs/03) and
           any batch replayed after a consumer crash before its offset commit
           (docs/exercises.md — 48 such rows in the Phase 4 corpus).

           Keep the earliest received_at. Two-Face's deliberate duplicate
           *requests* are a different thing entirely: they carry DISTINCT
           event_ids and must survive this (assert_twoface_duplicates_survive).
           kafka_offset breaks ties so the choice is deterministic rather than
           dependent on file read order. */
        row_number() over (
            partition by event_id
            order by received_at asc, kafka_offset asc
        ) as _delivery_seq
    from raw_events
),

typed as (
    select
        event_id,
        event_kind,
        run_id,
        session_id,
        attempt_id,
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
        kafka_partition,
        kafka_offset,
        landed_at,
        dt,
        cast(hour as integer) as hour,
        {{ is_valid_json('request_body') }} as body_is_valid_json,

        /* Quarantine reasons, in the order docs/02 lists them. Rows are FLAGGED,
           never dropped — they flow on to fct_quarantined_events with a reason
           code so nothing disappears silently. */
        case
            when event_id is null then 'null_event_id'
            when received_at is null then 'null_received_at'
            when path is null then 'null_path'
            /* Clock skew (docs/03 row 9) sets received_at an hour ahead.
               Compared against landed_at — the consumer's own clock at landing
               — rather than current_timestamp, which would stop flagging these
               rows an hour after the corpus was produced and make the whole
               test time-dependent. */
            when received_at > landed_at + interval 1 minute then 'future_received_at'
        end as quarantine_reason
    from deduplicated
    where _delivery_seq = 1
)

select
    *,
    quarantine_reason is not null as is_quarantined
from typed
