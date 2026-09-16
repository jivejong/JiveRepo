"""The typed empty relations in transform/macros/empty_event_relation.sql
declare columns by hand, because Track A lands no `chat_turn` or
`counterstrike` events to infer them from.

Hand-declared means they can drift from what the consumer will actually write
when Track B lands — and the whole point of building these models now is that
`int_session_features_observed` keeps a stable column contract across the Track
A / Track B boundary. These tests fail when the two drift.
"""

import re
from pathlib import Path

import duckdb
import pytest

from services.common.envelope import EventEnvelope
from services.consumer.schemas import CONSUMER_FIELDS, ENVELOPE_FIELDS

MACRO = Path(__file__).resolve().parent.parent / "transform" / "macros" / "empty_event_relation.sql"
EMPTY_KINDS = ("chat_turn", "counterstrike")


def _macro_body(name: str) -> str:
    """The text of one {% macro name(...) %}...{% endmacro %} block."""
    source = MACRO.read_text(encoding="utf-8")
    # The `-?` matters: these macros use the whitespace-control forms
    # ({%- endmacro %}), and a regex without it runs past the first endmacro
    # and swallows the rest of the file.
    match = re.search(
        rf"{{%-?\s*macro\s+{name}\(.*?-?%}}(.*?){{%-?\s*endmacro\s*-?%}}", source, re.DOTALL
    )
    assert match, f"macro {name} not found in {MACRO.name}"
    return match.group(1)


def _declared_columns(sql_fragment: str) -> list[str]:
    """Column aliases only. Anchored on the closing paren of cast(...), because
    a bare `as (\\w+)` also matches the type inside `cast(null as integer)`."""
    return re.findall(r"\)\s+as\s+([a-z_][a-z0-9_]*)", sql_fragment, re.IGNORECASE)


def _kind_block(event_kind: str) -> str:
    """The branch of empty_event_relation for one kind."""
    body = _macro_body("empty_event_relation")
    marker = f'== "{event_kind}"'
    start = body.index(marker)
    end = body.find("{%- elif", start)
    if end == -1:
        end = body.find("{%- else", start)
    return body[start:end]


def test_envelope_columns_match_the_envelope_model_and_consumer_fields():
    """The shared columns must match what the consumer actually writes, or a
    Track B chat_turn would land with a different envelope than this declares."""
    declared = _declared_columns(_macro_body("envelope_columns"))
    expected = [name for name, _ in ENVELOPE_FIELDS] + [name for name, _ in CONSUMER_FIELDS]
    # dt/hour are Hive partition columns read_parquet exposes, not written fields.
    assert declared[: len(expected)] == expected
    assert declared[len(expected) :] == ["dt", "hour"]
    assert set(name for name, _ in ENVELOPE_FIELDS) == set(EventEnvelope.model_fields)


@pytest.mark.parametrize("event_kind", EMPTY_KINDS)
def test_empty_relation_declares_the_docs02_fields(event_kind):
    """Field lists come from docs/02's fact-source section."""
    expected = {
        "chat_turn": {
            "turn_number",
            "speaker",
            "objective",
            "bot_text",
            "user_text",
            "extracted_intent_flags",
            "refused",
            "latency_ms",
            "input_tokens",
            "output_tokens",
        },
        "counterstrike": {
            "sequence",
            "readout_line",
            "attributed_villain_slug",
            "attributed_confidence",
            "attack_id",
        },
    }[event_kind]
    declared = set(_declared_columns(_kind_block(event_kind)))
    assert expected <= declared, f"missing from {event_kind}: {expected - declared}"


@pytest.mark.parametrize("event_kind", EMPTY_KINDS)
def test_empty_relation_is_valid_sql_and_returns_no_rows(event_kind):
    """Render the branch the way dbt would and run it: an empty relation that
    doesn't parse is worse than no model at all, and nothing else in the suite
    would catch it without a warehouse."""
    envelope = _macro_body("envelope_columns").strip()
    block = _kind_block(event_kind)
    # Strip the Jinja control tokens and splice the envelope in, leaving SQL.
    sql = block[block.index("select") :]
    sql = sql.replace("{{ envelope_columns() }}", envelope)
    sql = re.sub(r"{%-?.*?-?%}", "", sql, flags=re.DOTALL).strip()

    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    result = con.sql(sql)
    assert result.fetchall() == []
    assert "received_at" in result.columns
    assert "user_text" not in result.columns or event_kind == "chat_turn"


def test_user_text_exists_only_on_chat_turn():
    """docs/02: user_text is excluded from the committed sample partition
    without exception. It must not appear on any other kind."""
    assert "user_text" in _declared_columns(_kind_block("chat_turn"))
    assert "user_text" not in _declared_columns(_kind_block("counterstrike"))
