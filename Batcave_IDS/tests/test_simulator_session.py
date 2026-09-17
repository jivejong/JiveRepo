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


def test_technique_selection_reaches_the_whole_gated_catalog_over_many_seeds():
    """Phase 6 fix: candidates[0] / untried[0] picked strict techniques.csv row
    order, which is catalog-entry order, not a priority - confirmed by stage
    4's own rows not being monotonic in min_intelligence. That meant 4 of 23
    catalog techniques (the tail of stage 1's and stage 4's lists) never got
    an attempt in ANY corpus, docs/04/docs/06's pre-committed Phase 6 item.

    This sweeps many seeds across all twelve villains and asserts every
    technique that CAN be gated in by some villain (produces_traffic and
    reachable by at least one roster member) is attempted at least once -
    the thing that was false before the shuffle fix.
    """
    all_techniques = {t.technique_id: t for t in load_techniques()}
    villains = load_villains()

    seen: set[str] = set()
    for slug in villains:
        for seed in range(40):
            producer = _FakeProducer()
            client = _fake_http_client(f"sweep-{slug}-{seed}")
            result = run_scripted_session(
                slug,
                rng=random.Random(seed),
                kafka_producer=producer,
                http_client=client,
                sleep_fn=lambda _: None,
            )
            seen.update(a.technique_id for a in result.attempts)

    # Reachable = gated by at least one villain's stats, on the seeds tried.
    # Excludes only techniques no roster member could ever qualify for -
    # that's a gating/roster question, not this fix's concern.
    reachable = {
        tid
        for tid, t in all_techniques.items()
        if any(
            v.intelligence >= t.min_intelligence
            and v.power >= t.min_power
            and v.strength >= t.min_strength
            for v in villains.values()
        )
    }

    missing = reachable - seen
    assert not missing, (
        f"never attempted across {len(villains) * 40} sessions: {sorted(missing)} - "
        "selection is still avoiding part of the catalog"
    )

    # The four specifically named in docs/04/docs/06 as the known-zero set.
    previously_zero = {"identity_gather", "open_source_search", "exfil_over_c2", "deploy_batbot"}
    assert previously_zero <= seen, (
        f"the four techniques the Phase 5 finding named are still unreached: "
        f"{previously_zero - seen}"
    )


def test_pathologies_ride_headers_to_the_honeypot_and_run_id_is_stamped():
    """With an injector, driven requests carry X-Sim-Pathology / X-Run-Id, and
    attempt events carry run_id. Captures the headers the simulator sent."""
    from services.simulator.pathologies import PathologyConfig, PathologyInjector

    seen_headers: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        headers = {}
        if f"{COOKIE_NAME}=s1" not in request.headers.get("cookie", ""):
            headers["Set-Cookie"] = f"{COOKIE_NAME}=s1; Path=/"
        return httpx.Response(200, headers=headers, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://h")
    injector = PathologyInjector(PathologyConfig.load(), random.Random(7))
    result = run_scripted_session(
        "60-bane",
        rng=random.Random(7),
        kafka_producer=_FakeProducer(),
        http_client=client,
        sleep_fn=lambda _: None,
        injector=injector,
    )
    assert result.run_id
    assert all(a.run_id == result.run_id for a in result.attempts)
    # Every driven request carries X-Run-Id, and over a Bane run (high volume)
    # at least one pathology header fired.
    driven = [h for h in seen_headers if h.get("x-attempt-id") != "session-bootstrap"]
    assert driven and all("x-run-id" in h for h in driven)
    assert any("x-sim-pathology" in h for h in driven)


def test_session_produces_at_least_one_attempt_and_stamps_session_id():
    producer = _FakeProducer()
    client = _fake_http_client("fake-session-1")
    result = run_scripted_session(
        "538-ras-al-ghul",
        rng=random.Random(0),
        kafka_producer=producer,
        http_client=client,
        sleep_fn=lambda _: None,
    )
    assert result.session_id == "fake-session-1"
    assert result.attempts, "a scripted session should produce at least one attempt"
    assert all(a.session_id == "fake-session-1" for a in result.attempts)


def test_no_attempt_violates_its_gate():
    producer = _FakeProducer()
    client = _fake_http_client()
    villain = load_villains()["538-ras-al-ghul"]
    result = run_scripted_session(
        "538-ras-al-ghul",
        rng=random.Random(42),
        kafka_producer=producer,
        http_client=client,
        sleep_fn=lambda _: None,
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
        "386-killer-croc",
        rng=random.Random(1),
        kafka_producer=producer,
        http_client=client,
        sleep_fn=lambda _: None,
    )
    assert result.stalled is True
    assert result.max_stage_reached <= 2


def test_every_attempt_is_published_to_kafka_with_matching_key():
    producer = _FakeProducer()
    client = _fake_http_client()
    result = run_scripted_session(
        "60-bane",
        rng=random.Random(7),
        kafka_producer=producer,
        http_client=client,
        sleep_fn=lambda _: None,
    )
    import json

    # Attempt events are keyed by session_id; the one attack_run event is
    # keyed by run_id. Separate them by event_kind in the value.
    attempt_msgs = [m for m in producer.messages if json.loads(m[2])["event_kind"] == "attempt"]
    run_msgs = [m for m in producer.messages if json.loads(m[2])["event_kind"] == "attack_run"]
    assert len(attempt_msgs) == len(result.attempts)
    for _topic, key, _value in attempt_msgs:
        assert key == result.session_id.encode("utf-8")
    assert len(run_msgs) == 1
    assert run_msgs[0][1] == result.run_id.encode("utf-8")


def test_attempt_seq_is_monotonic_and_technique_attempt_seq_resets_per_technique():
    producer = _FakeProducer()
    client = _fake_http_client()
    result = run_scripted_session(
        "60-bane",
        rng=random.Random(3),
        kafka_producer=producer,
        http_client=client,
        sleep_fn=lambda _: None,
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
        "60-bane",
        rng=random.Random(5),
        kafka_producer=producer,
        http_client=client,
        sleep_fn=lambda _: None,
    )
    seen_stages: set[int] = set()
    for a in result.attempts:
        if a.stage not in seen_stages:
            assert a.decision == "initial"
            seen_stages.add(a.stage)
        else:
            assert a.decision in ("retry", "pivot")
