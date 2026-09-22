# Dev Workflow

A six-stage coding pipeline built out of separate system prompts, one per stage:
**Design → {Coder, Tester} → Linter → Reviewer → Human**, with a **Documenter**
that runs once at the end. They aren't six assistants that happen to share a
topic. They're one pipeline with a deliberate seam down the middle — and the seam
is the whole point.

> Why split a thing one capable agent could do in a single pass? Because a single
> agent silently reconciles its own ambiguities. It reads a vague spec, picks an
> interpretation, writes code to match, then writes tests that agree with the code
> — and the disagreement that should have surfaced the ambiguity never happens.
> Splitting the roles turns that hidden reconciliation into a visible divergence a
> human can catch.

## The stages

- **[01 — Design/Architect](./01-design-architect.md)** — turns a request into a
  **Spec Doc**: the single artifact Coder and Tester each act on independently.
  Defines _what correct looks like_ (acceptance criteria, interface, edge cases),
  not the build. Ambiguity here forks silently downstream, so precision is the
  highest-leverage work in the pipeline. → hands the spec to Coder _and_ Tester.
- **[02 — Coder](./02-coder.md)** — implements the spec, and _only_ the spec.
  Never sees Tester's tests while writing. Flags any interpretation it had to make
  under "Spec Interpretations." → hands off to Linter.
- **[03 — Tester](./03-tester.md)** — writes tests directly from the same spec, in
  parallel with Coder, **without ever reading Coder's code**. That independence is
  the error-catching mechanism: tests reverse-engineered from the implementation
  check nothing. On a failure it produces a **fault attribution** — code wrong /
  test wrong / spec ambiguous — but doesn't act on its own guess. → hands off to
  Linter.
- **[04 — Linter](./04-linter.md)** — narrow, mechanical, semantic layer _beneath_
  real tooling (ESLint, Ruff, etc.): dead branches, swallowed exceptions,
  misleading names, magic numbers. Deterministic tool output is a required input;
  it never re-reports what a real linter already caught. → hands off to Reviewer.
- **[05 — Reviewer](./05-reviewer.md)** — the high-judgment stage, and the _only_
  one that sees all three artifacts (spec, code, tests) together. Uniquely
  positioned to catch a **Coder/Tester divergence** by comparing their two "Spec
  Interpretations" sections, and to judge whether the tests are meaningful or just
  weak. Advisory only: recommends one of four targets — **Ship / Fix Code / Fix
  Test / Fix Spec** — and stops. → hands the recommendation to the Human.
- **[06 — Documenter](./06-documenter.md)** — runs exactly once, after the Human
  decides to ship. Describes what _actually_ shipped for whoever reads it later,
  and surfaces (never buries) known limitations and unresolved discrepancies.

## The shape

```
                     ┌──▶ CODER ──┐
 DESIGN ──(spec)─────┤            ├──▶ LINTER ──▶ REVIEWER ──▶ HUMAN ──▶ DOCUMENTER
   ▲                 └──▶ TESTER ─┘                              │
   │              parallel, independent,                        │
   │              from the same spec alone                      │
   └──────────── loop back (Fix Spec / Fix Code / Fix Test) ◀───┘
```

The fork after Design and the join before Reviewer are load-bearing. Coder and
Tester start from one document and never talk; Reviewer is where their two
independent readings are finally laid side by side. Anything the human decides
isn't ready routes back to the specific stage that owns it — the pipeline never
collapses to a binary "ship or reject," because that would throw away the
information about _where_ the problem is.

## Two target types

Every Spec Doc declares a **Target Type**, and it changes what each downstream
stage means by its job:

- **Application Code** — acceptance criteria describe behavior ("returns X given
  Y"); Tester writes unit tests; Reviewer's security lens is injection, authz,
  deserialization, secrets-in-app.
- **Infrastructure/Pipeline Config** (CI/CD, Dockerfiles, IaC, deploy manifests) —
  criteria describe pipeline guarantees ("build fails on lint error," "deploy
  gated on tag push"); Tester _validates_ with `actionlint`/`hadolint`/`terraform
  validate` rather than unit-testing; Reviewer's lens shifts to permission scopes,
  secret exposure, supply-chain pinning, and deploy gating.

## The shared bet

Every stage refuses the same thing: **deciding on the human's behalf.** No stage
picks a gate outcome. Coder doesn't approve its own work; Tester diagnoses a
failure but won't rule on it or fix the code; Linter reports findings but doesn't
pass/fail; Reviewer recommends but explicitly cannot block, approve, or merge. The
pipeline is engineered so the AI can't quietly absorb the judgment call — it keeps
surfacing divergences and hands the verdict, every time, to the person in the
chair.

## Handoff discipline (for maintainers)

The stages only compose if the seams are honored:

- **Independence is the invariant.** Coder and Tester must work from the spec
  alone and never from each other's output. If you edit these prompts, don't
  introduce a channel between them — that channel is exactly the thing the design
  removes.
- **"Spec Interpretations" is the wire.** Coder and Tester both emit it; Reviewer
  keys on the pair to detect divergence. Keep the section in both, and keep it
  named the same.
- **Format:** these prompts use a **light hybrid of Markdown + XML** — Markdown as
  the readable backbone, with XML tags (`<constraints>`, `<output_format>`, and
  one stage-specific block each) around the parts a stage must treat as a distinct
  instruction unit. Keep the tag set consistent across stages when editing; the
  structure is part of how each prompt stays legible to both a human and the model.
```
