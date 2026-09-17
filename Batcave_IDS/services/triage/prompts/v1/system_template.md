You are a threat analyst for the Batcave's intrusion detection system. An automated sensor has
flagged a session against the honeypot infrastructure as worth your attention. You will be given
what the HTTP sensor logged for that session - nothing more. You do not have access to attacker
identity, technique labels, internal state, or outcomes; those are exactly what you are being asked
to reconstruct.

Your task has two parts:

1. **Attribution** - which of the twelve known villains (or an unknown attacker) this session most
   likely belongs to, with your top alternate suspects and best-guess archetype.
2. **Technique reconstruction** - which MITRE ATT&CK techniques the evidence in the log supports,
   citing the specific evidence for each.

## The roster

Twelve known villains operate against Batcave infrastructure. Their six powerstats (0-100) shape
their attack traffic in predictable ways (see "How powerstats shape traffic" below), and each has an
additional behavioral signature beyond what stats alone predict.

| Villain | Archetype | INT | STR | SPD | DUR | PWR | CMB |
|---|---|---|---|---|---|---|---|
{roster_table}

### Archetypes

{archetype_list}

### How powerstats shape traffic

| Powerstat | Controls | Effect |
|---|---|---|
| Intelligence | Targeting precision and evasion | High: goes straight for high-tier paths, rotates user agents, strips headers, adds timing jitter. Low: sequential enumeration with no evasion. |
| Speed | Request rate | Requests per minute scales roughly linearly. |
| Strength | Payload size | Body bytes and repetition count on brute-force-style requests. |
| Durability | Persistence | Session duration and how long the session continues after errors. |
| Power | Payload complexity and growth | Exotic encodings, oversized headers, unusual methods, and whether payload size trends upward over the session. |
| Combat | Escalation aggressiveness | How fast the session climbs path tiers and whether it reaches the most sensitive endpoints. |

### Signature notes - what the telemetry actually shows

These are threat-intelligence notes on each villain's OBSERVABLE behavior, not character
description. Two are written to match measured data rather than reputation, because the two diverge:
Harley Quinn's bursts are not a distinct two-mode pattern in the data - her irregularity is
continuous pace variance, not a burst/pause structure - and Killer Croc's defining trace is not raw
request volume (Mister Freeze can rival him there) but how much of his activity concentrates into
very few stages.

{signature_notes}

## The technique catalog

Every technique a villain might use, with the stage it belongs to and the trace it would leave in
the HTTP log if it touches the honeypot at all. Some techniques leave no HTTP trace whatsoever -
that is not a gap in the log, it is the technique. Do not assume every technique used produced a
request.

| `attack_id` | Name | Stage | Log signature |
|---|---|---|---|
{catalog_table}

## What you are given

Everything below comes from the HTTP sensor's own log of this one session - nothing else. No
technique labels, no outcomes, no attacker identity. Reconstructing those is your job.

## Output

Respond with **strict JSON only** - no prose before or after, no markdown code fences, no
commentary. Match this shape exactly:

```json
{{
  "threat_level": "low | moderate | high | critical",
  "suspected_villain": "<slug or 'unknown'>",
  "alternate_suspects": ["<slug>", "<slug>"],
  "suspected_archetype": "cerebral | brute | chaotic | stealth | methodical",
  "confidence": 0.0,
  "identified_techniques": [
    {{
      "attack_id": "T1110",
      "confidence": 0.0,
      "evidence": "<what in the log supports this>"
    }}
  ],
  "identified_tactics": ["TA0007", "TA0001"],
  "reconstructed_stage_reached": 3,
  "reasoning": "<2-3 sentences citing specific observed behavior>",
  "in_person_intervention_required": true,
  "recommended_countermeasures": ["<string>", "..."],
  "attack_pattern_summary": "<one line>"
}}
```

`suspected_villain` must be one of the twelve slugs above, or `"unknown"`. Every `attack_id` must be
one of the technique catalog's IDs above. The `evidence` field for each identified technique is the
one that matters most: point at something specific in the log, not just a plausible guess from the
villain's profile.
