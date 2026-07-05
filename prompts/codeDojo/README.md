# The Code Dojo

A practice space for leveling up as a programmer, built by weaving four Eastern pedagogical traditions onto code — the one domain where their core mechanics stop being metaphors and become literal. The master's artifact and the learner's artifact are the same kind of object; they can be diffed, run, and tested. So knowledge-by-defended-inference, correction-by-showing, mastery-by-repetition, and insight-by-watching-the-automatic-mind all get real teeth here that they wouldn't have on paper.

Like the rest of the `thinking/` repository, every dojo bot is built to **expand the learner's thinking rather than replace it.** In code, the temptation to replace it is at its strongest — the AI can always just write the fix — so each bot is defined as much by _what it refuses to do for you_ as by what it does.

## The four forms of practice

- **The Defense** — _I have code I believe is correct, and I want my reason for believing it attacked._ You defend correctness as a formal five-membered inference (the Nyāya tradition); the dojo attacks not your code but your _reason for trusting it_. This is what debugging actually is: a bug is a place where your reason was a pseudo-reason. **Bridges to the Nyāya Inference bot in `process/`** — same machine, pointed at code.

- **The Mirror** — _I want to see my own gaps by reading a correction, not being told about it._ You attempt a problem; the dojo shows a corrected version with changes **located but not explained**, and you must read the difference yourself (the Guru-Shishya tradition of silent correction). Two modes: _correction_ (marks point at drift toward a better solution — for bug-finding) and _exemplar_ (marks point at drift from a style/pattern model you're matching — for absorbing an idiom; the Juku graft). Trains code-reading: seeing your gap instead of having it narrated.

- **The Forms** — _I want to internalize a pattern through repetition, not do it once and move on._ You implement the same kata more than once, each pass under a tighter constraint (make it work → make it idiomatic → make it robust), and the dojo assesses **fluency** across reps, not one-shot correctness (the Confucian tradition of the rite, _li_). The repetition is the teacher; in code, each pass is an observable artifact, which is what makes it work.

- **The Watch** — _I want to catch the blind spots my automatic solving hides from me._ You solve the same kata more than once, shifting _what you narrate_ each pass (silent → narrate-everything → narrate-only-hesitations), and through that attentional shift you surface where your confident automatic mind is quietly incomplete (the _mushin_ / "no-mind" tradition). Unlike the other bots, the deliverable is **self-knowledge, not better code** — this is a metacognition tool wearing dojo clothes. In the Code edition the runtime can expose the deepest blind spot of all: a gap you felt _no_ hesitation about, yet the code breaks there.

## Two editions: `chat/` and `code/`

Each bot exists in two editions, and the difference is not cosmetic — **it changes where the teaching authority lives.**

- **`chat/`** — pure reasoning, no execution. The dojo _reasons_ about your code: it asserts counterexamples, marks corrections, judges fluency by reading. Portable to any environment. Honest about its limit: **a claim reasoned-but-unrun is a hypothesis, and you (the learner) are the final checker.** When the chat dojo says a case would break your code, the right response is to trace it yourself — sometimes the dojo is wrong, and an honest one can be corrected by the learner.

- **`code/`** — one system prompt per bot, run inside a coding host with a real runtime. Each file is host-portable: an in-file `<environment_router>` binds the right adapter (Claude Code, Cursor, Copilot, or Codex) to whatever host is running it, so a single prompt replaces what used to be a per-host copy. The dojo _runs_ your code: a real failing test refutes your inference, a behavioral diff shows where two versions actually differ, a passing test suite proves a form works. **Here the runtime is the authority, not the AI** — if the test is green, the objection failed, and the dojo concedes.

This distinction is itself pedagogically load-bearing. In `chat/`, you are still the one who decides whether a counterexample is real — which trains judgment but can mislead. In `code/`, reality decides — which is more honest but only available where code can run. The two editions form a natural progression: **the chat dojo reasons to the boundary of what inspection can verify, then hands you the exact argument for taking the same code into the Code edition, where the unrun cases finally get run.**

## Not built as separate bots

One pedagogy from the brainstorm was deliberately _not_ given its own bot, to avoid the overlap that makes a learner pick wrong:

- **Juku / peer-modeling (mark-only correction)** collapses into The Mirror — its mark-where-not-what discipline is already The Mirror's core. Its one distinct contribution, _marking drift from a model you're matching rather than from a correctness reference_, was folded into The Mirror as **exemplar mode**. The group-facilitation scaffolding (rotation, designated non-teacher peer, the mob) doesn't translate to a single-learner tool and was dropped.

_(Mushin / no-mind was initially deferred here, then built as its own bot — **The Watch** — once it was clear it's a metacognition tool with a different success criterion than The Forms, not a mode of it. Folding it into The Forms would have given that bot two incompatible definitions of success; a separate bot keeps both sharp.)_

## The shared failure mode

The whole `thinking/` repo watches for one thing: **the AI quietly doing the cognitive work you were supposed to do.** In the dojo it surfaces at a precise, different moment in each bot, and each edition relocates it:

- **The Defense** — chat: _asserting_ a counterexample instead of posing it for you to check. Code: _narrating the traceback_ instead of letting you read it.
- **The Mirror** — chat: _explaining_ the diff instead of marking its location. Code: _narrating why_ two outputs differ instead of showing them.
- **The Forms** — chat: _writing the idiomatic version_ instead of pointing at the spot. Code: _narrating why_ a robustness test failed instead of showing the red.
- **The Watch** — chat: _telling the learner where they hesitated_ instead of letting the narration shift surface it. Code: _diagnosing the failing run_ instead of showing the collision between their confidence and the runtime's verdict.

In coding, that moment is seductive precisely because the AI _can_ just do it, and doing it feels like helping. It isn't. If a dojo session ever feels easy in a way that means _you_ stopped reading, defending, or repeating — that's the thing to notice, and the bot failing at its one job.

## Status

Proof-of-concept complete: all four bots exist in both editions, each with a fidelity checklist in `checklists/`. **Not all have been run against a real learner transcript yet** — the checklists are _designed-for-verification_, not verification already done, exactly like the `process/` folder's six. They exist so a real session can be scored against specific, falsifiable items, and to give a baseline to test the prompts against: where a real session fails an item, either the session drifted or the item is wrong, and both are useful findings. The next phase is human-in-the-loop testing.

## Inventory

- 4 bots × 2 editions (chat + code) = 8 system prompts
- `chat/` — The Defense, The Mirror, The Forms, The Watch (reasoning edition)
- `code/` — The Defense, The Mirror, The Forms, The Watch (execution edition); one file per bot, host-portable via an in-file `<environment_router>` adapter registry (claude_code, cursor, copilot, codex)
- `checklists/` — one fidelity checklist per bot (4 files), each with a shared core plus chat- and Code-specific subsections; The Mirror's also covers its two modes
- The Mirror carries two modes (correction / exemplar); the others are single-mode
