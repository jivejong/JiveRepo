# Metaphor Excavation Bot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). Two equally important failure modes to watch for: (1) forcing a metaphor onto material that doesn't actually contain one, and (2) stopping at naming the metaphor without running the swap — which is the distinctive payoff this bot is built to deliver.

---

## Step 1: Gathering Material

**M1. Material gathered through natural description, not direct prompt**
Did the bot ask the user to talk about something naturally rather than asking "what's your metaphor" directly — since the metaphor shows up in how people describe things, not in response to a meta-question about it?
- Pass: bot asked for natural description, not a direct metaphor-identification question
- Fail: bot asked the user to name or guess their own metaphor before gathering natural material

**M2. Sufficient material for pattern identification**
Was enough material gathered to identify a recurring pattern — not just a single passage that might reflect a one-off word choice?
- Pass: at least two instances or a sufficiently rich single passage were gathered before claiming a pattern
- Fail: bot claimed a metaphor from a single phrase or minimal material

---

## Step 2: Naming the Metaphor

**N1. Metaphor identified at structural level, not word-choice level**
Is the identified metaphor a structural frame (a recurring logic applied across domains) rather than a single isolated word or phrase?
- Pass: metaphor is characterized as a pattern across multiple uses
- Fail: bot identifies a single interesting word as "the metaphor" without demonstrating it recurs structurally

**N2. Named metaphor anchored to specific language**
Does the bot cite specific words, phrases, or sentences from the user's material that reveal the metaphor — not just assert the metaphor exists?
- Pass: specific language cited as evidence
- Fail: metaphor asserted without specific citations from the user's text

**N3. Multiple metaphors across domains noted if present**
If the material showed different metaphors in different domains (engineering at work, ecosystem in relationships), did the bot note both rather than forcing one unified metaphor?
- Pass: multi-domain variation is named as its own finding
- Fail: bot forces a single metaphor across material that genuinely shows different frames in different domains
- N/A: if the material genuinely showed one consistent metaphor throughout

**N4. Metaphor offered as observation, not verdict**
Was the named metaphor offered as something to check rather than declared as definitive?
- Pass: "does that sound like the shape you're actually thinking in, or am I off?" or equivalent
- Fail: metaphor declared without checking

---

## Step 3: Making the Metaphor Visible

**V1. Bot names what the current metaphor makes visible and invisible**
Does the bot explain what the identified metaphor makes naturally prominent — and what it structurally obscures or makes hard to ask?
- Pass: both the affordances and the blind spots of the current metaphor are named
- Fail: bot only names the metaphor without explaining what it does

---

## Step 4: The Swap (highest-weight section)

**SW1. Swap happened at all**
Did a genuine swap actually occur — not just a mention that other metaphors exist?
- Pass: a specific alternative metaphor was applied to the user's actual situation
- Fail: bot named an alternative metaphor but never actually ran it through the specifics — this is the single most common failure mode for this exercise

**SW2. Swap is a real structural departure**
Is the swapped metaphor genuinely different in structure from the original — not a synonym or a slight variation?
- Pass: swap opens up qualitatively different questions or concerns
- Fail: "swap" is a near-synonym (e.g., "journey" → "path") that leaves the underlying logic intact

**SW3. Swap applied concretely to the specific situation**
Does the bot actually redescribe the user's specific situation through the new metaphor's vocabulary and logic — not just name the alternative?
- Pass: concrete, specific redescription using the new metaphor's terms
- Fail: alternative named but not concretely applied ("you could also see this as a garden" without actually describing the garden)

**SW4. User asked what becomes visible under the new frame**
After running the swap, did the bot ask the user what the new metaphor makes visible that wasn't visible before?
- Pass: question asked
- Fail: swap run without prompting user reflection on what it opened up

**SW5. No winner declared between metaphors**
Does the bot avoid presenting the swapped metaphor as better or more "correct" than the original?
- Pass: both metaphors presented as choices with different affordances
- Fail: bot implies the alternative is the superior or healthier frame

---

## Closing

**CL1. Closing is open, not prescriptive**
Does the session end by leaving the user to decide what to do with the finding — without prescribing that they adopt the new metaphor or abandon the old one?
- Pass: close is open-ended
- Fail: bot recommends adopting the alternative metaphor as the better option going forward

---

## Whole-Session Integrity

**W1. No forced metaphor**
Looking at the full session — if the material was genuinely literal or showed no recurring figurative pattern, did the bot say so rather than manufacturing a metaphor?
- Pass: bot either found a real pattern or acknowledged its absence
- Fail: bot forced a metaphor onto material that didn't actually contain one

**W2. Wellbeing boundary honored**
If material shared touched on something distressing or beyond a reflective exercise, did the bot respond directly rather than continuing the exercise?
- Pass / Fail / N/A: if no such material surfaced

---

## Scoring Summary Template

| Step | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| 1. Gathering Material (M1–M2) | | | | |
| 2. Naming the Metaphor (N1–N4) | | | | |
| 3. Making It Visible (V1) | | | | |
| 4. The Swap (SW1–SW5) | | | | |
| Closing (CL1) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- SW1 Fail (no swap) — the exercise's distinctive payoff never happened; naming the metaphor without swapping it is the most common way this exercise undersells itself
- SW3 Fail (swap named but not applied) — same as SW1 in effect; the swap exists in name only
- W1 Fail (forced metaphor) — means the bot is finding patterns as a reflex rather than from evidence, the same pathology as Cognitive Bias Mapper's over-flagging problem
