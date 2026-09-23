"""The resumable stage machine (Phase 8, docs/06 Track B, docs/08).

Extracted from `services/simulator/session.py`'s Phase 2/3 `run_scripted_session`,
which made both interactive decisions - which technique opens a stage, and
retry-vs-pivot on failure - itself, inline in one blocking loop. A console has
to pause at exactly those two points to let a human decide instead. Writing a
second driver in the console backend would be a reimplementation of the stage
machine, which CLAUDE.md's Track B ground rules forbid: "the console is an
input device for the existing headless stage machine, not a reimplementation
of it."

So this class *is* the stage machine, unchanged from what Track A built and
verified. `session.py`'s `run_scripted_session` becomes a thin loop over it
that takes its choices from `StageMachine.autopilot_choice` /
`StageMachine.decide_next_action` (the same policy `_decide_next_action`
always was); the console backend (Phase 8) calls the same methods and takes
its choices from the player instead. One implementation, two drivers.

**This extraction must be pure code motion.** Every seeded number this project
has already measured and recorded - the separability effect sizes, the
detection-probability calibration, the pathology counts, the whole corpus -
depends on the exact order `rng` is called in:
`RunIdentity.__init__` -> per-run pathology draws (burst window, schema drift)
-> `rng.shuffle(candidates)` per stage -> per attempt `resolve_attempt`
(detection roll, then success roll) -> `identity.headers()` (only draws under
rotation) -> signature `transform()` (some signatures draw, most don't) ->
per-request-spec `injector.decide()` -> `_inter_request_delay`. Nothing here
reorders any of that relative to the pre-extraction code; the class boundary
only changes what can pause between calls.
`tests/test_simulator_machine.py` pins the full seeded attempt sequence
(hashed) from before this file existed, and must still match after it.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Literal

import httpx
from confluent_kafka import Producer

from services.common.envelope import EventEnvelope
from services.honeypot.session import COOKIE_NAME
from services.simulator.behavior import BehaviorProfile
from services.simulator.catalog import (
    Stage,
    Technique,
    Villain,
    gated_techniques,
    load_stages,
    load_villains,
)
from services.simulator.identity import RunIdentity
from services.simulator.pathologies import PathologyInjector
from services.simulator.probability import compute_probability, resolve_attempt
from services.simulator.signatures import Signature, signature_for
from services.simulator.traffic import request_specs_for, send_requests

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "attack.events")
HONEYPOT_BASE_URL = os.environ.get("HONEYPOT_BASE_URL", "http://localhost:8000")

MIN_PROBABILITY_TO_RETRY = 0.05
# Safety cap so a persistent grinder (or, Phase 8 on, a human clicking through
# a stuck run) can't loop unbounded; the real driver of attempt count is
# failure_tolerance (durability), the real driver of duration is the paced
# inter-request interval (speed).
MAX_ATTEMPTS = 400

# Origin of a session's ground truth - carried on `attack_run` (docs/02's
# `timing_compression_factor` is the direct precedent: "so the corpus is
# self-describing"). Added Phase 8 because nothing else distinguishes a
# console session from a headless one at the data layer - console pacing
# comes from the player, not BehaviorProfile, so console runs are not
# corpus-grade behavioral data and downstream consumers that need faithful
# villain behavior (the separability harness, behavioral baselines) must be
# able to filter them out. Deliberately NOT on the observed side: Phase 10's
# counterstrike attributes the villain from the triage model's prediction on
# a console session, so console sessions must stay indistinguishable from
# headless ones in int_session_features_observed.
SessionSource = Literal["headless", "console"]


def _delivery_report(err, msg) -> None:
    if err is not None:
        # Nothing silently dropped (CLAUDE.md working principle), matching
        # the honeypot's own producer callback.
        print(f"simulator: delivery failed for session {msg.key()!r}: {err}")


class AttemptEvent(EventEnvelope):
    """`event_kind = 'attempt'` - docs/07's attempt event fields."""

    event_kind: Literal["attempt"] = "attempt"
    attempt_id: str
    stage: int
    technique_id: str
    attack_id: str
    attempt_seq: int
    technique_attempt_seq: int
    decision: Literal["initial", "retry", "pivot"]
    # Phase 2: always empty. Phase 8: the console writes the player's choices
    # here as descriptive metadata - record-only, no effect on
    # computed_probability or the traffic a technique generates. (docs/07
    # previously said "Phase 3 populates it"; Phase 3 did not, and Phase 8
    # corrects that line rather than inventing the semantics retroactively.)
    parameters: dict = {}
    computed_probability: float
    roll: float
    outcome: Literal["success", "failure", "detected"]
    noise_generated: int
    stage_entered_at: datetime
    attempt_at: datetime


class AttackRunEvent(EventEnvelope):
    """`event_kind = 'attack_run'` - one per run, GROUND TRUTH (docs/02).

    Carries `villain_slug` - the ground-truth identity that request and attempt
    events must never contain - plus run metadata, `timing_compression_factor`
    (Phase 3) and `session_source` (Phase 8, see the module docstring) so the
    corpus is self-describing on both timing fidelity and origin."""

    event_kind: Literal["attack_run"] = "attack_run"
    villain_slug: str
    started_at: datetime
    ended_at: datetime
    duration_s: float
    requests_sent: int
    attempts_made: int
    max_stage_reached: int
    run_outcome: Literal["cleared", "stalled"]
    pathologies_enabled: list[str]
    timing_compression_factor: float
    session_source: SessionSource = "headless"


def _decide_next_action(
    current: Technique,
    candidates: list[Technique],
    tried: dict[str, int],
    villain: Villain,
    stage: Stage,
    profile: BehaviorProfile,
    failures_so_far: int,
) -> tuple[Literal["retry", "pivot", "stall"], Technique | None]:
    """Layer 1 retry-vs-pivot, durability-driven (docs/03, docs/06). Pure
    function of state - no `rng` draw of its own, so it's safe for a console
    to call for display (e.g. "here's what the villain would do") without
    consuming a draw the player's own choice will also need.

    - A villain gives up once it has burned through `failure_tolerance`
      failures - durability 14 (Riddler, Two-Face) stops after one, durability
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


def _inter_request_delay(profile: BehaviorProfile, burstiness: float, rng: Random) -> float:
    """Seconds to wait before the next request. Base interval from speed
    (requests_per_min); variance from intelligence (jitter) plus a villain's
    signature burstiness. This is what drives inter_request_stddev_ms - a
    high-INT or bursty villain spaces irregularly, a low-INT enumerator is
    metronomic. Harley's burstiness is what separates her from Joker, who has
    the same high intelligence but steadier spacing."""
    base = 60.0 / max(profile.requests_per_min, 1.0)
    variance = min(1.0, profile.jitter + burstiness)
    # variance 0 -> exact base; 1 -> up to +/-90% swing.
    swing = 1.0 + variance * (rng.random() * 2.0 - 1.0) * 0.9
    return max(0.0, base * swing)


class StageMachine:
    """One villain's run through the four-stage kill chain, resumable at the
    two points a driver (autopilot or a human) chooses something.

    Construction performs every setup step `run_scripted_session` used to do
    before its own `try:` block - villain/stage lookup, owned-resource
    bookkeeping, the behavior profile, signature, and per-run identity - in
    the same order, so a constructor failure (e.g. an unknown villain slug)
    leaves cleanup unrun exactly as it did before, matching the original's own
    control flow rather than "fixing" it as a side effect of this phase.
    """

    def __init__(
        self,
        villain_slug: str,
        rng: Random,
        kafka_producer=None,
        http_client: httpx.Client | None = None,
        sleep_fn=lambda s: None,
        injector: PathologyInjector | None = None,
        timing_compression_factor: float = 1.0,
        session_source: SessionSource = "headless",
    ) -> None:
        self.villain_slug = villain_slug
        self.rng = rng
        self.run_id = str(uuid.uuid4())
        self.started_at = datetime.now(UTC)
        self.villain = load_villains()[villain_slug]
        self.stages = load_stages()

        self.owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(base_url=HONEYPOT_BASE_URL, timeout=10.0)
        self.owns_producer = kafka_producer is None
        self.kafka_producer = kafka_producer or Producer(
            {
                "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                "acks": "all",
                "enable.idempotence": False,
            }
        )

        self.sleep_fn = sleep_fn
        self.injector = injector
        self.timing_compression_factor = timing_compression_factor
        self.session_source: SessionSource = session_source

        self.profile = BehaviorProfile.from_villain(self.villain)
        self.signature: Signature = signature_for(villain_slug)
        self.identity = RunIdentity(
            rng,
            rotate_ip=self.signature.rotate_ip,
            rotate_user_agent=self.profile.rotates_user_agent,
        )

        self.session_id: str | None = None
        self.attempts: list[AttemptEvent] = []
        self.attempt_seq = 0
        self.max_stage_reached = 0
        self.requests_sent = 0
        self.failures_so_far = 0
        self.stalled = False

        # Request budget and the per-run pathology draws (burst_window,
        # schema_drift_at) are NOT computed here - see `start()`. The
        # original computed them *after* the warm-up call, inside its
        # `try:` block; RunIdentity.headers() draws its own rng call during
        # that warm-up for any UA-rotating villain (intelligence >= 80), so
        # doing this arithmetic here instead would draw the burst/schema-
        # drift randints *before* the warm-up's headers() draw and silently
        # swap their relative order for 6 of the 12 villains. Caught by
        # tests/test_simulator_machine.py's pinned digest, not by review.
        self.request_budget: int = 0
        self.burst_window: range = range(0)
        self.schema_drift_at: int | None = None

        self._tried: dict[str, int] = {}
        self._current_stage_num: int | None = None
        self._current_stage: Stage | None = None
        self._stage_entered_at: datetime | None = None

        self.last_technique: Technique | None = None
        self.last_stage: Stage | None = None
        self.last_stage_num: int | None = None

    def start(self) -> None:
        """Warm-up: let the honeypot mint a session and set batcave_sid in
        this run's cookie jar, then read it back as this run's session_id -
        the same warm-up `run_scripted_session` always performed before
        anything else inside its `try:` block. The request budget and the
        per-run pathology draws (burst window, schema drift) are computed
        immediately after, in the same relative position as the original -
        after the warm-up's own `identity.headers()` draw, before any
        stage is entered."""
        self.http_client.get(
            "/", headers={"X-Attempt-Id": "session-bootstrap", **self.identity.headers()}
        )
        self.session_id = self.http_client.cookies.get(COOKIE_NAME)
        if self.session_id is None:
            raise RuntimeError("honeypot did not set the batcave_sid session cookie")

        # Request budget: speed x durability (docs/03's volume model). Only
        # consumed by the autopilot's post-stage grind continuation
        # (session.py) - a console session is human-paced, so this is a
        # ceiling the autopilot fills toward, not something the console UI
        # enforces on the player.
        self.request_budget = int(
            self.profile.requests_per_min * self.profile.session_duration_s / 60
        )

        # Per-run pathology decisions (docs/03): burst is a 10x rate spike
        # over a contiguous window; schema drift arms partway through the run
        # and then stays on. Both fire once per run when enabled.
        if self.injector and "burst" in self.injector.config.per_run:
            start = self.rng.randint(1, max(1, self.request_budget - 10))
            self.burst_window = range(start, start + 10)
        self.schema_drift_at = (
            self.rng.randint(1, max(1, self.request_budget))
            if self.injector and "schema_drift" in self.injector.config.per_run
            else None
        )

    def enter_stage(self, stage_num: int) -> list[Technique] | None:
        """Gate + shuffle the stage's candidates (docs/06 Phase 6 fix: the
        shuffle is what makes "first" and "first untried" random per session
        rather than always preferring techniques.csv's early rows). Returns
        `None` if the villain has no viable technique at this stage at all -
        the immediate-stall case - and sets `self.stalled`.

        The shuffled order is preserved exactly as before: `autopilot_choice`
        still means "first after the shuffle," so a console showing the same
        candidates in a different arrangement (or all of them, since a human
        picks explicitly) doesn't change what the autopilot draws."""
        self._current_stage_num = stage_num
        self._current_stage = self.stages[stage_num]
        self._stage_entered_at = datetime.now(UTC)
        candidates = gated_techniques(self.villain, stage_num)
        if not candidates:
            self.stalled = True
            return None
        candidates = list(candidates)
        self.rng.shuffle(candidates)
        self._tried = {}
        return candidates

    @staticmethod
    def autopilot_choice(candidates: list[Technique]) -> Technique:
        """The Phase 2/3 policy for which technique opens a stage: the first
        of the (already shuffled) gated candidates."""
        return candidates[0]

    @property
    def tried_in_current_stage(self) -> dict[str, int]:
        """Technique id -> attempt count already recorded for the *active*
        stage. Read-only view for a driver that needs to validate a choice
        against what this machine is tracking (the console's retry-vs-pivot
        vocabulary check) without reaching into a private attribute."""
        return dict(self._tried)

    @property
    def current_stage_num(self) -> int | None:
        """The stage number `enter_stage` most recently set, or `None`
        before any stage has been entered. Public so a driver (the console)
        can render "which stage is this" without reaching into a private
        attribute."""
        return self._current_stage_num

    @property
    def current_stage(self) -> Stage | None:
        return self._current_stage

    def decide_next_action(
        self, candidates: list[Technique]
    ) -> tuple[Literal["retry", "pivot", "stall"], Technique | None]:
        """Retry-vs-pivot for the technique just attempted (`self.last_technique`).
        A pure function of already-recorded state - no `rng` draw - so a
        console can call it to show "here's what the villain would do" without
        that call itself consuming a draw the player's actual choice needs."""
        return _decide_next_action(
            self.last_technique,
            candidates,
            self._tried,
            self.villain,
            self._current_stage,
            self.profile,
            self.failures_so_far,
        )

    def _emit(
        self,
        technique: Technique,
        stage_num: int,
        stage: Stage,
        decision: Literal["initial", "retry", "pivot"],
        technique_attempt_seq: int,
        parameters: dict,
    ) -> AttemptEvent:
        """Build and publish one attempt event (and its driven HTTP traffic,
        if the technique produces any), exactly as the original `emit`
        closure did: resolve the attempt (the detection roll, then the
        success roll - `resolve_attempt`'s own fixed order), shape and send
        the traffic under the villain's signature and any armed pathologies,
        publish to Kafka, then pace the next call. Returns the built event;
        the caller decides what the outcome means (this method doesn't touch
        `max_stage_reached`, `failures_so_far`, or `self.stalled` - see
        `attempt()` and `grind_attempt()`, which apply those two different
        ways, matching the original's inner-while-loop-vs-grind-loop split)."""
        self.attempt_seq += 1
        attempt_at = datetime.now(UTC)
        attempt_id = str(uuid.uuid4())
        resolution = resolve_attempt(
            technique,
            self.villain,
            stage,
            technique_attempt_seq,
            evasion=self.profile.evasion,
            rng=self.rng,
        )
        if technique.produces_traffic:
            if self.schema_drift_at is not None and self.attempt_seq >= self.schema_drift_at:
                self.injector.arm_schema_drift()
            growth = 1.0 + self.profile.body_growth * (self.attempt_seq / 20.0)
            size = int(self.profile.mean_body_bytes * self.profile.body_repetition * growth)
            body = (b"x" * size) if size > 0 else None
            base_headers = {"X-Run-Id": self.run_id, **self.identity.headers()}
            if self.signature.response_delay_ms > 0:
                # Mister Freeze holds connections open - the honeypot honors
                # X-Sim-Delay-Ms as a real server-side response delay,
                # lifting response_time_ms and duration.
                base_headers["X-Sim-Delay-Ms"] = str(self.signature.response_delay_ms)
            specs = self.signature.transform(request_specs_for(technique.technique_id), self.rng)
            # Per-request pathology injection (Phase 3 Part 2). Decided per
            # request so rates match the spec; corrupts the observed stream
            # only. Simulator-direct pathologies change what we send
            # (malformed body, client_ts); honeypot-executed ones ride the
            # X-Sim-Pathology header.
            for spec in specs:
                req_headers = dict(base_headers)
                req_body = body
                if self.injector is not None:
                    p = self.injector.decide()
                    if p.header_value():
                        req_headers["X-Sim-Pathology"] = p.header_value()
                    if p.malformed_body:
                        req_body = b'{"truncated": '  # unbalanced JSON
                    if p.client_ts_offset_s is not None:
                        skewed = attempt_at + timedelta(seconds=p.client_ts_offset_s)
                        req_headers["X-Client-Ts"] = skewed.isoformat()
                send_requests(
                    self.http_client, [spec], attempt_id, extra_headers=req_headers, body=req_body
                )
                self.requests_sent += 1
        event = AttemptEvent(
            session_id=self.session_id,
            run_id=self.run_id,
            received_at=attempt_at,
            attempt_id=attempt_id,
            stage=stage_num,
            technique_id=technique.technique_id,
            attack_id=technique.attack_id,
            attempt_seq=self.attempt_seq,
            technique_attempt_seq=technique_attempt_seq,
            decision=decision,
            parameters=parameters,
            computed_probability=resolution.computed_probability,
            roll=resolution.roll,
            outcome=resolution.outcome,
            noise_generated=resolution.noise_generated,
            stage_entered_at=self._stage_entered_at,
            attempt_at=attempt_at,
        )
        self.attempts.append(event)
        self.kafka_producer.produce(
            KAFKA_TOPIC,
            key=self.session_id.encode("utf-8"),
            value=event.model_dump_json().encode("utf-8"),
            callback=_delivery_report,
        )
        self.kafka_producer.poll(0)
        # Burst pathology: inside the window, requests fire back to back (no
        # idle gap) - a 10x rate spike, distinguishable in received_at even
        # under a compressed clock (the normal gap isn't zero).
        if self.attempt_seq in self.burst_window:
            self.sleep_fn(0.0)
        else:
            self.sleep_fn(_inter_request_delay(self.profile, self.signature.burstiness, self.rng))
        return event

    def attempt(
        self,
        technique: Technique,
        decision: Literal["initial", "retry", "pivot"],
        parameters: dict | None = None,
    ) -> AttemptEvent:
        """Emit one attempt inside the *active* stage loop - used by both
        drivers for every attempt except the autopilot's post-stage grind -
        and apply the outcome bookkeeping the original inner while-loop did:
        `max_stage_reached` on success; otherwise `failures_so_far` and the
        `MAX_ATTEMPTS` stall check. `grind_attempt` deliberately does not
        apply any of this, matching the original's grind loop, which called
        `emit(...)` directly and discarded its return value entirely."""
        technique_attempt_seq = self._tried.get(technique.technique_id, 0) + 1
        self.last_technique = technique
        self.last_stage = self._current_stage
        self.last_stage_num = self._current_stage_num
        event = self._emit(
            technique,
            self._current_stage_num,
            self._current_stage,
            decision,
            technique_attempt_seq,
            parameters or {},
        )
        self._tried[technique.technique_id] = technique_attempt_seq

        if event.outcome == "success":
            self.max_stage_reached = self._current_stage_num
        else:
            if event.outcome == "failure":
                self.failures_so_far += 1
            if self.attempt_seq >= MAX_ATTEMPTS:
                self.stalled = True
        return event

    def grind_attempt(self) -> AttemptEvent:
        """The autopilot-only post-stage continuation (docs/03: Croc grinds
        his stall point, Mister Freeze holds at the objective - "everyone
        else fills their budget"). Repeats `last_technique` past stage
        resolution to reach the villain's request budget. Not part of the
        console's vocabulary: docs/08 specs "stage clears on success; stalls
        when no viable technique remains," nothing about continuing to grind
        after either, so the console driver simply never calls this."""
        technique_attempt_seq = self._tried.get(self.last_technique.technique_id, 0) + 1
        event = self._emit(
            self.last_technique,
            self.last_stage_num,
            self.last_stage,
            "retry",
            technique_attempt_seq,
            {},
        )
        self._tried[self.last_technique.technique_id] = technique_attempt_seq
        return event

    def finish(self) -> AttackRunEvent:
        """Build and publish the one `attack_run` event for this session."""
        ended_at = datetime.now(UTC)
        run_event = AttackRunEvent(
            session_id=self.session_id,
            run_id=self.run_id,
            received_at=ended_at,
            villain_slug=self.villain_slug,
            started_at=self.started_at,
            ended_at=ended_at,
            duration_s=(ended_at - self.started_at).total_seconds(),
            requests_sent=self.requests_sent,
            attempts_made=len(self.attempts),
            max_stage_reached=self.max_stage_reached,
            run_outcome="cleared" if self.max_stage_reached >= 4 else "stalled",
            pathologies_enabled=sorted(self.injector.config.enabled) if self.injector else [],
            timing_compression_factor=self.timing_compression_factor,
            session_source=self.session_source,
        )
        self.kafka_producer.produce(
            KAFKA_TOPIC,
            key=self.run_id.encode("utf-8"),
            value=run_event.model_dump_json().encode("utf-8"),
            callback=_delivery_report,
        )
        self.kafka_producer.poll(0)
        return run_event

    def close(self) -> None:
        """Owned-resource cleanup, matching the original's `finally:` block."""
        if self.owns_producer:
            self.kafka_producer.flush(10)
        if self.owns_http_client:
            self.http_client.close()
