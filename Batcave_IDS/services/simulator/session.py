"""The autopilot driver over `StageMachine` (services/simulator/machine.py).

Through Phase 7 this module *was* the stage machine: `run_scripted_session`
made both interactive decisions - which technique opens a stage, retry-vs-
pivot - inline in one blocking loop. Phase 8 (docs/06 Track B, docs/08) pulled
that resumable core out into `StageMachine` so the console can pause at those
same two points instead of reimplementing them. This module is now a thin
loop over it that always takes the autopilot's choices
(`StageMachine.autopilot_choice`, `StageMachine.decide_next_action` - the same
`_decide_next_action` policy this file always ran) plus the one behavior that
is autopilot-only: the post-stage grind continuation that fills a non-clean-
operator's request budget (docs/03 - Croc grinds his stall point, Mister
Freeze holds at the objective). `services/simulator/machine.py`'s module
docstring covers the extraction itself and the RNG-purity requirement it had
to preserve; `tests/test_simulator_machine.py` proves it.

`AttemptEvent`, `AttackRunEvent`, and the `KAFKA_*`/`HONEYPOT_BASE_URL`/
`MAX_ATTEMPTS` constants now live in `machine.py` and are re-exported here so
every existing importer (`services/consumer/landing_check.py`,
`services/simulator/pathology_check.py`, `services/simulator/separability.py`,
`tests/test_consumer_schemas.py`) keeps working unchanged.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from services.simulator.machine import (
    HONEYPOT_BASE_URL,  # noqa: F401 - re-exported for existing importers
    KAFKA_BOOTSTRAP_SERVERS,  # noqa: F401 - re-exported for existing importers
    KAFKA_TOPIC,  # noqa: F401 - re-exported for existing importers
    MAX_ATTEMPTS,
    AttackRunEvent,  # noqa: F401 - re-exported for tests/test_consumer_schemas.py
    AttemptEvent,  # noqa: F401 - re-exported for tests/test_consumer_schemas.py
    StageMachine,
)
from services.simulator.pathologies import PathologyInjector


@dataclass
class SessionResult:
    session_id: str
    run_id: str
    villain_slug: str
    max_stage_reached: int
    stalled: bool
    requests_sent: int = 0
    attempts: list[AttemptEvent] = field(default_factory=list)


def run_scripted_session(
    villain_slug: str,
    rng: random.Random | None = None,
    kafka_producer=None,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    injector: PathologyInjector | None = None,
    timing_compression_factor: float = 1.0,
) -> SessionResult:
    """Run one villain's session headlessly, pacing requests in real time so
    the timing features (requests_per_min, duration_s, inter_request_stddev_ms)
    reflect Layer 1. `sleep_fn` is injectable so tests pass a no-op and don't
    wait.

    `injector` (Phase 3 Part 2) decides which deliberate pathologies corrupt
    each request event. None = clean stream (Phase 2 behavior). Publishes one
    `attack_run` (ground truth) at the end, recording `timing_compression_factor`
    so the corpus is self-describing. `session_source` on that event is always
    "headless" - this function is the headless driver; the console backend
    (services/console/) constructs its own `StageMachine` with
    `session_source="console"`.
    """
    rng = rng or random.Random()
    machine = StageMachine(
        villain_slug,
        rng=rng,
        kafka_producer=kafka_producer,
        http_client=http_client,
        sleep_fn=sleep_fn,
        injector=injector,
        timing_compression_factor=timing_compression_factor,
        session_source="headless",
    )
    try:
        machine.start()

        for stage_num in range(1, 5):
            candidates = machine.enter_stage(stage_num)
            if candidates is None:
                break

            current_technique = machine.autopilot_choice(candidates)
            decision = "initial"

            while True:
                event = machine.attempt(current_technique, decision)
                if event.outcome == "success":
                    break
                if machine.stalled:
                    break

                action, next_technique = machine.decide_next_action(candidates)
                if action == "retry":
                    decision = "retry"
                elif action == "pivot":
                    current_technique, decision = next_technique, "pivot"
                else:
                    machine.stalled = True
                    break

            if machine.stalled:
                break

        # Continuation: a villain that hasn't spent its request budget and
        # isn't a clean operator keeps generating traffic from where it ended -
        # Croc grinds his stall point, Mister Freeze holds at the objective.
        # Clean operators (Ra's al Ghul, Catwoman - a signature property, not a
        # stat) exit as soon as the stage machine resolves, below budget: low
        # request_count, direct.
        if machine.last_technique is not None and not machine.signature.clean_operator:
            while (
                machine.attempt_seq < machine.request_budget and machine.attempt_seq < MAX_ATTEMPTS
            ):
                machine.grind_attempt()

        machine.finish()

        return SessionResult(
            session_id=machine.session_id,
            run_id=machine.run_id,
            villain_slug=villain_slug,
            max_stage_reached=machine.max_stage_reached,
            stalled=machine.stalled,
            requests_sent=machine.requests_sent,
            attempts=machine.attempts,
        )
    finally:
        machine.close()
