"""Pydantic models for the shared Kafka event envelope and the `request` event kind.

Full contract: docs/02-data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "v1"


class EventEnvelope(BaseModel):
    """Fields shared by every event_kind on the attack.events topic."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_kind: Literal["request", "attempt", "chat_turn", "counterstrike"]
    run_id: str | None = None
    session_id: str
    received_at: datetime
    client_ts: datetime | None = None
    schema_version: str = SCHEMA_VERSION


class RequestEvent(EventEnvelope):
    """`event_kind = 'request'` — one row per HTTP request the honeypot receives.

    `attempt_id` is present from Phase 1 even though nothing populates it until
    Phase 2's stage machine exists, so Phase 1's own emitted events already match
    the Phase 2 contract instead of needing a schema change later.
    """

    event_kind: Literal["request"] = "request"

    source_ip: str | None = None
    user_agent: str | None = None
    http_method: str
    path: str
    query_string: str | None = None
    path_tier: int
    request_body: str | None = None
    body_bytes: int
    status_returned: int
    response_time_ms: float
    headers: str  # JSON-encoded string of the request headers
    attempt_id: str | None = None
