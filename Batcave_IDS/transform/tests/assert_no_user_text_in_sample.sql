/*
docs/02: `user_text` is excluded from the committed sample partition WITHOUT
EXCEPTION. Bat bot conversations are the one place a human could type something
personal into this project, and a sample partition is the part that gets
committed to a public repository.

Phase 8 shipped this as a column-existence check over `information_schema` -
`fct_botchat_turns` doesn't select `user_text`, so it can't reach a mart. That
was real, but it verified the *model definition*, not the *committed data*,
and it passed vacuously: no `chat_turn` data existed yet to test against, so
this would have passed exactly the same way even if the redaction it's
supposed to enforce had never been written correctly. docs/02 and docs/06 both
say the guarantee must be verified by reading the committed Parquet - Phase 9
is the first point real data exists to do that against, so this test is
upgraded in place, same name and location, to do the real thing.

Reads the committed sample partition's own `chat_turn` files directly -
`services/consumer/sample_partition.py`'s exact glob (`sample-*.parquet`
specifically, not every landed file - a landing-only `part-*.parquet` is
gitignored and never public; only the committed sample is) - and fails on any
row where `user_text` is not null.

Tolerant of "no chat_turn sample exists yet," the same way `raw_events_exist()`
is tolerant of an absent kind: a clean clone, or a corpus that predates the
first bat bot conversation ever being committed to the sample, must not fail
this test just because there is nothing to check yet. That tolerance is
deliberately NOT also given a permanent "is this vacuous" CI guard the way
`assert_twoface_test_is_not_vacuous` guards an always-true corpus invariant -
whether a chat_turn sample exists is a one-time historical event in this
repository (the first time `make sample-partition` runs after a real
conversation has been played), not an ongoing guarantee the way "Two-Face is
always in a twelve-villain corpus" is. Non-vacuousness is proven once, by
hand, as part of the Phase 9 checkpoint: land a `user_text`-bearing row
somewhere this test *would* catch it, confirm it does, record the run in
docs/09-engineering-log.md.
*/

{% set sample_glob = var("data_root", "../data") ~ "/raw/chat_turn/**/sample-*.parquet" %}

{% if execute %}
    {% set sample_file_count = run_query("select count(*) as n from glob('" ~ sample_glob ~ "')").columns[0][0] %}
{% else %}
    {#- Parse-time: no files to count yet, and none needed - the graph still builds. -#}
    {% set sample_file_count = 0 %}
{% endif %}

{% if sample_file_count > 0 %}
select event_id, session_id, user_text
from read_parquet('{{ sample_glob }}', union_by_name = true)
where user_text is not null
{% else %}
select null as event_id, null as session_id, null as user_text
where false
{% endif %}
