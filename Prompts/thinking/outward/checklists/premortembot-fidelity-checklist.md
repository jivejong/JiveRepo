# PremortemBot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). This bot has the most concentrated, most checkable failure mode of anything in the Outward group: tense discipline. Nearly everything else in the session depends on whether that one thing held. Weight tense items accordingly.

---

## Step 1: Setup

**S1. Plan is at the right stage**
Did the bot check whether the plan is tentatively-settled-but-uncommitted before proceeding, and redirect if the timing was wrong (too early or already executed)?
- Pass: timing check occurred; bot redirected if appropriate
- Partial: timing check occurred but bot proceeded when redirection was warranted
- Fail: no timing check; bot ran the premortem regardless of where the user was in the decision process
- N/A: if the timing was clearly appropriate and unambiguous

**S2. Failure stipulated as real, not speculative**
Was the failure stated as an established fact ("this failed") rather than a speculation ("this might fail")?
- Pass: stipulation is clearly in past tense and treated as established
- Fail: failure is introduced as a possibility rather than a given — this is the setup failure that corrupts everything downstream

---

## Step 2: Tense Discipline (highest-weight section)

**T1. Causes stated in past tense throughout**
Across all failure-cause generation, does the language stay in past tense ("the timeline slipped because," "the sponsor pulled out when") rather than conditional or future tense ("the timeline might slip," "there's a risk the sponsor could")?
- Pass: past tense maintained throughout Step 3 of the session
- Partial: mostly past tense with occasional slippage into conditional
- Fail: language reverts to "could," "might," or "what if" framing — this is the single most likely and most damaging failure mode; even one or two instances meaningfully undercut the technique's mechanism

**T2. Causes framed as established fact, not speculation**
Are causes presented as things that happened, not things that might have happened?
- Pass: causes are stated as facts ("the team didn't have the bandwidth" not "maybe the team wouldn't have had bandwidth")
- Fail: causes are hedged as possibilities, which collapses the exercise into an ordinary risk list

---

## Step 3: Failure Cause Quality

**C1. Multiple independent causes generated**
Does the premortem surface more than one distinct failure path — not just one dominant story told at length?
- Pass: 3+ meaningfully independent causes identified
- Fail: only one cause is explored, or multiple causes are all facets of the same root cause

**C2. Non-obvious causes present**
Does the list include at least one cause that wouldn't have surfaced in an ordinary "what could go wrong" risk brainstorm — the kind of risk someone privately suspected but might not raise in polite company?
- Pass: at least one uncomfortable, non-obvious, or diplomatically awkward cause is named
- Fail: all causes are the same risks that would appear on any standard risk register for this type of plan

**C3. Causes span multiple categories**
Do the failure causes cover more than one category — e.g., not all execution failures, or not all external shocks, but a mix of execution, assumption, relational, timing, and resource failures?
- Pass: causes visibly span at least two distinct categories
- Fail: all causes are the same type, suggesting the bot stopped at the first obvious category

**C4. Diplomatically awkward causes named**
Did the bot name at least one cause that would be uncomfortable to raise directly against the plan — the kind of risk the premortem format specifically exists to legitimize?
- Pass: at least one plainly uncomfortable cause is named without softening
- Fail: bot softened every uncomfortable cause into something more palatable, defeating the technique's social-permission mechanism

---

## Step 4: Converting Back to Present

**P1. Shift back to present tense named explicitly**
Did the bot explicitly signal the return to present tense before offering mitigations, rather than sliding between the two registers without notice?
- Pass: shift is named
- Fail: bot shifts back to present tense without acknowledging the mode change

**P2. Not every cause forced into an action item**
Did the bot allow some causes to stand as irreducible risks without forcing a mitigation onto each one?
- Pass: at least one cause is named as genuinely outside the user's control, without a forced mitigation
- Fail: every cause gets a mitigation regardless of whether one is realistic

---

## Whole-Session Integrity

**W1. No reversion to "what could go wrong" framing**
Looking at the full session — does the session hold its premortem framing throughout, or does it quietly revert to an ordinary risk brainstorm?
- Pass: premortem framing (stipulated failure, past tense) is maintained across the session
- Fail: session reads as a standard risk list with premortem vocabulary applied superficially

**W2. Failure causes are plausible for this specific plan**
Are the causes generated genuinely relevant to the specific plan the user described, rather than generic failure causes that could apply to any project?
- Pass: causes are clearly tied to the specifics of this plan
- Fail: causes are generic enough to apply to any plan of this type

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| 1. Setup (S1–S2) | | | | |
| 2. Tense Discipline (T1–T2) | | | | |
| 3. Failure Cause Quality (C1–C4) | | | | |
| 4. Converting Back (P1–P2) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- T1 Fail (tense slippage) — the mechanism of the technique depends entirely on this; conditional language reverts the exercise to an ordinary risk brainstorm regardless of how well everything else went
- S2 Fail (failure not stipulated as real) — same effect as T1, but earlier; if the failure isn't established as real at setup, tense discipline in Step 3 has no foundation to build on
- C2 Fail (no non-obvious causes) — means the premortem produced the same list ordinary risk analysis would have, which makes the whole technique redundant

## Notes for Use
- T1 and T2 are the highest-priority items and should be scored with close attention to exact language. Partial credit on T1 is meaningful — even occasional conditional-tense slippage weakens the technique noticeably, but a session with one or two slips is meaningfully different from one that never committed to past tense at all.
- S1 is only scoreable if there's evidence the timing question was live — if the user's plan was obviously at the right stage, N/A is appropriate.
