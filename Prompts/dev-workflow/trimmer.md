Goal: reduce this codebase to the simplest architecture and implementation that
satisfies docs/REQUIREMENTS.md. The previous agent over-engineered it. Less code,
fewer layers, fewer abstractions, same behavior.

AUTHORITY

- docs/REQUIREMENTS.md (with my answers in docs/OPEN_QUESTIONS.md) defines
  correct behavior. The external contracts section must not change.
- If code and requirements disagree, requirements win, but flag it; don't silently
  change behavior.

PHASE 0: BASELINE (before touching anything)

- Run the full test suite; record pass/fail/skip counts, runtime, and coverage.
- Record: total LOC (source and tests separately), file count, dependency count,
  deepest call/inheritance chain you can find.
- Write these to docs/REFACTOR_BASELINE.md. Do all of this before any edit.

PHASE 1: PLAN (stop here for my approval)
Produce docs/REFACTOR_PLAN.md containing, in execution order:

- Every deletion (dead code, SPECULATIVE components, unused config, unused deps)
- Every consolidation (layers or abstractions to collapse, with before/after
  call path)
- Every test change (see test rules below)
- Risks: anything where behavior preservation is uncertain, and how you'll verify it
  Keep each step small enough to be one reviewable commit. Do not start executing.

PHASE 2: EXECUTE (only after I approve the plan)
Order of operations: delete first, then simplify, then restructure. Deleting
unused things is the cheapest and safest win. Do not rewrite what you can delete.

CODE RULES

- Remove abstractions with a single implementation, wrappers that only delegate,
  factories/builders/registries with one product, config options nobody varies,
  and indirection that doesn't earn its keep.
- Prefer plain functions and data over class hierarchies. Prefer the standard
  library over dependencies. Remove any dependency that can be replaced with a
  short amount of straightforward code.
- Do not add dependencies. Do not introduce new frameworks, patterns, or layers.
- Do not make speculative generalizations. Solve the requirement in front of you.
- No behavior changes smuggled into refactoring commits. If you find a bug, record
  it in docs/FOUND_BUGS.md and fix it in a separate, clearly labeled commit only
  if I approve.
- Preserve the readability of what remains: clear names, short functions, comments
  that explain why rather than what.

TEST RULES

- Every kept test must trace to a requirement ID. Add the ID in the test name or a
  comment.
- Delete tests that assert implementation details: mock call counts, private
  methods, internal structure, snapshots of incidental output, and tests of code
  you are deleting.
- Collapse duplicated and near-duplicate tests. Prefer a few behavior-level tests
  over many micro-tests mocking every collaborator.
- Over-mocking is a smell: where a test mocks internals of its own system, replace
  it with a test against real components (fakes only at true external boundaries
  like network, clock, or filesystem).
- If a requirement has no test, add one BEFORE refactoring the code it covers
  (characterization test), then refactor.
- Never weaken an assertion to make a test pass.

VERIFICATION

- Full suite green after every commit. If it goes red, fix or revert that step;
  never batch the repair into a later commit.
- Commits: one logical change each, message states what was removed or simplified
  and why it's safe.
- At each milestone, re-measure against the baseline.

STOP AND ASK when:

- You're about to change an external contract
- A deletion's safety isn't provable from code and tests
- Requirements are ambiguous or conflict
- A step needs more than ~300 lines changed in one commit

DONE WHEN
Every requirement is covered by a passing test, nothing remains that doesn't trace
to a requirement, and docs/REFACTOR_REPORT.md shows baseline vs. final numbers
(LOC, files, dependencies, test count and runtime, coverage), plus a list of
anything you chose not to touch and why.
