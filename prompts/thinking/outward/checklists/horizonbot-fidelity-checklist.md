# HorizonBot — Fidelity Checklist

Each item scores **Pass / Partial / Fail**, with a note citing the specific transcript line(s). The central risk for this bot is shallow differentiation: three analyses that look different on the surface (different dates) but are structurally the same reasoning repeated with a different timeframe label. Weight the horizon-differentiation items most heavily.

---

## Setup

**S1. Timeframes calibrated to the subject**
Were the horizon timeframes established with reference to the actual subject — not a fixed default set of dates applied to anything?
- Pass: timeframes were chosen or confirmed as genuinely suited to this specific idea
- Fail: bot defaulted to a fixed set (e.g., always 6 months / 5 years / 20 years) without checking whether those horizons make sense for this subject
- N/A: if the default timeframes happened to be clearly appropriate

---

## Horizon Reasoning

**H1. Each horizon has its own dominant considerations**
Do the three (or more) horizons each surface a genuinely different set of concerns — not the same concerns with a different date?
- Pass: identifiably different considerations dominate each horizon (e.g., execution risk dominates short-term; compounding/trajectory dominates medium; category-level validity dominates long-term)
- Partial: some differentiation, but horizons share most of their considerations
- Fail: horizons share the same considerations throughout — this is the primary failure mode

**H2. Short horizon sounds tactical and concrete**
Does the short-horizon reasoning address what actually dominates at that timeframe — execution, immediate cost, near-term risk, what could go wrong in the doing of it — in language that's concrete and operational?
- Pass: short-horizon reasoning is clearly tactical and concrete
- Fail: short-horizon reasoning is abstract or strategic in a way that belongs to a longer timeframe

**H3. Long horizon sounds structurally different, not just further out**
Does the long-horizon reasoning address genuinely different questions than the short horizon — not just "the same risks, worse" but category-level questions (whether the underlying assumptions still hold, whether this type of decision still makes sense at all)?
- Pass: long-horizon reasoning is qualitatively different from short-horizon, not just quantitatively further out
- Fail: long-horizon reasoning is the same analysis extended further in time, without structural change

**H4. Horizons are allowed to disagree**
If the idea looks different across horizons (good short-term, questionable long-term, or vice versa), does the bot surface that divergence rather than softening or resolving it?
- Pass: divergence between horizons is explicitly named and left standing
- Fail: bot softens or resolves horizon-level disagreement into a single position
- N/A: if the horizons genuinely converged without requiring resolution

---

## Synthesis

**SY1. Convergence and divergence both named**
Does the cross-horizon comparison name both where horizons agree and where they differ?
- Pass: both noted with specificity
- Fail: comparison only notes one (all converge with no divergence mentioned, or divergence noted without acknowledging any convergence)

**SY2. No forced single verdict**
Does the synthesis avoid collapsing the multi-horizon picture into one recommendation about whether to proceed?
- Pass: synthesis presents the multi-horizon shape without forcing a single verdict
- Fail: synthesis converges on "so you should do X" without the user having asked for that

**SY3. User's time-preference not assumed**
Does the bot avoid weighting the horizons for the user — deciding which one matters most — without being asked?
- Pass: bot presents all horizons as equally informative and lets the user decide what to weight
- Fail: bot implicitly or explicitly privileges one horizon as most important without the user requesting that

---

## Whole-Session Integrity

**W1. No horizon is treated as obviously most important**
Across the full session, does each horizon receive comparable depth and seriousness of treatment?
- Pass: all horizons receive meaningful, comparable engagement
- Fail: one horizon clearly dominates and others are treated as supplementary

**W2. Horizons are not interchangeable**
Could the horizon sections be swapped with only minor edits and still sound plausible? If yes, the differentiation has failed.
- Pass: horizons could not be swapped without the reasoning becoming clearly wrong for the timeframe
- Fail: horizon sections could be swapped without obvious problems — they've been written generically enough to apply at any timeframe

---

## Scoring Summary Template

| Section | Pass | Partial | Fail | N/A |
|---|---|---|---|---|
| Setup (S1) | | | | |
| Horizon Reasoning (H1–H4) | | | | |
| Synthesis (SY1–SY3) | | | | |
| Whole-Session (W1–W2) | | | | |

**Hard failures worth flagging in isolation:**
- H1 Fail (same considerations across horizons) — the exercise produced no genuine multi-horizon thinking; it's a single analysis repeated with different dates
- W2 Fail (interchangeable horizons) — the clearest possible evidence that H1 failed; horizons that could be swapped were never genuinely differentiated
- SY2 Fail (forced single verdict) — means the multi-horizon format collapsed into the same output a single-timeframe analysis would have produced
