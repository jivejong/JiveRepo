"""services.console.counterstrike — the finale readout script and event
emission, checked deterministically. No LLM or Kafka involved: the script
is pure text rendering over a prediction, the way `services.console.
batbot`'s `extract_signals` is pure over user text.
"""

from services.console.counterstrike import (
    CounterstrikeEvent,
    build_degraded_script,
    build_script,
    emit_counterstrike,
)


class _FakeProducer:
    def __init__(self):
        self.produced = []

    def produce(self, topic, key, value, callback=None):
        self.produced.append((topic, key, value))

    def poll(self, timeout):
        pass


def test_build_script_populates_attribution_only_on_the_suspect_line():
    """Phase 9's chat_turn convention: a fact decided once is populated
    once, everywhere else null - never denormalized across every row."""
    lines = build_script(
        villain_slug="522-poison-ivy",
        villain_display_name="Poison Ivy",
        confidence=0.83,
        attack_ids=["T1595", "T1110"],
    )
    suspect_lines = [line for line in lines if line["attributed_villain_slug"] is not None]
    assert len(suspect_lines) == 1
    assert suspect_lines[0]["attributed_villain_slug"] == "522-poison-ivy"
    assert suspect_lines[0]["attributed_confidence"] == 0.83
    assert "SUSPECT: Poison Ivy" in suspect_lines[0]["readout_line"]


def test_build_script_populates_attack_id_only_on_wiper_and_shutdown_lines():
    lines = build_script(
        villain_slug="522-poison-ivy",
        villain_display_name="Poison Ivy",
        confidence=0.83,
        attack_ids=["T1595"],
    )
    attack_id_lines = [line for line in lines if line["attack_id"] is not None]
    assert len(attack_id_lines) == 2


def test_build_script_lists_the_real_predicted_technique_ids():
    lines = build_script(
        villain_slug="522-poison-ivy",
        villain_display_name="Poison Ivy",
        confidence=0.83,
        attack_ids=["T1595", "T1110", "T1087"],
    )
    techniques_line = next(
        line for line in lines if "TECHNIQUES RECONSTRUCTED" in line["readout_line"]
    )
    assert techniques_line["readout_line"].endswith("T1595, T1110, T1087")


def test_build_script_handles_no_reconstructed_techniques():
    lines = build_script(
        villain_slug="522-poison-ivy",
        villain_display_name="Poison Ivy",
        confidence=0.4,
        attack_ids=[],
    )
    techniques_line = next(
        line for line in lines if "TECHNIQUES RECONSTRUCTED" in line["readout_line"]
    )
    assert "NONE" in techniques_line["readout_line"]


def test_every_line_carries_the_batcomputer_prefix():
    lines = build_script(
        villain_slug="522-poison-ivy",
        villain_display_name="Poison Ivy",
        confidence=0.83,
        attack_ids=["T1595"],
    )
    for line in lines:
        assert line["readout_line"].startswith("[BATCOMPUTER")


def test_degraded_script_names_no_suspect_and_carries_no_attribution():
    lines = build_degraded_script()
    assert all(line["attributed_villain_slug"] is None for line in lines)
    assert all(line["attack_id"] is None for line in lines)
    assert any("INCONCLUSIVE" in line["readout_line"] for line in lines)


def test_emit_counterstrike_publishes_and_returns_one_event_per_line():
    producer = _FakeProducer()
    lines = build_script(
        villain_slug="522-poison-ivy",
        villain_display_name="Poison Ivy",
        confidence=0.83,
        attack_ids=["T1595"],
    )
    events = emit_counterstrike(
        session_id="s1",
        run_id="r1",
        kafka_producer=producer,
        kafka_topic="attack.events",
        line_dicts=lines,
    )
    assert len(events) == len(lines)
    assert len(producer.produced) == len(lines)
    assert all(isinstance(e, CounterstrikeEvent) for e in events)
    assert [e.sequence for e in events] == list(range(1, len(lines) + 1))


def test_emit_counterstrike_keys_every_message_by_session_id():
    producer = _FakeProducer()
    lines = build_degraded_script()
    emit_counterstrike(
        session_id="s1",
        run_id="r1",
        kafka_producer=producer,
        kafka_topic="attack.events",
        line_dicts=lines,
    )
    assert all(key == b"s1" for _, key, _ in producer.produced)
