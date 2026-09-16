{{ config(tags=["ground_truth"]) }}

/*
The one place `attempt_id` survives on the observed stream: event_id ->
attempt_id, for mart_detection_correlation to join attempts to the requests
they generated.

Tagged ground_truth even though every column comes from a request event,
because that is what the tag means here — knowing which attempt produced a
request reveals the attempt boundaries the triage model is supposed to infer.
Tagging it ground_truth makes assert_no_ground_truth_leakage reject any
triage_input model that reaches for it, which a neutral tag would not.
*/

select
    event_id,
    session_id,
    run_id,
    attempt_id,
    received_at
from {{ ref('stg_attack_events_typed') }}
where attempt_id is not null
