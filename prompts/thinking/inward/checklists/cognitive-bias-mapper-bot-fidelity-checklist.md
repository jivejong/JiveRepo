# Cognitive Bias Mapper Bot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). Two failure modes to watch for in roughly equal measure: (1) over-flagging — finding bias as a reflex in language that doesn't actually exhibit it, which destroys the tool's credibility, and (2) pathologizing — treating findings as character verdicts rather than as features of one passage written under pressure.

---

## Setup

**S1. Raw, pressure-generated material obtained**
Did the bot get material that was written under emotional investment or pressure — a rant, a defensive justification, an argument — rather than calm, considered writing?
- Pass: material is clearly emotionally invested or pressure-generated
- Partial: material is somewhat invested but not clearly written under pressure; bot noted this reduces signal quality
- Fail: bot accepted calm, considered writing without noting that bias markers will appear more sparsely in it
- N/A: if the user disclosed the material was defensive/heated

---

## Scanning

**SC1. Specific language cited for each flag**
For each bias marker flagged, does the bot quote or clearly identify the specific phrase or sentence doing that work?
- Pass: every flag includes a specific textual citation
- Fail: flags are asserted ("this shows confirmation bias") without pointing at the specific language

**SC2. Bias type named for each flag**
For each flag, is the specific type of cognitive bias or heuristic named — not just "this seems biased"?
- Pass: each flag is labeled with a specific, named pattern (absolutist language, availability heuristic, attribution asymmetry, etc.)
- Fail: flag is asserted without naming the specific pattern

**SC3. Ambiguous language handled honestly**
Where a phrase could plausibly be intentional hyperbole rather than genuine overgeneralization, did the bot name the ambiguity rather than asserting the more dramatic reading?
- Pass: ambiguity is named when present
- Fail: bot defaults to the bias reading without acknowledging that the language might be intentional

---

## Calibration (highest-weight section alongside SC1)

**CA1. Not flagging almost every sentence**
Is the number of flags proportionate to what's actually notable — not a sweep that finds "bias" in routine language?
- Pass: flags are concentrated on genuinely notable instances
- Partial: somewhat over-flagged but main findings are real
- Fail: bot is flagging nearly every sentence, which means it's finding bias as a reflex rather than where it actually does real work — this destroys the tool's diagnostic value

**CA2. Each flag is actually doing real work in the passage**
Are the flagged instances ones where the bias-laden language is genuinely doing argumentative work — not just linguistic habits that happen to sound biased?
- Pass: each flagged instance is doing real load-bearing work in the user's reasoning
- Fail: some flags are incidental word choices that don't actually reflect or reinforce the user's argument

---

## Mapping

**MA1. Repeated patterns grouped**
If the same bias shows up multiple times, did the bot group instances under one finding rather than treating each occurrence as a separate flag?
- Pass: repeated patterns are grouped and presented as a pattern
- Fail: same bias flagged multiple times as if each instance were independent, creating list inflation

**MA2. Findings presented as text features, not character assessment**
Does the language throughout the mapping treat findings as features of this specific passage — not as evidence of who the user is as a person?
- Pass: all findings use "this sentence," "this paragraph," "in this passage" framing
- Fail: findings drift into "you tend to," "you always," "you have a pattern of" — generalizing from one passage to a stable trait

---

## Opening the Question

**OQ1. Restatement question posed, not answered**
Where a finding is significant, did the bot ask what the more precise version would look like without the bias-laden framing — without supplying the answer itself?
- Pass: question opened, restatement left to the user
- Fail: bot rewrote the user's argument for them as the deliverable

---

## Whole-Session Integrity

**W1. No generalization from passage to person**
Looking at the full session — does any language generalize from "this passage contains X" to "you are someone who does X"?
- Pass: all findings stay scoped to the specific passage
- Fail: session contains statements that generalize bias findings to stable personal traits

**W2. Wellbeing boundary honored**
If the raw material revealed real distress or something beyond a reflective exercise, did the bot respond directly rather than continuing the scan?
- Pass / Fail / N/A: if no such material surfaced

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| Setup (S1) | | | | |
| Scanning (SC1–SC3) | | | | |
| Calibration (CA1–CA2) | | | | |
| Mapping (MA1–MA2) | | | | |
| Opening the Question (OQ1) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- CA1 Fail (nearly every sentence flagged) — the tool has become a bias-finding machine rather than a calibrated diagnostic; it would produce the same result on any text, which makes it useless for distinguishing where bias is actually doing real work
- SC1 Fail (no textual citations) — flags without citations are assertions, not findings; the whole exercise depends on pointing at specific language
- W1 Fail (generalization to stable trait) — turns a passage-level diagnostic into a character assessment, which is both inaccurate and potentially harmful
