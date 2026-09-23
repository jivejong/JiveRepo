"""services.console.app, exercised against a fake HTTP transport and a fake
Kafka producer - the same seam services/honeypot/app.py's own
HONEYPOT_DISABLE_KAFKA / _NullProducer gives that service's tests. The real
integration (a live honeypot and Redpanda, a console session's attempt/
request events correlating on attempt_id and session_id, read back with
`rpk topic consume`) is verified manually for the Phase 8 checkpoint - this
file checks the API's own request handling and bookkeeping in isolation.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from services.honeypot.session import COOKIE_NAME
from services.simulator.catalog import gated_techniques, load_villains


class _FakeProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, bytes]] = []

    def produce(self, topic, key, value, callback=None) -> None:
        self.messages.append((topic, key, value))

    def poll(self, timeout=0) -> int:
        return 0

    def flush(self, timeout=0) -> int:
        return 0


def _fake_http_client() -> httpx.Client:
    """A fresh MockTransport-backed client per console session - each needs
    its own cookie jar, exactly like a real browser session would, so the
    honeypot's per-session cookie mint doesn't leak between two players."""

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {}
        if COOKIE_NAME not in request.headers.get("cookie", ""):
            headers["Set-Cookie"] = f"{COOKIE_NAME}={uuid.uuid4()}; Path=/"
        return httpx.Response(200, headers=headers, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testhoneypot")


@pytest.fixture
def client(monkeypatch):
    import services.console.app as console_app

    monkeypatch.setattr(console_app, "kafka_producer_factory", _FakeProducer)
    monkeypatch.setattr(console_app, "http_client_factory", _fake_http_client)
    with TestClient(console_app.app) as c:
        yield c


def test_list_villains_returns_all_twelve_with_live_gating(client):
    resp = client.get("/api/villains")
    assert resp.status_code == 200
    villains = resp.json()
    assert len(villains) == 12
    by_slug = {v["slug"]: v for v in villains}

    # Killer Croc: stage 3 has zero gated techniques for him (docs/03, docs/07
    # - his int-19 wall). The live-gating requirement in docs/08 means this
    # must be visible before the player commits, not discovered mid-run.
    croc = by_slug["386-killer-croc"]
    stage_3 = next(r for r in croc["stage_reach"] if r["stage"] == 3)
    assert stage_3["available"] == 0
    assert stage_3["total"] > 0

    # Ra's al Ghul clears every min_intelligence gate (docs/06 Phase 2
    # checkpoint) - stage 1 should show him with the full stage-1 catalog.
    ras = by_slug["538-ras-al-ghul"]
    stage_1 = next(r for r in ras["stage_reach"] if r["stage"] == 1)
    assert stage_1["available"] == stage_1["total"]


def test_create_session_enters_stage_one_with_gated_candidates(client):
    resp = client.post("/api/session", json={"villain_slug": "538-ras-al-ghul"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage"]["stage_num"] == 1
    assert body["session_id"]  # the honeypot's own session_id, learned via warm-up
    villain = load_villains()["538-ras-al-ghul"]
    expected = {t.technique_id for t in gated_techniques(villain, 1)}
    assert {t["technique_id"] for t in body["stage"]["available"]} == expected


def test_create_session_rejects_unknown_villain(client):
    resp = client.post("/api/session", json={"villain_slug": "not-a-real-villain"})
    assert resp.status_code == 404


def test_attempt_rejects_a_technique_not_offered_this_stage(client):
    session = client.post("/api/session", json={"villain_slug": "538-ras-al-ghul"}).json()
    resp = client.post(
        f"/api/session/{session['console_session_id']}/attempt",
        json={"technique_id": "deploy_batbot"},  # a stage-4 technique, not stage 1
    )
    assert resp.status_code == 400


def test_first_attempt_of_a_stage_is_labeled_initial(client):
    session = client.post("/api/session", json={"villain_slug": "538-ras-al-ghul"}).json()
    technique_id = session["stage"]["available"][0]["technique_id"]
    resp = client.post(
        f"/api/session/{session['console_session_id']}/attempt",
        json={"technique_id": technique_id, "parameters": {"aggression": "high"}},
    )
    assert resp.status_code == 200
    assert resp.json()["event"]["decision"] == "initial"


def test_attempt_on_unknown_session_is_404(client):
    resp = client.post(f"/api/session/{uuid.uuid4()}/attempt", json={"technique_id": "port_sweep"})
    assert resp.status_code == 404


def test_a_played_session_reaches_a_terminal_state_and_finishes_exactly_once(client):
    """Drives a real session to completion via the API alone - retry the
    current technique, or pivot to the first still-untried candidate - the
    same envelope the headless autopilot operates within (Phase 8's
    "choice inside the envelope" decision). Verifies the run always reaches
    a terminal state (docs/08: clears or stalls, nothing else) and that
    finishing publishes exactly one attack_run event, not zero and not two."""
    import services.console.app as console_app

    session = client.post("/api/session", json={"villain_slug": "386-killer-croc"}).json()
    console_session_id = session["console_session_id"]

    for _ in range(500):
        state = client.get(f"/api/session/{console_session_id}/state").json()
        if state["finished"]:
            break
        # Try the stage's candidates in order until one is accepted: the
        # server rejects only a technique that's already been tried and
        # abandoned this stage (not the current/last one), so this always
        # finds either a legitimate retry or an untried pivot target as long
        # as the run itself isn't over.
        resp = None
        for candidate in state["stage"]["available"]:
            resp = client.post(
                f"/api/session/{console_session_id}/attempt",
                json={"technique_id": candidate["technique_id"]},
            )
            if resp.status_code == 200:
                break
        assert resp is not None and resp.status_code == 200, resp.text if resp else "no candidates"
    else:
        pytest.fail("session never reached a terminal state within 500 attempts")

    final_state = client.get(f"/api/session/{console_session_id}/state").json()
    assert final_state["finished"] is True
    assert final_state["run_outcome"] in ("cleared", "stalled")

    # Exactly one attack_run event published for this run_id - not zero
    # (finish() never called) and not two (a double-finish bug).
    import json

    run_events = [
        json.loads(value)
        for _, _, value in console_app.app.state.producer.messages
        if json.loads(value)["event_kind"] == "attack_run"
    ]
    assert len(run_events) == 1
    assert run_events[0]["session_source"] == "console"

    # Idempotent explicit finish: calling it again after the run already
    # ended must not publish a second attack_run.
    client.post(f"/api/session/{console_session_id}/finish")
    run_events_after = [
        json.loads(value)
        for _, _, value in console_app.app.state.producer.messages
        if json.loads(value)["event_kind"] == "attack_run"
    ]
    assert len(run_events_after) == 1
