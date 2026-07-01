# Process

Tools that borrow a real, named pedagogical structure and hold to its actual mechanics rather than a generic gloss of it. Each of these was drafted only after checking the real instructional-design literature, specifically to avoid the trap of compressing a named pedagogy down to its most flashy-sounding feature while losing the structural details that make it actually work (and the discipline that makes it hard).

Every prompt here ships with a fidelity checklist of the same name — a transcript-scoring instrument built from the prompt's own guardrails plus the literature, organized so a real session can be checked against specific, falsifiable items rather than a vague "did this feel faithful" impression. None of these checklists have been run against real transcripts yet; treat them as designed-for-verification, not as verification that's already happened.

## The shared risk across this folder

Every pedagogy here has its own specific moment where an AI's instinct to over-help will strike — found by asking "where in this pedagogy's structure would skipping the hard part look like helping?" That's the single most useful question to ask before trusting any of these in a real session.

## Socratic Circle

**Files**: `socratic-circle-bot-system-prompt.md` · `socratic-circle-bot-fidelity-checklist.md`

Simulates a real Socratic Circle's inner/outer structure — not the generic "Socratic method" of an AI interrogator asking questions, but the actual classroom mechanic: peers discussing in an inner circle, an outer circle observing and giving process feedback. The AI plays a flexible, unnamed set of inner-circle peers (genuinely disagreeing with each other, not just the user) plus a single outer-circle narrator voice, while the user supplies the seed question and sits as a real participant in the inner circle. Risk to watch: peers steering toward a predetermined answer, or the outer circle sliding into content evaluation instead of process commentary.

## Think-Pair-Share

**Files**: `think-pair-share-bot-system-prompt.md` · `think-pair-share-bot-fidelity-checklist.md`

Three sharply distinct phases: Think (the AI stays out of it entirely — protecting the user's first unassisted formulation), Pair (the AI becomes one attentive partner using four named moves — React, Challenge, Clarify, Extend — never finishing the idea on the user's behalf), and Share (a small, informal, unlabeled panel reacts to the refined idea, deliberately lighter-weight than Board of Advisors rather than a thin copy of it). Risk to watch: Pair quietly completing the idea instead of pushing on it, or Share board-ifying itself.

## Cognitive Apprenticeship

**Files**: `cognitive-apprenticeship-bot-system-prompt.md` · `cognitive-apprenticeship-bot-fidelity-checklist.md`

The full six-stage Collins, Brown & Newman model — modeling, coaching, scaffolding, articulation, reflection, exploration — not just "AI shows its work." The AI performs a named domain expert's real, procedural reasoning (Modeling), reacts to the user's own attempt (Coaching), provides direct temporary support for a specific gap (Scaffolding), deliberately steps back (Fading), prompts a real comparison between the user's reasoning and the original modeled performance (Articulation/Reflection), then hands over problem-_definition_ itself (Exploration). The longest and most stage-dependent prompt in the collection. Risk to watch: stage bleed — Coaching relapsing into a second Modeling performance, or Fading quietly reverting to heavy involvement the moment the user struggles.

## Project-Based Learning

**Files**: `project-based-learning-bot-system-prompt.md` · `project-based-learning-bot-fidelity-checklist.md`

The only prompt in this folder built for genuine multi-session use — real PBL spans real time the AI doesn't get to observe. Establishes a driving question, a real artifact, and an explicit authenticity check at launch; every return session opens with the user restating where they left off rather than the bot assuming continuity; closes with an explicit reflection connecting the finished artifact back to the original question. Risk to watch: false continuity (the bot asserting things about "last time" that weren't actually restated), or the bot doing the actual research/building instead of coaching it.

## Inquiry-Based Learning

**Files**: `inquiry-based-learning-bot-system-prompt.md` · `inquiry-based-learning-bot-fidelity-checklist.md`

Built around the field's real four-level structure (structured / guided / open), chosen explicitly by the user rather than defaulted — deliberately not "always ask questions, never answer," which is specifically the open-inquiry pattern and is a known-bad default for anyone who hasn't built up to it. The same behavior is correct at one level and a violation at another (e.g., providing a procedural outline is required at Structured, a violation at Open), so the checklist is structured as three parallel level-specific sub-checklists rather than one flat list.

## Case-Based Learning

**Files**: `case-based-learning-bot-system-prompt.md` · `case-based-learning-bot-fidelity-checklist.md`

Presents a real or invented case, runs directed analysis, forces an actual committed decision, then plays out a realistic consequence that does the correcting — preferring the consequence to reveal a misconception over stopping to lecture about it, though direct correction is allowed when the consequence alone wouldn't make the gap legible. The only prompt in this folder where the user has to commit to something and live with what follows. Risk to watch: the "right answer" leaking out before the decision is made, or consequences that read as punitive theater rather than realistic complication.
