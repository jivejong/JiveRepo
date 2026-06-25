# Writing

System prompts for fixing how AI writes, not just what it writes.

Most "make this sound less like AI" advice targets the symptom: swap the
em-dashes, break up the cadence, kill a few stock phrases. That treats word
choice as the problem. Usually it isn't. The collapse happens upstream, in
the thinking — claims with nothing under them, conclusions that never
survived a complication, paragraphs that exist to look substantial rather
than say something. Fix the writing without fixing that, and you get prose
that's technically varied and still says nothing.

These prompts work the structure, not the surface.

## Prompts

### [Anti-LinkedIn Editor](./anti-linkedin_editor.md)

**Failure mode it prevents:** executive-summary cadence pretending to be a
thought — one declarative sentence per line, no clause leaning on the next,
claims with nothing underneath them.

It won't write in fragments. It builds paragraphs that develop one idea across
several sentences, requires a claim to survive a complication before it
reaches a conclusion, and strips filler without overcorrecting into
academic bloat. Works as a generator or a rewrite tool: hand it a draft and
it'll find the one real idea buried in the fragments and rebuild around it.

---

*More prompts land here as they're built. Each one targets a specific,
nameable failure mode — not a vague "write better" — because a prompt that
can't say what it's fixing usually isn't fixing anything.*

## Using these

Each file is a complete system prompt, ready to drop into a custom GPT,
Gemini Gem, Claude Project, or any tool that takes a system/instructions
field. They're also just useful to *read* — the rules inside are the actual
diagnostic, independent of which model runs them.

## Part of [Thoughtful AI](../)

Healthy AI use is healthy for the human, the model, and humanity. These
prompts are one piece of that thesis: a model is only as useful as the
thinking you hand it, and a model that quietly does your thinking for you
is a worse deal than one that makes you do it better.
