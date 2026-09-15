"""Gap-based session derivation, keyed on (source_ip, user_agent).

A session for a given key continues while the gap since that key's last
request is under `gap_seconds`; once the gap is met or exceeded, the next
request starts a new session. Gaps are measured against `received_at` (the
honeypot's own receipt time), never a client-supplied timestamp.

This replaces a fixed tumbling time-bucket, which would fragment any session
that straddles a bucket boundary regardless of the bucket's width — a real
risk given how much session length varies by villain durability (docs/03).

State is a small in-memory dict, acceptable at this project's scale. A real
(non-portfolio) deployment running more than one honeypot replica, or wanting
sessions to survive a restart, would externalize this — Redis or similar.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass
class _SessionState:
    session_id: str
    last_seen_at: float  # epoch seconds, from received_at


class SessionTracker:
    def __init__(self, gap_seconds: float, eviction_multiple: float = 4.0) -> None:
        self._gap_seconds = gap_seconds
        self._eviction_after = gap_seconds * eviction_multiple
        self._sessions: dict[tuple[str, str], _SessionState] = {}

    def session_id_for(
        self, source_ip: str | None, user_agent: str | None, now_epoch: float
    ) -> str:
        key = (source_ip or "", user_agent or "")
        state = self._sessions.get(key)

        if state is None or (now_epoch - state.last_seen_at) >= self._gap_seconds:
            state = _SessionState(session_id=str(uuid.uuid4()), last_seen_at=now_epoch)
        else:
            state.last_seen_at = now_epoch

        self._sessions[key] = state
        self._evict_stale(now_epoch)
        return state.session_id

    def _evict_stale(self, now_epoch: float) -> None:
        stale_keys = [
            key
            for key, state in self._sessions.items()
            if (now_epoch - state.last_seen_at) > self._eviction_after
        ]
        for key in stale_keys:
            del self._sessions[key]

    def __len__(self) -> int:
        return len(self._sessions)
