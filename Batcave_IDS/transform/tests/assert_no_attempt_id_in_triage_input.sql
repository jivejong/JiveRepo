/*
Belt and braces on the correlation key (docs/02).

assert_no_ground_truth_leakage walks lineage, which is the real protection.
This is the cheap column-level complement: attempt_id must not appear in any
model the triage LLM reads, because grouping requests by it reveals the attempt
boundaries the model is supposed to infer.

Reads information_schema rather than a fixed model list, so a triage_input
model added in Phase 6 is covered the moment it exists.
*/

select
    table_name,
    column_name
from information_schema.columns
where
    lower(column_name) = 'attempt_id'
    and table_name in (
        'stg_attack_events',
        'int_session_events',
        'int_session_features_observed',
        'fct_attack_events',
        'fct_botchat_turns'
    )
