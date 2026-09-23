"""services.console.batbot's engine, exercised deterministically - always via
the zero-credential (`llm_client=None`) path, since that's the fully
deterministic one and the engagement/turn-cap logic is identical regardless
of who writes `bot_text` (docs/08). Pins the boundary conditions in code
rather than leaving them to a live play-through - the same
characterization-test-*and*-live-verification pairing Phase 8 used for the
StageMachine RNG order (Phase 9 plan).
"""

from __future__ import annotations

import pytest

from services.console.batbot import (
    MAX_TOTAL_TURNS,
    MIN_TOTAL_TURNS,
    PROBE_IDENTITY,
    PROBE_LOCATION,
    PROBE_SECONDARY,
    RAPPORT,
    REVEAL,
    BatBotConversation,
    extract_signals,
)


class _FakeProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, bytes]] = []

    def produce(self, topic, key, value, callback=None) -> None:
        self.messages.append((topic, key, value))

    def poll(self, timeout=0) -> int:
        return 0

    def flush(self, timeout=0) -> int:
        return 0


def _conversation() -> BatBotConversation:
    return BatBotConversation(
        session_id="s1",
        run_id="r1",
        kafka_producer=_FakeProducer(),
        kafka_topic="attack.events",
        llm_client=None,  # zero-credential fallback - fully deterministic
    )


def test_always_engaged_input_terminates_at_exactly_five_turns():
    """The hard cap, proven on the input most likely to breach it: a reply
    that registers as engaged every single time must still stop at
    MAX_TOTAL_TURNS (5), never more, regardless of how many times engagement
    would otherwise say "keep going.\""""
    convo = _conversation()
    convo.start()
    engaged_reply = (
        "My name is Bruce and I'm in Gotham at the Batcave."  # triggers 3 flags, no refusal
    )

    while not convo.finished:
        _, bot_event = convo.reply(engaged_reply)

    assert convo.history[-1].objective == REVEAL
    assert convo.history[-1].turn_number == MAX_TOTAL_TURNS == 5
    # Exactly 9 events: bot(1) + [user, bot] x4 = 1 + 8.
    assert len(convo.history) == 9
    assert [e.speaker for e in convo.history] == ["bot", "user"] * 4 + ["bot"]
    assert [e.objective for e in convo.history[::2]] == [
        RAPPORT,
        PROBE_IDENTITY,
        PROBE_LOCATION,
        PROBE_SECONDARY,
        REVEAL,
    ]


def test_always_refused_input_terminates_at_the_three_turn_floor():
    """The floor: a reply that always refuses must still get one probe turn
    in (the first probe is never optional - MIN_TOTAL_TURNS = 3), but no
    more than that once it refuses."""
    convo = _conversation()
    convo.start()
    refused_reply = "No, I'm not telling you that."

    while not convo.finished:
        _, bot_event = convo.reply(refused_reply)

    assert convo.history[-1].objective == REVEAL
    assert convo.history[-1].turn_number == MIN_TOTAL_TURNS == 3
    # bot(1) + [user, bot] x2 = 5 events.
    assert len(convo.history) == 5
    assert [e.objective for e in convo.history[::2]] == [RAPPORT, PROBE_IDENTITY, REVEAL]


def test_first_probe_turn_is_never_optional():
    """A disengaged (but not refused) reply to RAPPORT itself must still lead
    to the first probe turn, not straight to reveal - this was a real bug
    caught before it shipped: engagement only governs continuation PAST the
    first probe, never whether the first one happens at all."""
    convo = _conversation()
    convo.start()
    vague_reply = "hmm, not sure"  # not a refusal keyword, but no flags either -> not engaged

    _, bot_event = convo.reply(vague_reply)

    assert bot_event.objective == PROBE_IDENTITY
    assert not convo.finished


def test_mixed_engagement_produces_a_turn_count_in_the_middle_of_the_range():
    """The reply to RAPPORT never affects continuation - the first probe is
    unconditional regardless of it. Only replies to probe turns do: engaged
    once (continues past probe 1 to probe 2), then disengaged (stops at
    reveal instead of probe 3) = rapport, probe1, probe2, reveal = 4 total
    turns, strictly between the floor and the cap."""
    convo = _conversation()
    convo.start()
    convo.reply("whatever, doesn't matter")  # reply to RAPPORT - ignored either way
    convo.reply("I'm Bruce, from Gotham.")  # reply to probe 1, engaged -> continues to probe 2
    _, bot_event = convo.reply("not answering that")  # reply to probe 2, disengaged -> reveal now

    assert bot_event.objective == REVEAL
    assert bot_event.turn_number == 4
    assert MIN_TOTAL_TURNS < 4 < MAX_TOTAL_TURNS


def test_reply_after_the_conversation_ended_raises_rather_than_silently_continuing():
    convo = _conversation()
    convo.start()
    refused_reply = "no"
    while not convo.finished:
        convo.reply(refused_reply)

    with pytest.raises(RuntimeError):
        convo.reply("anything")


@pytest.mark.parametrize(
    "text,expected_flags,expected_refused,expected_engaged",
    [
        ("My name is Bruce.", ["gave_identity"], False, True),
        ("I work in Gotham at the Batcave.", ["gave_role", "gave_location"], False, True),
        ("No, not telling you.", [], True, False),
        ("", [], True, False),
        ("   ", [], True, False),
        ("hmm, not sure", [], False, False),  # no refusal, no flags -> not engaged
    ],
)
def test_extract_signals_engagement_rule(text, expected_flags, expected_refused, expected_engaged):
    """engaged = (not refused) and bool(extracted_intent_flags) - enforced,
    not just described (docs/08)."""
    result = extract_signals(text)
    assert sorted(result.extracted_intent_flags) == sorted(expected_flags)
    assert result.refused == expected_refused
    assert result.engaged == expected_engaged
    assert result.engaged == ((not result.refused) and bool(result.extracted_intent_flags))


def test_bot_and_user_rows_null_the_fields_that_dont_apply_to_them():
    convo = _conversation()
    bot_event = convo.start()
    assert bot_event.speaker == "bot"
    assert bot_event.bot_text is not None
    assert bot_event.user_text is None
    assert bot_event.refused is None
    assert bot_event.extracted_intent_flags is None

    user_event, _ = convo.reply("I'm Bruce, from Gotham.")
    assert user_event.speaker == "user"
    assert user_event.user_text is not None
    assert user_event.bot_text is None
    assert user_event.latency_ms is None
    assert user_event.input_tokens is None
    assert user_event.output_tokens is None
    assert user_event.refused is not None
    assert user_event.extracted_intent_flags is not None


def test_two_rows_share_turn_number_per_round():
    convo = _conversation()
    convo.start()
    user_event, bot_event = convo.reply("I'm Bruce, from Gotham.")
    assert user_event.turn_number == 1
    assert bot_event.turn_number == 2


def test_every_event_is_published_with_the_right_key():
    convo = _conversation()
    convo.start()
    convo.reply("no")
    while not convo.finished:
        convo.reply("no")

    for _topic, key, _value in convo.kafka_producer.messages:
        assert key == b"s1"
    assert len(convo.kafka_producer.messages) == len(convo.history)


def test_extraction_version_constant_exists_and_is_reachable():
    """A silent change to the keyword list should be detectable via this
    constant - the module-level version, same precedent as PROMPT_VERSION."""
    from services.console.batbot import EXTRACTION_VERSION

    assert isinstance(EXTRACTION_VERSION, str) and EXTRACTION_VERSION
