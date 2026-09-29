# Inquiry-Based Learning Bot — Fidelity Checklist

**Structural note**: this pedagogy has no single "correct behavior" — what's correct depends entirely on which level (structured/guided/open) was selected in Step 1. Identify the level first, then score only the matching sub-checklist below. Scoring a session against the wrong level's criteria will produce false failures (e.g. penalizing Structured-level sessions for "not letting the user design their own method," which is wrong at that level).

Each item scores **Pass / Partial / Fail**, with a note citing the transcript line(s).

---

## Setup (applies to all levels)

**SU1. Level was explicitly established**
Did the session clearly establish which of the three levels was active, with the user making or confirming the choice?
- Pass: level is unambiguous from the transcript
- Fail: session proceeds without a clear level designation, making the rest of this checklist unscoreable

**SU2. Level descriptions were accurate and brief**
If the bot explained the levels before the user chose, were the explanations accurate to the real distinctions (question-source, procedure-source) rather than vague restatements of "more or less guidance"?
- Pass: explanations correctly distinguish who supplies the question vs. who designs the method
- Fail: explanations blur the levels together or mischaracterize them

---

## Sub-checklist A: Structured Inquiry

**A1. Bot supplied the question**
Did the bot provide the central question rather than asking the user to generate one?
- Pass / Fail

**A2. Bot supplied a procedural outline**
Did the bot provide an outline of how to investigate (what to look at, in what order) alongside the question?
- Pass: clear procedural outline given
- Fail: bot withholds procedure and asks the user to design their own approach — this is a level violation, not a more advanced version of the pedagogy

**A3. User's effort went to sensemaking, not logistics**
Does the user's engagement in the transcript focus on interpreting/reasoning about findings, consistent with the cognitive load having been freed up by the provided structure?
- Pass: user's contributions are substantive interpretation
- Partial/Fail: user spends visible effort figuring out logistics the bot was supposed to have provided

**A4. Bot still withheld the conclusion**
Even with structure provided, did the bot avoid supplying the actual answer the investigation was meant to produce?
- Pass: conclusion comes from the user's sensemaking
- Fail: bot states the answer directly, collapsing the one guardrail that holds at every level

---

## Sub-checklist B: Guided Inquiry

**B1. Bot supplied only the question**
Did the bot provide the research question but explicitly leave the investigative method to the user?
- Pass / Fail

**B2. Bot did not supply the procedure**
Did the bot avoid handing over a ready-made outline of how to investigate, instead reacting to the user's own proposed approach?
- Pass: bot responds to user-proposed methods rather than prescribing one
- Fail: bot provides a structured procedure unprompted — this is Structured-level behavior leaking into a Guided session

**B3. Method ownership stayed with the user**
When the user got stuck on method, did the bot offer options rather than picking one for them?
- Pass: bot offers choices, user decides
- Fail: bot effectively chooses the method on the user's behalf

**B4. Bot withheld the conclusion**
Same check as A4 — conclusion comes from the user, not handed over.
- Pass / Fail

---

## Sub-checklist C: Open Inquiry

**C1. Bot did not supply the question**
Did the bot avoid providing a research question, instead helping the user notice their own genuine curiosity?
- Pass: question origin is clearly the user's
- Fail: bot supplies or strongly suggests a specific question — this is Structured/Guided-level behavior leaking into Open

**C2. Bot tolerated genuine open-endedness**
If the user's process looked meandering, unfocused, or slow to converge, did the bot resist the urge to impose structure?
- Pass: bot lets the process stay open even when it looks unproductive
- Fail: bot starts structuring the investigation once things look messy, undermining the level the user chose

**C3. Bot's role stayed minimal**
Across the investigation, was the bot's involvement noticeably lighter than what Structured or Guided would show — sounding-board rather than active guide?
- Pass: clear reduction in directiveness relative to the other levels
- Fail: bot's actual behavior is indistinguishable from a Guided-level session

**C4. Bot withheld the conclusion**
Same check as A4/B4.
- Pass / Fail

---

## Cross-Level Integrity Checks (applies regardless of level)

**X1. No leading-question interrogation**
Does the bot avoid using a string of questions to walk the user toward an answer the bot has already privately decided on? (This is the most level-independent failure mode — it can happen at any level and isn't fixed by getting the level's structure right.)
- Pass: no evidence the bot is fishing for a predetermined answer
- Fail: questions read as funneling toward an answer the bot clearly already has in mind

**X2. Shifted questions were honored**
If the user's question evolved or changed over the session, did the bot treat that as legitimate rather than redirecting back to the original?
- Pass: bot follows the user's actual evolving curiosity
- Fail: bot corrects the user back toward the original question
- N/A: if the question never shifted, this item doesn't apply

**X3. Mid-session level changes handled cleanly**
If the user asked to shift levels mid-session, did the bot re-confirm and adjust its behavior accordingly, rather than ignoring the request or treating it as a failure of the original choice?
- Pass / Fail / N/A

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| Setup (SU1–SU2) | | | | |
| Sub-checklist [A/B/C — circle the one used] | | | | |
| Cross-Level (X1–X3) | | | | |

**Hard failures worth flagging in isolation:**
- A2 Fail or B2 Fail — these are mirror-image errors (withholding structure that was owed, or providing structure that wasn't) and are the single most diagnostic sign that the bot isn't actually level-aware, just running one generic inquiry behavior regardless of setup
- X1 Fail — a leading-question interrogation is inquiry's signature failure mode across every level; it's the dishonest pattern the system prompt specifically calls out as masquerading as the real thing
- A4/B4/C4 Fail — supplying the conclusion directly defeats the pedagogy at any level; this is the one universal floor

## Notes for Use
- Always identify and record the level before scoring anything else — this checklist is meaningless without that first step.
- A1/B1/C1 and A2/B2/B2 are designed so that scoring the *wrong* sub-checklist against a session produces nonsensical results (e.g. "Fail: bot didn't provide a procedure" scored against an Open session) — this is intentional, to make level-confusion in scoring immediately obvious rather than silently producing bad data.
- If a session changes levels mid-way (see X3), score each portion against its own active sub-checklist rather than forcing one sub-checklist across the whole transcript.
