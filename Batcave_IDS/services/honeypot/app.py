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
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

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


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def catch_all(full_path: str, request: Request) -> JSONResponse:
    start = time.monotonic()

    body_bytes = await request.body()
    request_body = body_bytes.decode("utf-8", errors="replace") if body_bytes else None

    delay_ms = _parse_sim_delay_ms(request)
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)

    path = "/" + full_path
    route = match_route(path)

    received_at = datetime.now(UTC)
    source_ip = _resolve_source_ip(request)
    user_agent = request.headers.get("user-agent")
    cookie_token = request.cookies.get(COOKIE_NAME)
    session_id = request.app.state.sessions.session_id_for(cookie_token, received_at.timestamp())

    response_time_ms = (time.monotonic() - start) * 1000

    event = RequestEvent(
        session_id=session_id,
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

    producer = request.app.state.producer
    producer.produce(
        KAFKA_TOPIC,
        key=session_id.encode("utf-8"),
        value=event.model_dump_json().encode("utf-8"),
        callback=_delivery_report,
    )
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
