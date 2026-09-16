/* Neither triage_input nor ground_truth: an operational data-quality fact, not
   a model input and not an answer key.

   Quarantined rows are never dropped (docs/02). Every landed request event
   appears in exactly one of this and fct_attack_events, which is what lets
   mart_reconciliation show that nothing vanished. */

select
    event_id,
    session_id,
    run_id,
    received_at,
    landed_at,
    path,
    quarantine_reason,
    kafka_partition,
    kafka_offset,
    dt,
    hour
from {{ ref('stg_attack_events') }}
where is_quarantined
