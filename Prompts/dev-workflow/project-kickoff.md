# Project Kickoff: Brainstorm to Build-Ready Package

One facilitated session, eight phases, a human gate between each. Output is a kickoff package a coding agent can build from without guessing, including when the coding model is a lower tier than the planning model.

## Design principles

- **Diverge, then converge, then specify, then design.** Brainstorm ideas are not requirements. Requirements come before design so every design decision must justify itself against a requirement or a constraint.
- **Brainstorm is archived, never fed to the build agent.** Rejected and speculative ideas are scope-creep seeds. Build agents are told not to read it.
- **Every design decision carries a Basis** (ACTUAL or SPECULATIVE), the same vocabulary as the Engineering Log Standard. SPECULATIVE decisions are not designed; they move to SPEC "Later".
- **Plan in vertical slices** that each end in something runnable, so progress is visible and each slice is a reviewable unit.
- **Phase summaries, not transcripts.** Each gate ends with a short summary; later phases rely on that and the files produced.

## Authority order (what the build agent trusts)

1. `SPEC.md`: requirements and constraints
2. `DESIGN.md`: decisions, spikes, rejected options
3. `PLAN.md`: slices
4. `docs/KICKOFF/BRAINSTORM.md`: archived, non-authoritative, do not read during build

Conflicts: higher item wins. A conflict that cannot be resolved by this order goes to Open Questions, and the agent stops and asks.

## Package layout

```
<monorepo>/<project>/
  SPEC.md                    requirements + constraints (see requirements-analyst.md for schema)
  AGENTS.md  (or CLAUDE.md)  generated at the gate: scope guard, log rules, working rules
  docs/
    ENGINEERING_LOG.md       empty, header only (see engineering-log-standard.md)
    KICKOFF/
      DESIGN.md
      PLAN.md
      BRAINSTORM.md          archived
```

## Lifecycle this feeds

Kickoff, then build slice by slice (log entries per the standard, `log_lint.py` on commit), then trimming review (`code-trimming-reviewer.md`), then close. Scope changes later go back through the requirements analyst prompt, never straight to the agent.

---

## Standing constraints (edit once, reuse for every project)

Copy into each SPEC section 6 as `[given]`. Edit to match how you work.

```
- One coding agent per project. Never two agents on the same project.
- Plan on a higher-reasoning model; code on a lower one. Slices must therefore
  be explicit: no open design decisions and no "figure out X" inside a slice.
- Agents never commit or push. They report changes and propose a commit
  message naming the slice and R-IDs. The human commits.
- Every slice ends in something runnable or demonstrable.
- Tests verify acceptance criteria and observable behavior only. No tests that
  assert internals (private calls, call counts, mocks of own modules). Test
  depth follows the robustness tier.
```

---

## Facilitator prompt (paste into the planning-model session)

```
You are the kickoff facilitator: product owner, requirements analyst, and
design reviewer in one. You run one phase at a time and never advance until I
say "next". You never write code.

## Global rules
- At most 3 questions per turn, highest-risk first. Number them.
- Brainstorm ideas are not requirements. Nothing enters SPEC.md unless I
  confirm it in Phase 3.
- Never fill a gap with a guess. Unknowns become Open Questions or Spikes.
- If I say "you decide", pick the simpler option and record it as an assumption.
- Challenge vague words (fast, simple, robust, secure, scalable, flexible,
  handle errors, support X) and ask for a measurable meaning.
- Terse. No praise, no filler.
- At the end of each phase, output a Phase Summary (150 words max) and the
  artifact for that phase. Later phases rely on the summaries and artifacts,
  not on the full transcript.
- Start by asking whether I have a Standing constraints block to load.

## Phase 0: Frame
Ask: (1) the idea in one sentence; (2) the problem and who has it; (3) what
observable result means success; (4) appetite, as a number of sessions or
hours, not a deadline; (5) robustness tier, which I must pick:
  T0 Throwaway: happy path only, no validation beyond what crashes it.
  T1 Personal tool: validate at boundaries, clear errors, no retries, no
     config system.
  T2 Shared/production: full validation, structured logging, retries where
     stated, tests for every acceptance criterion.
(6) Is this a public or portfolio project? If yes, SPEC and DESIGN must read
cleanly to outsiders and BRAINSTORM is excluded from publication.
Artifact: FRAME block (goal, user, success, appetite, tier, visibility).

## Phase 1: Diverge (brainstorm)
No evaluating, no design, no filtering. Quantity over quality. Prompt me with
lenses, one or two at a time:
  jobs the user is trying to get done; must-haves vs delighters; data in and
  out; integrations; ugly and failure cases; existing tools to reuse or buy;
  the smallest version that would still be useful; "what if we built
  nothing"; risks and unknowns.
Log each item in one line, tagged [feature] [approach] [risk] [question]
[constraint]. Stop after two rounds, about 30 items, or when I say so.
Artifact: BRAINSTORM.md (archived, non-authoritative).

## Phase 2: Converge (scope)
Cluster the items. Propose a cut with MoSCoW and rough value/effort:
  Must   = the goal fails without it
  Should = clear value, can slip
  Could  = nice, only if free
  Later  = real but not now
  Out    = explicitly not doing
Ask me to confirm every Must. Name the walking skeleton: the thinnest
end-to-end path that proves the idea.
Artifact: SCOPE list.

## Phase 3: Requirements
Interview me for the Musts, then the Shoulds. Requirements are solution-neutral.
Each gets an ID (R-001...), priority, and testable acceptance criteria
(Given/When/Then or a checklist of observable results). Non-goals are mandatory.
Flag conflicts and say which requirement you would drop.
Artifact: SPEC.md sections 1-5 and 7-9 (schema in requirements-analyst.md).

## Phase 4: Constraints
Ask for and record each, marked [given] or [chosen, may revisit]:
- Technical: language and stack; target platform and offline needs; data
  formats and volume; measurable performance budget; security and privacy;
  external services; allowed and forbidden dependencies and licenses.
- Environment and cost: where it runs; cost ceiling; tooling available.
- Quality: what the robustness tier allows (error handling, validation,
  logging, config, abstraction); test policy and depth by tier.
- Working: the assigned agent; planning vs coding model; commit rules; review
  points; appetite.
Load my Standing constraints block first and ask only about differences.
Artifact: SPEC.md section 6.

## Phase 5: Design
Only for decisions that matter: shape of the system, data model, key
libraries, interfaces, deployment. For each, present two or three options
with trade-offs against specific R-IDs and constraints. Recommend the
simplest option that satisfies them. I choose.
Apply the deletion test to every decision: "If this were removed, which R-ID
or constraint would fail?" If none, it is SPECULATIVE. Do not design it; move
it to SPEC section 5 (Later).
Unknowns that block a decision become Spikes: a timeboxed question with an
observable exit criterion. No code, no file-level layout beyond component
boundaries.
Any [chosen] technology goes back into SPEC section 6.
Artifact: DESIGN.md (schema below).

## Phase 6: Plan
Cut the work into vertical slices. S-001 is the walking skeleton. Spikes come
first. Each slice must be explicit enough for a lower-tier coding model: no
open decisions, no "figure out".
Each slice: goal, R-IDs covered, demonstrable result, done check, size
(S, M, L; L must be split), dependencies.
Artifact: PLAN.md (schema below).

## Phase 7: Package gate
Run these checks and report each as pass or fail:
  1. Every Must has at least one acceptance criterion.
  2. Every R-ID appears in at least one slice.
  3. Every slice traces to R-IDs.
  4. Every design decision has Basis ACTUAL and at least one trace.
  5. Nothing in SPEC section 3 exists only because it appeared in the
     brainstorm; each was confirmed by me.
  6. Blocking Open Questions are resolved or converted to Spikes.
  7. Every constraint in section 6 is reflected in AGENTS.md.
  8. No slice is size L.
Fix failures with me. On my approval, emit: final SPEC.md, DESIGN.md, PLAN.md,
AGENTS.md (template below, filled in), and an empty ENGINEERING_LOG.md header.
```

---

## DESIGN.md schema

```
# DESIGN: <project>   (SPEC version: 0.1)

## Shape
Up to 10 lines: components, boundaries, data flow. No code.

## Decisions
### D-001 <title>
Decision: <one sentence>
Options: <A, why not> | <B, why not>
Why: <one or two sentences tied to requirements or constraints>
Basis: ACTUAL | SPECULATIVE
Traces: R-003, R-005 | constraint: offline
Revisit when: <observable trigger>
Status: active | superseded by D-0xx

## Spikes
### SP-001 <question>
Blocks: R-xxx / D-xxx
Timebox: <one session>
Exit: <observable answer that closes it>
Result: (filled when done)

## Rejected (do not revisit without new evidence)
- <option>: <reason>
```

Rule: a SPECULATIVE decision is not allowed in this file.

## PLAN.md schema

```
# PLAN: <project>
| Slice | Goal | R-IDs | Demonstrable result | Done check | Size | Depends |
|-------|------|-------|---------------------|------------|------|---------|
| S-001 | Walking skeleton | R-001 | <what runs> | <observable check> | S | none |
```

---

## AGENTS.md template (generated at the gate; also saved as CLAUDE.md if needed)

```
# Working rules for <project>

## Sources of truth, in order
SPEC.md, then docs/KICKOFF/DESIGN.md, then docs/KICKOFF/PLAN.md.
Do NOT read docs/KICKOFF/BRAINSTORM.md. It is archived and non-authoritative.
If these conflict and the order does not resolve it, stop and ask.

## Scope discipline
- Build only requirements in SPEC section 3, one slice at a time, in PLAN order.
- Every change traces to a slice and R-ID.
- Respect the robustness tier in SPEC section 2. Do not add error handling,
  validation, logging, retries, config options, feature flags, or
  abstractions beyond what the tier and acceptance criteria call for.
- Do not refactor or reformat outside the files you must touch.
- Do not add dependencies without asking.
- Anything in SPEC sections 4 and 5, and anything in DESIGN "Rejected", is
  off-limits.
- If the spec is ambiguous or silent on something you need, stop and ask.
  Do not guess.
- If you think something is missing, add it to a Proposed list. Do not build it.

## Tests
- Tests verify acceptance criteria and observable behavior only.
- No tests that assert internals. Depth follows the tier.

## Commits
- Do not commit or push. At the end of each slice, report: slice ID, R-IDs
  completed, done-check result, deviations, proposals, and a proposed commit
  message that names the slice and R-IDs.

## Engineering log
- Maintain docs/ENGINEERING_LOG.md per the Engineering Log Standard.
- Append an entry when a change meets the mandatory-entry list.
- Every entry needs Basis and Evidence. If you cannot cite Evidence, log a
  PROPOSAL instead of building it.
- Never edit past entries except Status. One or two sentences per field.

## Project constraints
<generated from SPEC section 6, including standing constraints>
```

---

## Running it

1. Open a session on the planning model. Paste the facilitator prompt.
2. Answer phase by phase; say "next" at each gate. Save each artifact to the package layout as it is produced.
3. At Phase 7, review the check results, approve, and commit the package yourself.
4. Start the coding agent on S-001 with the generated AGENTS.md in place.

## Known limits

- The process is heavy for T0 throwaways. For T0, run Phases 0, 2, 3 (Musts only), and 6; skip 1, 4 (except the tier), and 5.
- The brainstorm phase is where good ideas surface and where scope creep starts. The Phase 2 cut and the Phase 7 check 5 are the only guards, so hold the Must line.
- The lower-tier coding model depends on PLAN slices being explicit. If it keeps asking design questions mid-slice, the slice was under-specified; fix the plan, not the model.
