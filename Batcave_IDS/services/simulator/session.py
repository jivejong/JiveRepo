"""The Phase 2 stage machine: enter stage -> gate techniques -> attempt ->
resolve -> advance or stall.

**The retry/pivot policy here is a Phase 2 placeholder** — a small fixed
retry cap and a simple pivot-to-next-untried-technique rule — explicitly
not the durability-and-signature-driven behavior docs/06 assigns to
Phase 3. The plumbing around it (attempt sequencing, event publishing,
gating, probability) is what Phase 3 keeps; only `_decide_next_action`
gets replaced.

Session bootstrap: `attack_attempts` and `attack_events` join on
session_id (docs/02), but the honeypot derives session_id itself from
(source_ip, user_agent, gap) — the simulator can't predict it. A warm-up
call before the stage machine starts reads it off the honeypot's
`X-Session-Id` response header, so every attempt event this run produces
carries the same session_id as the request events its own driven traffic
lands in.
"""

from __future__ import annotations

import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import httpx
from confluent_kafka import Producer

from services.common.envelope import EventEnvelope
from services.simulator.catalog import Technique, gated_techniques, load_stages, load_villains
from services.simulator.probability import compute_probability, resolve_attempt
from services.simulator.traffic import drive_honeypot

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "attack.events")
HONEYPOT_BASE_URL = os.environ.get("HONEYPOT_BASE_URL", "http://localhost:8000")

# Phase 2 placeholders (docs/06 Phase 3 recalibration list) — replaced by
# durability/signature-driven decisions once BehaviorProfile exists.
RETRY_CAP = 3
MIN_PROBABILITY_TO_RETRY = 0.05


def _delivery_report(err, msg) -> None:
    if err is not None:
        # Nothing silently dropped (CLAUDE.md working principle), matching
        # the honeypot's own producer callback.
        print(f"simulator: delivery failed for session {msg.key()!r}: {err}")


class AttemptEvent(EventEnvelope):
    """`event_kind = 'attempt'` — docs/07's attempt event fields."""

    event_kind: Literal["attempt"] = "attempt"
    attempt_id: str
    stage: int
    technique_id: str
    attack_id: str
    attempt_seq: int
    technique_attempt_seq: int
    decision: Literal["initial", "retry", "pivot"]
    parameters: dict = {}  # Phase 2: always empty; Phase 3 tunes real params
    computed_probability: float
    roll: float
    outcome: Literal["success", "failure", "detected"]
    noise_generated: int
    stage_entered_at: datetime
    attempt_at: datetime


@dataclass
class SessionResult:
    session_id: str
    villain_slug: str
    max_stage_reached: int
    stalled: bool
    attempts: list[AttemptEvent] = field(default_factory=list)


def _decide_next_action(
    current: Technique,
    candidates: list[Technique],
    tried: dict[str, int],
    villain,
    stage,
) -> tuple[Literal["retry", "pivot", "stall"], Technique | None]:
    """Phase 2 placeholder policy — see module docstring."""
    attempt_count = tried[current.technique_id]
    next_probability = compute_probability(current, villain, stage, attempt_count + 1)
    if attempt_count < RETRY_CAP and next_probability >= MIN_PROBABILITY_TO_RETRY:
        return "retry", current

    untried = [t for t in candidates if t.technique_id not in tried]
    if untried:
        return "pivot", untried[0]

    return "stall", None


def run_scripted_session(
    villain_slug: str,
    rng: random.Random | None = None,
    kafka_producer=None,
    http_client: httpx.Client | None = None,
) -> SessionResult:
    rng = rng or random.Random()
    villain = load_villains()[villain_slug]
    stages = load_stages()

    owns_http_client = http_client is None
    http_client = http_client or httpx.Client(base_url=HONEYPOT_BASE_URL, timeout=10.0)
    owns_producer = kafka_producer is None
    kafka_producer = kafka_producer or Producer(
        {"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS, "acks": "all", "enable.idempotence": False}
    )

    try:
        warmup = http_client.get("/", headers={"X-Attempt-Id": "session-bootstrap"})
        session_id = warmup.headers["X-Session-Id"]

        attempts: list[AttemptEvent] = []
        attempt_seq = 0
        max_stage_reached = 0
        stalled = False

        for stage_num in range(1, 5):
            stage = stages[stage_num]
            stage_entered_at = datetime.now(UTC)
            candidates = gated_techniques(villain, stage_num)
            if not candidates:
                stalled = True
                break

            current_technique = candidates[0]
            decision: Literal["initial", "retry", "pivot"] = "initial"
            tried: dict[str, int] = {}
            advanced = False

            while True:
                technique_attempt_seq = tried.get(current_technique.technique_id, 0) + 1
                attempt_seq += 1
                attempt_at = datetime.now(UTC)
                attempt_id = str(uuid.uuid4())

                resolution = resolve_attempt(
                    current_technique, villain, stage, technique_attempt_seq, rng=rng
                )

                if current_technique.produces_traffic:
                    drive_honeypot(http_client, current_technique.technique_id, attempt_id)

                event = AttemptEvent(
                    session_id=session_id,
                    received_at=attempt_at,
                    attempt_id=attempt_id,
                    stage=stage_num,
                    technique_id=current_technique.technique_id,
                    attack_id=current_technique.attack_id,
                    attempt_seq=attempt_seq,
                    technique_attempt_seq=technique_attempt_seq,
                    decision=decision,
                    computed_probability=resolution.computed_probability,
                    roll=resolution.roll,
                    outcome=resolution.outcome,
                    noise_generated=resolution.noise_generated,
                    stage_entered_at=stage_entered_at,
                    attempt_at=attempt_at,
                )
                attempts.append(event)
                kafka_producer.produce(
                    KAFKA_TOPIC,
                    key=session_id.encode("utf-8"),
                    value=event.model_dump_json().encode("utf-8"),
                    callback=_delivery_report,
                )
                kafka_producer.poll(0)

                tried[current_technique.technique_id] = technique_attempt_seq

                if resolution.outcome == "success":
                    max_stage_reached = stage_num
                    advanced = True
                    break

                action, next_technique = _decide_next_action(
                    current_technique, candidates, tried, villain, stage
                )
                if action == "retry":
                    decision = "retry"
                    continue
                elif action == "pivot":
                    current_technique = next_technique
                    decision = "pivot"
                    continue
                else:
                    stalled = True
                    break

            if stalled or not advanced:
                break

        return SessionResult(
            session_id=session_id,
            villain_slug=villain_slug,
            max_stage_reached=max_stage_reached,
            stalled=stalled,
            attempts=attempts,
        )
    finally:
        if owns_producer:
            kafka_producer.flush(10)
        if owns_http_client:
            http_client.close()
