"""Cookie-based session derivation, gap-enforced.

The honeypot mints an opaque random session token on first contact and sets
it as a cookie. A request presenting a known cookie continues that session —
whatever its source IP or user agent — which is what lets a rotating attacker
(Penguin's henchmen, high-intelligence UA rotation, docs/03) stay one session
while `distinct_source_ips` / `distinct_user_agents` still count the rotation.

The gap rule still applies on top of the cookie: a returned cookie whose last
request is older than `gap_seconds` starts a NEW session rather than resuming,
so two unrelated runs that happen to reuse a client don't stitch together
(the Phase 2 (ip, ua) merge bug in cookie form). A request with no cookie, or
an unknown/expired one, mints a fresh session — so curl and manual tests work
without carrying cookies.

The token is opaque: minted here, never derived from IP, UA, run, or villain.
The simulator receives and returns it like any browser would.

**Known simplification** (docs/02, README): a cookie hands the defender
session continuity that a real rotating-attacker scenario would not. Real
sessionization under identifier rotation is probabilistic and hard. This is a
deliberate portfolio simplification, stated plainly rather than implied.

State is a small in-memory dict, acceptable at this project's scale. A real
deployment with more than one honeypot replica, or wanting sessions to survive
a restart, would externalize this — Redis or similar.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

COOKIE_NAME = "batcave_sid"


@dataclass
class _SessionState:
    session_id: str
    last_seen_at: float  # epoch seconds, from received_at


class SessionTracker:
    def __init__(self, gap_seconds: float, eviction_multiple: float = 4.0) -> None:
        self._gap_seconds = gap_seconds
        self._eviction_after = gap_seconds * eviction_multiple
        self._sessions: dict[str, _SessionState] = {}

    def session_id_for(self, cookie_token: str | None, now_epoch: float) -> str:
        """Return the session id for this request, minting a new one when the
        cookie is absent, unknown, or older than the gap. The returned value is
        also the token the caller should set back as the cookie."""
        state = self._sessions.get(cookie_token) if cookie_token else None

        if state is None or (now_epoch - state.last_seen_at) >= self._gap_seconds:
            token = str(uuid.uuid4())
            state = _SessionState(session_id=token, last_seen_at=now_epoch)
        else:
            state.last_seen_at = now_epoch

        self._sessions[state.session_id] = state
        self._evict_stale(now_epoch)
        return state.session_id

    def _evict_stale(self, now_epoch: float) -> None:
        stale = [
            token
            for token, state in self._sessions.items()
            if (now_epoch - state.last_seen_at) > self._eviction_after
        ]
        for token in stale:
            del self._sessions[token]

    def __len__(self) -> int:
        return len(self._sessions)
