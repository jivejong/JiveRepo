# FrameBot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). The central risk for this bot is content-drift: slipping from auditing the frame into critiquing the content, which is a different and legitimate tool but not this one. Weight the frame-vs-content distinction items most heavily.

---

## Step 1: Getting the Question

**G1. Question or idea taken exactly as given**
Did the bot take the user's question or idea without rephrasing it before beginning the frame analysis?
- Pass: original phrasing preserved through the analysis
- Fail: bot rewrites the question before locating the frame, potentially changing what's being analyzed

---

## Step 2: Locating the Frame

**F1. Frame is identified at the level of assumption, not content**
Does the identified frame name what the question *takes for granted in order to be askable* — not a critique of the answer or approach?
- Pass: frame is clearly an assumption underlying the question's structure
- Fail: "frame" is actually a content critique (e.g., "you're assuming this is a good idea" rather than "you're assuming A or B are the only options")

**F2. Frame is specific, not generic**
Is the identified frame precise enough that it couldn't equally apply to almost any question? ("you're assuming things will stay the same" is too generic; "you're assuming the team, not the individual, is the right unit to optimize for" is specific.)
- Pass: frame is specific to this question's structure
- Fail: frame is so generic it would apply to almost anything

**F3. Frame is real, not manufactured**
Does the identified frame genuinely exist in how the question is posed — not invented to give the exercise something to expose?
- Pass: frame is clearly traceable to the actual phrasing of the question
- Fail: bot manufactures a frame the question doesn't actually contain, forcing a gotcha

**F4. Frame named plainly**
Is the frame stated clearly — "this question assumes X" — without excessive hedging or philosophizing that obscures what's actually being named?
- Pass: frame stated clearly and directly
- Fail: frame is buried in so much hedging or meta-commentary that the user would struggle to state what was found

---

## Step 3: Dropping the Frame

**D1. Alternative is a real structural departure**
Is the dropped-frame version a genuinely different framing — not a synonym or a minor reword of the original?
- Pass: alternative opens up questions or considerations that were structurally excluded by the original frame
- Fail: "alternative" is a slight reword that leaves the underlying assumption intact (e.g., swapping "journey" for "path" instead of "journey" for "garden")

**D2. Alternative is worked through concretely**
Does the bot actually apply the alternative frame to the specific situation — not just say "you could also see this differently" as an abstract gesture?
- Pass: alternative frame is concretely applied to the user's actual situation
- Fail: alternative is named but not actually run through the specifics

**D3. No winner declared between frames**
Does the bot avoid presenting the alternative frame as obviously superior to the original?
- Pass: both frames are presented as choices with different implications, neither ranked as correct
- Fail: bot implies the alternative is the "real" or "better" framing

---

## Step 4: Handing Back

**H1. Both frames presented side by side**
Are the original and alternative frames both visible in the final handback, so the user can choose between them?
- Pass: both remain visible and comparable
- Fail: original frame disappears or gets implicitly discarded in favor of the alternative

**H2. User gets to decide**
Does the bot leave the choice of frame to the user rather than recommending one?
- Pass: user is explicitly given the choice
- Fail: bot recommends a frame rather than presenting both as options

---

## Whole-Session Integrity

**W1. No content critique disguised as frame analysis**
Looking at the full session — does every "frame" finding genuinely target the question's assumptions, or did some findings drift into evaluating whether the plan/idea itself is good?
- Pass: all findings are about assumptions, not about the quality of the content
- Fail: session includes what are really content critiques labeled as frame findings

**W2. No manufactured frame where none exists**
If the question was already genuinely open and unassuming, did the bot say so rather than forcing a frame-exposure for its own sake?
- Pass: bot acknowledged a well-framed question when applicable
- Fail: bot manufactured a frame to expose even when the question didn't warrant it
- N/A: if the question clearly did contain a frame worth examining

---

## Scoring Summary Template

| Step | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| 1. Getting the Question (G1) | | | | |
| 2. Locating the Frame (F1–F4) | | | | |
| 3. Dropping the Frame (D1–D3) | | | | |
| 4. Handing Back (H1–H2) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- F1 Fail (content critique disguised as frame analysis) — the bot has become a critic of the idea rather than an auditor of the question; every subsequent finding is compromised
- D1 Fail (synonym, not departure) — means no genuine reframing happened; the exercise produced only cosmetic variation
- W2 Fail (manufactured frame) — means the bot is finding "bias" as a reflex rather than where it actually exists, the same pathology Cognitive Bias Mapper needs to guard against
