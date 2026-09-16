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

# `attack_run` (Phase 3) is a fifth kind beyond docs/02's original four. It
# carries the ground-truth run metadata (including villain_slug) so a run's
# observed session joins to its truth; it's produced through the same topic to
# keep one data path (Kafka -> consumer -> Parquet -> dbt), landed separately,
# and tagged `ground_truth` in dbt like attack_attempts.
EventKind = Literal["request", "attempt", "chat_turn", "counterstrike", "attack_run"]


class EventEnvelope(BaseModel):
    """Fields shared by every event_kind on the attack.events topic."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_kind: EventKind
    run_id: str | None = None
    session_id: str
    received_at: datetime
    client_ts: datetime | None = None
    schema_version: str = SCHEMA_VERSION
