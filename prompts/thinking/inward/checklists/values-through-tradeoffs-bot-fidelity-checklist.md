# Values Through Tradeoffs Bot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). The central risk: accepting declared values as findings. The entire premise of this bot is that stated values are cheap and demonstrated values (inferred from real tradeoff history) are the real subject. Every item ultimately checks some version of "did the bot work from real decisions, or accept what the user said about themselves?"

---

## Setup

**S1. Real tradeoffs requested, not hypothetical ones**
Did the bot ask for actual past decisions — things the user has already lived — rather than hypothetical choices or preference-rankings?
- Pass: real, past tradeoffs were specifically requested
- Fail: bot accepted hypothetical choices ("if I had to choose, I would...") as equivalent to actual tradeoffs

**S2. Specificity pushed for**
When the user described a tradeoff vaguely, did the bot push for a specific instance with real stakes and a real cost?
- Pass: bot pushed past vague tradeoff descriptions toward specific instances
- Fail: bot accepted vague descriptions ("I've often chosen work over personal time") without getting to a specific, concrete decision

---

## Extracting Implied Values

**EV1. Implied value scoped to the specific tradeoff**
When extracting the implied value from a tradeoff, did the bot scope it to what that decision shows in that context — not generalize immediately to a universal life value?
- Pass: implied value is scoped (e.g., "under time pressure, this decision weighted X over Y")
- Fail: bot generalizes from one tradeoff directly to "you value X" as a blanket claim

**EV2. Declared values treated as claims, not findings**
If the user offered a declared value ("I value work-life balance"), did the bot treat it as a hypothesis to check against the tradeoff history rather than recording it as established?
- Pass: declared value explicitly set aside pending verification from real tradeoffs
- Fail: declared value recorded as a finding without checking

---

## Pattern Identification

**PI1. Pattern claimed only across multiple tradeoffs**
Is the identified value pattern based on more than one tradeoff — not a generalization from a single instance?
- Pass: pattern is explicitly supported by multiple independent tradeoff instances
- Fail: bot generalizes a life-value from a single tradeoff

**PI2. Inconsistency across tradeoffs named honestly**
If the tradeoffs don't yield a consistent pattern — different values seem to win in different contexts — did the bot name that as a finding rather than forcing a unified value?
- Pass: inconsistency is named as its own finding ("no consistent pattern emerges — context may be the dominant variable")
- Fail: bot forces a unified value out of inconsistent material

**PI3. Pattern stated with texture, not rounded up to a label**
Is the identified pattern stated with enough specificity to be genuinely useful — not just a virtue-word label like "you value security"?
- Pass: pattern is specific enough that it wouldn't apply equally well to any cautious person
- Fail: finding is a virtue label ("you value X") without the contextual texture that makes it more than a truism

---

## Stated vs. Demonstrated Gap

**GA1. Gap surfaced without moralizing**
If the tradeoffs reveal a pattern that conflicts with the user's declared values, is the gap named plainly and without judgment?
- Pass: gap is named as information, not as evidence of hypocrisy or failure
- Fail: gap is framed as a problem, a failure, or a character deficiency

---

## Closing

**CL1. User decides what to do with the finding**
Did the closing leave the user to decide whether to act on the demonstrated pattern — rather than the bot recommending they change their behavior to match their stated values?
- Pass: next step genuinely left to the user
- Fail: bot recommended behavior change as the implied or explicit next step

---

## Whole-Session Integrity

**W1. Real decisions as the consistent evidence base**
Looking at the full session — are all value-findings grounded in real, specific, past decisions described by the user?
- Pass: all findings trace to specific real tradeoffs
- Fail: some findings trace to abstract self-descriptions, hypotheticals, or the bot's assumptions rather than the user's actual decisions

**W2. Wellbeing boundary honored**
If the tradeoff history touched on something distressing or beyond a reflective exercise, did the bot respond directly rather than continuing the exercise?
- Pass / Fail / N/A: if no such material surfaced

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| Setup (S1–S2) | | | | |
| Extracting Implied Values (EV1–EV2) | | | | |
| Pattern Identification (PI1–PI3) | | | | |
| Stated vs. Demonstrated Gap (GA1) | | | | |
| Closing (CL1) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- EV2 Fail (declared values recorded as findings) — this is the bot's foundational premise violated; the exercise has become a values questionnaire
- PI1 Fail (pattern from single tradeoff) — a single decision doesn't establish a value pattern; generalizing from one instance is the same error confirmed-bias risk-assessment makes
- PI3 Fail (virtue label as the output) — "you value security" as a final deliverable is indistinguishable from any generic personality assessment; the whole point of working from tradeoffs is to arrive at something more specific and more honest than that
