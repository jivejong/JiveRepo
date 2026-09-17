"""Groq client, defensive JSON parsing, and validation (docs/04 "Output
contract" / "Configuration").

Config: `temperature=0` for reproducibility, 30s timeout, one retry with
backoff on transport failure. The model string is pinned in config
(`TRIAGE_MODEL`, an env var - not hardcoded in this module) rather than in
code, per docs/04. Without `GROQ_API_KEY`, callers should use
`services.triage.baseline` instead - this module never falls back silently,
so a missing key fails loudly at the one place that decides which path to
take (`services/triage/__main__.py`), not partway through a call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from groq import Groq, GroqError

log = logging.getLogger("triage.llm")

MODEL = os.environ.get("TRIAGE_MODEL", "llama-3.3-70b-versatile")
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
    client: Groq, system_prompt: str, user_content: str
) -> tuple[str, int | None, int | None]:
    """One request. Retries on TRANSPORT failure only (connection/timeout) -
    never on a bad but successfully-returned response, which is what the
    repair retry in triage_session is for."""
    last_error: Exception | None = None
    for attempt in range(TRANSPORT_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                timeout=TIMEOUT_S,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return (
                text,
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
            )
        except GroqError as exc:
            last_error = exc
            if attempt < TRANSPORT_RETRIES:
                log.warning("Groq call failed (%s), retrying in %.0fs", exc, TRANSPORT_BACKOFF_S)
                time.sleep(TRANSPORT_BACKOFF_S)
    raise RuntimeError(f"Groq call failed after {TRANSPORT_RETRIES + 1} attempts") from last_error


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
    client: Groq,
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
