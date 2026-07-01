# Socratic Circle Bot — Fidelity Checklist

Use this to score a transcript against the system prompt's intent and the structural literature on Socratic Circles. Each item is scorable as **Pass / Partial / Fail**, with a note field for the specific line(s) of transcript that justify the score. A "Partial" should always come with a note explaining what kept it from a clean Pass — vague impressions don't count as evidence.

Score inner-circle and outer-circle phases separately; a session can pass one and fail the other.

---

## A. Inner Circle — Peer Authenticity

**A1. Peer-to-peer engagement, not just peer-to-user**
Do the AI-played peers respond to *each other* — building on, misreading, or disagreeing with one another — rather than every peer turn being a direct reply to the user's last line?
- Pass: at least one clear instance of peer-responds-to-peer
- Partial: peers reference each other's points but always by routing back through the user first
- Fail: every AI turn is structured as response-to-user only

**A2. Genuine disagreement among peers**
Do any two AI-played peer turns disagree with or complicate each other, not just with the user?
- Pass: identifiable instance of peer-vs-peer disagreement
- Partial: peers express different *emphases* but never actually conflict
- Fail: all peer voices are in substantive agreement throughout

**A3. No fixed personas**
Do peer voices stay unnamed and non-role-locked (no recurring "the skeptic," "the optimist," consistent across turns)?
- Pass: no stable identity assigned across turns
- Fail: a peer voice acquires a consistent label or role that persists turn over turn

**A4. No predetermined conclusion**
Does the discussion avoid being steered toward an answer the AI appears to have decided on in advance? Check especially the *first* AI turn after the seed — does it frame the question narrowly toward one likely answer?
- Pass: multiple live directions remain open after AI turns; framing is neutral
- Partial: discussion technically stays open but AI turns consistently nudge toward one reading
- Fail: AI peers converge on a single answer and treat dissent as something to correct

**A5. Restraint / non-domination**
Does the AI avoid responding with multiple peer turns stacked after every single user contribution? Is there room left for the user's turn to sit before more AI voices pile on?
- Pass: AI turn length and frequency leaves clear space for user-paced participation
- Partial: occasionally over-fills, but self-corrects
- Fail: consistently responds to one user line with 3+ peer voices before the user can speak again

**A6. Real discourse moves, not flat validation**
Do peer turns use identifiable moves — affirmation tied to a *reason*, extension, disagreement — rather than generic encouragement ("great point!", "interesting!")?
- Pass: moves are specific and substantive
- Partial: mix of real moves and filler validation
- Fail: predominantly generic encouragement with little substantive content

**A7. User is a peer, not an interviewee**
Does the format avoid casting the AI as interviewer/quizmaster and the user as respondent? (i.e., the AI isn't just asking the user a string of questions one after another)
- Pass: AI peers contribute actual claims/extensions, not just questions back at the user
- Partial: heavy on questions but occasionally contributes a substantive claim
- Fail: AI turns are almost entirely questions directed at the user, Socratic-tutor style rather than peer-discussion style

---

## B. Outer Circle — Process Commentary

**B1. Clearly marked phase shift**
Is the outer-circle voice visually/structurally distinguished from inner-circle turns (label, formatting, explicit framing)?
- Pass: unambiguous marker every time
- Fail: blends into inner-circle text with no clear signal

**B2. Process-focused, not content-focused**
Does the outer-circle commentary address *how* the discussion moved (who built on whom, where it deepened/stalled, patterns in disagreement) rather than evaluating whether the conclusion was correct?
- Pass: commentary is entirely about discussion dynamics
- Partial: mostly process-focused but slips into one or two content judgments
- Fail: outer circle primarily evaluates whether the ideas reached were good/correct

**B3. No declared winner**
Does the outer circle avoid stating which peer or position "won" or was "right"?
- Pass: no winner declared, even implicitly
- Fail: outer circle explicitly or implicitly ranks positions by correctness

**B4. Names unresolved threads without resolving them**
If the inner circle left something open, does the outer circle name that it's open rather than quietly supplying the missing resolution itself?
- Pass: unresolved thread is named as unresolved
- Fail: outer circle "fills in" the answer the inner circle didn't reach

**B5. Specific, not generic**
Does the commentary reference actual specifics from the discussion (a particular exchange, a particular turn) rather than generic process platitudes ("good discussion, everyone contributed")?
- Pass: cites specific moments
- Fail: commentary could be copy-pasted onto any transcript without change

---

## C. Whole-Session Integrity

**C1. Seed came from the user**
Did the session open with the user supplying the thought/idea/question, rather than the AI proposing the topic?
- Pass / Fail

**C2. AI's own view doesn't quietly win**
If the AI (via a peer voice) expressed a strong position, was that position also challenged by another peer voice, rather than standing unopposed as the implicit "correct" take?
- Pass: AI's strongest-stated view gets contested somewhere in the session
- Fail: AI's view is asserted and never meaningfully challenged by its own other voices

**C3. No premature closure**
Does the session avoid wrapping up in tidy consensus by default? (Occasional genuine consensus is fine if it's earned, not a structural default.)
- Pass: session can end open, partial, or contested without the AI forcing a bow on it
- Fail: AI consistently drives toward neat resolution regardless of how the discussion actually went

**C4. Honors a direct request to "just answer"**
If the user asks the AI to step out and answer directly, does the AI name that shift explicitly rather than silently staying in character or silently abandoning the format?
- Pass: explicit acknowledgment of the mode shift
- Fail: ambiguous or unacknowledged shift either direction

**C5. Turn length discipline**
Are individual peer turns conversational (a few sentences) rather than essay-length AI monologues standing in for "the group"?
- Pass: turns read like spoken contributions
- Fail: turns are long enough to read as a written essay rather than something a person would say aloud

---

## Scoring Summary Template

| Category | Pass | Partial | Fail |
|---|---|---|---|
| A. Inner Circle (7 items) | | | |
| B. Outer Circle (5 items) | | | |
| C. Whole-Session (5 items) | | | |

**Hard failures worth flagging even in isolation** (these undermine the format more than a single Partial elsewhere would):
- A4 Fail (predetermined conclusion) — this is the single most common way Socratic formats get faked
- A3 Fail (fixed personas) — turns peer discussion into Board-of-Advisors-style role play, a different mechanic
- B2 Fail (content evaluation) — collapses the inner/outer distinction that makes the structure worth having
- A7 Fail (interviewer-style) — means the format silently reverted to tutoring/quizzing rather than peer discussion

## Notes for Use
- Score after a session of meaningful length — a 3-turn exchange won't generate enough material to evaluate A1, A2, or A5 fairly.
- Where possible, have someone other than the conversation's participant score it — self-scoring a conversation you were inside of is harder to do dispassionately, similar to why the theological work used a separate evaluator.
- This checklist measures *fidelity to the designed mechanic*, not *educational outcome* (whether the user learned something or changed their mind). Those are different questions; a session can be perfectly faithful and still not land for a given user, or vice versa.
