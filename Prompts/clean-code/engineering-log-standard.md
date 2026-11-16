<engineering_log_standard version="1.1">

<purpose>
The engineering log is the project's dated record of decisions and the facts behind them. It starts when design opens and runs to close-out. It records what was decided or changed, why, and the evidence. It does not narrate activity.
</purpose>

<header_schema>
# Engineering log: {project}
Opened: {YYYY-MM-DD}
SPEC version: {version at open}
Lock: tier {T0, T1, or T2}; situation {greenfield or existing}; style {id or unset}
Standard: engineering-log-standard {version}
</header_schema>

<entry_schema>
## L-001 {title}
Date: {YYYY-MM-DD}
Stage: {design, build, trim, or close}
Type: {DECISION, SPIKE, SLICE, CHANGE, DEVIATION, PLAN_CHANGE, PROPOSAL, or CLOSE}
What: {the decision, result, or change; one or two sentences}
Why: {the reason; one or two sentences}
Basis: {ACTUAL or SPECULATIVE}
Evidence: {something a person who was not there can check}
Traces: {R-xxx, D-xxx, SP-xxx, S-xxx, L-xxx}
Status: {see statuses}
</entry_schema>

<types>
DECISION: a choice that shapes the project.
SPIKE: the result of a timeboxed question.
SLICE: a plan slice was completed.
CHANGE: a change to shipped code outside a slice's scope, such as a fix or a trimming deletion.
DEVIATION: the work departs from DESIGN.md or PLAN.md in a way that stays inside SPEC.md.
PLAN_CHANGE: the plan or the SPEC version changed (re-slice, split, merge, reorder, defer, or a SPEC version bump).
PROPOSAL: something the agent thinks is missing or worth doing and did not build.
CLOSE: the project was closed out.
</types>

<statuses>
All types except PROPOSAL: active (in force) or superseded by L-xxx.
PROPOSAL: open, accepted, or rejected.
</statuses>

<mandatory_entries>
<event type="DECISION" stage="design">Each decision in DESIGN.md.</event>
<event type="SPIKE" stage="design or build">Each spike result.</event>
<event type="SLICE" stage="build">Each completed slice. What names the result; Evidence is the done-check command and its result; Traces holds the slice and its R-IDs.</event>
<event type="DECISION" stage="build">Each dependency added with the user's approval.</event>
<event type="DEVIATION" stage="build">Any departure from DESIGN.md or PLAN.md. A departure from SPEC.md is a scope change: stop and ask instead.</event>
<event type="PLAN_CHANGE" stage="design or build">Any re-slice, split, merge, reorder, deferral, or SPEC version bump.</event>
<event type="PROPOSAL" stage="any">Each item the agent adds to its Proposed list.</event>
<event type="CHANGE" stage="build or trim">Each change to shipped code outside a slice's scope.</event>
<event type="CLOSE" stage="close">The close-out. Evidence cites the close-out report.</event>
<not_required>Routine edits inside a slice, formatting, and renames inside files the slice touches.</not_required>
</mandatory_entries>

<rules>
<rule id="L1" level="MUST">Append entries at the end. Never edit or delete a past entry except its Status line.</rule>
<rule id="L2" level="MUST">Write one entry per event. IDs run L-001, L-002, ... and are never reused.</rule>
<rule id="L3" level="MUST">Keep each field to two sentences or fewer.</rule>
<rule id="L4" level="MUST">Give every entry a Basis and an Evidence a person who was not there can check: an R-ID or constraint, a command and its result, a test name, a file and line, a measurement, or a dated user instruction.</rule>
<rule id="L5" level="MUST">If you cannot cite Evidence for something you want to build, log a PROPOSAL and do not build it.</rule>
<rule id="L6" level="MUST">Use Basis SPECULATIVE only on PROPOSAL entries. Anything built is ACTUAL.</rule>
<rule id="L7" level="MUST">Write the entry in the same session as the event, before reporting the slice or proposing a commit message.</rule>
<rule id="L8" level="MUST NOT">Record narration of steps taken, restate code, or include secrets or credentials.</rule>
<rule id="L9" level="MUST">Change a PROPOSAL's Status only on the user's instruction, and cite that instruction and its date in Evidence.</rule>
</rules>

<lint_rules>
A manual checklist for the user. No stage prompt runs it.
1. The header is present with all five lines.
2. Entry IDs are sequential, with none missing or repeated.
3. Every entry has every field, and Stage, Type, Basis, and Status hold valid values for the type.
4. Dates are ISO and never earlier than the previous entry's date.
5. Basis SPECULATIVE appears only on PROPOSAL entries.
6. Evidence is not empty and not a placeholder.
7. No field runs past two sentences.
8. Past entries are unchanged against git HEAD, except their Status line.
9. Every ID in Traces exists in SPEC.md, DESIGN.md, PLAN.md, or the log itself.
10. Every completed slice in PLAN.md has a SLICE entry.
</lint_rules>

<example>
## L-001 Store ledger as append-only CSV
Date: 2026-03-02
Stage: design
Type: DECISION
What: The ledger is one append-only CSV file with no database.
Why: R-004 needs a human-readable export and the data stays under 10,000 rows.
Basis: ACTUAL
Evidence: R-004; constraint "no external services"; DESIGN.md D-002.
Traces: R-004, D-002
Status: active

## L-002 Add parse command
Date: 2026-03-04
Stage: build
Type: SLICE
What: The parse command reads a bank export and prints normalized rows.
Why: Slice S-002 delivers R-001, the first end-to-end path.
Basis: ACTUAL
Evidence: Done check "tool parse sample.csv" printed 42 rows and exited 0.
Traces: S-002, R-001
Status: active

## L-003 Support a second bank format
Date: 2026-03-04
Stage: build
Type: PROPOSAL
What: Parsing could accept a second bank's export format.
Why: Sample data hints at it, but no requirement asks for it.
Basis: SPECULATIVE
Evidence: No R-ID; noted while building S-002.
Traces: S-002
Status: open
</example>

</engineering_log_standard>
