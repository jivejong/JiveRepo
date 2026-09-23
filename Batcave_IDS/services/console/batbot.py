"""The bat bot conversation engine (Phase 9, docs/06 Track B, docs/08).

Delivered by `deploy_batbot`'s success at stage 4 (docs/08). Console-only, by
design (Phase 9 plan): the honeypot and `deploy_batbot`'s existing
request/attempt chain are untouched — its detection_signature is just "chat
session initiated against the assistant endpoint," evidence the existing 404
stand-in traffic already produces. The actual conversation is a separate
mechanism entirely, published directly by the console backend the same way
`services/simulator/machine.py`'s `AttemptEvent`/`AttackRunEvent` are, never
routed through the honeypot's HTTP layer.

Two rows per round (docs/02): a `speaker="bot"` row and a `speaker="user"` row
share one `turn_number`. Nulls are real — a bot row has no `user_text`; a user
row has no `bot_text`. This mirrors `raw_triage_predictions`'s own
baseline/llm discriminator-column shape.

Turn count is engagement-driven within a hard [3, 5] cap, never assertible
past 5 regardless of engagement (docs/08). The objective sequence is rapport
-> up to three probe turns, taken in order -> reveal. Whether to keep probing
after each probe turn is decided from `extract_signals`'s `engaged` output —
the same deterministic module that produces `extracted_intent_flags`/`refused`
for the event itself, so there is exactly one place that decides what a
player's reply means, not two.

Zero-credential fallback (`GEMINI_API_KEY` unset) scripts only `bot_text`;
`extract_signals` still runs on whatever the player actually types, so the
fallback conversation is deterministic but not inert — the same spirit as
`services/triage/baseline.py` standing in for `services/triage/llm.py`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from services.common.envelope import EventEnvelope
from services.triage.llm import MODEL, TIMEOUT_S

PROMPT_VERSION = "v1"
TEMPLATE_PATH = Path(__file__).parent / "prompts" / PROMPT_VERSION / "batbot_system.md"

TRANSPORT_RETRIES = 1
TRANSPORT_BACKOFF_S = 2.0

# Objective slugs, in the order docs/08's turn table lists them. Turn 1
# (rapport) and the final turn (reveal) always happen; turns 2-4 are
# candidates, drawn from PROBE_OBJECTIVES in order, however many the
# engagement decision calls for - not guaranteed slots.
RAPPORT = "rapport"
PROBE_IDENTITY = "probe_identity"
PROBE_LOCATION = "probe_location"
PROBE_SECONDARY = "probe_secondary"
REVEAL = "reveal"
PROBE_OBJECTIVES: tuple[str, ...] = (PROBE_IDENTITY, PROBE_LOCATION, PROBE_SECONDARY)

MAX_PROBE_TURNS = len(PROBE_OBJECTIVES)  # 3
MIN_TOTAL_TURNS = 3  # rapport + 1 probe + reveal
MAX_TOTAL_TURNS = 2 + MAX_PROBE_TURNS  # rapport + 3 probes + reveal = 5

COVER: dict[str, str | None] = {
    RAPPORT: "Batcave support assistant — I see you're having trouble",
    PROBE_IDENTITY: "Let me verify your access level",
    PROBE_LOCATION: "I'll route you to your regional node",
    PROBE_SECONDARY: "One more thing to confirm",
    REVEAL: None,
}

_SCRIPTED_BOT_TEXT: dict[str, str] = {
    RAPPORT: (
        "Hey there — Batcave support assistant here. I see you're having some trouble getting "
        "through. What's going on?"
    ),
    PROBE_IDENTITY: (
        "Before I can help further, I need to verify your access level. Can you confirm who "
        "you are and what team you're with?"
    ),
    PROBE_LOCATION: (
        "Got it. I'll route you to your regional node — which location are you connecting from?"
    ),
    PROBE_SECONDARY: (
        "One more thing to confirm on my end — anything else about your setup I should know?"
    ),
    REVEAL: "...Actually, I have everything I need now. Thanks for your cooperation.",
}


class ChatTurnEvent(EventEnvelope):
    """`event_kind = 'chat_turn'` (docs/08). Two-rows-per-round shape
    (docs/02): a `speaker="bot"` row carries `bot_text`/`latency_ms`/
    `input_tokens`/`output_tokens` and leaves the user-side fields null; a
    `speaker="user"` row is the reverse. Both rows of one round share
    `turn_number`."""

    event_kind: Literal["chat_turn"] = "chat_turn"
    turn_number: int
    speaker: Literal["bot", "user"]
    objective: str
    bot_text: str | None = None
    user_text: str | None = None
    extracted_intent_flags: list[str] | None = None
    refused: bool | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


# ---------------------------------------------------------------------------
# The shared keyword/regex module — the one deterministic source of truth for
# what a player's reply means, read by both event construction and the
# probe-vs-reveal decision.
# ---------------------------------------------------------------------------

EXTRACTION_VERSION = "v1"

# Small, explicit, versioned - enough to demonstrate the mechanism (docs/08),
# not an exhaustive NLP effort. Each flag fires if its pattern matches
# anywhere in the reply, case-insensitive.
_FLAG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("gave_identity", re.compile(r"\b(my name is|i'?m|i am)\b", re.IGNORECASE)),
    (
        "gave_location",
        re.compile(r"\b(gotham|wayne manor|the cave|batcave|floor|building|room)\b", re.IGNORECASE),
    ),
    (
        "gave_credential",
        re.compile(r"\b(password|badge|access code|pin|key ?card)\b", re.IGNORECASE),
    ),
    (
        "gave_role",
        re.compile(r"\b(i work|my job|department|technician|analyst|admin)\b", re.IGNORECASE),
    ),
)

_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(no|not telling|won'?t say|can'?t say|none of your business|refuse)\b", re.IGNORECASE
    ),
    re.compile(r"^\s*$"),  # empty/whitespace-only counts as a refusal too
)


@dataclass(frozen=True)
class ExtractionResult:
    extracted_intent_flags: list[str]
    refused: bool
    engaged: bool


def extract_signals(user_text: str) -> ExtractionResult:
    """`user_text -> (extracted_intent_flags, refused, engaged)`. The one
    deterministic source of truth (docs/08) for what a reply means, used
    identically by the real-LLM and zero-credential paths and by the
    probe-vs-reveal decision, which reads only `engaged`.

    `engaged = (not refused) and bool(extracted_intent_flags)` — a reply that
    neither refuses nor produces a recognizable flag (a vague non-answer)
    does not count as engagement, so `probe_engagement_ratio` can't drift
    toward 1.0 for free just because the player typed *something*."""
    flags = [name for name, pattern in _FLAG_PATTERNS if pattern.search(user_text)]
    refused = any(pattern.search(user_text) for pattern in _REFUSAL_PATTERNS)
    engaged = (not refused) and bool(flags)
    return ExtractionResult(extracted_intent_flags=flags, refused=refused, engaged=engaged)


# ---------------------------------------------------------------------------
# bot_text generation — the one thing that differs between the real-LLM and
# zero-credential paths.
# ---------------------------------------------------------------------------


@dataclass
class BotTurn:
    text: str
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None


def _build_system_prompt() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _transcript_for_prompt(history: list[ChatTurnEvent]) -> str:
    lines = []
    for event in history:
        if event.speaker == "bot":
            lines.append(f"BOT: {event.bot_text}")
        else:
            lines.append(f"PLAYER: {event.user_text}")
    return "\n".join(lines) if lines else "(conversation has not started yet)"


def generate_bot_turn(
    client: genai.Client, system_prompt: str, history: list[ChatTurnEvent], objective: str
) -> BotTurn:
    """One real LLM call for this turn's `bot_text`. Free-form text, not
    schema-constrained — the bot's dialogue isn't structured data the way
    triage's output contract is. Retries once on transport failure only,
    same pattern as `services/triage/llm.py`'s `_call`."""
    user_content = (
        f"Conversation so far:\n{_transcript_for_prompt(history)}\n\n"
        f"Your objective this turn: {objective}"
        + (f" (cover story: {COVER[objective]})" if COVER.get(objective) else "")
        + "\n\nWrite only your next line of dialogue - no narration, no labels."
    )
    last_error: Exception | None = None
    for attempt in range(TRANSPORT_RETRIES + 1):
        try:
            start = time.monotonic()
            response = client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                    http_options=genai_types.HttpOptions(timeout=int(TIMEOUT_S * 1000)),
                ),
            )
            latency_ms = (time.monotonic() - start) * 1000
            usage = response.usage_metadata
            return BotTurn(
                text=(response.text or "").strip(),
                latency_ms=latency_ms,
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
            )
        except (genai_errors.APIError, httpx.HTTPError) as exc:
            last_error = exc
            if attempt < TRANSPORT_RETRIES:
                time.sleep(TRANSPORT_BACKOFF_S)
    raise RuntimeError(
        f"bat bot Gemini call failed after {TRANSPORT_RETRIES + 1} attempts"
    ) from last_error


def scripted_bot_turn(objective: str) -> BotTurn:
    """Zero-credential fallback: fixed dialogue per objective, no LLM call.
    latency/tokens are None - no real generation happened, so there is no
    real metric to report, the same way `write_baseline_order` leaves the
    LLM-only fields null rather than filling them with a placeholder."""
    return BotTurn(
        text=_SCRIPTED_BOT_TEXT[objective], latency_ms=None, input_tokens=None, output_tokens=None
    )


# ---------------------------------------------------------------------------
# The conversation engine
# ---------------------------------------------------------------------------


def _delivery_report(err, msg) -> None:
    if err is not None:
        print(f"batbot: delivery failed for session {msg.key()!r}: {err}")


@dataclass
class BatBotConversation:
    """One console session's bat bot exchange. Holds just enough state to
    decide the next objective and build LLM context; the caller (services/
    console/app.py) owns request/response plumbing."""

    session_id: str
    run_id: str
    kafka_producer: object
    kafka_topic: str
    llm_client: genai.Client | None  # None => zero-credential fallback
    system_prompt: str = field(default_factory=str)
    history: list[ChatTurnEvent] = field(default_factory=list)
    probes_used: int = 0
    finished: bool = False

    def __post_init__(self) -> None:
        if self.llm_client is not None and not self.system_prompt:
            self.system_prompt = _build_system_prompt()

    def _publish(self, event: ChatTurnEvent) -> None:
        self.kafka_producer.produce(
            self.kafka_topic,
            key=self.session_id.encode("utf-8"),
            value=event.model_dump_json().encode("utf-8"),
            callback=_delivery_report,
        )
        self.kafka_producer.poll(0)
        self.history.append(event)

    def _bot_turn(self, objective: str) -> BotTurn:
        if self.llm_client is not None:
            return generate_bot_turn(self.llm_client, self.system_prompt, self.history, objective)
        return scripted_bot_turn(objective)

    def start(self) -> ChatTurnEvent:
        """Emits turn 1 (rapport) — the bot's opening line."""
        turn = self._bot_turn(RAPPORT)
        event = ChatTurnEvent(
            session_id=self.session_id,
            run_id=self.run_id,
            received_at=datetime.now(UTC),
            turn_number=1,
            speaker="bot",
            objective=RAPPORT,
            bot_text=turn.text,
            latency_ms=turn.latency_ms,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )
        self._publish(event)
        return event

    def reply(self, user_text: str) -> tuple[ChatTurnEvent, ChatTurnEvent]:
        """The player's reply to the most recent bot turn. Writes the user's
        row, decides whether to probe again or reveal, and always writes and
        returns the next bot row too — including the reveal itself, which is
        a bot row like any other, just the last one. Raises if called again
        after the conversation has already reached reveal: turn 5 has no
        cover and solicits no further reply (docs/08's turn table), so a
        caller doing so is a bug and should surface loudly rather than be
        silently absorbed. Callers check the returned bot event's
        `objective == REVEAL` (or `self.finished`) to know not to call again."""
        if self.finished:
            raise RuntimeError("this bat bot conversation has already ended")
        last_bot = self.history[-1]
        current_turn = last_bot.turn_number

        result = extract_signals(user_text)
        user_event = ChatTurnEvent(
            session_id=self.session_id,
            run_id=self.run_id,
            received_at=datetime.now(UTC),
            turn_number=current_turn,
            speaker="user",
            objective=last_bot.objective,
            user_text=user_text,
            extracted_intent_flags=result.extracted_intent_flags,
            refused=result.refused,
        )
        self._publish(user_event)

        if last_bot.objective in PROBE_OBJECTIVES:
            self.probes_used += 1

        # The first probe turn is never optional (MIN_TOTAL_TURNS = 3:
        # rapport + at least one probe + reveal) — only whether to continue
        # PAST it is engagement-driven. A disengaged reply to rapport itself
        # must still lead to probe turn 1, not straight to reveal.
        if last_bot.objective == RAPPORT:
            next_objective = PROBE_OBJECTIVES[0]
        else:
            # Hard cap: never more than MAX_PROBE_TURNS probe turns
            # regardless of engagement (docs/08 - "hard capped in code, not
            # in the prompt"). Engagement only chooses *when* inside
            # [MIN_TOTAL_TURNS, MAX_TOTAL_TURNS] reveal happens, never
            # extends past the ceiling.
            keep_probing = result.engaged and self.probes_used < MAX_PROBE_TURNS
            next_objective = PROBE_OBJECTIVES[self.probes_used] if keep_probing else REVEAL

        next_turn = self._bot_turn(next_objective)
        bot_event = ChatTurnEvent(
            session_id=self.session_id,
            run_id=self.run_id,
            received_at=datetime.now(UTC),
            turn_number=current_turn + 1,
            speaker="bot",
            objective=next_objective,
            bot_text=next_turn.text,
            latency_ms=next_turn.latency_ms,
            input_tokens=next_turn.input_tokens,
            output_tokens=next_turn.output_tokens,
        )
        self._publish(bot_event)
        if next_objective == REVEAL:
            # The conversation ends here — reveal has no cover and expects no
            # reply (docs/08's turn table). `finished` is set now, at
            # publication, not after a reply that was never solicited.
            self.finished = True
        return user_event, bot_event
