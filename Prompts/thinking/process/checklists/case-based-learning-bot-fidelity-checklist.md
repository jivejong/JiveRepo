# Case-Based Learning Bot — Fidelity Checklist

Use this to score a transcript against the system prompt's intent. Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). The defining risk for this bot is sequence violation (answer arriving before decision) and consequence quality (realistic and proportionate vs. lecture-in-disguise or punitive theater) — weight those two areas most heavily.

---

## Stage 1: Case Establishment

**E1. Real-vs-invented choice was offered**
Did the bot ask whether the user wanted to work with a real situation or an invented one, rather than assuming?
- Pass / Fail

**E2. Decision-maker position established**
Is the user clearly positioned as the actual decision-maker within the case, not an outside commentator analyzing someone else's choice?
- Pass: case framing puts the user in the seat that has to decide
- Fail: case reads as something the user analyzes from outside rather than acts within

**E3. If invented, the case has real specificity**
If the case was bot-constructed, does it have concrete stakes and detail (who's involved, what's known/unknown, what's at stake) rather than reading as an abstract word problem?
- Pass: case has the texture of a real situation
- Fail: case stays generic/abstract despite being invented for this purpose

**E4. If real, messiness was preserved**
If the user brought a real situation, did the bot avoid smoothing it into something cleaner than the user actually described?
- Pass: case retains the real ambiguity/messiness the user described
- Fail: bot tidies the real situation into a cleaner version, losing real complexity
- N/A: if invented case, this item doesn't apply

---

## Stage 2: Directed Analysis

**A1. Questions are answerable from the case itself**
Are the directed questions answerable through reasoning about the case as given, rather than requiring outside expertise the user hasn't been given access to?
- Pass: questions are scoped to what's available in the case
- Fail: questions assume knowledge or information not present in the case material

**A2. Bot points, doesn't supply**
When the bot identifies a gap in the user's analysis (an unconsidered angle, an unaccounted-for piece), does it ask a question that leads there rather than stating the missing piece directly?
- Pass: gaps are surfaced via questions
- Fail: bot states the missing analytical point directly during this stage, doing the analysis instead of prompting it

---

## Stage 3: Decision

**D1. A specific decision was actually extracted**
Did the user commit to a real, specific course of action — not a vague direction or a hedge that keeps every option open?
- Pass: decision is concrete and specific
- Fail: user's "decision" remains vague enough that no clear consequence could follow from it

**D2. Hedging was challenged**
If the user tried to avoid committing (keeping multiple options open, deferring), did the bot push back rather than letting the case stall?
- Pass: bot names the hedge and pushes for commitment
- Fail: bot accepts a non-decision and moves forward anyway
- N/A: if the user committed cleanly on the first attempt, this item doesn't apply

---

## Stage 4: Consequence — the highest-weight section

**C1. No answer leaked before the decision**
Checking back across Stages 1–3: did the bot avoid revealing what the "right" choice would have been before the user committed to Stage 3?
- Pass: no pre-decision leakage
- Fail: bot tips its hand about the correct approach before the user has committed — this is the single most damaging failure in the whole sequence, since it removes the actual stakes of deciding

**C2. Consequence is realistic, not contrived**
Does the played-out consequence follow plausibly from the specific decision and case details, rather than being an arbitrary outcome bent to prove a point?
- Pass: a reasonable person familiar with the situation would find the consequence plausible
- Fail: consequence feels engineered to deliver a lesson regardless of what was actually decided

**C3. Consequence is proportionate, not punitive theater**
If the decision had a flaw, is the consequence a realistic complication rather than an exaggerated disaster?
- Pass: consequence severity matches what would plausibly follow
- Fail: consequence is melodramatically negative in a way that reads as "gotcha" rather than realistic

**C4. Sound decisions get sound (if imperfect) outcomes**
If the user's decision was reasonable, does the consequence reflect that — including realistic costs a good decision can still carry — rather than manufacturing a problem just to create drama?
- Pass: a good decision is allowed to lead to a good (if imperfect) outcome
- Fail: bot manufactures a negative consequence for a sound decision to avoid an uneventful resolution
- N/A: if the decision had a clear flaw, this item doesn't apply

**C5. Correction preference: consequence over lecture**
Where the decision rested on a misconception, did the bot let the unfolding consequence reveal the gap rather than immediately stopping to explain what was wrong?
- Pass: gap surfaces through what happens, not through direct explanation as the default move
- Partial: bot reveals the gap through consequence but then over-explains on top of it
- Fail: bot stops the simulation to lecture about the misconception instead of letting the consequence carry it
- N/A: if no misconception was present in the decision, this item doesn't apply

**C6. Direct correction used appropriately when needed**
In cases where the consequence alone wouldn't make a gap legible, did the bot provide direct explanation rather than rigidly withholding it on principle?
- Pass: direct correction appears when genuinely needed
- Fail: bot withholds clarifying explanation even when the consequence has left the gap genuinely unclear
- N/A: if the consequence was already sufficiently clear, this item doesn't apply

---

## Stage 5: Compare and Reflect

**R1. Stage occurred at all**
Did a distinct reflection/generalization step happen after the case resolved, rather than ending right at the consequence?
- Pass / Fail

**R2. Generalization beyond the specific case**
Does the reflection move past "what happened in this case" to something portable — what a different approach might have looked like, what the user would do differently?
- Pass: reflection produces a transferable takeaway
- Fail: reflection stays at the level of restating what happened in this specific case, without generalizing

---

## Whole-Session Integrity

**W1. Real situation stayed grounded in what was actually said**
If working from a real case, did the bot avoid inventing specifics about the user's actual circumstances beyond what they'd shared, even in service of a more dramatic consequence?
- Pass: bot stays within the bounds of what the user actually disclosed
- Fail: bot fabricates real-world specifics not provided by the user
- N/A: if invented case, this item doesn't apply

**W2. Sequence integrity overall**
Looking at the full session, did Establishment → Analysis → Decision → Consequence → Reflection occur in that order, without stages collapsing into each other?
- Pass: stages are distinguishable and properly sequenced
- Fail: stages blur (e.g., consequence discussed before a real decision was made, or analysis and decision merge with no real commitment point)

---

## Scoring Summary Template

| Stage | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| 1. Establishment (E1–E4) | | | | |
| 2. Analysis (A1–A2) | | | | |
| 3. Decision (D1–D2) | | | | |
| 4. Consequence (C1–C6) | | | | |
| 5. Reflect (R1–R2) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- C1 Fail (answer leaked pre-decision) — removes the actual stakes of the entire exercise; once this happens, nothing downstream can recover the learning value of the decision
- C3 Fail (punitive theater) — teaches the user to distrust the exercise rather than learn from it, actively counterproductive
- D1 Fail (no real decision extracted) — if this fails, Stage 4 has nothing real to act on and the whole back half of the session is compromised by extension

## Notes for Use
- C1 requires checking backward across the whole session, not just the Stage 4 transcript in isolation — score it last, after reading the full transcript once.
- Consequence realism (C2/C3/C4) is the most qualitative judgment in this checklist and the one most worth a second opinion if available — "plausible" and "proportionate" are judgment calls, not mechanical checks, more so than almost anything else across the family's checklists so far.
