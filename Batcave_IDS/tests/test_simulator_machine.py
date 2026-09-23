"""Characterization test for the Phase 8 StageMachine extraction
(docs/06 Track B, Phase 8 plan).

`run_scripted_session` is being split into a resumable `StageMachine` so the
console can pause at exactly the two points a human makes a decision (which
technique opens a stage, retry-vs-pivot) instead of reimplementing the stage
machine in the console backend. That extraction must be **pure code motion**:
every seeded number in the repository (the separability baseline, the
calibration numbers, the corpus itself) depends on the exact order `rng` is
called in.

This file pins that order *before* the refactor touches anything. It mirrors
`services/simulator/__main__.py`'s real usage exactly: one `Random` instance
shared between the session and its `PathologyInjector` (`_run_one` passes the
same `rng` to both), because that sharing means the injector's per-request
`decide()` calls interleave with the session's own draws and are therefore
part of the draw order that must survive the refactor unchanged.

The captured sequence is hashed rather than stored verbatim (12 villains x 5
seeds x every attempt field would be an unreadable fixture) — the hash is
recomputed against this same file after the extraction lands, and any change
at all is exactly the signal this test exists to catch. If it ever needs to
change *legitimately* (a real behavior change, not a refactor), regenerate it
deliberately and say so in the commit, never as a side effect of touching
unrelated code.
"""

from __future__ import annotations

import hashlib
import json
import random

import httpx

from services.honeypot.session import COOKIE_NAME
from services.simulator.catalog import load_villains
from services.simulator.pathologies import PathologyConfig, PathologyInjector
from services.simulator.session import run_scripted_session

# All twelve so every gating branch and every villain's signature/behavior
# path is exercised, not just a convenient subset.
_VILLAIN_SLUGS = sorted(load_villains())
_SEEDS = (0, 1, 2, 3, 4)

# Pinned once against the pre-refactor code (services/simulator/session.py as
# it stood at the end of Track A). Recompute deliberately, never silently.
_EXPECTED_DIGEST = "9b982989593ab9199986eaec0ac06adc90c4af6f3fad9b2fcc93b79efa48c632"


class _FakeProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, bytes]] = []

    def produce(self, topic, key, value, callback=None) -> None:
        self.messages.append((topic, key, value))

    def poll(self, timeout=0) -> int:
        return 0

    def flush(self, timeout=0) -> int:
        return 0


def _fake_http_client(session_id: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {}
        if f"{COOKIE_NAME}={session_id}" not in request.headers.get("cookie", ""):
            headers["Set-Cookie"] = f"{COOKIE_NAME}={session_id}; Path=/"
        return httpx.Response(200, headers=headers, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testhoneypot")


def _attempt_fingerprint(attempt) -> dict:
    """Every RNG-determined field on an attempt event. Deliberately excludes
    event_id (uuid4, not seeded from `rng`) and the two real timestamps
    (wall-clock, not seeded) — everything else here traces back to a `rng`
    draw, directly or through compute_probability/resolve_attempt."""
    return {
        "stage": attempt.stage,
        "technique_id": attempt.technique_id,
        "attempt_seq": attempt.attempt_seq,
        "technique_attempt_seq": attempt.technique_attempt_seq,
        "decision": attempt.decision,
        "computed_probability": attempt.computed_probability,
        "roll": attempt.roll,
        "outcome": attempt.outcome,
        "noise_generated": attempt.noise_generated,
    }


def _run_one(slug: str, seed: int) -> dict:
    rng = random.Random(seed)
    injector = PathologyInjector(PathologyConfig.load(), rng)
    result = run_scripted_session(
        slug,
        rng=rng,
        kafka_producer=_FakeProducer(),
        http_client=_fake_http_client(f"char-{slug}-{seed}"),
        sleep_fn=lambda _: None,
        injector=injector,
        timing_compression_factor=0.02,
    )
    return {
        "villain_slug": result.villain_slug,
        "max_stage_reached": result.max_stage_reached,
        "stalled": result.stalled,
        "requests_sent": result.requests_sent,
        "attempts": [_attempt_fingerprint(a) for a in result.attempts],
    }


def test_seeded_attempt_sequence_is_pinned_across_the_stage_machine_extraction():
    """Runs every villain at five fixed seeds, with the injector sharing the
    session's own `rng` exactly as `make attack` does, and hashes the full
    RNG-determined attempt sequence. A changed hash after the StageMachine
    extraction means the code motion was not pure — some `rng` call moved
    relative to another — and the corpus this project has already measured
    (separability, calibration, pathology counts) would silently no longer
    match what the refactored code produces."""
    records = [_run_one(slug, seed) for slug in _VILLAIN_SLUGS for seed in _SEEDS]
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    assert digest == _EXPECTED_DIGEST, (
        "the seeded attempt sequence changed - if this is the Phase 8 "
        "StageMachine extraction, the code motion was not pure (some rng "
        "call moved relative to another) and must be fixed, not this "
        "fixture. If it is a deliberate behavior change, regenerate "
        "_EXPECTED_DIGEST and say so explicitly in the commit."
    )
