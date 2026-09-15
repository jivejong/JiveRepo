"""services.simulator.session, exercised against a fake HTTP transport and
a fake Kafka producer so it runs without a live honeypot or broker. The
real integration (real honeypot, real Redpanda, attempt_id correlation
read back with rpk topic consume) is verified manually for the Phase 2
checkpoint — this file checks the stage machine's own logic in isolation.
"""

import random

import httpx

from services.honeypot.session import COOKIE_NAME
from services.simulator.catalog import load_techniques, load_villains
from services.simulator.session import run_scripted_session


class _FakeProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, bytes]] = []

    def produce(self, topic, key, value, callback=None) -> None:
        self.messages.append((topic, key, value))

    def poll(self, timeout=0) -> int:
        return 0

    def flush(self, timeout=0) -> int:
        return 0


def _fake_http_client(session_id: str = "fake-session-1") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        # Mimic the honeypot: set the session cookie only when the client
        # isn't already carrying it, so httpx's jar holds it after warm-up.
        headers = {}
        if f"{COOKIE_NAME}={session_id}" not in request.headers.get("cookie", ""):
            headers["Set-Cookie"] = f"{COOKIE_NAME}={session_id}; Path=/"
        return httpx.Response(200, headers=headers, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testhoneypot")


def test_session_produces_at_least_one_attempt_and_stamps_session_id():
    producer = _FakeProducer()
    client = _fake_http_client("fake-session-1")
    result = run_scripted_session(
        "538-ras-al-ghul", rng=random.Random(0), kafka_producer=producer, http_client=client
    )
    assert result.session_id == "fake-session-1"
    assert result.attempts, "a scripted session should produce at least one attempt"
    assert all(a.session_id == "fake-session-1" for a in result.attempts)


def test_no_attempt_violates_its_gate():
    producer = _FakeProducer()
    client = _fake_http_client()
    villain = load_villains()["538-ras-al-ghul"]
    result = run_scripted_session(
        "538-ras-al-ghul", rng=random.Random(42), kafka_producer=producer, http_client=client
    )

    techniques_by_id = {t.technique_id: t for t in load_techniques()}
    for attempt in result.attempts:
        t = techniques_by_id[attempt.technique_id]
        assert villain.intelligence >= t.min_intelligence
        assert villain.power >= t.min_power
        assert villain.strength >= t.min_strength


def test_killer_croc_stalls_at_stage_two_or_earlier():
    producer = _FakeProducer()
    client = _fake_http_client()
    result = run_scripted_session(
        "386-killer-croc", rng=random.Random(1), kafka_producer=producer, http_client=client
    )
    assert result.stalled is True
    assert result.max_stage_reached <= 2


def test_every_attempt_is_published_to_kafka_with_matching_key():
    producer = _FakeProducer()
    client = _fake_http_client()
    result = run_scripted_session(
        "60-bane", rng=random.Random(7), kafka_producer=producer, http_client=client
    )
    assert len(producer.messages) == len(result.attempts)
    for _topic, key, _value in producer.messages:
        assert key == result.session_id.encode("utf-8")


def test_attempt_seq_is_monotonic_and_technique_attempt_seq_resets_per_technique():
    producer = _FakeProducer()
    client = _fake_http_client()
    result = run_scripted_session(
        "60-bane", rng=random.Random(3), kafka_producer=producer, http_client=client
    )
    seqs = [a.attempt_seq for a in result.attempts]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, len(seqs) + 1))

    seen_per_technique: dict[str, int] = {}
    for a in result.attempts:
        expected = seen_per_technique.get(a.technique_id, 0) + 1
        assert a.technique_attempt_seq == expected
        seen_per_technique[a.technique_id] = expected


def test_decision_is_initial_only_on_the_first_attempt_of_each_stage():
    producer = _FakeProducer()
    client = _fake_http_client()
    result = run_scripted_session(
        "60-bane", rng=random.Random(5), kafka_producer=producer, http_client=client
    )
    seen_stages: set[int] = set()
    for a in result.attempts:
        if a.stage not in seen_stages:
            assert a.decision == "initial"
            seen_stages.add(a.stage)
        else:
            assert a.decision in ("retry", "pivot")
