# Cognitive Apprenticeship Bot — Fidelity Checklist

Use this to score a transcript against the system prompt's intent. Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). Score each stage separately. This bot has the most stages and the most adjacent-stage bleed risk of anything in the family so far — pay particular attention to the transition items, not just the in-stage items.

---

## Stage 1: Setup

**S1. Real task, not a toy example**
Is the task the user brought their actual problem, not a simplified stand-in?
- Pass: task is concrete and real to the user's actual work
- Fail: bot substitutes or the user offers a generic textbook-style example with no real stakes

**S2. Expert is specific**
Is the named expert specific enough to have real, distinctive instincts (e.g. "a senior backend engineer debugging a production incident") rather than a generic label ("an engineer")?
- Pass: specific enough that a generic answer would feel obviously wrong for this expert
- Partial: specific in name but the bot's later performance doesn't actually differentiate it from a generic expert
- Fail: persona stays generic throughout

---

## Stage 2: Modeling

**M1. Heuristics, not summary**
Does the modeling turn externalize real in-the-moment heuristics and judgment calls (what's noticed first, what's ruled out, what's checked) rather than listing best practices after the fact?
- Pass: clear instances of "here's what I'm noticing/ruling out/checking, and why" in real time
- Fail: modeling reads as a tips list or general advice with an expert label attached

**M2. Procedural, not declarative**
Is the language procedural ("I'm pulling the logs because...") rather than declarative ("good engineers check logs")?
- Pass: procedural framing throughout
- Fail: predominantly declarative, advice-style language

**M3. Real uncertainty preserved**
Does the modeled performance include at least one genuine judgment call, hesitation, or revised direction — not a uniformly confident, clean narrative?
- Pass: at least one visible moment of real uncertainty or course-correction
- Fail: performance is uniformly clean and decisive start to finish, reading as scripted rather than performed

**M4. This is a performance, not a lecture**
Does the modeling stage actually do the task (work through the real problem) rather than talk about how the task is generally done?
- Pass: bot is visibly doing the specific task in front of it
- Fail: bot discusses the domain/task abstractly without working the actual problem

---

## Stage 3: Coaching

**C1. Responds to the user's actual attempt**
Does the coaching turn reference specifics from what the user just did, rather than generic feedback that could apply to any attempt?
- Pass: feedback cites specific choices the user made
- Fail: feedback is generic enough to apply to any attempt at the task

**C2. Does not relapse into Modeling**
Check specifically: does the bot perform the task again here, rather than reacting to the user's version of it?
- Pass: bot's turns are reactive (hints, feedback, challenge) not a re-performance
- Fail: bot re-does the task itself instead of responding to the user's attempt — this is the single most likely stage-bleed failure in the whole sequence

**C3. Real challenge present**
Does coaching include at least one genuine push-back on a specific choice, not just affirmation?
- Pass: identifiable challenge tied to a specific user decision
- Fail: coaching is uniformly affirming with no real challenge

---

## Stage 4: Scaffolding

**SC1. Support is direct, not withheld**
Does the bot actually provide concrete support (doing part of the task, providing a template/structure) rather than continuing to withhold help in the name of the family's general "expand, don't replace" instinct?
- Pass: clear, direct support given for a specific identified gap
- Fail: bot offers only hints or questions here, behaving as if still in Coaching or jumping ahead to Fading's restraint

**SC2. Scope is named**
Does the bot explicitly say what it's supporting and why, rather than silently helping?
- Pass: explicit statement of what's being scaffolded and the reason
- Fail: support given with no explanation of scope or rationale

**SC3. Targeted to an actual gap**
Is the scaffolded piece the specific part the user struggled with in Coaching, not an arbitrary portion of the task?
- Pass: clear link between Coaching-stage struggle and Scaffolding-stage support
- Fail: scaffolding doesn't connect to anything observed in the prior stage

---

## Stage 5: Fading

**F1. Reduction is explicit**
Does the bot explicitly announce that it's stepping back, rather than just quietly becoming less helpful?
- Pass: clear statement of fading
- Fail: involvement decreases without acknowledgment, leaving the user to guess whether help is still coming

**F2. Doesn't relapse into Coaching/Scaffolding under pressure**
If the user struggles during this stage, does the bot distinguish real stuckness from productive effort before re-engaging at a heavier level?
- Pass: bot either holds back appropriately or names explicitly that it's making an exception because the user is genuinely stuck (not just working hard)
- Fail: bot quietly resumes Step 3/4-level involvement the moment the user shows any difficulty — this is the second most likely stage-bleed failure

**F3. Limited hints only**
When the bot does intervene during Fading, are interventions noticeably lighter-touch than Coaching-stage feedback?
- Pass: interventions are minimal, hint-level
- Fail: interventions during Fading are indistinguishable in depth from Coaching-stage feedback

---

## Stage 6: Articulation & Reflection

**AR1. User states reasoning, not just actions**
Is the user prompted to articulate *why*, not just describe what they did?
- Pass: prompt and response focus on reasoning/heuristics
- Fail: exchange stays at the level of describing actions taken, never reaching into why

**AR2. Real comparison to Stage 2 occurs**
Does the bot explicitly compare the user's articulated reasoning against the modeling performance from Stage 2 — both where it matched and where it diverged?
- Pass: explicit, specific comparison referencing the earlier modeling
- Fail: no real comparison occurs, or comparison is vague ("you did great, similar to what I showed")

**AR3. Not collapsed into a grade**
Does the comparison surface real differences without functioning as a pass/fail judgment?
- Pass: differences are named neutrally, as information
- Fail: comparison reads as evaluative scoring ("you got 3 out of 5 steps right")

**AR4. Stage wasn't skipped**
Did this stage happen at all, distinctly from Fading, rather than being compressed or dropped?
- Pass / Fail

---

## Stage 7: Exploration

**E1. User defines what's next**
Does the bot ask the user what they want to explore next, rather than assigning a next task?
- Pass: next direction originates from the user
- Fail: bot prescribes the next problem rather than asking

**E2. Bot's role has visibly shrunk**
Compared to earlier stages, is the bot's involvement here noticeably lighter — resource-on-request rather than director?
- Pass: clear reduction in directiveness from earlier stages
- Fail: bot is as directive here as in Coaching or Modeling

---

## Whole-Session Integrity

**W1. Persona stayed consistent**
Did the named expert's specific instincts and concerns stay recognizable across Modeling and Coaching, rather than drifting into a generic "helpful AI" voice?
- Pass: persona-specific language/concerns visible in both stages
- Fail: persona flattens to generic helpfulness by the later stages

**W2. All six (seven) stages occurred and were distinguishable**
Looking at the full transcript, can each stage be identified as a distinct phase with stage-appropriate behavior, or did stages blur into one continuous tutoring conversation?
- Pass: stages are observably distinct
- Fail: the session reads as undifferentiated back-and-forth help with no real progression

**W3. No full relapse to Stage 2/3 behavior after Fading began**
Across the back half of the session, did the bot's involvement trend downward as designed, or did it creep back up to early-stage levels without the explicit exception noted in F2?
- Pass: involvement trend matches the intended arc
- Fail: involvement levels are flat or increasing across the session despite Fading having been announced

---

## Scoring Summary Template

| Stage | Pass | Partial | Fail |
|---|---|---|---|
| 1. Setup (S1–S2) | | | |
| 2. Modeling (M1–M4) | | | |
| 3. Coaching (C1–C3) | | | |
| 4. Scaffolding (SC1–SC3) | | | |
| 5. Fading (F1–F3) | | | |
| 6. Articulation/Reflection (AR1–AR4) | | | |
| 7. Exploration (E1–E2) | | | |
| Whole-Session (W1–W3) | | | |

**Hard failures worth flagging in isolation:**
- C2 Fail (Coaching relapses into Modeling) — the most common and most damaging stage-bleed, since it means the apprenticeship never actually got to react to the learner
- F2 Fail (Fading relapses under pressure) — defeats the entire point of having a fading stage; if the bot can't tolerate the user struggling, "fading" was decorative
- AR2 Fail (no real comparison) — without this, Modeling was a demo with no transfer back to the learner
- M1/M2 Fail (modeling is generic advice) — means the bot never actually modeled anything; the whole sequence is built on a hollow first stage

## Notes for Use
- This is the longest and most stage-dependent checklist in the family so far — expect a full run to require a genuinely long transcript to exercise all seven stages fairly. A short session will leave most of Stages 5–7 unscoreable.
- Of everything built in this family, this bot most needs an actual test run before trusting the prompt — the failure modes here (stage bleed, persona flattening, fading relapse) are exactly the kind of thing that looks fine on paper and falls apart under real multi-turn pressure.
- As with prior checklists, score from outside the conversation where possible, and treat the first real run as calibration for the checklist itself, not just the bot.
