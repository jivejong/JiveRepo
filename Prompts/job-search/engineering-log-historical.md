Act as a Staff Engineer performing code archaeology on a repository that has no usable decision record. Analyze the codebase in the working directory and draft a `LOG.md` of its major engineering decisions, based strictly on evidence in the code.

Step 1: Survey. Read the directory tree, entry points, dependency manifests, configuration, tests, benchmarks, CI files, and any existing docs. Then list the 5-10 most consequential design decisions you can evidence (concurrency model, storage choice, retry and failure handling, data contracts, dependency choices, performance work). For each, give one line, the file paths that support it, and a confidence label. Stop and wait for my corrections before writing entries.

Step 2: Write entries. After I confirm, write one entry per decision in this format:

`## [UNDATED] - <Subsystem / Decision>`

- Evidence: File paths and line ranges that support the entry (e.g., `src/consumer.py:40-88`).
- Confidence: OBSERVED (stated in code, comments, tests, or config) or INFERRED (reasoned from structure).
- Problem / Constraint: The issue or requirement this code appears to address.
- Approaches Attempted: Only what the repository shows: commented-out code, deprecated modules, TODO or FIXME notes, tests for rejected paths, config flags. If none, write "Not recoverable from code. Author to supply."
- Chosen Solution & Trade-Off: The implemented pattern and what it costs (memory, latency, operational complexity, coupling).
- Verification: Existing tests, benchmarks, or CI checks that exercise it, with the command to run them. If none, write "None found."
- Author to Confirm: One or two questions only I can answer, such as why this approach was chosen or what it replaced.

Step 3: Close with an "Open Questions" section listing every INFERRED claim and every "Author to supply," so I can fill them in from memory.

Rules:

- Base every entry strictly on the code present. Do not invent incidents, benchmarks, dates, outages, or rejected alternatives that left no trace in the repository.
- Do not write phrases like "we observed" or "after load testing" unless a file in the repository records it.
- Organize by decision, not chronology. Use [UNDATED] unless a date appears in the repository.
- Write in plain practitioner language. No generic summaries like "added features" or "improved performance."

Optional context I remember (dates, incidents, abandoned approaches, constraints):
[PASTE NOTES HERE, OR WRITE "NONE"]
