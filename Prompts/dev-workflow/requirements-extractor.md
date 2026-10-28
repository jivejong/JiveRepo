You are taking over a project that a previous agent over-engineered. Before anything is cleaned up, I need a requirements document that defines what this
application is actually supposed to do, independent of how it currently does it.

CONSTRAINTS

- Read-only phase. Do not modify, delete, or create any file except
  docs/REQUIREMENTS.md and docs/OPEN_QUESTIONS.md.
- Do not add dependencies or run anything that mutates state.
- You may run the existing test suite and the application to observe behavior.

SOURCES (read all of them before writing anything)

- Engineering log: <path>
- README and any other docs: <paths>
- Source code, tests, config, schemas, migrations, CI files
- Git history (git log, notable large commits) for evidence of intent

TREAT DOCUMENTS AS CLAIMS, NOT FACTS. The engineering log describes what the
previous agent built, not necessarily what is needed. For every requirement you
extract, verify it against the code and tests. Where documents and code disagree,
record the discrepancy; do not silently pick one.

PROCESS

1. Inventory: list entry points, public interfaces (CLI, HTTP, UI, jobs), data
   stores, external integrations, and config surfaces. Cite file paths.
2. Extract the purpose in 2-3 sentences: who uses this and what problem it solves.
   If you cannot determine this from the sources, say so in OPEN_QUESTIONS.md
   instead of guessing.
3. Extract requirements. Each one must be:
   - Atomic (one testable behavior)
   - Written as observable behavior ("Given X, the system does Y"), never as
     implementation ("uses a repository pattern")
   - Given an ID (FR-001 for functional, NFR-001 for non-functional)
   - Tagged with a source (doc path / code path / test path)
   - Tagged with confidence: CONFIRMED (docs + code + test agree),
     CODE-ONLY (code does it, no doc or test mentions it),
     DOC-ONLY (documented, but not implemented or not working),
     or CONFLICT (sources disagree)
4. Classify every significant module, abstraction layer, config option, and feature
   you encounter into one of:
   - REQUIRED: traces to a requirement
   - INCIDENTAL: implementation detail serving a requirement; candidate for
     simplification
   - SPECULATIVE: built for a hypothetical future need, with no current requirement
     or usage (unused extension points, single-implementation interfaces,
     plugin systems, config flags nobody sets, unused feature flags)
   - DEAD: unreachable or unreferenced
     Provide evidence for each classification (grep results, call graph, coverage).
5. Capture non-functional requirements only where evidenced: performance targets,
   data volumes, security or compliance constraints, deployment environment,
   supported platforms.
6. Record the actual data contracts: schemas, file formats, API shapes, anything
   an external consumer depends on. These are the things refactoring must not break.

OUTPUT: docs/REQUIREMENTS.md with these sections

- Purpose
- External contracts (must not change)
- Functional requirements
- Non-functional requirements
- Component classification table (component | class | evidence)
- Doc/code discrepancies

OUTPUT: docs/OPEN_QUESTIONS.md
Anything that needs a human decision: ambiguous intent, features you can't tell are
wanted, conflicts. Phrase each as a specific question with the options you see.
Do not resolve these yourself.

When finished, report: counts of requirements by confidence tag, counts of
components by class, and the five most significant open questions. Then stop.
Do not begin refactoring.
