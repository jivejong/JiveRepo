"""The shared Kafka event envelope — fields every event_kind on attack.events
carries, regardless of which service produces it.

Full contract: docs/02-data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "v1"

EventKind = Literal["request", "attempt", "chat_turn", "counterstrike"]


class EventEnvelope(BaseModel):
    """Fields shared by every event_kind on the attack.events topic."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_kind: EventKind
    run_id: str | None = None
    session_id: str
    received_at: datetime
    client_ts: datetime | None = None
    schema_version: str = SCHEMA_VERSION
