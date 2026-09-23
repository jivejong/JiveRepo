"""The landed Parquet schema is hand-declared (services/consumer/schemas.py) so
column types stay stable across flushes. Hand-declared means it can drift from
the Pydantic models it mirrors — these tests fail when it does, so a new event
field can't land as an untyped drift string by accident.
"""

import pyarrow as pa
import pytest

from services.common.envelope import EventEnvelope
from services.console.batbot import ChatTurnEvent
from services.console.counterstrike import CounterstrikeEvent
from services.consumer.schemas import (
    CONSUMER_FIELDS,
    ENVELOPE_FIELDS,
    KIND_FIELDS,
    known_field_names,
    landed_schema,
)
from services.honeypot.models import RequestEvent
from services.simulator.session import AttackRunEvent, AttemptEvent

MODELS = {
    "request": RequestEvent,
    "attempt": AttemptEvent,
    "attack_run": AttackRunEvent,
    "chat_turn": ChatTurnEvent,
    "counterstrike": CounterstrikeEvent,
}


def test_envelope_fields_match_the_envelope_model():
    declared = {name for name, _ in ENVELOPE_FIELDS}
    assert declared == set(EventEnvelope.model_fields)


@pytest.mark.parametrize("event_kind", sorted(MODELS))
def test_landed_schema_covers_every_model_field(event_kind):
    """Every field on the Pydantic model has a typed column, and the schema
    declares nothing the model doesn't have."""
    model_fields = set(MODELS[event_kind].model_fields)
    assert known_field_names(event_kind) == model_fields


@pytest.mark.parametrize("event_kind", sorted(MODELS))
def test_landed_schema_appends_the_three_consumer_columns(event_kind):
    schema = landed_schema(event_kind)
    for name, dtype in CONSUMER_FIELDS:
        assert schema.field(name).type == dtype


def test_unknown_kind_still_gets_a_usable_schema():
    """Every real kind in the contract now has a declared schema
    (counterstrike got its own in Phase 10, the last one) - so this
    exercises the fallback path with a kind that doesn't exist at all,
    standing in for whatever kind a future phase adds before its schema is
    declared here. An unknown kind must not halt the consumer - it lands
    envelope + consumer columns and its own fields ride along as drift
    strings."""
    schema = landed_schema("some_future_kind_not_yet_declared")
    assert schema.names == [name for name, _ in ENVELOPE_FIELDS + CONSUMER_FIELDS]


def test_no_column_is_null_typed():
    """A null-typed column is exactly the inference failure this module exists
    to prevent - it can't merge with a real type in a sibling file."""
    for event_kind in [*KIND_FIELDS, "some_future_kind_not_yet_declared"]:
        for field in landed_schema(event_kind):
            assert field.type != pa.null(), f"{event_kind}.{field.name} is null-typed"
