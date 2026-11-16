<kernel version="1.4">
<!-- Canonical shared definitions. Stage prompts embed verbatim copies of the blocks they use, tagged kernel="block@version". Edit here first, then re-copy and bump the version. -->

<tiers kernel="tiers@1">
<tier id="T0" name="Throwaway">
<summary>happy path only; no validation beyond what crashes it.</summary>
<rule>Keep everything in one file until it exceeds 300 lines.</rule>
<rule>Hardcode configs and sample inputs.</rule>
<rule>Let errors crash with the raw stack trace.</rule>
<rule>Do not write tests, mocks, interfaces, factories, dependency injection, Dockerfiles, or READMEs.</rule>
<closing>End with a 3-bullet "Proved / Faked" summary so faked assumptions stay visible.</closing>
</tier>
<tier id="T1" name="Personal tool">
<summary>validate at boundaries, clear errors, no retries, no config system.</summary>
<rule>Validate external input at the boundary; trust internal data.</rule>
<rule>Errors name the input and the cause, then exit non-zero. No retries.</rule>
<rule>Config is constants at the top of a module or command-line arguments. No config system.</rule>
<rule>Test the acceptance criteria of Must items through observable behavior only.</rule>
</tier>
<tier id="T2" name="Shared or production">
<summary>full validation, structured logging, retries where stated, tests for every acceptance criterion.</summary>
<rule>Validate every external input and field at the system boundary; keep internal domain logic free of defensive checks.</rule>
<rule>Handle errors explicitly. Never swallow one.</rule>
<rule>Set a timeout on every I/O call. Shut down gracefully.</rule>
<rule>Log in a structured format.</rule>
<rule>Add retries only where a requirement states them.</rule>
<rule>Test every acceptance criterion and the failure modes of critical paths.</rule>
</tier>
</tiers>

<vague_words kernel="vague_words@2">
<words>fast, simple, robust, secure, scalable, flexible, intuitive, clean, user-friendly, handle errors, support X</words>
<rule>Challenge each one and require a measurable meaning: a number, a threshold, or an observable behavior.</rule>
</vague_words>

<precedence kernel="precedence@1">
<order>
1. SPEC.md, then DESIGN.md, then PLAN.md, when they exist; otherwise the user's current message.
2. Tier rules.
3. Anti-bloat baseline.
4. Style profile.
</order>
<rule>A conflict the order does not resolve: stop and ask.</rule>
<rule>A request that conflicts with SPEC.md is a scope change. Do not build it; send it back to the Requirements stage.</rule>
</precedence>

<missing_info_policy kernel="missing_info@1">
<rule>Session parameters (tier, style, situation): if omitted, infer the closest match, state the lock in one line, and proceed.</rule>
<rule>Behavior (what the software does): if the spec is silent or ambiguous, stop and ask. Never guess. In an unattended run, record a blocked question and build nothing for it.</rule>
</missing_info_policy>

<anti_bloat_baseline kernel="baseline@3">
<rule id="B1" level="MUST NOT">Build anything the SPEC or the user's current request does not call for.</rule>
<rule id="B2" level="MUST NOT">Create a class, interface, factory, wrapper, or helper module with fewer than two call sites, unless DESIGN.md names it. Inline it. A named function split out of a longer one under CL2 or CL3 is not a wrapper.</rule>
<rule id="B3" level="MUST NOT">Write a function whose body only forwards its arguments to another call.</rule>
<rule id="B4" level="MUST NOT">Add checks, try/catch blocks, or fallbacks for conditions the code's own invariants rule out. Validate only where the tier requires it.</rule>
<rule id="B5" level="MUST NOT">Add a parameter, flag, option, hook, or config key for a value that has one value today.</rule>
<rule id="B6" level="MUST NOT">Write comments that restate the code or docstrings that restate the signature. Comment only a non-obvious why or an invariant.</rule>
<rule id="B7" level="MUST NOT">Leave unused code: imports, variables, parameters, functions, dead branches, or commented-out code.</rule>
<rule id="B8" level="MUST NOT">Add a dependency without asking. Prefer the standard library.</rule>
<rule id="B9" level="MUST NOT">Refactor, reformat, or rename outside the lines the task requires.</rule>
<rule id="B10" level="MUST NOT">Write tests that assert internals (private calls, call counts, mocks of the project's own modules) or language and framework guarantees (a getter returns its field, a validator rejects a wrong type). Mock only external I/O: network, third-party APIs, the system clock.</rule>
<rule id="B11" level="MUST">Write tests that assert acceptance criteria through observable behavior. Name each test for the R-ID it verifies, and cover variations of one rule in a single table-driven test. A test with no R-ID is dead weight; do not write it.</rule>
<rule id="B12" level="MUST NOT">Let total test lines exceed 35% of implementation lines, unless a requirement or constraint mandates exhaustive test vectors. If the tier's required tests would exceed it, build them and report the ratio under Deviations.</rule>
<rule id="B13" level="MUST">List anything you think is missing under "Proposed". Do not build it.</rule>
<self_check>Before returning code, scan it against B1-B12 and fix every hit. End with one line: [BLOAT CHECK: clean] or [BLOAT CHECK: fixed B2 name, B7 name].</self_check>
</anti_bloat_baseline>

<readability kernel="readability@1">
<rule id="CL1" level="MUST">Name each thing for what it holds or does, in the problem's own words. Do not abbreviate beyond the language's conventions, and do not use names that say nothing: data, info, manager, helper, util.</rule>
<rule id="CL2" level="MUST">Give each function one job, which its name states. If describing it needs "and", split it.</rule>
<rule id="CL3" level="MUST">Keep each function under about 40 lines and nested at most 3 levels. Use guard clauses and early returns before you add a level.</rule>
<rule id="CL4" level="MUST">In existing code, match the file around you: its naming, layout, error handling, and idiom. The surrounding code outranks the style profile.</rule>
<rule id="CL5" level="MUST NOT">Write the same logic in a third place. Extract it on the third occurrence, not before.</rule>
<rule id="CL6" level="MUST">Prefer the plain construct to the clever one. No nested ternaries, no one-liner that does several steps, and no metaprogramming where a loop or an if reads directly.</rule>
<self_check>Scan the code against CL1-CL6 in the same pass as the bloat check, fix every hit, and name the fixes in the BLOAT CHECK line: [BLOAT CHECK: fixed B7 name, CL3 name].</self_check>
</readability>

<session_hygiene kernel="session_hygiene@1">
<rule>Work from the files and artifacts, not from your memory of this conversation. Re-read a requirement, decision, or rule in its source before you act on it.</rule>
<rule>Recommend a fresh session when any of these happens: you ask for something the user already gave; you contradict an earlier summary or artifact; you break a rule of this prompt and have to correct it; or the session passes a limit this prompt sets.</rule>
<rule>To recommend it, end the turn with one line, [FRESH SESSION: {reason}], then the handoff: what to save, and exactly what to paste into the new session to resume. The user may answer "continue" to stay in this session.</rule>
</session_hygiene>

<gate_reporting kernel="gate_report@1">
<description>Every stage ends by recording its gate in its main artifact: as the last child of the root element for XML artifacts, or as a final "Gate report" section for Markdown artifacts.</description>
<form>
<gate_report stage="{stage id}" date="{date}">
  <check n="{number}" result="{pass, fixed, or na}">{one-line note}</check>
  <summary>{what was decided and what was deferred, 150 words max}</summary>
</gate_report>
</form>
<rule>pass: the check held. fixed: it failed and was corrected with the user before emission. na: it does not apply to this tier or situation.</rule>
</gate_reporting>

<packet_envelope kernel="packet_envelope@2">
<description>Every handoff into the Requirements stage uses this core. Source-specific sections extend it. Root element: handoff_packet, with attributes source and version. The core ends with a gate_report, which the next stage ignores.</description>
<core>
<frame><problem/><success/><tier/></frame>
<constraints_stated><constraint basis="given, chosen, or observed"/></constraints_stated>
<assumptions><assumption/></assumptions>
<open_questions><question blocking="yes or no"/></open_questions>
<gate_report/>
</core>
<extensions>
<source id="kickoff">frame adds idea, appetite, visibility. Adds scope (items F-xx with priority), walking_skeleton, excluded_input.</source>
<source id="spike">Adds validated_mechanics (items with status proven), shortcuts_and_debt (items with severity and area), data_contracts. Measured limits go in constraints_stated with basis observed.</source>
</extensions>
</packet_envelope>

<styles kernel="styles@1">
<style id="minimalist_surgeon">
<aesthetic>Flat control flow, data-oriented; every line earns its place.</aesthetic>
<rule>Prefer plain structs or records and pure functions over class hierarchies.</rule>
<rule>Use guard clauses and early returns. Nest at most 2 levels.</rule>
<rule>Delete single-use abstractions and redundant wrappers.</rule>
</style>
<style id="functional_purist">
<aesthetic>Immutable data pipelines with I/O pushed to the outer shell.</aesthetic>
<rule>Keep core logic pure: data in, data out, no side effects or in-place mutation.</rule>
<rule>Represent domain states with tagged unions or strict types, not boolean flags.</rule>
<rule>Express multi-step logic as composable data transformations.</rule>
</style>
<style id="systems_low_level">
<aesthetic>Mechanical sympathy: predictable memory layout, no hidden allocations, explicit lifetimes.</aesthetic>
<rule>Minimize heap allocations on hot paths; prefer contiguous data and value types.</rule>
<rule>Make ownership, concurrency synchronization, and resource cleanup explicit.</rule>
<rule>Avoid dynamic dispatch and reflection where static types suffice.</rule>
</style>
<style id="idiomatic_pragmatist">
<aesthetic>Boring, idiomatic code that reads like the language's standard library.</aesthetic>
<rule>Follow the language's native conventions; do not import patterns from other ecosystems.</rule>
<rule>Balance conciseness with readability for any experienced engineer joining the repo.</rule>
</style>
</styles>

<log_standard kernel="log_standard@2">
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
</log_standard>

<trim_finding kernel="trim_finding@2">
<description>One entry in docs/trim/TRIM-REPORT.md. The review writes it and the execute stage reads it. Every field is required. T is a code finding, TT a test finding, and TC a clarity finding on code that stays.</description>
<code_finding>
### T-001 {title}
Location: {file:lines}
What: {one sentence}
Rule: {the baseline rule id or tier rule the code breaks}
Why suspect: {the search or tool output that found no requirement and no reference}
Created by: {L-xxx entries or a commit hash, or unknown}
Blast radius: {callers, tests, dependent config}
Risk: {Low, Medium, or High}: {reason}
Action: {delete, inline, simplify, or keep and document}
If wrongly removed: {the R-xxx that would break}
Orphan tests: {names of the tests removed with this change, or none}
Lines: {estimated lines removed}
</code_finding>
<test_finding>
### TT-001 {title}
Location: {file:lines}
Class: {ORPHAN, COUPLED, REDUNDANT, or UNCLEAR}
Rule: {B10, B11, B12, or the tier rule}
Tied to: {T-xxx, or none}
Action: {delete with its code, rewrite to test behavior, or drop}
Lines: {estimated lines removed}
</test_finding>
<clarity_finding>
### TC-001 {title}
Location: {file:lines}
Rule: {CL1 to CL6}
What: {one sentence: what a reader trips on}
Serves: {the R-xxx the code implements}
Action: {rename, split, flatten, deduplicate, or rewrite plainly}
Risk: {Low, Medium, or High}: {reason}
Lines: {estimated lines changed}
</clarity_finding>
<rule>Order code findings by lines removed divided by risk. Action "keep and document" changes no behavior: it adds only a comment or doc line that states the reason.</rule>
</trim_finding>

</kernel>
