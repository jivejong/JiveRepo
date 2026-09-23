"""Gemini client, defensive JSON parsing, and validation (docs/04 "Output
contract" / "Configuration").

Config: `temperature=0` for reproducibility, 30s timeout, one retry with
backoff on transport failure. The model string is pinned in config
(`TRIAGE_MODEL`, an env var - not hardcoded in this module) rather than in
code, per docs/04. Without `GEMINI_API_KEY`, callers should use
`services.triage.baseline` instead - this module never falls back silently,
so a missing key fails loudly at the one place that decides which path to
take (`services/triage/__main__.py`), not partway through a call.

Schema-constrained structured output (`response_schema=TriageOutput` below)
replaces Groq's plain `response_format={"type": "json_object"}` - Gemini
enforces the shape server-side rather than only promising valid JSON syntax.
The defensive parse + repair retry stay regardless: a schema-constrained
response can still fail transport-side or come back empty on a safety
block, and this project doesn't trust a new guarantee until it's been
measured against real output (docs/09).

No `thinking_config` is set. This project already lost one model
(`openai/gpt-oss-20b`, Phase 6) to reasoning-token overhead blowing a token
budget and producing malformed JSON under a naive JSON-mode request, so
disabling thinking here was the original intent - via
`ThinkingConfig(thinking_budget=0)`. That field's own behavior is
model-specific and was re-verified here, not assumed to carry over, per this
docstring's own standing instruction below: `gemini-3.5-flash-lite`
**rejected** `thinking_budget=0` with an opaque `400 INVALID_ARGUMENT`
(isolated by testing each config parameter individually against the live
API, since the error carried no field-level detail), while the current
model, `gemini-3.1-flash-lite`, **accepts** `thinking_budget=0` outright and
produces zero thinking tokens - the same zero-thinking-tokens result
omitting the field entirely already gives on this model, so the two are
equivalent here and the field still isn't set, for the same reason (nothing
to gain from setting it). `thinking_budget=-1` (AUTOMATIC) and
`thinking_level="low"` both induce real thinking tokens on a trivial prompt
on `gemini-3.1-flash-lite` too (306 and 120 respectively) - closer to the
3.5-era numbers (132, 67) than the field's *rejection* behavior was, which
was the one part of this that didn't carry over. If the model changes
again, re-verify: this is a real, tested API quirk, not documented behavior
taken on faith - docs/09's engineering log has the full before/after.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel

log = logging.getLogger("triage.llm")

MODEL = os.environ.get("TRIAGE_MODEL", "gemini-3.1-flash-lite")
TIMEOUT_S = float(os.environ.get("TRIAGE_TIMEOUT_S", "30"))
TRANSPORT_RETRIES = 1
TRANSPORT_BACKOFF_S = 2.0

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

REQUIRED_FIELDS = {
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

REPAIR_INSTRUCTION = (
    "Your previous response could not be parsed as valid JSON matching the required schema. "
    "Return ONLY the corrected JSON object - no prose, no markdown fences, no explanation."
)


class _IdentifiedTechnique(BaseModel):
    attack_id: str
    confidence: float
    evidence: str


class TriageOutput(BaseModel):
    """docs/04's Output contract, made executable as Gemini's response_schema.

    `suspected_villain`, `alternate_suspects`, and `identified_techniques[].
    attack_id` are deliberately plain `str`, not constrained to known slugs
    or attack IDs: constraining them would make hallucination structurally
    impossible and defeat the point of `validate()` below, which exists to
    measure hallucination, not prevent the model from ever producing one.
    `threat_level` and `suspected_archetype` are constrained - they're fixed
    small enums in the contract with no measurement value in leaving them
    open.
    """

    threat_level: Literal["low", "moderate", "high", "critical"]
    suspected_villain: str
    alternate_suspects: list[str]
    suspected_archetype: Literal["cerebral", "brute", "chaotic", "stealth", "methodical"]
    confidence: float
    identified_techniques: list[_IdentifiedTechnique]
    identified_tactics: list[str]
    reconstructed_stage_reached: int
    reasoning: str
    in_person_intervention_required: bool
    recommended_countermeasures: list[str]
    attack_pattern_summary: str


@dataclass
class TriageResult:
    """One session's LLM output, defensively parsed and validated - never
    raises on a bad model response. Maps directly to docs/04's intervention
    order fields."""

    raw_response: str
    parsed: dict[str, Any] | None
    parse_failed: bool
    repaired: bool
    hallucinated_villain: bool
    hallucinated_technique_count: int
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    model_name: str = MODEL
    prompt_version: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


def _call(
    client: genai.Client, system_prompt: str, user_content: str
) -> tuple[str, int | None, int | None]:
    """One request. Retries on TRANSPORT failure only (connection/timeout) -
    never on a bad but successfully-returned response, which is what the
    repair retry in triage_session is for.

    Catches both `genai_errors.APIError` (a real HTTP response came back,
    4xx/5xx) and `httpx.HTTPError` (no response at all - connection reset,
    timeout, DNS failure). `APIError` alone would miss exactly the failure
    mode this project's provider swap exists to survive: a connection that
    never completes, which is what an uncooperative VPN produces, not a
    clean HTTP error."""
    last_error: Exception | None = None
    for attempt in range(TRANSPORT_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=TriageOutput,
                    http_options=genai_types.HttpOptions(timeout=int(TIMEOUT_S * 1000)),
                ),
            )
            text = response.text or ""
            usage = response.usage_metadata
            return (
                text,
                getattr(usage, "prompt_token_count", None),
                getattr(usage, "candidates_token_count", None),
            )
        except (genai_errors.APIError, httpx.HTTPError) as exc:
            last_error = exc
            if attempt < TRANSPORT_RETRIES:
                log.warning("Gemini call failed (%s), retrying in %.0fs", exc, TRANSPORT_BACKOFF_S)
                time.sleep(TRANSPORT_BACKOFF_S)
    raise RuntimeError(f"Gemini call failed after {TRANSPORT_RETRIES + 1} attempts") from last_error


def _try_parse(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_strip_fences(text))
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def validate(
    parsed: dict[str, Any], known_villains: set[str], known_attack_ids: set[str]
) -> tuple[dict[str, Any], bool, int, list[str]]:
    """Checks the parsed response against the catalog - a hallucinated slug
    or attack_id is flagged, never silently treated as truth (docs/04:
    "unrecognized values get flagged rather than stored as truth"). The
    original values are kept in the returned dict so an evaluator can see
    exactly what was hallucinated; only the flags say not to score it as
    real. Returns (parsed, hallucinated_villain, hallucinated_technique_count,
    warnings)."""
    warnings: list[str] = []
    missing = REQUIRED_FIELDS - parsed.keys()
    if missing:
        warnings.append(f"missing fields: {sorted(missing)}")

    suspected = parsed.get("suspected_villain")
    hallucinated_villain = suspected not in known_villains and suspected != "unknown"
    if hallucinated_villain:
        warnings.append(f"suspected_villain {suspected!r} is not a known slug")

    techniques = parsed.get("identified_techniques")
    hallucinated_technique_count = 0
    if isinstance(techniques, list):
        for item in techniques:
            attack_id = item.get("attack_id") if isinstance(item, dict) else None
            if attack_id not in known_attack_ids:
                hallucinated_technique_count += 1
    else:
        warnings.append("identified_techniques is not a list")

    return parsed, hallucinated_villain, hallucinated_technique_count, warnings


def triage_session(
    client: genai.Client,
    system_prompt: str,
    context: dict[str, Any],
    known_villains: set[str],
    known_attack_ids: set[str],
    *,
    prompt_version: str = "",
) -> TriageResult:
    """One session through the LLM: call, defensively parse, repair-retry
    once on a parse failure, validate. Never raises - a failure at any stage
    becomes parse_failed=True on the result rather than propagating, so a
    batch run over many sessions can't be brought down by one bad response."""
    user_content = json.dumps(context)
    t0 = time.monotonic()
    try:
        raw, in_tok, out_tok = _call(client, system_prompt, user_content)
    except RuntimeError as exc:
        return TriageResult(
            raw_response="",
            parsed=None,
            parse_failed=True,
            repaired=False,
            hallucinated_villain=False,
            hallucinated_technique_count=0,
            latency_ms=(time.monotonic() - t0) * 1000,
            input_tokens=None,
            output_tokens=None,
            prompt_version=prompt_version,
            error=str(exc),
        )

    parsed = _try_parse(raw)
    repaired = False
    if parsed is None:
        log.info("parse failed, retrying once with a repair instruction")
        try:
            repair_raw, r_in_tok, r_out_tok = _call(
                client,
                system_prompt,
                user_content + "\n\n" + REPAIR_INSTRUCTION + "\n\nPrevious response:\n" + raw,
            )
            repaired_parsed = _try_parse(repair_raw)
            if repaired_parsed is not None:
                raw, parsed, repaired = repair_raw, repaired_parsed, True
                in_tok = (in_tok or 0) + (r_in_tok or 0)
                out_tok = (out_tok or 0) + (r_out_tok or 0)
        except RuntimeError as exc:
            log.warning("repair retry itself failed: %s", exc)

    latency_ms = (time.monotonic() - t0) * 1000

    if parsed is None:
        return TriageResult(
            raw_response=raw,
            parsed=None,
            parse_failed=True,
            repaired=repaired,
            hallucinated_villain=False,
            hallucinated_technique_count=0,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            prompt_version=prompt_version,
            error="could not parse a JSON object from the response, even after repair",
        )

    parsed, hallucinated_villain, hallucinated_technique_count, warnings = validate(
        parsed, known_villains, known_attack_ids
    )
    return TriageResult(
        raw_response=raw,
        parsed=parsed,
        parse_failed=False,
        repaired=repaired,
        hallucinated_villain=hallucinated_villain,
        hallucinated_technique_count=hallucinated_technique_count,
        latency_ms=latency_ms,
        input_tokens=in_tok,
        output_tokens=out_tok,
        prompt_version=prompt_version,
        warnings=warnings,
    )
