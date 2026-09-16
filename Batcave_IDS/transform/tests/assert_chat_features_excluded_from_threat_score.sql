/*
The three chat features are structurally zero in Track A (nothing produces
chat_turn events until Track B). They are carried anyway so the triage model's
input contract does not change shape across the Track boundary.

That only stays safe if they are NOT threat-score components. If they were,
Track B landing real values would silently shift every historical score and
make Track A's recorded numbers non-comparable — with nothing flagging it.

mart_threat_scores arrives in Phase 6. This test asserts the invariant now, and
starts biting the moment that model exists, rather than being remembered later.
*/

with threat_score_columns as (
    select column_name
    from information_schema.columns
    where lower(table_name) = 'mart_threat_scores'
)

select column_name
from threat_score_columns
where lower(column_name) in (
    'chat_turns_completed',
    'probe_engagement_ratio',
    'intent_flags_triggered'
)
