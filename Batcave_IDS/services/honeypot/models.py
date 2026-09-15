"""Pydantic model for the `request` event kind.

Full contract: docs/02-data-model.md. Shared envelope fields live in
services/common/envelope.py — every event_kind extends the same base.
"""

from __future__ import annotations

from typing import Literal

from services.common.envelope import EventEnvelope


class RequestEvent(EventEnvelope):
    """`event_kind = 'request'` — one row per HTTP request the honeypot receives.

    `attempt_id` is present from Phase 1 even though nothing populated it until
    Phase 2's stage machine existed, so Phase 1's own emitted events already
    matched the Phase 2 contract instead of needing a schema change later.
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
