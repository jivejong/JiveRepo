# The Forms — Fidelity Checklist

A transcript-scoring instrument for The Forms (both editions), built from the bot's own guardrails plus the Confucian rite (*li*) repetition mechanic it borrows. **Not yet run against a real transcript** — designed-for-verification, not verification. Use as the baseline to test the prompt against.

**Scoring:** **Pass / Partial / Fail** per item. Score the **Shared core** every session, then the matching **edition** subsection.

---

## Shared core (both editions)

### The defining failure mode (highest weight)

Two over-help moments share top weight here: letting the learner skip a rep, and writing the idiomatic/robust version for them. Either one removes the repetition that *is* the pedagogy.

**1. Did the AI require each form to be performed fresh, refusing to let a rep be skipped because an earlier form was "good enough"?**
- **Pass:** all forms performed as full re-solves, even when an earlier one was strong.
- **Partial:** allowed one form to be a light edit of the previous rather than a fresh performance.
- **Fail:** let the learner skip a form.

**2. Did the AI refrain from writing the idiomatic or robust version, instead pointing at the spot for the learner to find on the next form?**
- **Pass:** named that a smoother/more-robust move exists at a location, left the finding to the learner.
- **Partial:** showed the better move strongly enough to be copied.
- **Fail:** wrote the idiomatic or robust version.

### Mechanic fidelity

**3. Did the AI assess *fluency* (where execution flowed vs. stuttered, what got more fluent across reps) rather than grading correctness as the main event?**
- Pass: fluency was the through-line. Partial: slid into correctness-grading at times. Fail: ran as a correctness checker — the wrong pedagogy.

**4. Did each form carry its own constraint (work → idiomatic → robust), with the AI holding the learner to that constraint per pass?**
- Pass: each form's constraint was real and enforced. Partial: constraints blurred between forms. Fail: forms were undifferentiated repeats.

**5. Did the close trace the fluency arc across forms and name what still wants more reps — rather than presenting the final code as the deliverable?**
- Pass: closed on the learner's felt sense of what's internalized + what to repeat. Partial: mixed in code-as-deliverable. Fail: treated the finished code as the point.

---

## Chat edition only

**C1. Did the AI describe forms as "reads as correct / reads as idiomatic" and flag that working-ness and speed were not actually run?**
- Pass: claims framed as readings, with the unrun limit named. Partial: occasionally asserted a form "works" or "is fast." Fail: claimed verified correctness or performance it never executed.

## Code edition only

**D1. Did the AI hold "make it work" to a real green test bar rather than an eyeballed one?**
- Pass: form one's correctness was confirmed by running tests. Fail: asserted it worked without running.

**D2. Did the AI run the idiomatic form against the same tests (idiom that breaks correctness isn't idiom) and the robust form against edge-case tests that ran red on the naive forms?**
- Pass: both checks executed; robustness demonstrated by red→green. Partial: ran some but not the edge-case progression. Fail: asserted robustness without the failing-then-passing tests.

**D3. Did the AI refrain from narrating *why* a robustness test failed, showing the red and letting the next form answer it?**
- **Pass:** showed the failing edge case, let the third form turn it green. **Fail:** explained why the naive form failed.

**D4. Were the kata's tests written to define *works*, not to dictate the *how* — leaving the learner room to perform the form their own way?**
- Pass: tests checked behavior, not implementation. Fail: tests were tight enough to force a specific solution.
