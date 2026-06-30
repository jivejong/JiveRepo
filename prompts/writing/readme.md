# Writing

Two kinds of writing prompts: ones that fix how AI writes, and ones you write *with*.

The first kind is corrective. You've got a draft, the AI flattened it, and you
want the structure fixed without the voice sanded off. Anti-LinkedIn Bot and
Voice Finder Bot live here — they work on something that already exists.

The second kind is collaborative. There's no draft yet. You're writing, and you
want a partner who helps you find what to say and how to say it without quietly
taking over the wheel. These are *assistants* — emphasis on the assist. They
don't write for you. They write with you, and they're built to keep you in the
chair through a long session, which is exactly when AI writing partners tend to
drift, repeat themselves, and trail off before the ending.

## The two families

**Corrective** — fix what came out wrong
- [Anti-LinkedIn Bot](./anti-linkedin-bot.md) — strips executive-summary cadence; makes claims survive a complication before concluding
- [Voice Finder Bot](./voice-finder-bot.md) — explores and refines voice instead of flattening it toward the generic professional default

**Collaborative** — write alongside
- [Creative Assistant](./creative-assistant.md) — fiction: short story, novel, script, poetry. Identifies the form, then writes to its demands
- [Non-Fiction Assistant](./non-fiction-assistant.md) — essays, articles, criticism. Argument architecture, not just prose
- [Business Assistant](./business-assistant.md) — memos, proposals, correspondence. Pushes on the ask, not only the wording
- [Technical Assistant](./technical-assistant.md) — docs, references, specs. Accuracy and audience before generation
- [Synthesis Assistant](./synthesis-assistant.md) — the reductive one. Meeting notes, research, threads. Faithful compression, not new content

## The shared spine: Collaboration Discipline

Every collaborative prompt inherits the same discipline block. This is the
canonical version. Each prompt restates it in its own opening so the file works
standalone (a system prompt never sees this README) — but when the discipline
changes, it changes *here* first, then propagates to the prompts.

> **Collaboration Discipline** — inherited by every assistant in this suite
>
> - **Identify before you generate.** Know what's being written (the form),
>   who it's for (the audience), and what it's trying to do (the purpose). If
>   any of these is load-bearing and missing, ask or state your assumption
>   plainly before producing. Don't generate into a void and hope.
> - **Offer directions, not a verdict.** When generating from scratch, give 2–3
>   genuinely distinct options — different approaches, not the same idea
>   reworded. The writer chooses; you don't decide for them.
> - **Keep the writer in the chair.** You assist. You do not quietly take over.
>   Surface the choice points; don't smooth past them. The goal is the writer's
>   piece, sharpened — not your piece, delivered.
> - **Monitor your own drift.** Long sessions are where AI writing partners
>   fail: same cadence, same structural tic, the same three-beat list, the same
>   reused image. Track your accumulating patterns and break them deliberately.
>   When you catch yourself repeating a move, say so and offer a different shape.
> - **Finish what you start.** A piece has an arc. Don't trail off at the
>   interesting part or hand back all rising action. If length forces a
>   tradeoff, name it and propose where to compress — don't abandon the ending.

The reductive bot (Synthesis) inherits a *modified* spine: "identify before you
generate" and "monitor drift" still hold, but "offer directions" and "finish the
arc" are replaced by reductive discipline — don't add what isn't in the source,
preserve disagreement, attribute claims, flag uncertainty. Its own file spells
this out.

## Why "assistant" is the whole point

A tool that writes *for* you produces text and a dependency. A tool that writes
*with* you produces text and a writer who got sharper doing it. The suite is
built on the second bet: healthy AI use expands what the human can do rather
than replacing the doing. Every design choice here — offer options instead of
deciding, surface choice points instead of smoothing them, keep the arc in the
writer's hands — serves that.
