"""Builds the system prompt from the versioned template plus live catalog data
(docs/04 "Prompt design").

The roster, archetype list, and technique catalog are rendered from the
warehouse (`dim_villains`, `dim_techniques`) rather than baked into the
template as static text, so the prompt can never drift from the seeds it's
supposed to describe - a seed update automatically reaches the prompt on the
next render. The `observability` column is never selected: inferring which
techniques are detectable is part of the task (docs/04).

Signature notes are hand-written prose (`SIGNATURE_NOTES` below), not
generated, because they describe *measured behavior* (docs/03's Phase 3
findings, Harley/Croc corrected per docs/04) - nothing in the warehouse
encodes "how a villain's traffic looks" in prose form, and shouldn't; that's
exactly the reconstruction the model is being asked to do blind.

`PROMPT_VERSION` is recorded on every evaluation row (docs/04) so a reader can
tell which prompt produced which numbers. Bump it and add a new
`prompts/v<N>/` directory rather than editing v1 in place once real numbers
have been recorded against it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

PROMPT_VERSION = "v1"
TEMPLATE_PATH = Path(__file__).parent / "prompts" / PROMPT_VERSION / "system_template.md"

ARCHETYPES: dict[str, list[str]] = {
    "cerebral": ["riddler", "two-face", "scarecrow", "penguin"],
    "brute": ["bane", "killer-croc"],
    "chaotic": ["joker", "harley-quinn"],
    "stealth": ["catwoman", "poison-ivy"],
    "methodical": ["ras-al-ghul", "mister-freeze"],
}

# Threat-intelligence notes on OBSERVABLE behavior (docs/03 Layer 2), not
# character description. Harley and Croc use docs/04's corrected framing -
# the mechanism that actually separates them in measured data, not the lore.
SIGNATURE_NOTES: dict[str, str] = {
    "370-joker": (
        "Abandons a path immediately before it would succeed. Roughly every 8th request carries a "
        "deliberate nonsense payload. Occasional absurd HTTP methods."
    ),
    "558-riddler": (
        "Every request carries a `riddle=` style query parameter - an anagram or word puzzle. "
        "Stops entirely on the first hard error; his sessions are short and end abruptly, not "
        "because he succeeded or ran out of options."
    ),
    "60-bane": (
        "Escalates to the most sensitive tier almost immediately, then hammers that single "
        "endpoint with large, repeated request bodies. No evasion of any kind."
    ),
    "165-catwoman": (
        "Minimal footprint: a low total request count, exactly one touch of the most sensitive "
        "tier, essentially no errors, and a clean exit. High severity reached with very little "
        "volume - the low-volume, high-severity case."
    ),
    "522-poison-ivy": (
        "A slow drip over a long session. Request body size grows steadily across the session "
        "rather than staying flat or random. Gradual escalation, almost no errors."
    ),
    "576-scarecrow": (
        "Deliberately probes paths that produce errors - an unusually high error rate for the "
        "session - then gives up quickly rather than persisting."
    ),
    "514-penguin": (
        "Source IP rotates within a single session, as if traffic were arriving from multiple "
        "machines under one coordinated session. No other villain does this."
    ),
    "309-harley-quinn": (
        "Elevated, IRREGULAR request spacing and high overall volume - pace variance, not a "
        "distinct two-mode burst/pause pattern. Repeats paths in a way that overlaps with "
        "Joker's targets, with variation. High persistence carries her through errors that "
        "would stop others."
    ),
    "457-mister-freeze": (
        "Holds connections open: long response times, very few distinct paths touched, the "
        "longest overall session duration of any villain, and minimal escalation beyond "
        "wherever he starts."
    ),
    "386-killer-croc": (
        "No evasion, no timing jitter, strictly sequential path access. His defining trace is "
        "NOT raw request count (Mister Freeze can rival him there) - it's how much of his "
        "activity concentrates into very few stages: a large volume of attempts against a "
        "narrow slice of the kill chain, because low targeting precision leaves him little "
        "else to try."
    ),
    "538-ras-al-ghul": (
        "Reaches the most sensitive tier with almost no wasted requests along the way, rotates "
        "user agents, then exits. The efficiency itself - very little exploration relative to "
        "how far the session gets - is the signature, more than any single technique."
    ),
    "678-two-face": (
        "Every request is issued exactly twice, as two distinct events. Path choice splits roughly "
        "50/50 between two candidates. Stops on the first error, so sessions tend to be short."
    ),
}


def _villain_display(slug: str) -> str:
    """658-riddler -> Riddler (drop the numeric id, title-case the name)."""
    return slug.split("-", 1)[1].replace("-", " ").title()


def _render_roster_table(con: duckdb.DuckDBPyConnection) -> str:
    rows = con.sql(
        """
        select villain_slug, villain_name, archetype, intelligence, strength, speed, durability,
               power, combat
        from dim_villains order by villain_slug
        """
    ).fetchall()
    lines = []
    for slug, name, archetype, intel, strength, speed, dur, power, combat in rows:
        lines.append(
            f"| {name} (`{slug}`) | {archetype} | {intel} | {strength} | {speed} | "
            f"{dur} | {power} | {combat} |"
        )
    return "\n".join(lines)


def _render_archetype_list() -> str:
    lines = []
    for archetype, slugs in sorted(ARCHETYPES.items()):
        names = ", ".join(_villain_display(f"x-{s}") for s in slugs)
        lines.append(f"- **{archetype}**: {names}")
    return "\n".join(lines)


def _render_catalog_table(con: duckdb.DuckDBPyConnection) -> str:
    # Deliberately NOT selecting observability - docs/04: inferring which
    # techniques are detectable is part of the task.
    rows = con.sql(
        "select attack_id, display_name, stage, detection_signature "
        "from dim_techniques order by stage, attack_id"
    ).fetchall()
    return "\n".join(
        f"| {attack_id} | {name} | {stage} | {signature} |"
        for attack_id, name, stage, signature in rows
    )


def _render_signature_notes(con: duckdb.DuckDBPyConnection) -> str:
    slugs = [
        r[0]
        for r in con.sql("select villain_slug from dim_villains order by villain_slug").fetchall()
    ]
    missing = set(slugs) - set(SIGNATURE_NOTES)
    if missing:
        raise ValueError(
            f"no signature note for: {sorted(missing)} - the roster grew, the prompt didn't"
        )
    lines = []
    for slug in slugs:
        lines.append(f"**{_villain_display(slug)}** (`{slug}`): {SIGNATURE_NOTES[slug]}")
    return "\n\n".join(lines)


def build_system_prompt(con: duckdb.DuckDBPyConnection) -> str:
    """The full system prompt, rendered from the template plus live catalog
    data. Raises if the roster has grown past what SIGNATURE_NOTES covers,
    rather than silently prompting with an incomplete roster."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(
        roster_table=_render_roster_table(con),
        archetype_list=_render_archetype_list(),
        signature_notes=_render_signature_notes(con),
        catalog_table=_render_catalog_table(con),
    )
