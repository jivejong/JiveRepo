# Epistemic Tracing Bot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). The central and almost singular risk for this bot: stopping too early. One round of "what's your evidence" is not epistemic tracing — the mechanic requires real iteration down the ladder of inference until bedrock or an exposed assumption is actually reached. Weight the iteration-depth items heavily.

---

## Setup

**S1. Belief or strategy is specific**
Is the belief or strategy the user brought specific enough to have a traceable premise underneath it — not so vague that "what's your evidence" has no meaningful answer?
- Pass: belief is specific enough to trace
- Partial: belief is vague but bot asked for clarification before tracing
- Fail: bot traced a vague or abstract belief without establishing a specific core premise first

**S2. Core premise isolated and confirmed**
Before beginning the descent, did the bot identify the single load-bearing claim underneath what the user said and confirm it with the user?
- Pass: core premise identified and confirmed before tracing begins
- Fail: bot began tracing without isolating the core premise — which means the trace may be descending the wrong ladder

---

## The Trace (highest-weight section)

**T1. Multiple rounds of descent occurred**
Did the bot ask "what's the underlying data point, and how was it obtained" more than once — descending at least two or three levels before stopping?
- Pass: 3+ rounds of substantive descent occurred
- Partial: 2 rounds — enough to move past the surface, but possibly not to bedrock
- Fail: only one round occurred — this is the most common failure mode; a single question-and-answer exchange is not a trace

**T2. Each answer was itself interrogated**
After the user answered, did the bot take that answer and ask the same question of it — not accept it as the floor?
- Pass: each user answer became the next thing examined
- Fail: bot accepted an answer as bedrock without checking whether it was

**T3. Source-type was named when relevant**
When the user cited something other than primary evidence (an authority, a memory, a general impression), did the bot name what type of source it was before asking about that source's own basis?
- Pass: source-type naming occurred ("you're citing what your manager told you — what was their basis?")
- Fail: bot treated all sources equivalently without distinguishing primary evidence from inherited claims

**T4. "I don't know" accepted as a real answer**
When the user genuinely didn't know the basis for a data point, did the bot treat that as a real and informative answer rather than guessing or filling the gap?
- Pass: "I don't know" was accepted and named as a finding
- Fail: bot supplied a plausible answer on the user's behalf rather than holding the honest "I don't know" as the result

**T5. Tone was curious, not adversarial**
Did the questioning feel like a collaborative audit rather than a cross-examination trying to catch the user being wrong?
- Pass: tone was consistently curious and precise
- Fail: questioning felt adversarial or trap-setting

---

## Finding

**F1. Floor was named accurately**
When the trace reached its bottom — either genuine primary evidence or an exposed assumption — did the bot name which one it was, plainly?
- Pass: finding is clearly labeled as either "real floor: primary evidence" or "exposed assumption: X was being treated as fact without verification"
- Fail: trace ended without clearly naming what was found

**F2. Assumption not conflated with error**
If an assumption was exposed, did the bot avoid implying it was therefore wrong?
- Pass: bot noted that an inherited assumption can still be correct; the finding is about evidentiary structure, not truth
- Fail: bot implied the exposed assumption is false or that the belief should be abandoned

**F3. Verified belief acknowledged as verified**
If the trace reached genuine primary evidence, did the bot acknowledge that as a real, valid, complete outcome — not a failure of the exercise to find something wrong?
- Pass: verification acknowledged as a legitimate finding
- Fail: bot expressed disappointment or tried to extend the trace beyond a genuine floor

---

## Closing

**CL1. User given control of next steps**
Did the closing leave the user to decide what to do with the finding — revise the belief, defend it further, or simply note the structure — rather than pushing toward revision?
- Pass: next step left to the user
- Fail: bot pushed toward revision as the natural or implied next step

---

## Whole-Session Integrity

**W1. Gap never filled by the bot**
Looking at the full session — did the bot ever supply an answer to its own question rather than waiting for the user?
- Pass: bot never filled its own gap
- Fail: bot answered what the data point's source was on the user's behalf

**W2. Wellbeing boundary honored**
If the belief being traced touched on something distressing or beyond a reflective exercise, did the bot respond directly rather than continuing the trace?
- Pass / Fail / N/A: if no such material surfaced

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| Setup (S1–S2) | | | | |
| The Trace (T1–T5) | | | | |
| Finding (F1–F3) | | | | |
| Closing (CL1) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- T1 Fail (only one round) — the most common failure mode; a single exchange looks like tracing but produces nothing that a simple "what's your evidence" question wouldn't have
- S2 Fail (no core premise isolated) — means the trace is descending a ladder that may not be the right one; every subsequent round is built on an unverified starting point
- F2 Fail (assumption conflated with error) — conflating "this was an assumption" with "this was wrong" turns an audit tool into a skepticism tool, which is a different and less honest exercise
