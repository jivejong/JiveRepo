"""The Phase 2 stage machine: enter stage -> gate techniques -> attempt ->
resolve -> advance or stall.

**The retry/pivot policy here is a Phase 2 placeholder** — a small fixed
retry cap and a simple pivot-to-next-untried-technique rule — explicitly
not the durability-and-signature-driven behavior docs/06 assigns to
Phase 3. The plumbing around it (attempt sequencing, event publishing,
gating, probability) is what Phase 3 keeps; only `_decide_next_action`
gets replaced.

Session bootstrap: `attack_attempts` and `attack_events` join on
session_id (docs/02), but the honeypot mints it (cookie, gap-enforced) —
the simulator can't predict it. A warm-up call before the stage machine
starts lets the honeypot set the `batcave_sid` cookie in this run's
httpx client jar; the simulator reads it back as its session_id. Every
attempt event this run produces carries that session_id, matching the
request events its own driven traffic lands in — even as the run rotates
source IP or user agent (the cookie survives rotation).

One `httpx.Client` per run: its cookie jar keeps rotation inside a single
session, and a fresh client per run keeps back-to-back runs separate.
"""

from __future__ import annotations

import os
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import httpx
from confluent_kafka import Producer

from services.common.envelope import EventEnvelope
from services.honeypot.session import COOKIE_NAME
from services.simulator.behavior import BehaviorProfile
from services.simulator.catalog import Technique, gated_techniques, load_stages, load_villains
from services.simulator.identity import RunIdentity
from services.simulator.probability import compute_probability, resolve_attempt
from services.simulator.signatures import signature_for
from services.simulator.traffic import request_specs_for, send_requests

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "attack.events")
HONEYPOT_BASE_URL = os.environ.get("HONEYPOT_BASE_URL", "http://localhost:8000")

MIN_PROBABILITY_TO_RETRY = 0.05
# Safety cap so a persistent grinder can't loop unbounded; the real driver of
# attempt count is failure_tolerance (durability), the real driver of duration
# is the paced inter-request interval (speed).
MAX_ATTEMPTS = 400


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
    profile: BehaviorProfile,
    failures_so_far: int,
) -> tuple[Literal["retry", "pivot", "stall"], Technique | None]:
    """Layer 1 retry-vs-pivot, durability-driven (docs/03, docs/06):

    - A villain gives up once it has burned through `failure_tolerance`
      failures — durability 14 (Riddler, Two-Face) stops after one, durability
      90 (Croc) grinds on. This is what makes retry_ratio and duration diverge.
    - Retry the same technique while its next-attempt probability is still
      worthwhile *and* the villain has alternatives-appetite below its
      targeting precision; otherwise pivot to the next untried gated technique.
      High-precision (high-INT) villains pivot sooner rather than grinding,
      producing the high pivot_ratio Ra's al Ghul is meant to show; low-INT
      Croc has nothing to pivot to and grinds, producing the high retry_ratio.
    """
    if failures_so_far >= profile.failure_tolerance:
        return "stall", None

    attempt_count = tried[current.technique_id]
    next_probability = compute_probability(current, villain, stage, attempt_count + 1)
    untried = [t for t in candidates if t.technique_id not in tried]

    # Prefer pivoting when precise and an alternative exists; otherwise retry
    # while the odds hold up.
    if untried and profile.targeting_precision >= 0.5:
        return "pivot", untried[0]
    if next_probability >= MIN_PROBABILITY_TO_RETRY:
        return "retry", current
    if untried:
        return "pivot", untried[0]
    return "stall", None


def _inter_request_delay(profile: BehaviorProfile, burstiness: float, rng: random.Random) -> float:
    """Seconds to wait before the next request. Base interval from speed
    (requests_per_min); variance from intelligence (jitter) plus a villain's
    signature burstiness. This is what drives inter_request_stddev_ms — a
    high-INT or bursty villain spaces irregularly, a low-INT enumerator is
    metronomic. Harley's burstiness is what separates her from Joker, who has
    the same high intelligence but steadier spacing."""
    base = 60.0 / max(profile.requests_per_min, 1.0)
    variance = min(1.0, profile.jitter + burstiness)
    # variance 0 -> exact base; 1 -> up to +/-90% swing.
    swing = 1.0 + variance * (rng.random() * 2.0 - 1.0) * 0.9
    return max(0.0, base * swing)


def run_scripted_session(
    villain_slug: str,
    rng: random.Random | None = None,
    kafka_producer=None,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> SessionResult:
    """Run one villain's session, pacing requests in real time so the timing
    features (requests_per_min, duration_s, inter_request_stddev_ms) reflect
    Layer 1. `sleep_fn` is injectable so tests pass a no-op and don't wait."""
    rng = rng or random.Random()
    villain = load_villains()[villain_slug]
    stages = load_stages()

    owns_http_client = http_client is None
    http_client = http_client or httpx.Client(base_url=HONEYPOT_BASE_URL, timeout=10.0)
    owns_producer = kafka_producer is None
    kafka_producer = kafka_producer or Producer(
        {"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS, "acks": "all", "enable.idempotence": False}
    )

    profile = BehaviorProfile.from_villain(villain)
    signature = signature_for(villain_slug)
    identity = RunIdentity(
        rng,
        rotate_ip=signature.rotate_ip,
        rotate_user_agent=profile.rotates_user_agent,
    )

    try:
        # Warm-up: let the honeypot mint a session and set batcave_sid in this
        # client's cookie jar, then read it back as our session_id.
        http_client.get("/", headers={"X-Attempt-Id": "session-bootstrap", **identity.headers()})
        session_id = http_client.cookies.get(COOKIE_NAME)
        if session_id is None:
            raise RuntimeError("honeypot did not set the batcave_sid session cookie")

        attempts: list[AttemptEvent] = []
        state = {"attempt_seq": 0, "max_stage_reached": 0}

        # Request budget: speed x durability, the docs/03 volume model. A fast
        # or persistent villain generates more traffic over its session; a
        # low-durability one that stops on first error never gets near it. This
        # is what gives request volume its stat-driven spread — the stage
        # progression alone only ever produced ~4-9 attempts regardless of
        # speed, because it terminated on stage resolution, not on a duration.
        request_budget = int(profile.requests_per_min * profile.session_duration_s / 60)

        def emit(technique, stage_num, stage, decision, technique_attempt_seq):
            state["attempt_seq"] += 1
            attempt_at = datetime.now(UTC)
            attempt_id = str(uuid.uuid4())
            resolution = resolve_attempt(
                technique, villain, stage, technique_attempt_seq, evasion=profile.evasion, rng=rng
            )
            if technique.produces_traffic:
                growth = 1.0 + profile.body_growth * (state["attempt_seq"] / 20.0)
                size = int(profile.mean_body_bytes * profile.body_repetition * growth)
                body = (b"x" * size) if size > 0 else None
                specs = signature.transform(request_specs_for(technique.technique_id), rng)
                headers = identity.headers()
                if signature.response_delay_ms > 0:
                    # Mister Freeze holds connections open — the honeypot honors
                    # X-Sim-Delay-Ms as a real server-side response delay,
                    # lifting response_time_ms and duration.
                    headers["X-Sim-Delay-Ms"] = str(signature.response_delay_ms)
                send_requests(http_client, specs, attempt_id, extra_headers=headers, body=body)
            event = AttemptEvent(
                session_id=session_id,
                received_at=attempt_at,
                attempt_id=attempt_id,
                stage=stage_num,
                technique_id=technique.technique_id,
                attack_id=technique.attack_id,
                attempt_seq=state["attempt_seq"],
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
            sleep_fn(_inter_request_delay(profile, signature.burstiness, rng))
            return resolution.outcome

        stalled = False
        failures_so_far = 0  # session-wide; durability sets the tolerance
        last_technique = None
        last_stage = last_stage_num = None

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
                last_technique, last_stage, last_stage_num = current_technique, stage, stage_num
                outcome = emit(current_technique, stage_num, stage, decision, technique_attempt_seq)
                tried[current_technique.technique_id] = technique_attempt_seq

                if outcome == "success":
                    state["max_stage_reached"] = stage_num
                    advanced = True
                    break
                if outcome == "failure":
                    failures_so_far += 1
                if state["attempt_seq"] >= MAX_ATTEMPTS:
                    stalled = True
                    break

                action, next_technique = _decide_next_action(
                    current_technique, candidates, tried, villain, stage, profile, failures_so_far
                )
                if action == "retry":
                    decision = "retry"
                elif action == "pivot":
                    current_technique, decision = next_technique, "pivot"
                else:
                    stalled = True
                    break

            if stalled or not advanced:
                break

        # Continuation: a villain that hasn't spent its request budget and
        # isn't a clean operator keeps generating traffic from where it ended —
        # Croc grinds his stall point, Mister Freeze holds at the objective.
        # Clean operators (Ra's al Ghul, Catwoman — a signature property, not a
        # stat) exit as soon as the stage machine resolves, below budget: low
        # request_count, direct.
        if last_technique is not None and not signature.clean_operator:
            grind_seq = tried.get(last_technique.technique_id, 1)
            while state["attempt_seq"] < request_budget and state["attempt_seq"] < MAX_ATTEMPTS:
                grind_seq += 1
                emit(last_technique, last_stage_num, last_stage, "retry", grind_seq)

        return SessionResult(
            session_id=session_id,
            villain_slug=villain_slug,
            max_stage_reached=state["max_stage_reached"],
            stalled=stalled,
            attempts=attempts,
        )
    finally:
        if owns_producer:
            kafka_producer.flush(10)
        if owns_http_client:
            http_client.close()
