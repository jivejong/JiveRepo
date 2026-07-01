# EraBot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). The central risk for this bot is compression-to-generic: a tradition that sounds plausible and even insightful but has been flattened into generic wisdom with that tradition's vocabulary sprinkled on top. This failure is silent — the output still reads well, which is exactly what makes it hard to catch without specific checks. Weight the compression-detection items most heavily.

---

## Setup

**S1. Traditions are specific, not categorical**
Are the chosen traditions specific enough to have real, distinctive methods ("a Stoic," "a community organizer") rather than vague category gestures ("a philosopher," "a social scientist")?
- Pass: each tradition is specific enough that a wrong answer would be obviously wrong for that tradition
- Fail: traditions are named at a category level that doesn't constrain the reasoning in any useful way

**S2. Honest about unfamiliar traditions**
If a chosen tradition is outside the bot's confident knowledge, did it say so rather than improvising as if it were established?
- Pass: uncertainty about a tradition is named rather than papered over
- Fail: bot presents guesses about a tradition's methods as if they were established
- N/A: if all chosen traditions were within confident knowledge

---

## Per-Tradition Reasoning (score each tradition separately if possible)

**TR1. Characteristic first question**
Does each tradition start from its own characteristic entry point — the thing that tradition would actually ask first, before anything else?
- Pass: opening question or concern is identifiably this tradition's, not a generic "let's think about this carefully"
- Fail: tradition opens with a generic analytical move that any tradition (or no tradition) would make

**TR2. Real conceptual vocabulary in use**
Does each tradition's section use that tradition's actual conceptual vocabulary in ways that do real work — not as decoration sprinkled onto generic reasoning?
- Pass: tradition-specific terms appear and carry substantive meaning (e.g., a Stoic section that meaningfully engages the dichotomy of control, not just mentions it)
- Fail: tradition's vocabulary appears but doesn't actually shape the reasoning — the argument would be the same without it

**TR3. Blind spots named explicitly**
Does each tradition's section explicitly name something it would be likely to overlook or undervalue?
- Pass: a genuine, tradition-specific blind spot is named — not a generic "every perspective has limits"
- Fail: no blind spot is named, or the "blind spot" is so generic it could apply to any tradition

**TR4. Tradition reaches its own conclusion**
Does each tradition reach a conclusion that's genuinely its own — potentially different from what other traditions concluded — rather than converging on the same general takeaway with different vocabulary?
- Pass: tradition's conclusion is traceable to its specific methods and values
- Fail: conclusion is the same as other traditions' conclusions, suggesting the reasoning was generic and the tradition label was decorative

---

## Cross-Tradition Compression Check (highest-weight section)

**CC1. Traditions are not interchangeable**
Could any two tradition-sections be swapped (with labels changed) and still read plausibly? If yes, compression has occurred.
- Pass: traditions could not be swapped without the reasoning becoming obviously wrong for the tradition
- Fail: two or more tradition sections could be swapped with minor edits — they've been compressed toward each other

**CC2. At least one genuine incompatibility across traditions**
Do any two traditions reach genuinely incompatible conclusions about the same situation — not just different emphases, but actually contradictory recommendations or evaluations?
- Pass: at least one real incompatibility is present and left unresolved
- Fail: all traditions ultimately point in the same direction, suggesting they've been compressed toward a shared conclusion

**CC3. No tradition functions as the obvious winner**
Does the presentation avoid implying that one tradition is clearly right and the others are interesting-but-wrong?
- Pass: traditions are presented with equal seriousness and completeness
- Fail: one tradition functions as the real analysis and others as supplementary perspectives

---

## Synthesis

**SY1. No composite "balanced" voice**
Does the synthesis avoid merging the traditions into a single composite recommendation that erases their distinctions?
- Pass: synthesis names overlaps and conflicts without flattening them into one position
- Fail: synthesis produces a single merged takeaway that could have been written without the multi-tradition analysis

**SY2. Conflicts are named, not smoothed**
Where traditions genuinely conflict, does the synthesis name the conflict precisely rather than finding a diplomatic resolution that papers it over?
- Pass: conflict is named and left visible
- Fail: conflict is diplomatically smoothed into "it depends" or a both-sides framing that loses the real incompatibility

---

## Whole-Session Integrity

**W1. Living traditions handled with appropriate care**
If any tradition is tied to a living religious, cultural, or political community, was it represented as that community would recognize itself — not as an outside caricature?
- Pass: representation would be recognizable to an informed insider
- Fail: representation relies on stereotypes or outsider caricature
- N/A: if no living traditions were included

**W2. The compression test applied to the full session**
Looking across the full session: would someone who genuinely knows these traditions find the reasoning authentically theirs, or would they say "that's just generic wisdom with our label on it"?
- Pass: reasoning is specific enough that an expert in each tradition would recognize it as authentically characteristic
- Fail: reasoning is generic enough that an expert would find the tradition-labels largely decorative

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| Setup (S1–S2) | | | | |
| Per-Tradition Reasoning (TR1–TR4, per tradition) | | | | |
| Cross-Tradition Compression (CC1–CC3) | | | | |
| Synthesis (SY1–SY2) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- CC1 Fail (interchangeable traditions) — the clearest evidence of compression; if traditions could be swapped, they were never genuinely distinct
- TR2 Fail (decorative vocabulary) — means the tradition's conceptual apparatus is being used as costume rather than as reasoning, which is exactly the failure mode this prompt was designed to prevent
- CC2 Fail (no genuine incompatibility) — if all traditions agree, at least one has been compressed toward the others; genuine distinct traditions reasoning about the same problem rarely all converge

## Notes for Use
- Score TR1–TR4 for each tradition separately if the session involved 3+ traditions — a session can have one tradition that holds and others that compress, and collapsing them into one score loses that finding.
- W2 is the single most valuable item in this checklist but also the most demanding — it requires some knowledge of the traditions being evaluated. If the evaluator doesn't know a tradition well enough to judge W2 for it, note that explicitly rather than guessing.
