# Clean-code prompt pipeline

A set of agent-agnostic prompts that take an idea to working, understandable code and keep AI bloat out along the way. Each stage is one prompt file. You paste one stage prompt into a chat or coding agent, work through it, and carry its output file to the next stage.

## Philosophy

The goal is clean code: code that does what was asked and nothing more, and that a person who did not write it can read.

AI coding agents miss that goal in three predictable ways. They guess when the request is vague. They add things nobody asked for (extra layers, options, error handling, tests) because those look thorough. And over a long session they drift from the instructions they started with. Every mechanism in this pipeline targets one of those three.

| Mechanism                | What it does                                                                                                                                                            | Where                   |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| Requirements before code | Every requirement is one behavior with a criterion you can observe. Code that traces to no requirement is not built (rule B1).                                          | stages 1 and 2          |
| Rigor tier               | You pick T0, T1, or T2 once. It sets how much validation, error handling, and testing is right, so "production-grade" extras do not land in a personal tool.            | every stage             |
| Ask, do not guess        | A gap becomes an open question, never an assumption. An idea the agent has is listed under "Proposed" and not built.                                                    | every stage             |
| Smallest design          | Each design decision must pass the deletion test: if it were removed, which requirement would fail? If none, it is not designed.                                        | stage 3                 |
| Explicit slices          | The plan cuts the work into small runnable slices, each with a done check and a "Not in this slice" line, so the coding agent makes no design decisions of its own.     | stages 3 and 4          |
| Anti-bloat baseline      | Rules B1-B13: no single-use abstractions, pass-through wrappers, unused options, defensive checks for impossible states, restating comments, or dead code.              | stages 4 and 5          |
| Readability rules        | Rules CL1-CL6: honest names, one job per function, short and shallow functions, match the surrounding code, no clever constructs.                                       | stages 4 and 5          |
| Test discipline          | Tests assert observable behavior and are named for the requirement they verify. Tests of internals are banned, and test code is capped at 35% of implementation code.   | stages 4 and 5          |
| Self-checks per slice    | The build agent scans each slice against the rules and against the SPEC, and reports `[BLOAT CHECK]` and `[DRIFT CHECK]` lines you can hold it to.                      | stage 4                 |
| Independent review       | A separate read-only session hunts for code that no requirement justifies and must prove each finding. You approve findings by ID; nothing is removed without your say. | stage 5                 |
| Evidence log             | Every decision is logged with evidence a person who was not there can check. Speculative ideas are marked as such and can never justify code.                           | stage 3 onward          |
| Session hygiene          | One stage per session, state kept in files, and reminders to start a fresh chat before the context clutters.                                                            | every interactive stage |
| You hold the gates       | Each stage ends with a checklist it must pass, and waits for you. The agent proposes commits; you make them.                                                            | every stage             |

## Stages

| #   | Prompt                           | Reads                                            | Produces                                                                                                                        |
| --- | -------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `01-Kickoff.md`                  | your idea, or brainstorm/design notes            | `docs/KICKOFF/HANDOFF.md` (the brainstorm is archived in `BRAINSTORM.md`; later stages do not read it)                          |
| 2a  | `02a-requirements-interview.md`  | `HANDOFF.md`, or nothing                         | `SPEC.md` (`docs/KICKOFF/SPEC.chat.md` when an audit and reconcile follow)                                                      |
| 2b  | `02b-requirements-extractor.md`  | existing code (read-only)                        | `docs/KICKOFF/SPEC.audit.md`                                                                                                    |
| 2c  | `02c-requirements-reconciler.md` | `SPEC.chat.md` and `SPEC.audit.md`               | `SPEC.md` (reconciled, with `execution_directives`)                                                                             |
| 3   | `03-design-planner.md`           | `SPEC.md`                                        | `DESIGN.md`, `PLAN.md`, `AGENTS.md`, `docs/ENGINEERING_LOG.md` (opened)                                                         |
| 4   | `04-build.md`                    | `AGENTS.md`, `SPEC.md`, `DESIGN.md`, `PLAN.md`   | code, one slice at a time, plus log entries                                                                                     |
| 5a  | `05a-trim-review.md`             | `SPEC.md`, the log, the code (read-only)         | `docs/trim/TRIM-REPORT.md` (findings T-xxx, TT-xxx, and TC-xxx); `docs/trim/REQUIREMENTS-DERIVED.md` only when there is no SPEC |
| 5b  | `05b-trim-execute.md`            | `TRIM-REPORT.md` and the finding IDs you approve | one commit per finding, `docs/trim/BASELINE.md`, log entries; on "CLOSE OUT", `docs/CLOSE-OUT.md` and the `CLOSE` log entry     |

Stage 5 also runs on its own, on any repository: the review derives requirements from the documents and git history when there is no `SPEC.md` (and stops for your review of them), and the execute prompt creates the log if there is none.

2a is the default requirements path. 2b and 2c are optional and apply only to existing code. If there is no `SPEC.md`, you can create one with 2a, or with 2b and 2c.

Stages 1 to 3 end with a gate report, so each handoff is checked once before the next stage starts. Stage 4 reports per slice (the slice report) and writes a gate report only when a spike hands off to design. Stage 5a ends its report with a gate report, and 5b reports per batch and writes its gate report in the close-out.

## How to use it

### Before you start

- **Two kinds of session.** Stages 1, 2a, 2c, and 3 are conversations; any chat assistant works, and you save the output files yourself. Stages 2b, 4, 5a, and 5b need a coding agent that can read and run things inside the project folder.
- **Start every stage in a new session.** Paste the stage's prompt file and nothing else as the first message, then give it the inputs from the table. The prompts are self-contained.
- **Never paste** `kernel.md`, `engineering-log-standard.md`, or anything in `tools/`. Those are for maintaining the prompts, not for agents.
- **Use git.** Stage 5 requires it, and stage 4 uses it for its drift check.

### Pick your path

| Your situation                                   | Run                                              |
| ------------------------------------------------ | ------------------------------------------------ |
| A new idea                                       | 1, 2a, 3, 4, 5a, 5b                              |
| Brainstorm or design session notes               | 1 (upload notes), 2a, 3, 4, 5a, 5b               |
| You already know exactly what you want           | 2a (say there is no packet), 3, 4, 5a, 5b        |
| Changing or rebuilding existing code             | 2a, 2b, 2c, 3, 4, 5a, 5b                         |
| Cleaning up a codebase without changing behavior | 5a, 5b                                           |
| A quick prototype to learn something             | 4 alone at T0, then "HANDOFF" and 2a if it works |

### Pick a tier

You choose the tier in the first stage you run, and every later stage obeys it. Choose the lowest one that fits; a higher tier means more code.

| Tier | Use it for           | What you get                                                                                   |
| ---- | -------------------- | ---------------------------------------------------------------------------------------------- |
| T0   | Throwaway            | Happy path only, one file, no tests, errors crash. Ends with a "Proved / Faked" summary.       |
| T1   | Personal tool        | Input validated at the boundary, clear errors, tests for the Must requirements.                |
| T2   | Shared or production | Full validation, structured logging, timeouts, tests for every criterion and critical failure. |

### What to say

| Say                             | In stage | Effect                                                                                    |
| ------------------------------- | -------- | ----------------------------------------------------------------------------------------- |
| "next"                          | all      | Advance to the next phase, slice, or finding. Nothing advances without it.                |
| "you decide"                    | 1, 2a, 3 | The agent picks the smallest option and records it as an assumption.                      |
| "resume at Phase N"             | 1, 2a, 3 | Continue in a new chat. Paste the prompt, the last Phase Summary, and its artifact first. |
| "continue"                      | all      | Stay in this session after a `[FRESH SESSION]` reminder.                                  |
| "LOCK SPEC"                     | 2a       | Stop interviewing and go to the gate. Unresolved areas become open questions.             |
| "HANDOFF"                       | 4        | End a spike and emit a packet for stage 2a.                                               |
| "task harden" or "task teach"   | 4        | Attack the code before release, or produce explained code.                                |
| "REVIEW SINCE S-xxx" (or a ref) | 5a       | Review only what changed since that slice or git ref.                                     |
| "proceed"                       | 5a       | Accept the derived requirements (only when there is no SPEC) and continue the review.     |
| finding IDs, or "all Low risk"  | 5b       | Approve the findings to apply.                                                            |
| "CLOSE OUT"                     | 5b       | Finish: verify coverage and write `docs/CLOSE-OUT.md`.                                    |

### Stage 1: Kickoff

Turns a raw idea—or existing brainstorm and design notes—into a scope you have confirmed. Skip it if you already know what you want.

1. Paste `01-Kickoff.md`. You can either upload/paste existing brainstorm or design notes, or start from scratch. It asks up to three questions per turn through four phases: Frame, Diverge, Converge, Handoff.
2. In Frame, answer in plain words and pick the tier (with uploaded notes, it drafts the Frame from your docs and asks only about missing fields). If you use a word like "fast" or "simple", it will ask for a number or an observable behavior.
3. Diverge is a brainstorm (skipped at T0). Uploaded notes are extracted here as non-authoritative candidates, with design choices logged as `[approach]` or `[constraint]`. Nothing here is a commitment.
4. In Converge, it proposes what is Must, Should, Could, Later, and Out. Confirm every Must and every Out yourself; only confirmed items go forward.
5. Save the brainstorm as `docs/KICKOFF/BRAINSTORM.md` and the final packet as `docs/KICKOFF/HANDOFF.md`. Later stages never read the brainstorm or the raw source notes.

### Stage 2a: Requirements interview

Turns the scope into requirements that can be checked.

1. Paste `02a-requirements-interview.md`, then `HANDOFF.md`. With no packet, say so and it asks four framing questions instead.
2. It drafts one requirement at a time with its criteria, each in the form "Given, when, then". Confirm or edit each one. Bracketed defaults are the smallest option; confirm them to move quickly.
3. It will not add logging, retries, auth, or similar on its own. If you want one, ask for it; otherwise the tier covers the baseline.
4. In the Constraints phase, state your language, platform, forbidden dependencies, and working rules such as commit policy. These are copied into `AGENTS.md` later.
5. At the gate it plays back what it understood. Correct it, then save the result as `SPEC.md` in the project root (or `docs/KICKOFF/SPEC.chat.md` if you will run 2b and 2c).

### Stages 2b and 2c: Existing code

Use these when code already exists, so the plan knows what the code does today.

1. **2b, in a coding agent inside the repository:** paste `02b-requirements-extractor.md`. It is read-only and runs without asking questions. It writes `docs/KICKOFF/SPEC.audit.md`: what the code does, with evidence, and which parts look speculative or dead.
2. **2c, in a chat:** paste `02c-requirements-reconciler.md`, then `SPEC.chat.md` and `SPEC.audit.md`. It puts every disagreement to you, up to five decisions per turn, each with options such as [KEEP-MUST], [DELETE], [USE CHAT], or [KEEP CODE].
3. Save its output as `SPEC.md`. It now lists what to build, what to delete, and what existing behavior must not break.

### Stage 3: Design and plan

Turns the SPEC into the smallest design and a slice-by-slice plan.

1. Paste `03-design-planner.md`, then `SPEC.md`.
2. Choose a code style when asked: `idiomatic_pragmatist` (the default), `minimalist_surgeon`, `functional_purist`, or `systems_low_level`.
3. For each design decision it shows two or three options and recommends the simplest. You choose. At T0 there is no design phase.
4. Read the plan before you approve it. Each slice should be something you can run, with a done check you could perform yourself. If a slice is vague, say so now; vagueness here becomes guessing in the build.
5. Save `DESIGN.md` and `PLAN.md` under `docs/KICKOFF/`, `AGENTS.md` in the project root, and the log as `docs/ENGINEERING_LOG.md`.

### Stage 4: Build

Builds one slice at a time and stops after each.

1. In a coding agent inside the project, paste `04-build.md`. It reads `AGENTS.md`, the SPEC, the design, and the plan, then builds the next slice.
2. Read the slice report. The lines that matter:
   - **Done check** and **Regression**: actual results, not claims. A slice is not done without them.
   - **Criteria**: which test verifies each criterion.
   - **Lines**: how much was added, and the test ratio.
   - **Deviations** and **Proposed**: where it departed from the plan, and ideas it did not build. Accept or reject proposals explicitly; silence leaves them open.
   - **`[BLOAT CHECK]`** and **`[DRIFT CHECK]`**: what its self-checks found and fixed.
3. Read the code. If something is there that you did not ask for, say so before you go on.
4. Commit using the proposed message, then say "next".
5. After three slices it tells you to start a new session. Do so: paste `04-build.md` again and say "next". The plan and the log carry the state.
6. If it says a slice cannot be built as written, it proposes a plan change and waits for your approval. It will not re-plan silently.

Without a `PLAN.md`, stage 4 treats your message as the spec. That is the way to run a T0 spike.

### Stage 5a: Trim review

An independent, read-only hunt for code and tests that nothing justifies, and for code that is hard to read.

1. Open a new session in a coding agent, never the one that built the code. Paste `05a-trim-review.md`.
2. With no `SPEC.md`, it first writes `docs/trim/REQUIREMENTS-DERIVED.md` and stops. Correct that file, then say "proceed".
3. It writes `docs/trim/TRIM-REPORT.md` and shows the five best findings. Each finding names the rule broken, the evidence, the risk, and what would break if it were wrong.
4. Read the report and decide which findings to approve. Answer the questions listed under UNKNOWN; those items are never touched until you do.

### Stage 5b: Trim execute and close-out

Applies the findings you approved, one at a time, with the tests passing after each.

1. Start with a clean working tree. Open a new session, paste `05b-trim-execute.md`, and give the approved IDs.
2. It runs the test suite and records a baseline first. If the suite is not passing, it stops.
3. For each finding it makes the one change, runs the suite, and proposes a commit. Commit it and say "next". A change that breaks a test is reverted and reported as BLOCKED, not forced through.
4. After the batch, read the batch report and approve more findings in a new session, or say "CLOSE OUT".
5. Close-out checks that every active requirement is verified, then writes `docs/CLOSE-OUT.md`: baseline against final numbers, requirement coverage, and what was left untouched and why.

## Project layout

```
{project}/
  SPEC.md
  AGENTS.md
  docs/
    ENGINEERING_LOG.md
    CLOSE-OUT.md
    trim/
      TRIM-REPORT.md, BASELINE.md, REQUIREMENTS-DERIVED.md   (the last only without a SPEC)
      TRIM-REPORT-{hash}.md                                  (earlier reviews, renamed by the next review)
    KICKOFF/
      HANDOFF.md, BRAINSTORM.md, SPEC.chat.md, SPEC.audit.md   (as used)
      DESIGN.md
      PLAN.md
```

## Traceability

Every piece of work traces back to the idea:

`F-xx` (HANDOFF scope) -> `R-xxx` (SPEC) -> `D-xxx` / `SP-xxx` (DESIGN) and `S-xxx` (PLAN) -> `L-xxx` (log) and commit messages. Trimming adds `T-xxx` (code findings), `TT-xxx` (test findings), and `TC-xxx` (clarity findings), which appear in `trim:` commit messages and in the evidence of the log's `CHANGE` entries.

The documentation record for the whole process is `BRAINSTORM.md` and `HANDOFF.md` (stage 1), `SPEC.md` (stage 2), and `ENGINEERING_LOG.md` (stage 3 onward), closed by `CLOSE-OUT.md` (stage 5). The log is append-only, and plan changes during the build are mandatory entries. Its format is defined in `engineering-log-standard.md`. The lint rules at the end of that file are a manual checklist; no stage runs them.

## SPEC.md schema

`SPEC.md` is XML so that an interview spec and a code spec can be compared and reconciled. The schema below is the one `02a-requirements-interview.md` writes. It is copied here by `tools/assemble.py` each time it runs, so edit it in `02a-requirements-interview.md`, not here.

<!-- spec-schema:start -->

```xml
<requirements_spec source="user_chat" version="0.1" project="{name}">
  <goal>
    <idea>{one sentence}</idea>
    <problem>{who has it and why it matters}</problem>
    <success>{observable result; what must be demonstrably true to call it finished}</success>
    <actors><actor>{who or what interacts with the system}</actor></actors>
  </goal>
  <tier visibility="{private or public}">{T0, T1, or T2}; tier rules apply by reference. <deviation>{none, or list}</deviation></tier>
  <requirements>
    <requirement id="R-001" priority="{must or should}" source="{F-01, user, or derived: reason}" status="{active or withdrawn}">
      <title>{title}</title>
      <statement>{one sentence with MUST, MUST NOT, or SHOULD; solution-neutral}</statement>
      <verification>
        <criterion>Given {state}, when {action}, then {observable result}</criterion>
      </verification>
    </requirement>
  </requirements>
  <data_contracts>
    <contract id="DC-01">{observable shape of data crossing the boundary or persisting between runs: fields, formats, retention; no storage technology}</contract>
  </data_contracts>
  <non_goals><item>{item}: {one-line reason}</item></non_goals>
  <later><item priority="{could or later}">{item}</item></later>
  <constraints>
    <constraint basis="{given, chosen, or observed}" area="{technical, environment, or working}">{constraint}</constraint>
  </constraints>
  <assumptions_and_debt>
    <assumption status="{open or confirmed}">{assumption}</assumption>
    <debt severity="{high, medium, or low}" area="{auth, state, io, concurrency, or schema}">{spike shortcut to engineer properly}</debt>
  </assumptions_and_debt>
  <open_questions>
    <question blocks="{R-xxx, design, or nothing}">{question}</question>
  </open_questions>
  <change_log>
    <entry version="{0.1}" date="{date}" r_ids="{R-xxx}">{change}</entry>
  </change_log>
  <gate_report stage="requirements_chat" date="{date}">
    <check n="{1-9}" result="{pass, fixed, or na}">{one-line note}</check>
    <summary>{what was decided and what was deferred, 150 words max}</summary>
  </gate_report>
</requirements_spec>
```

<!-- spec-schema:end -->

The other two requirements prompts use the same element names:

| Element         | Interview spec                        | Audit spec (`02b-requirements-extractor.md`)                               | Reconciled spec (`02c-requirements-reconciler.md`)                                                                  |
| --------------- | ------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `source`        | `user_chat`                           | `codebase_audit`                                                           | `reconciled_truth`                                                                                                  |
| `tier`          | T0, T1, or T2                         | `unset`                                                                    | T0, T1, or T2                                                                                                       |
| Requirement IDs | `R-xxx`, with `verification` criteria | `FR-xxx`, with `confidence` and `evidence`, no criteria                    | `R-xxx`, plus `origin` (chat, code, or both)                                                                        |
| Extra elements  | none                                  | `components` (required, incidental, speculative, dead) and `discrepancies` | `components`, `decisions` (`DEC-n`), and `execution_directives` (`to_build`, `to_delete`, `invariants_to_preserve`) |
| Absent          | none                                  | `non_goals`, `later`, `change_log`                                         | none                                                                                                                |

## Sessions and drift

A long session drifts: the agent starts working from its memory of the chat and its rules lapse. The prompts guard against that in three ways.

- **One stage per session.** State lives in the files, not the chat, so a new session loses nothing.
- **Fresh-session reminders.** The interactive prompts (1, 2a, 3, 4, 5b) end a turn with `[FRESH SESSION: reason]` and the exact handoff when they catch themselves re-asking, contradicting an artifact, or breaking a rule. Answer "continue" to stay. Stage 4 also says it after every third slice, and 5b after every batch.
- **Drift checks.** Stage 4 checks each slice's whole change against the SPEC, DESIGN, and lock before it reports (`[DRIFT CHECK: ...]`). For an independent check mid-build, open a new session with `05a-trim-review.md` and say "REVIEW SINCE S-xxx" (or a git ref); it reviews only what changed since then. Never run a review in the session that built the code.

## After the first build

- **A requirement changes:** run 2a in change mode (existing `SPEC.md` plus the change), then 3 in change mode (the new `SPEC.md`, `DESIGN.md`, `PLAN.md`), then build the new slices.
- **A bug:** give it to stage 4. It reproduces the defect with a failing test named for the R-ID before fixing it. A problem that no requirement covers is a scope change and goes to 2a.
- **Other build tasks:** open stage 4 with "task harden" to attack the code before release, or "task teach" for explained code.

## Commits

Stage 4 never commits unless a project constraint in `AGENTS.md` says it may; it proposes a commit message per slice. Stage 5b works the same way by default: it makes one change, proposes the commit, and waits for you to commit and say "next". Say the agent may commit, or put that in the `AGENTS.md` project constraints, to let it commit on its own.

## The kernel

`kernel.md` is the single source for definitions shared by more than one stage. Each stage prompt carries a verbatim copy of the blocks it uses, tagged `kernel="block@version"`, so it works on its own.

| Block                                  | Used by                                                                                       |
| -------------------------------------- | --------------------------------------------------------------------------------------------- |
| `tiers@1` (T0, T1, T2)                 | kickoff, interview, reconciler (summary lines); build, trim-review, trim-execute (full rules) |
| `vague_words@2`                        | kickoff, interview                                                                            |
| `packet_envelope@2`                    | kickoff, interview, build (spike packet)                                                      |
| `precedence@1`, `missing_info@1`       | build                                                                                         |
| `baseline@3` (anti-bloat rules B1-B13) | build, trim-review, trim-execute                                                              |
| `readability@1` (rules CL1-CL6)        | build, trim-review, trim-execute                                                              |
| `session_hygiene@1`                    | kickoff, interview, design-planner, build, trim-execute                                       |
| `styles@1`                             | build                                                                                         |
| `gate_report@1`                        | the shape of a gate report; each stage's own schema carries a matching copy (not tagged)      |
| `log_standard@2`                       | design-planner, build, trim-execute                                                           |
| `trim_finding@2` (the finding formats) | trim-review (writes it), trim-execute (reads it)                                              |

To change a shared definition:

1. Edit the block in `kernel.md` and bump its version (`baseline@3` becomes `baseline@4`).
2. Update the copy in each prompt that uses it, and change its `kernel=` tag to the new version. `04-build.md`, `05a-trim-review.md`, and `05b-trim-execute.md` are rebuilt for you: change the `{{kernel:...}}` placeholder in the matching `tools/*.template.md` to the new version.
3. Run `python -I tools/assemble.py .` from this folder. It rebuilds those three prompts and fails if any copy in any prompt is stale, differs from the kernel, or no longer matches `engineering-log-standard.md`.

Edit `04-build.md`, `05a-trim-review.md`, and `05b-trim-execute.md` only through their `tools/*.template.md`. A direct edit is overwritten on the next run.

## Precedence

When instructions conflict: `SPEC.md` / `DESIGN.md` / `PLAN.md` (or your message, when there is no SPEC) > tier rules > baseline and readability > style.

## Status

All five stages are written.
