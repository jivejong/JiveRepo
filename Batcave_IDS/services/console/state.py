"""In-memory console session registry (Phase 8, docs/06 Track B, docs/08).

One `StageMachine` (`services/simulator/machine.py`) per browser session, held
in a process-local dict keyed by a token this module mints. This is the same
simplification `services/honeypot/session.py`'s `SessionTracker` already makes
and documents for the honeypot's own cookie-keyed sessions: a small in-memory
dict, no persistence across a restart, fine for a single-player local demo. A
real (non-portfolio) multi-replica deployment would externalize this the same
way that module says the honeypot's session state would need to be.

The console's own session token is deliberately a *different* identifier from
the honeypot's `session_id` (the `batcave_sid` cookie value docs/02 describes).
The browser needs something to address "which StageMachine" across stateless
HTTP calls; the honeypot's session_id is learned *from* the machine's warm-up
call and only exists once `start()` has run. Two tokens, two purposes - the
console token is never published anywhere; only `session_id` (the honeypot's)
ever reaches an event envelope.
"""

from __future__ import annotations

import random
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from services.console.batbot import BatBotConversation
from services.simulator.catalog import (
    Technique,
    Villain,
    gated_techniques,
    load_stages,
    load_techniques,
)
from services.simulator.machine import KAFKA_TOPIC, StageMachine
from services.simulator.pathologies import PathologyConfig, PathologyInjector

# A session that outlives being useful (abandoned mid-play, browser closed)
# never gets cleaned up automatically - same simplification as the honeypot's
# SessionTracker, stated rather than hidden. Fine for a single-player local
# demo; a real deployment would add eviction.


@dataclass
class ConsoleSession:
    console_session_id: str
    machine: StageMachine
    # The current stage's gated (available) candidates, in the shuffled order
    # StageMachine.enter_stage produced - stored so /attempt can validate the
    # player's chosen technique_id against exactly what was offered, and so
    # /state doesn't need to re-derive it (gating is a pure function of the
    # villain's stats and the stage, but re-deriving would silently diverge
    # from the shuffled instance this session actually has candidates from
    # if catalog.py's gating logic ever became non-deterministic).
    current_candidates: list[Technique] = field(default_factory=list)
    finished: bool = False
    run_outcome: str | None = None  # "cleared" | "stalled", set once finished
    # Set the instant `deploy_batbot` succeeds at stage 4 (docs/08: the bat
    # bot is "delivered by the deploy_batbot technique at stage 4"). This
    # BLOCKS the run from finishing - the consent notice is "not dismissible
    # by clicking away" (docs/08), so there is no decline path once this is
    # set, only a conversation to complete. /attempt does not call
    # machine.finish() when this is set, even though stage 4 cleared;
    # completion happens when the bat bot conversation reaches reveal.
    batbot_pending: bool = False
    batbot: BatBotConversation | None = None


class SessionRegistry:
    """Process-local. `kafka_producer` is shared across every session (one
    librdkafka producer for the app's lifetime, matching the honeypot's own
    one-producer-per-process pattern); each session still gets its own
    `httpx.Client` from `StageMachine`'s own default construction, because the
    honeypot's session cookie - and therefore `session_id` - lives in that
    client's cookie jar and must not be shared between two players."""

    def __init__(
        self,
        kafka_producer,
        http_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._kafka_producer = kafka_producer
        # None (the default) means "let StageMachine build its own real
        # httpx.Client against the honeypot" - the production path.
        # tests/test_console_api.py overrides this with a MockTransport
        # factory, the same testing seam services/honeypot/app.py's
        # HONEYPOT_DISABLE_KAFKA / _NullProducer gives the honeypot's own
        # Kafka dependency.
        self._http_client_factory = http_client_factory
        self._sessions: dict[str, ConsoleSession] = {}
        self._lock = threading.Lock()

    def create(self, villain_slug: str) -> ConsoleSession:
        rng = random.Random()
        injector = PathologyInjector(PathologyConfig.load(), rng)
        machine = StageMachine(
            villain_slug,
            rng=rng,
            kafka_producer=self._kafka_producer,
            http_client=self._http_client_factory() if self._http_client_factory else None,
            sleep_fn=lambda _seconds: None,  # the player's own pacing IS the delay
            injector=injector,
            timing_compression_factor=1.0,  # human real time - nothing compressed
            session_source="console",
        )
        machine.start()
        session = ConsoleSession(console_session_id=str(uuid.uuid4()), machine=machine)
        with self._lock:
            self._sessions[session.console_session_id] = session
        return session

    def create_batbot(self, session: ConsoleSession, llm_client) -> BatBotConversation:
        """One `BatBotConversation` for a session whose `deploy_batbot`
        attempt just succeeded. Shares the registry's own Kafka producer -
        `chat_turn` events are published the same way `attempt`/`attack_run`
        already are, directly by the console backend (Phase 9 plan:
        console-only architecture, honeypot untouched). `llm_client` is
        `None` for the zero-credential fallback path (docs/08), threaded
        through from `services/console/app.py`'s own startup the same way
        `services/triage/__main__.py` conditionally builds one."""
        convo = BatBotConversation(
            session_id=session.machine.session_id,
            run_id=session.machine.run_id,
            kafka_producer=self._kafka_producer,
            kafka_topic=KAFKA_TOPIC,
            llm_client=llm_client,
        )
        session.batbot = convo
        return convo

    def get(self, console_session_id: str) -> ConsoleSession:
        with self._lock:
            session = self._sessions.get(console_session_id)
        if session is None:
            raise KeyError(console_session_id)
        return session

    def close_all(self) -> None:
        """App shutdown: close every session's own http_client. The shared
        kafka_producer is the caller's to flush/close (it isn't `owns_producer`
        on any of these machines, so `machine.close()` won't touch it)."""
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if not session.finished:
                session.machine.close()


def stage_catalog(stage_num: int) -> list[Technique]:
    """Every technique defined for a stage, gated or not - the full menu
    docs/08 wants so locked techniques can be shown greyed out."""
    return [t for t in load_techniques() if t.stage == stage_num]


def lock_reason(villain: Villain, technique: Technique) -> dict | None:
    """Which stat gate(s) this villain fails for this technique, or None if
    they qualify. Mirrors catalog.gated_techniques' own gate list exactly, so
    "why is this greyed out" always agrees with the actual gating logic."""
    failing = []
    if villain.intelligence < technique.min_intelligence:
        failing.append(
            {
                "stat": "intelligence",
                "required": technique.min_intelligence,
                "have": villain.intelligence,
            }
        )
    if villain.power < technique.min_power:
        failing.append({"stat": "power", "required": technique.min_power, "have": villain.power})
    if villain.strength < technique.min_strength:
        failing.append(
            {"stat": "strength", "required": technique.min_strength, "have": villain.strength}
        )
    return {"failing_gates": failing} if failing else None


def villain_stage_reach(villain: Villain) -> list[dict]:
    """docs/08's villain-select requirement: "show which stages and how many
    techniques each villain can reach, computed live from the gating rules."
    One row per stage 1-4, straight from catalog.gated_techniques - no second
    gating implementation."""
    reach = []
    for stage_num in load_stages():
        if stage_num == 0:
            continue
        total = stage_catalog(stage_num)
        available = gated_techniques(villain, stage_num)
        reach.append({"stage": stage_num, "available": len(available), "total": len(total)})
    return reach
