# Claude Code handoff prompts

Two prompts: one for the first session, one to reuse at the start of every phase after that.

Recommended model: `claude --model opusplan`. Switch to `/model opus` for Phase 3 (separability
tuning) and Phase 5 (the observed/truth split), then back.

---

## 1. Kickoff prompt — first session only

Paste this after placing the docs in the repo.

---

I'm building a portfolio data engineering project from a complete design spec. The spec is already
written and I don't want it redesigned — I want it built, phase by phase, with the checkpoints
respected.

**Read these first, in this order, before writing anything:**

1. `CLAUDE.md` — conventions, hard constraints, and the CURRENT POSITION block at the top
2. `docs/06-implementation-plan.md` — the phased plan and its checkpoints
3. `docs/01-architecture.md` — components and decisions
4. `docs/02-data-model.md` — event contracts, dbt layers, and the observed/truth boundary

Do not read `docs/08-interactive-experience.md`. It is Track B and out of scope until Track A
Phase 7 is finished and pushed.

**How I want us to work:**

- One phase at a time. Do not start the next phase until I confirm the current checkpoint passed.
- Start each phase in plan mode. Show me the plan, including how you'll satisfy that phase's
  checkpoint, before you write code.
- Commit in small logical increments as you go. Not one commit per phase.
- If something in the spec is wrong, ambiguous, or contradicts reality, stop and tell me. Do not
  silently work around it. The docs follow the data, not the other way around — if real output
  disagrees with a doc, we fix the doc.
- Ask before adding any dependency not listed in `CLAUDE.md`.

**Non-negotiable constraints** (they're in `CLAUDE.md`, but they matter enough to repeat):

- Ground truth — `attack_attempts`, `attack_runs`, `int_stage_progression`,
  `int_session_features_truth` — must never reach the triage model. `attempt_id` is an evaluation
  join key and is stripped from every model tagged `triage_input`. Getting this wrong silently
  invalidates every number the project produces.
- Every dbt model carries exactly one of the tags `triage_input` or `ground_truth`, or neither if
  it's a shared dimension. The leakage test reads these from the manifest.
- Never use `client_ts` for partitioning, ordering, or watermarking. Use `received_at`.
- No cloud services, no Terraform, no Kubernetes in this repo. Track A is local only.

**Start with Phase 0.** Read its checkpoint first, then plan how you'll meet it.

When Phase 0 is done, update the CURRENT POSITION block in `CLAUDE.md` and stop.

---

## 2. Per-phase prompt — reuse for every session after the first

Replace `<N>` and adjust the model note.

---

Continuing the batcave-ids build. We're starting **Phase `<N>`**.

Re-read the CURRENT POSITION block in `CLAUDE.md` and the Phase `<N>` section of
`docs/06-implementation-plan.md`, including its checkpoint. Read any doc that phase references.

Plan mode first. Show me:

1. What you're building this phase
2. How you'll verify the checkpoint against real output, not against your own assumptions
3. Anything in the spec that looks wrong or underspecified

Then wait for me before writing code.

Same rules as before: small commits, no scope creep into later phases, no Track B work, stop and ask
if the spec conflicts with what you find.

---

## 3. Checkpoint verification prompt — use when a phase claims to be done

---

Before we call Phase `<N>` complete, verify its checkpoint from
`docs/06-implementation-plan.md` against actual output — not against the code you wrote.

Show me the real evidence: the actual query results, the actual file listing, the actual test output.
If any part of the checkpoint can't be demonstrated with real output, say so plainly rather than
reasoning about why it should pass.

If it passes, update the CURRENT POSITION block in `CLAUDE.md` and summarize what changed.

---

## Notes on using these

**The checkpoints are the point.** They're the part most likely to get skipped and the reason this
handoff is worth anything. Phase 1's checkpoint in particular — inspecting real emitted JSON by hand
before any dbt model exists — prevents the most expensive failure mode in the whole build.

**Phase 3 will loop.** The separability checkpoint is empirical and will take several rounds of
adjustment. That's expected, not a sign something's broken. Worth switching to `/model opus` for it.

**Phase 5 deserves care.** The observed/truth split and the manifest-based lineage test are where a
quiet mistake does the most damage. Also worth Opus.

**Push public at the end of Phase 7.** Track B and Track C are enhancements to a finished project,
not prerequisites for finishing it.
