"""The honeypot: serves plausible-looking Batcave endpoints with canned
responses and publishes one `request` event per request to `attack.events`.

Logs and responds — never proxies, forwards, or executes anything from a
request body (CLAUDE.md hard constraint).

Responsibility boundary, so it doesn't get rediscovered in a later phase:
this service owns per-request response delay (`X-Sim-Delay-Ms`, feeding
`response_time_ms` — Mister Freeze's signature). It does NOT own inter-request
pacing/jitter (`inter_request_stddev_ms`) — that's the simulator's own
client-side call spacing in Phase 2+ and never touches this service.

Session identity travels by cookie (`batcave_sid`, Phase 3), gap-enforced —
see services/honeypot/session.py. `attack_attempts` and `attack_events` join
on session_id (docs/02); the honeypot mints it and the simulator learns it by
carrying the cookie like any client, so its driven traffic and its attempt
events share a session_id. This replaced the Phase 2 `X-Session-Id` response
header, which couldn't survive the IP/UA rotation Penguin and the high-INT
villains need.

Private simulator control channel (`X-*` request headers) — see the full
table in docs/01-architecture.md. None of this exists in a real honeypot;
it is how the simulator drives this one. The consolidated table is the
source of truth for what each header does and who sets it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from confluent_kafka import Producer
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from services.honeypot.models import RequestEvent
from services.honeypot.routes import match_route
from services.honeypot.session import COOKIE_NAME, SessionTracker

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "attack.events")
SESSION_GAP_SECONDS = float(os.environ.get("SESSION_GAP_SECONDS", "120"))
TRUST_FORWARDED_FOR = os.environ.get("HONEYPOT_TRUST_FORWARDED_FOR", "").lower() in (
    "1",
    "true",
    "yes",
)
# Skips the real Kafka producer entirely — routing/session tests exercise the
# honeypot's own logic and shouldn't require a live broker. The manual
# checkpoint (rpk topic consume) always runs against a real one.
DISABLE_KAFKA = os.environ.get("HONEYPOT_DISABLE_KAFKA", "").lower() in ("1", "true", "yes")


class _NullProducer:
    def produce(self, *_args, **_kwargs) -> None:
        pass

    def poll(self, *_args, **_kwargs) -> int:
        return 0

    def flush(self, *_args, **_kwargs) -> int:
        return 0


def _delivery_report(err, msg) -> None:
    if err is not None:
        # Nothing silently dropped (CLAUDE.md working principle) — a failed
        # produce is logged, not swallowed, even though the honeypot must
        # keep responding either way.
        print(f"honeypot: delivery failed for session {msg.key()!r}: {err}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if DISABLE_KAFKA:
        app.state.producer = _NullProducer()
    else:
        app.state.producer = Producer(
            {
                "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                "acks": "all",
                "enable.idempotence": False,
            }
        )
    app.state.sessions = SessionTracker(gap_seconds=SESSION_GAP_SECONDS)
    yield
    app.state.producer.flush(10)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _resolve_source_ip(request: Request) -> str | None:
    if TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _parse_client_ts(request: Request) -> datetime | None:
    raw = request.headers.get("x-client-ts")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _parse_sim_delay_ms(request: Request) -> float:
    raw = request.headers.get("x-sim-delay-ms")
    if not raw:
        return 0.0
    try:
        delay = float(raw)
    except ValueError:
        return 0.0
    return max(delay, 0.0)


def _parse_attempt_id(request: Request) -> str | None:
    # Set by the simulator (Phase 2) so its attempt events correlate with
    # the HTTP traffic they generate. Absent on a plain curl / manual test,
    # same as X-Client-Ts and X-Sim-Delay-Ms before it.
    return request.headers.get("x-attempt-id") or None


def _parse_run_id(request: Request) -> str | None:
    # Set by the simulator (Phase 3) so request events carry the same run_id
    # as the run's attempt events and its attack_runs row — the ground-truth
    # join key. Absent on a plain curl.
    return request.headers.get("x-run-id") or None


def _parse_pathology_tokens(request: Request) -> set[str]:
    # Deliberate data pathologies the simulator asks the honeypot to inject
    # into THIS request's event (docs/03, services/simulator/pathologies.py).
    # Corrupts the observed request stream only; never attempt events.
    raw = request.headers.get("x-sim-pathology", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


@app.get("/healthz")
async def healthz() -> dict:
    # Registered before the catch-all so it takes precedence (Starlette
    # matches routes in registration order). Deliberately does not publish
    # a request event: an infrastructure probe (Docker's own healthcheck,
    # every 5s per docker-compose.yml) is not attacker-facing traffic, and
    # logging it would silently mix synthetic noise into attack.events
    # indistinguishable from a real session — found the hard way when the
    # Phase 2 checkpoint's session/request counts didn't match expectations.
    return {"status": "ok"}


async def _handle_request(request: Request) -> JSONResponse:
    """Log the request and return its canned response. The single code path
    for every request the honeypot logs, reached both from the catch-all
    route and from the 405 handler below."""
    start = time.monotonic()

    body_bytes = await request.body()
    request_body = body_bytes.decode("utf-8", errors="replace") if body_bytes else None

    delay_ms = _parse_sim_delay_ms(request)
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)

    path = request.url.path
    route = match_route(path)

    received_at = datetime.now(UTC)
    source_ip = _resolve_source_ip(request)
    user_agent = request.headers.get("user-agent")
    cookie_token = request.cookies.get(COOKIE_NAME)
    session_id = request.app.state.sessions.session_id_for(cookie_token, received_at.timestamp())

    response_time_ms = (time.monotonic() - start) * 1000

    event = RequestEvent(
        session_id=session_id,
        run_id=_parse_run_id(request),
        received_at=received_at,
        client_ts=_parse_client_ts(request),
        source_ip=source_ip,
        user_agent=user_agent,
        http_method=request.method,
        path=path,
        query_string=request.url.query or None,
        path_tier=route.tier,
        request_body=request_body,
        body_bytes=len(body_bytes),
        status_returned=route.status_code,
        response_time_ms=response_time_ms,
        headers=json.dumps(dict(request.headers)),
        attempt_id=_parse_attempt_id(request),
    )

    # Apply the deliberate pathologies the simulator asked for. Field-level
    # ones are dict mutations (cleaner than fighting the model's types);
    # produce-level ones change how/whether the value is published.
    pathologies = _parse_pathology_tokens(request)
    payload = event.model_dump(mode="json")
    if "missing_source_ip" in pathologies:
        payload["source_ip"] = None
    if "missing_path" in pathologies:
        payload["path"] = None
    if "clock_skew_future_received_at" in pathologies:
        payload["received_at"] = (received_at + timedelta(hours=1)).isoformat()
    if "clock_skew_negative_response" in pathologies:
        payload["response_time_ms"] = -abs(response_time_ms) - 1.0
    if "schema_drift" in pathologies:
        payload["schema_version"] = "v2"
        payload["tls_fingerprint"] = "ja3:0123456789abcdef"

    value = json.dumps(payload).encode("utf-8")
    key = None if "unkeyed" in pathologies else session_id.encode("utf-8")

    producer = request.app.state.producer
    if "undeserializable" in pathologies:
        # Raw non-JSON bytes instead of the event — the consumer (Phase 4)
        # quarantines these; here we only verify they reach the topic.
        garbage = b"\x00\x01not-json\xff"
        producer.produce(KAFKA_TOPIC, key=key, value=garbage, callback=_delivery_report)
    else:
        producer.produce(KAFKA_TOPIC, key=key, value=value, callback=_delivery_report)
        if "duplicate_delivery" in pathologies:
            # Re-send the identical event (same event_id) — at-least-once
            # replay. Phase 5's dedupe on event_id removes this copy while
            # keeping Two-Face's distinct-event_id duplicate requests.
            producer.produce(KAFKA_TOPIC, key=key, value=value, callback=_delivery_report)
    producer.poll(0)

    response = JSONResponse(
        status_code=route.status_code, content=route.body, headers=dict(route.extra_headers)
    )
    # Carry session identity via cookie so a rotating attacker (Penguin's IP
    # rotation, high-INT UA rotation) stays one session while the rotation
    # still shows up in distinct_source_ips / distinct_user_agents.
    if cookie_token != session_id:
        response.set_cookie(COOKIE_NAME, session_id, httponly=True, samesite="strict")
    return response


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def catch_all(full_path: str, request: Request) -> JSONResponse:
    return await _handle_request(request)


@app.exception_handler(405)
async def method_not_allowed(request: Request, exc) -> JSONResponse:
    """A honeypot logs whatever arrives, including absurd HTTP methods —
    Joker's signature (docs/03) is "occasional absurd HTTP methods." The
    catch-all route only lists the standard verbs, so anything else (BREW,
    PROPFIND, ...) would otherwise get a bare 405 with no event published,
    making that signature silently invisible. Re-dispatch through the same
    logging path instead. (A non-GET /healthz lands here too and is logged
    as ordinary traffic — an attacker probing /healthz is attack traffic;
    only the infra healthcheck's GET is exempt.)"""
    return await _handle_request(request)
