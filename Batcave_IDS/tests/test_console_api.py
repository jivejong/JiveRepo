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
    # Explicit, not just an accident of the ambient environment lacking the
    # key: tests must never make a real Gemini call, the same way the triage
    # test suite never does. Forcing this here means a developer running
    # tests locally with GEMINI_API_KEY genuinely exported still gets the
    # deterministic zero-credential path, not a flaky real-network test.
    monkeypatch.setattr(console_app, "llm_client_factory", lambda: None)
    # The finale pipeline (services/console/finale.py) opens the real
    # data/warehouse.duckdb and spawns a real `dbt build` subprocess -
    # neither of which the three seams above touch. Without this, every
    # test that finishes a session would start a genuine background
    # pipeline against production data. A no-op stands in, the same way
    # the other three seams keep this suite off real infrastructure.
    monkeypatch.setattr(console_app, "finale_runner", lambda **kwargs: None)
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


def _play_to_stage_four(client, console_session_id: str) -> dict | None:
    """Drives a session through stages 1-3 with whatever's offered, stopping
    the instant stage 4 is reached. Returns `None` (not a failure - a real,
    if unlikely, outcome for any villain, since a console session's rng is
    genuinely unseeded, docs/06 Phase 8) if the run stalls or clears via some
    other route before ever getting there. Killer Croc specifically can
    never reach deploy_batbot at all (min_intelligence 40, his is 19)."""
    for _ in range(200):
        state = client.get(f"/api/session/{console_session_id}/state").json()
        if state["finished"]:
            return None
        if state["stage"]["stage_num"] == 4:
            return state
        resp = None
        for candidate in state["stage"]["available"]:
            resp = client.post(
                f"/api/session/{console_session_id}/attempt",
                json={"technique_id": candidate["technique_id"]},
            )
            if resp.status_code == 200:
                break
        assert resp is not None and resp.status_code == 200, resp.text if resp else "no candidates"
    pytest.fail("never reached stage 4 or finished within 200 attempts")


def _one_attempt_at_stage_four_with_deploy_batbot(
    client, villain_slug: str
) -> tuple[str, dict] | None:
    """One real playthrough: fresh session, played to stage 4, then
    deploy_batbot attempted up to 3 times (enough to see real retry variance
    without chasing `retry_penalty`'s own decay into exhausting
    `failure_tolerance` on its own - each retry lowers deploy_batbot's own
    success chance, so retrying it in a loop large enough to "guarantee"
    eventual success can, on a long enough tail, stall the run by itself).
    Returns `(console_session_id, success_event_body)` on success, `None` on
    any other outcome (never reached stage 4, deploy_batbot unavailable,
    stalled) - never fails the test itself, so the caller can retry with a
    completely independent fresh session instead."""
    session = client.post("/api/session", json={"villain_slug": villain_slug}).json()
    console_session_id = session["console_session_id"]
    state = _play_to_stage_four(client, console_session_id)
    if state is None or not any(
        t["technique_id"] == "deploy_batbot" for t in state["stage"]["available"]
    ):
        return None
    for _ in range(3):
        resp = client.post(
            f"/api/session/{console_session_id}/attempt", json={"technique_id": "deploy_batbot"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["event"]["outcome"] == "success":
            return console_session_id, body
        if body["session"]["finished"]:
            return None
    return None


def _succeed_at_deploy_batbot(client, villain_slug: str) -> tuple[str, dict]:
    """Real playthroughs (real unseeded rng, docs/06 Phase 8) until one
    reaches stage 4 with deploy_batbot available and succeeds at it within a
    few real attempts. Each failed playthrough is abandoned outright rather
    than retried in place, since a stall anywhere (stages 1-3, or
    deploy_batbot's own retry_penalty decay) ends that session for good -
    there's nothing left inside it worth retrying. Bounded, not infinite: a
    villain who gates for deploy_batbot at all should get there this way the
    overwhelming majority of the time within a handful of fresh tries."""
    for _ in range(20):
        result = _one_attempt_at_stage_four_with_deploy_batbot(client, villain_slug)
        if result is not None:
            return result
    pytest.fail(f"deploy_batbot never succeeded for {villain_slug} across 20 fresh sessions")


def test_deploy_batbot_success_blocks_completion_until_the_bat_bot_finishes(client):
    """The real bug this test exists to catch: deploy_batbot succeeding at
    stage 4 must NOT finish the run immediately the way every other stage-4
    technique does - completion has to wait for the bat bot conversation to
    reach reveal."""
    console_session_id, body = _succeed_at_deploy_batbot(client, "538-ras-al-ghul")
    assert body["event"]["outcome"] == "success"
    assert body["session"]["batbot_pending"] is True
    assert body["session"]["finished"] is False
    assert body["session"]["stage"] is None  # no stage-5 menu to show

    # /finish still works as an escape hatch mid-bat-bot-pending, and reads
    # as "cleared" - stage 4 (the real gate) was already cleared.
    finish_resp = client.post(f"/api/session/{console_session_id}/finish")
    assert finish_resp.status_code == 200
    assert finish_resp.json()["finished"] is True
    assert finish_resp.json()["run_outcome"] == "cleared"
    assert finish_resp.json()["batbot_pending"] is False


def test_full_bat_bot_conversation_via_the_api_reaches_reveal_and_finishes_the_run(client):
    """End-to-end through the real API surface: deploy_batbot succeeds,
    /batbot/start opens the conversation, /batbot/reply is called
    repeatedly with an engaged reply until reveal, and only then does the
    console session finish - with exactly one attack_run published."""
    import json

    import services.console.app as console_app

    console_session_id, _body = _succeed_at_deploy_batbot(client, "538-ras-al-ghul")

    start_resp = client.post(f"/api/session/{console_session_id}/batbot/start")
    assert start_resp.status_code == 200, start_resp.text
    start_body = start_resp.json()
    assert start_body["event"]["speaker"] == "bot"
    assert start_body["event"]["turn_number"] == 1
    assert start_body["event"]["objective"] == "rapport"
    assert start_body["session"]["finished"] is False

    engaged_reply = "My name is Bruce and I'm calling from Gotham."
    last_body = None
    for _ in range(10):
        reply_resp = client.post(
            f"/api/session/{console_session_id}/batbot/reply",
            json={"user_text": engaged_reply},
        )
        assert reply_resp.status_code == 200, reply_resp.text
        last_body = reply_resp.json()
        if last_body["session"]["finished"]:
            break
    else:
        pytest.fail("bat bot conversation never reached reveal within 10 replies")

    assert last_body["bot_event"]["objective"] == "reveal"
    assert 3 <= last_body["bot_event"]["turn_number"] <= 5
    assert last_body["session"]["run_outcome"] == "cleared"
    assert last_body["session"]["batbot_pending"] is False

    # Calling /batbot/start again now must not silently restart it.
    restart_resp = client.post(f"/api/session/{console_session_id}/batbot/start")
    assert restart_resp.status_code == 409

    # Filtered to THIS test's own session_id, not the whole shared producer
    # log: _succeed_at_deploy_batbot abandons any playthrough that doesn't
    # reach stage 4 with a deploy_batbot success, and an abandoned session
    # that stalled naturally along the way legitimately published its own
    # real attack_run - that's correct behavior, not a double-finish bug on
    # THIS session, so counting the whole log would be the wrong assertion.
    session_id = last_body["session"]["session_id"]
    all_events = [json.loads(value) for _, _, value in console_app.app.state.producer.messages]
    run_events = [
        e for e in all_events if e["event_kind"] == "attack_run" and e["session_id"] == session_id
    ]
    assert len(run_events) == 1
    chat_events = [
        e for e in all_events if e["event_kind"] == "chat_turn" and e["session_id"] == session_id
    ]
    assert len(chat_events) >= 5  # at least the 3-turn floor's worth of rows
    assert any(e["speaker"] == "user" for e in chat_events)
    assert all(e["user_text"] is None for e in chat_events if e["speaker"] == "bot")


def test_batbot_reply_before_start_is_rejected(client):
    console_session_id, _body = _succeed_at_deploy_batbot(client, "538-ras-al-ghul")
    resp = client.post(
        f"/api/session/{console_session_id}/batbot/reply", json={"user_text": "hello"}
    )
    assert resp.status_code == 409


def test_batbot_start_before_deploy_batbot_succeeds_is_rejected(client):
    session = client.post("/api/session", json={"villain_slug": "538-ras-al-ghul"}).json()
    resp = client.post(f"/api/session/{session['console_session_id']}/batbot/start")
    assert resp.status_code == 409
