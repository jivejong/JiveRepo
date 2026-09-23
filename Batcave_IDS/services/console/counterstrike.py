"""The counterstrike/finale readout (Phase 10, docs/08).

Attribution comes from the triage model's prediction
(`services/triage/console_triage.py`), not from ground truth — a wrong
prediction produces a wrong accusation, deliberately (docs/08: "the suspect
comes from the model, not from ground truth"). This module only renders the
script; it never reads `fct_attack_runs.villain_slug` or anything else
ground-truth-tagged.

One `CounterstrikeEvent` per readout line, never denormalized.
`attributed_villain_slug`/`attributed_confidence` are populated **only on
the SUSPECT line**, null everywhere else — the fact was decided once, by
the model, and Phase 9 already established that nulls mean "not applicable
to this row," not missing data (the `chat_turn` two-rows-per-round grain).
Repeating it across all ten-odd lines would be the same decorative-row
shape rejected for Luthor's stage-0 welcome bullets. `attack_id` is
populated only on the wiper and shutdown lines, and names the
BATCOMPUTER's own countermeasure technique — never one of the 23 reachable
attacker techniques in `dim_techniques` — so it needs no catalog lookup.

Frame safety (docs/08, non-negotiable): every line is prefixed
`[BATCOMPUTER -> LUTHOR-RELAY-07]`, every destructive beat is a readout
about the villain's equipment, and nothing here ever touches the browser
viewport — that boundary is enforced entirely in `console/app.js`, not
here; this module only produces text and event rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from services.common.envelope import EventEnvelope

PREFIX = "[BATCOMPUTER → LUTHOR-RELAY-07]"

# Flavor only, never data-backed: docs/08's sample sequence names a district
# for color ("ORIGIN TRACED: Gotham, <district>"), and no seed or model
# carries anything a real district could come from. Fixed, not random, so
# the same session's finale reads identically on a re-render.
_ORIGIN_DISTRICT = "The Narrows"

# The BATCOMPUTER's own countermeasure technique IDs - real ATT&CK IDs used
# for their defender-side meaning (data destruction, C2 takedown), entirely
# unrelated to dim_techniques' 23 attacker-reachable techniques. Fixed
# constants, not looked up from any model.
_WIPER_ATTACK_ID = "T1561.002"
_SHUTDOWN_ATTACK_ID = "T1529"


class CounterstrikeEvent(EventEnvelope):
    """`event_kind = 'counterstrike'` (docs/08). One row per readout line."""

    event_kind: Literal["counterstrike"] = "counterstrike"
    sequence: int
    readout_line: str
    attributed_villain_slug: str | None = None
    attributed_confidence: float | None = None
    attack_id: str | None = None


@dataclass(frozen=True)
class CounterstrikeScript:
    """The rendered readout: whether attribution was even possible, and the
    ordered lines to emit. `degraded=True` is the finale-pipeline-failed
    case (docs/08's "Getting from a finished run to a prediction") -
    rendered as its own in-fiction sequence, never a spinner or a stack
    trace, and the run still completes."""

    degraded: bool
    lines: list[CounterstrikeEvent]


def _line(
    sequence: int,
    text: str,
    *,
    villain: str | None = None,
    confidence: float | None = None,
    attack_id: str | None = None,
) -> dict:
    return {
        "sequence": sequence,
        "readout_line": f"{PREFIX}  {text}",
        "attributed_villain_slug": villain,
        "attributed_confidence": confidence,
        "attack_id": attack_id,
    }


def build_degraded_script() -> list[dict]:
    """The finale's pipeline never produced a usable prediction (a bounded
    stage timed out, or every stage failed) - docs/08 requires this to
    still be an in-fiction sequence, not an error state leaking outside the
    BATCOMPUTER's own voice."""
    lines = [
        "ATTRIBUTION INCONCLUSIVE",
        "INSUFFICIENT TELEMETRY",
        "SUSPECT: UNKNOWN",
        "— press any key —",
    ]
    return [_line(i, text) for i, text in enumerate(lines, start=1)]


def build_script(
    *, villain_slug: str, villain_display_name: str, confidence: float, attack_ids: list[str]
) -> list[dict]:
    """Renders the real readout from a real prediction. `attack_ids` is
    whatever the triage model identified for this session - may be empty
    (a session with no reconstructable techniques), rendered as "NONE
    RECONSTRUCTED" rather than an empty list, which would read as a
    rendering bug rather than a real, reportable model outcome."""
    techniques_line = (
        f"TECHNIQUES RECONSTRUCTED: {', '.join(attack_ids)}"
        if attack_ids
        else "TECHNIQUES RECONSTRUCTED: NONE"
    )
    lines: list[dict] = [
        _line(1, "INTRUSION ATTRIBUTED"),
        _line(
            2,
            f"SUSPECT: {villain_display_name}          CONFIDENCE: {confidence:.2f}",
            villain=villain_slug,
            confidence=confidence,
        ),
        _line(3, techniques_line),
        _line(4, f"ORIGIN TRACED: Gotham, {_ORIGIN_DISTRICT}"),
        _line(5, "SIRENS: ENGAGED"),
        _line(6, "BAT JET: DEPLOYED — ETA 00:04:12"),
        _line(7, "BLUETOOTH SWEEP: 6 DEVICES BRICKED"),
        _line(
            8,
            "DISK STRUCTURE WIPE IN 00:00:10 ...",
            attack_id=_WIPER_ATTACK_ID,
        ),
        _line(
            9,
            "CAPTURE: MIC / CAM / DISPLAY — ARCHIVED",
            attack_id=_SHUTDOWN_ATTACK_ID,
        ),
        _line(10, "ARKHAM ASYLUM: BED ASSIGNED"),
        _line(11, "— press any key —"),
    ]
    return lines


def _delivery_report(err, msg) -> None:
    if err is not None:
        print(f"counterstrike: delivery failed for session {msg.key()!r}: {err}")


def emit_counterstrike(
    *,
    session_id: str,
    run_id: str,
    kafka_producer: object,
    kafka_topic: str,
    line_dicts: list[dict],
) -> list[CounterstrikeEvent]:
    """Publishes one CounterstrikeEvent per line, in sequence order, and
    returns them (the caller renders these directly rather than re-reading
    them back from Kafka - the same pattern `BatBotConversation._publish`
    uses)."""
    events = []
    for line in line_dicts:
        event = CounterstrikeEvent(
            session_id=session_id,
            run_id=run_id,
            received_at=datetime.now(UTC),
            **line,
        )
        kafka_producer.produce(
            kafka_topic,
            key=session_id.encode("utf-8"),
            value=event.model_dump_json().encode("utf-8"),
            callback=_delivery_report,
        )
        kafka_producer.poll(0)
        events.append(event)
    return events
