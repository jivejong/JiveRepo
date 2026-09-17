"""services.triage.llm — the defensive parsing, fence-stripping, and
validation logic, tested against fabricated Groq responses (no live API
calls). The transport-retry and repair-retry paths are exercised with a fake
client that returns pre-scripted responses in sequence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from groq import GroqError

from services.triage.llm import (
    REQUIRED_FIELDS,
    _strip_fences,
    _try_parse,
    triage_session,
    validate,
)

VALID_OUTPUT = {
    "threat_level": "high",
    "suspected_villain": "558-riddler",
    "alternate_suspects": ["678-two-face"],
    "suspected_archetype": "cerebral",
    "confidence": 0.8,
    "identified_techniques": [
        {"attack_id": "T1110", "confidence": 0.7, "evidence": "repeated 401s on /login"}
    ],
    "identified_tactics": ["TA0006"],
    "reconstructed_stage_reached": 2,
    "reasoning": "riddle params and repeated auth failures point to Riddler.",
    "in_person_intervention_required": False,
    "recommended_countermeasures": ["rate-limit /login"],
    "attack_pattern_summary": "credential brute force with riddle-tagged requests",
}

KNOWN_VILLAINS = {"558-riddler", "678-two-face", "370-joker"}
KNOWN_ATTACK_IDS = {"T1110", "T1190"}


# --- fence stripping -------------------------------------------------------


def test_strip_fences_removes_json_fence():
    text = '```json\n{"a": 1}\n```'
    assert _strip_fences(text) == '{"a": 1}'


def test_strip_fences_removes_bare_fence():
    text = '```\n{"a": 1}\n```'
    assert _strip_fences(text) == '{"a": 1}'


def test_strip_fences_is_a_noop_on_plain_json():
    text = '{"a": 1}'
    assert _strip_fences(text) == '{"a": 1}'


# --- parsing -----------------------------------------------------------


def test_try_parse_handles_fenced_json():
    text = "```json\n" + json.dumps(VALID_OUTPUT) + "\n```"
    assert _try_parse(text) == VALID_OUTPUT


def test_try_parse_returns_none_on_garbage():
    assert _try_parse("not json at all") is None


def test_try_parse_returns_none_on_a_json_scalar():
    """A valid JSON value that isn't an object can't be the output - must not
    be accepted as if it were."""
    assert _try_parse("42") is None
    assert _try_parse('"just a string"') is None


def test_try_parse_returns_none_on_truncated_json():
    assert _try_parse('{"threat_level": "high"') is None


# --- validation --------------------------------------------------------


def test_validate_accepts_a_known_villain_and_technique():
    parsed, hallucinated_villain, hallucinated_count, warnings = validate(
        VALID_OUTPUT, KNOWN_VILLAINS, KNOWN_ATTACK_IDS
    )
    assert hallucinated_villain is False
    assert hallucinated_count == 0
    assert warnings == []


def test_validate_flags_an_unknown_villain_but_keeps_the_value():
    """docs/04: unrecognized values get flagged, not silently stored as
    truth - which means keeping the raw value so it's inspectable, not
    discarding it."""
    output = {**VALID_OUTPUT, "suspected_villain": "999-not-a-real-villain"}
    parsed, hallucinated_villain, _, warnings = validate(output, KNOWN_VILLAINS, KNOWN_ATTACK_IDS)
    assert hallucinated_villain is True
    assert parsed["suspected_villain"] == "999-not-a-real-villain"
    assert any("999-not-a-real-villain" in w for w in warnings)


def test_validate_accepts_unknown_as_a_villain_value():
    """'unknown' is a legitimate answer (docs/04), not a hallucination."""
    output = {**VALID_OUTPUT, "suspected_villain": "unknown"}
    _, hallucinated_villain, _, _ = validate(output, KNOWN_VILLAINS, KNOWN_ATTACK_IDS)
    assert hallucinated_villain is False


def test_validate_counts_hallucinated_techniques_individually():
    output = {
        **VALID_OUTPUT,
        "identified_techniques": [
            {"attack_id": "T1110", "confidence": 0.5, "evidence": "real"},
            {"attack_id": "T9999", "confidence": 0.5, "evidence": "made up"},
            {"attack_id": "T8888", "confidence": 0.5, "evidence": "also made up"},
        ],
    }
    _, _, hallucinated_count, _ = validate(output, KNOWN_VILLAINS, KNOWN_ATTACK_IDS)
    assert hallucinated_count == 2


def test_validate_flags_missing_required_fields():
    incomplete = {"threat_level": "low"}
    _, _, _, warnings = validate(incomplete, KNOWN_VILLAINS, KNOWN_ATTACK_IDS)
    assert any("missing fields" in w for w in warnings)


def test_required_fields_matches_docs04_output_contract():
    assert REQUIRED_FIELDS == {
        "threat_level",
        "suspected_villain",
        "alternate_suspects",
        "suspected_archetype",
        "confidence",
        "identified_techniques",
        "identified_tactics",
        "reconstructed_stage_reached",
        "reasoning",
        "in_person_intervention_required",
        "recommended_countermeasures",
        "attack_pattern_summary",
    }


# --- triage_session: the repair-retry path ------------------------------


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()


class _ScriptedClient:
    """Returns each scripted response in sequence, one per call. Mimics the
    subset of the Groq client triage_session actually uses."""

    def __init__(self, responses: list[str | Exception]):
        self._responses = list(responses)
        self.calls: list[str] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs["messages"][-1]["content"])
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def test_triage_session_succeeds_first_try():
    client = _ScriptedClient([json.dumps(VALID_OUTPUT)])
    result = triage_session(
        client, "system", {"session_id": "s1"}, KNOWN_VILLAINS, KNOWN_ATTACK_IDS
    )
    assert result.parse_failed is False
    assert result.repaired is False
    assert result.parsed["suspected_villain"] == "558-riddler"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


def test_triage_session_repairs_a_bad_first_response():
    client = _ScriptedClient(["not valid json", json.dumps(VALID_OUTPUT)])
    result = triage_session(
        client, "system", {"session_id": "s1"}, KNOWN_VILLAINS, KNOWN_ATTACK_IDS
    )
    assert result.parse_failed is False
    assert result.repaired is True
    assert result.parsed["suspected_villain"] == "558-riddler"
    assert len(client.calls) == 2
    assert "not valid json" in client.calls[1]  # repair prompt includes the bad response


def test_triage_session_sets_parse_failed_when_repair_also_fails():
    client = _ScriptedClient(["garbage", "still garbage"])
    result = triage_session(
        client, "system", {"session_id": "s1"}, KNOWN_VILLAINS, KNOWN_ATTACK_IDS
    )
    assert result.parse_failed is True
    assert result.repaired is False
    assert result.parsed is None
    assert result.error is not None


def test_triage_session_never_raises_on_transport_failure():
    client = _ScriptedClient([GroqError("connection reset"), GroqError("connection reset")])
    result = triage_session(
        client, "system", {"session_id": "s1"}, KNOWN_VILLAINS, KNOWN_ATTACK_IDS
    )
    assert result.parse_failed is True
    assert "Groq call failed" in result.error


def test_triage_session_records_prompt_version():
    client = _ScriptedClient([json.dumps(VALID_OUTPUT)])
    result = triage_session(
        client,
        "system",
        {"session_id": "s1"},
        KNOWN_VILLAINS,
        KNOWN_ATTACK_IDS,
        prompt_version="v1",
    )
    assert result.prompt_version == "v1"
