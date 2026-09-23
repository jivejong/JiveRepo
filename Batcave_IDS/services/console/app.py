"""The console backend (Phase 8, docs/06 Track B, docs/08).

Holds one `StageMachine` (`services/simulator/machine.py`) per browser
session and exposes it over a small HTTP API the static SPA (`console/`)
calls. This service **is not a second stage machine** - every gating,
probability, signature, and pathology decision is made by `StageMachine`
itself, identically to the headless autopilot (`services/simulator/
session.py`). What this module adds is exactly the two things a human
player needs that an autopilot doesn't: turning "which technique did the
player click" into the `initial`/`retry`/`pivot` decision label
`StageMachine.attempt` expects, and turning its return values into JSON.

Runs on the host via `make console` (docs/01), not in the compose stack - it
needs the honeypot and Redpanda already up (`make dev-up`), same as
`make attack`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from confluent_kafka import Producer
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.console.batbot import ChatTurnEvent
from services.console.finale import run_finale_pipeline
from services.console.state import (
    ConsoleSession,
    SessionRegistry,
    lock_reason,
    stage_catalog,
    villain_stage_reach,
)
from services.simulator.catalog import Technique, Villain, load_villains
from services.simulator.machine import KAFKA_BOOTSTRAP_SERVERS, MAX_ATTEMPTS


def _default_kafka_producer() -> Producer:
    return Producer(
        {"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS, "acks": "all", "enable.idempotence": False}
    )


def _default_llm_client():
    """`None` (zero-credential fallback, docs/08) unless `GEMINI_API_KEY` is
    set - the same conditional-client pattern `services/triage/__main__.py`'s
    `run_triage` already uses, and the same env var (docs/08: "same Gemini
    model as triage"), not a separate one."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    from google import genai

    return genai.Client(api_key=key)


# Testing seam, mirroring services/honeypot/app.py's own HONEYPOT_DISABLE_KAFKA
# / _NullProducer pattern: tests/test_console_api.py monkeypatches these
# before constructing a TestClient (which runs `lifespan` on entry), so the
# API is exercised without a live broker, honeypot, or Gemini key. Production
# leaves all four at their defaults.
kafka_producer_factory: Callable[[], object] = _default_kafka_producer
http_client_factory: Callable[[], httpx.Client] | None = None
llm_client_factory: Callable[[], object | None] = _default_llm_client
# The finale pipeline (services/console/finale.py) opens the REAL
# data/warehouse.duckdb and spawns a REAL `dbt build` subprocess - neither
# of which any of the three seams above touches, so a test that finishes a
# session would otherwise start a genuine background pipeline against
# production data on every run. This seam is what tests/test_console_api.py
# overrides to a no-op, the same way the other three exist so tests never
# reach a real broker, honeypot, or Gemini key.
finale_runner: Callable[..., None] = run_finale_pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One producer for the process lifetime, shared across every console
    # session - matches the honeypot's own one-producer-per-process pattern
    # (services/honeypot/app.py) rather than opening a new librdkafka client
    # per player.
    app.state.producer = kafka_producer_factory()
    app.state.registry = SessionRegistry(
        app.state.producer, http_client_factory=http_client_factory, finale_runner=finale_runner
    )
    # One Gemini client for the process lifetime, shared across every bat
    # bot conversation - genai.Client is stateless per call, so there is no
    # reason to build a fresh one per session the way each session gets its
    # own httpx.Client (that one carries per-player cookie state; this one
    # doesn't carry anything session-specific).
    app.state.llm_client = llm_client_factory()
    yield
    app.state.registry.close_all()
    app.state.producer.flush(10)


app = FastAPI(lifespan=lifespan)
# The SPA (console/) is served separately (a plain static file server, or
# opened directly from disk) - no framework, no build step, per docs/08. CORS
# is wide open here on purpose: this is a local single-player demo backend,
# never deployed, with no credential or session data worth protecting from a
# browser origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _technique_dict(t: Technique) -> dict:
    return {
        "technique_id": t.technique_id,
        "attack_id": t.attack_id,
        "display_name": t.display_name,
        "attack_name": t.attack_name,
        "observability": t.observability,
        "detection_signature": t.detection_signature,
    }


def _villain_dict(v: Villain) -> dict:
    return {
        "slug": v.slug,
        "name": v.name,
        "archetype": v.archetype,
        "stats": {
            "intelligence": v.intelligence,
            "strength": v.strength,
            "speed": v.speed,
            "durability": v.durability,
            "power": v.power,
            "combat": v.combat,
        },
        "stage_reach": villain_stage_reach(v),
    }


def _stage_payload(session: ConsoleSession) -> dict:
    """The current stage's menu: available (gated) techniques from what
    `enter_stage` actually returned, plus every other technique defined for
    the stage shown locked with the specific stat gate it fails - docs/08:
    "Locked techniques shown greyed with the stat requirement visible.\""""
    machine = session.machine
    available_ids = {t.technique_id for t in session.current_candidates}
    locked = [
        {**_technique_dict(t), **lock_reason(machine.villain, t)}
        for t in stage_catalog(machine.current_stage_num)
        if t.technique_id not in available_ids
    ]
    return {
        "stage_num": machine.current_stage_num,
        "stage_name": machine.current_stage.name if machine.current_stage else None,
        "available": [_technique_dict(t) for t in session.current_candidates],
        "locked": locked,
    }


def _counters(session: ConsoleSession) -> dict:
    machine = session.machine
    detected_tally = sum(1 for a in machine.attempts if a.outcome == "detected")
    elapsed_s = (datetime.now(UTC) - machine.started_at).total_seconds()
    return {
        "attempts": len(machine.attempts),
        "requests_sent": machine.requests_sent,
        "noise_generated": sum(a.noise_generated for a in machine.attempts),
        "elapsed_s": round(elapsed_s, 1),
        "failures_so_far": machine.failures_so_far,
        "failure_tolerance": machine.profile.failure_tolerance,
        "remaining_failure_tolerance": max(
            0, machine.profile.failure_tolerance - machine.failures_so_far
        ),
        # `detected` is neither success nor failure in the stage machine - it
        # doesn't clear the stage and doesn't count against failure_tolerance,
        # so without a counter a run stuck on repeated detections would look
        # identical to a UI that stopped responding (docs/08's "Stage
        # terminal" section). Shown alongside the MAX_ATTEMPTS safety cap so
        # a stuck run reads as "getting caught," not as broken.
        "detected_tally": detected_tally,
        "attempt_seq": machine.attempt_seq,
        "max_attempts": MAX_ATTEMPTS,
    }


def _chat_event_dict(event: ChatTurnEvent) -> dict:
    return {
        "turn_number": event.turn_number,
        "speaker": event.speaker,
        "objective": event.objective,
        "bot_text": event.bot_text,
        "user_text": event.user_text,
        "extracted_intent_flags": event.extracted_intent_flags,
        "refused": event.refused,
    }


def _session_payload(session: ConsoleSession) -> dict:
    return {
        "console_session_id": session.console_session_id,
        "villain_slug": session.machine.villain_slug,
        "session_id": session.machine.session_id,
        "max_stage_reached": session.machine.max_stage_reached,
        "stalled": session.machine.stalled,
        "finished": session.finished,
        "run_outcome": session.run_outcome,
        "counters": _counters(session),
        # deploy_batbot succeeding sets this instead of finishing the run
        # immediately (services/console/state.py) - the stage-4 menu is
        # stale once it's set (no further stage exists to show), so `stage`
        # goes null here too, same as once the run is actually finished.
        "batbot_pending": session.batbot_pending,
        "stage": None if (session.finished or session.batbot_pending) else _stage_payload(session),
    }


@app.get("/api/villains")
def list_villains() -> list[dict]:
    return [_villain_dict(v) for v in sorted(load_villains().values(), key=lambda v: v.slug)]


class CreateSessionRequest(BaseModel):
    villain_slug: str


@app.post("/api/session")
def create_session(req: CreateSessionRequest) -> dict:
    if req.villain_slug not in load_villains():
        raise HTTPException(404, f"unknown villain slug: {req.villain_slug}")
    session = app.state.registry.create(req.villain_slug)
    candidates = session.machine.enter_stage(1)
    if candidates is None:
        # No villain in the roster has an empty stage-1 catalog today (docs/03
        # confirms Killer Croc's wall is stage 3), but a stage machine with a
        # roster edit that produced one should fail the run cleanly rather
        # than crash the request.
        session.finished = True
        session.run_outcome = "stalled"
        session.machine.finish()
    else:
        session.current_candidates = candidates
    return _session_payload(session)


def _get_session(console_session_id: str) -> ConsoleSession:
    try:
        return app.state.registry.get(console_session_id)
    except KeyError:
        raise HTTPException(404, "unknown console session") from None


@app.get("/api/session/{console_session_id}/state")
def get_state(console_session_id: str) -> dict:
    return _session_payload(_get_session(console_session_id))


class AttemptRequest(BaseModel):
    technique_id: str
    parameters: dict = {}


def _decide_decision(session: ConsoleSession, technique: Technique) -> str:
    """Which of `initial`/`retry`/`pivot` this attempt is, computed from state
    the machine already tracks rather than trusted from the client - the same
    three labels the autopilot always produced, just chosen by the player's
    click instead of `_decide_next_action`."""
    machine = session.machine
    if not machine.tried_in_current_stage:
        return "initial"
    if (
        machine.last_technique is not None
        and technique.technique_id == machine.last_technique.technique_id
    ):
        return "retry"
    return "pivot"


@app.post("/api/session/{console_session_id}/attempt")
def attempt(console_session_id: str, req: AttemptRequest) -> dict:
    session = _get_session(console_session_id)
    if session.finished:
        raise HTTPException(409, "this run has already ended")

    by_id = {t.technique_id: t for t in session.current_candidates}
    technique = by_id.get(req.technique_id)
    if technique is None:
        raise HTTPException(400, f"'{req.technique_id}' is not available in this stage")

    tried = session.machine.tried_in_current_stage
    last = session.machine.last_technique
    is_retry_of_current = last is not None and req.technique_id == last.technique_id
    if req.technique_id in tried and not is_retry_of_current:
        raise HTTPException(
            400,
            f"'{req.technique_id}' was already tried and abandoned this stage - "
            "retry the current technique or pivot to one not yet tried",
        )

    decision = _decide_decision(session, technique)
    event = session.machine.attempt(technique, decision, req.parameters)

    stage_cleared = event.outcome == "success"
    run_finished = False
    if stage_cleared:
        if session.machine.current_stage_num >= 4:
            if technique.technique_id == "deploy_batbot":
                # Delivers the bat bot (docs/08) instead of finishing the run
                # outright - completion is gated on the conversation reaching
                # reveal (services/console/app.py's /batbot/reply), not on
                # this stage-4 success by itself. Every OTHER stage-4
                # technique still finishes the run immediately, unchanged.
                session.batbot_pending = True
            else:
                run_finished = True
                session.run_outcome = "cleared"
        else:
            next_candidates = session.machine.enter_stage(session.machine.current_stage_num + 1)
            if next_candidates is None:
                run_finished = True
                session.run_outcome = "stalled"
            else:
                session.current_candidates = next_candidates
    elif session.machine.stalled:
        # Either MAX_ATTEMPTS (checked inside StageMachine.attempt itself) or
        # decide_next_action below says no viable path remains.
        run_finished = True
        session.run_outcome = "stalled"
    else:
        # "Choice inside the envelope" (Phase 8 plan): the player picks retry
        # vs pivot freely, but failure_tolerance/no-untried-alternative still
        # ends the run exactly like it would headlessly. decide_next_action is
        # a pure function of already-recorded state - calling it here to
        # check viability doesn't consume an rng draw the player's next
        # choice needs.
        action, _ = session.machine.decide_next_action(session.current_candidates)
        if action == "stall":
            session.machine.stalled = True
            run_finished = True
            session.run_outcome = "stalled"

    if run_finished:
        session.finished = True
        session.machine.finish()
        app.state.registry.start_finale(session, app.state.llm_client)

    return {
        "event": {
            "stage": event.stage,
            "technique_id": event.technique_id,
            "attack_id": event.attack_id,
            "decision": event.decision,
            "attempt_seq": event.attempt_seq,
            "technique_attempt_seq": event.technique_attempt_seq,
            "computed_probability": event.computed_probability,
            "roll": event.roll,
            "outcome": event.outcome,
            "noise_generated": event.noise_generated,
        },
        "stage_cleared": stage_cleared,
        "session": _session_payload(session),
    }


@app.post("/api/session/{console_session_id}/batbot/start")
def batbot_start(console_session_id: str) -> dict:
    """Called once the player acknowledges the consent notice (docs/08 -
    the notice itself is a frontend-only gate, same as Phase 8's precedent
    for "nothing in the schema supports a consent event, so none is
    emitted"; reaching this endpoint at all is what "acknowledged" means).
    Emits the bat bot's turn-1 opening line."""
    session = _get_session(console_session_id)
    if not session.batbot_pending or session.batbot is not None:
        raise HTTPException(409, "bat bot conversation is not available to start right now")
    convo = app.state.registry.create_batbot(session, app.state.llm_client)
    bot_event = convo.start()
    return {"event": _chat_event_dict(bot_event), "session": _session_payload(session)}


class BatBotReplyRequest(BaseModel):
    user_text: str


@app.post("/api/session/{console_session_id}/batbot/reply")
def batbot_reply(console_session_id: str, req: BatBotReplyRequest) -> dict:
    """The player's reply to the bat bot's most recent turn. Always returns
    both the user's row and the next bot row (docs/08's turn contract -
    `BatBotConversation.reply` never returns without the next turn, including
    the reveal itself). Once the bot's turn is the reveal, the console
    session finishes here - the bat bot conversation is what was gating
    completion (services/console/state.py), not the stage-4 success that
    started it."""
    session = _get_session(console_session_id)
    if session.batbot is None:
        raise HTTPException(409, "bat bot conversation has not started")

    user_event, bot_event = session.batbot.reply(req.user_text)

    if session.batbot.finished:
        session.batbot_pending = False
        session.finished = True
        session.run_outcome = "cleared"
        session.machine.finish()
        app.state.registry.start_finale(session, app.state.llm_client)

    return {
        "user_event": _chat_event_dict(user_event),
        "bot_event": _chat_event_dict(bot_event),
        "session": _session_payload(session),
    }


@app.post("/api/session/{console_session_id}/finish")
def finish(console_session_id: str) -> dict:
    """Explicit early end (the player quits mid-run, including mid-bat-bot-
    conversation - there's no other way to bail out of it once started).
    Idempotent: a session already finished by /attempt or /batbot/reply
    reaching a terminal state just returns its existing payload rather than
    publishing a second attack_run."""
    session = _get_session(console_session_id)
    if not session.finished:
        session.run_outcome = "cleared" if session.machine.max_stage_reached >= 4 else "stalled"
        session.finished = True
        session.batbot_pending = False
        session.machine.finish()
        app.state.registry.start_finale(session, app.state.llm_client)
    return _session_payload(session)


@app.get("/api/session/{console_session_id}/finale/status")
def finale_status(console_session_id: str) -> dict:
    """Polled by the frontend once a run finishes (docs/08's "Getting from
    a finished run to a prediction"). `terminal=True` on `ready` or
    `failed` is what tells the poll loop to stop - never inferred from a
    client-side timeout, since the pipeline enforces its own bounds
    (services/console/finale.py) and reports failure explicitly rather
    than leaving the frontend to guess "not ready yet" from "will never be
    ready\""""
    session = _get_session(console_session_id)
    result = session.finale_result
    if result is None:
        return {
            "state": "pending",
            "reason": None,
            "terminal": False,
            "prediction": None,
            "lines": [],
        }
    prediction = None
    if result.prediction is not None:
        prediction = {
            "source": result.prediction.source,
            "suspected_villain": result.prediction.suspected_villain,
            "confidence": result.prediction.confidence,
            "identified_attack_ids": result.prediction.identified_attack_ids,
        }
    return {
        "state": result.state,
        "reason": result.reason,
        "terminal": result.terminal,
        "prediction": prediction,
        "lines": result.lines,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
