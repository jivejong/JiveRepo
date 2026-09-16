/*
docs/02: `user_text` is excluded from the committed sample partition WITHOUT
EXCEPTION. Bat bot conversations are the one place a human could type something
personal into this project, and a sample partition is the part that gets
committed to a public repository.

Enforced structurally rather than by inspection: fct_botchat_turns does not
select the column at all, so it cannot reach a mart. This asserts that stays
true — if someone adds it back, this fails rather than a reviewer having to
notice.

Track A produces no chat turns, so the model is empty today. The test is here
for the phase where it is not.
*/

select column_name
from information_schema.columns
where
    lower(table_name) in ('fct_botchat_turns', 'int_session_features_observed')
    and lower(column_name) = 'user_text'
