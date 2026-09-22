# Portfolio Architecture

This document specifies the architecture governing every system in this repository. It is written for
engineers evaluating design judgment, not for users evaluating features. Every section states a
trade-off explicitly and names what was rejected, because the rejection is the signal.

The repository is a monorepo of independently deployable systems unified by one non-negotiable
architectural law, stated once here and enforced individually in every project below:

> **A generative model never makes a decision that state, money, access, or a security verdict
> depends on. It only produces language.** Routing, scoring, eligibility, stage transitions, and
> classification are computed by deterministic code. Models write prose, interpret ambiguity, and
> synthesize narrative — and nothing else. Where a model's output must influence downstream state, it
> passes through a schema contract and a code-owned boundary first.

This is not a stylistic preference. It is the load-bearing decision that makes every evaluation number,
every audit trail, and every access decision in this portfolio reproducible and defensible. The
sections below show the same law implemented seven different ways, under seven different sets of
constraints, because a single example would read as coincidence.

---

## 1. The Deterministic/Probabilistic Boundary

**Trade-off: Auditability vs. Fluency.** A deterministic state machine gives an identical answer to an
identical input, forever, and that answer can be replayed and explained. A language model gives a
plausible answer, with variance, and cannot fully explain itself. Every system in this repository
routes decisions to the first kind and delegates only synthesis to the second, and each does so at a
different layer of its stack:

| System | What stays deterministic | What the model is allowed to touch |
|---|---|---|
| `SpringfieldTalentPipeline` | Pipeline stage legality (Spring Statemachine), offer accept/decline (arithmetic against BLS wage bands), full-text candidate ranking | Candidate fit narrative + score (parsed against a JSON schema), mock-interview dialogue |
| `SuperHeroOps` | Intervention effect percentage (a pure function of powerstats, normalized against the seeded roster) | The prose report interpreting that number — never the number itself |
| `Batcave_IDS` | Threat score, stage-gating, session correlation, technique catalog | Attacker attribution and technique reconstruction — and only as a *scored hypothesis*, graded against a rule-based baseline that the model has to beat |
| `Force_Balance_Pipeline` | Disturbance signature classification (SQL pattern-matching on z-scores), the Yoda agent's deployment constraints | Report-to-sensor-value inference at intake time; planet/Jedi reference-data generation (offline, frozen) |
| Databricks notebook suite | Routing (`Issue → House` lookup table), composite scoring, dimensional pivoting | Sentiment, summarization, persona-driven writing, qualitative synthesis |

The pattern generalizes: **wherever a number must be trusted, code produces the number. Wherever
prose must be produced, and only there, a model is invoked.** A reviewer can locate the boundary in
every one of these systems by asking "what would break if the model returned garbage?" — the answer is
always "a report reads badly," never "the wrong person got hired" or "the wrong stage transition
executed."

### Why this beats the alternative

The tempting alternative — let the model reason over raw state and decide — collapses the moment the
decision needs to be reproduced, audited, or trusted under adversarial input. Two concrete failures
this architecture prevents, both encountered directly during development:

- **Prompted, unconstrained scoring drifts.** `SpringfieldTalentPipeline`'s AI fit-score prompt, before
  explicit scoring bands were imposed, returned the same candidate anywhere from 20 to 70 across
  identical calls — temperature was not the cause, the model was re-inventing its own scale on every
  invocation. The fix was not a better prompt; it was moving the scale definition into a constraint the
  model cannot renegotiate.
- **Self-grading inflates its own scores.** The Databricks Gringotts pipeline separates the *generation*
  call from the *evaluation* call into two independent contexts specifically because a model grading
  its own output clusters scores near the top of the range — a documented LLM-as-judge failure mode.
  Generation and evaluation are architecturally forced apart, not just prompted apart.

A deterministic core with a bounded, schema-constrained model attached at the edges is not a
compromise on capability. It is the only architecture in which a wrong model output degrades to "the
prose is unconvincing" instead of "the system did the wrong thing."

---

## 2. Zero-Trust Boundaries: The Sensor Never Decides

`Batcave_IDS` is the clearest expression of a Zero-Trust posture in this portfolio, and its central
rule generalizes to every ingestion point in the repository: **an untrusted producer is a dumb
recorder of what it observed, never an interpreter of what it means.**

The honeypot service is the trust boundary, and its contract is one sentence: it **logs and responds —
it never evaluates, forwards, or executes request content.** Interpretation happens downstream, on
landed data, in a service that the untrusted input can never reach directly. This is enforced
structurally, not by convention:

- The honeypot's `X-Forwarded-For` trust is **off by default**, and only ever honored inside the
  compose-internal network — a service whose entire purpose is receiving attacker traffic does not
  unconditionally trust an attacker-controlled header.
- Attempt correlation, ground-truth villain identity, and technique labels never transit the same
  channel an attacker's traffic does. The simulator's private control headers (`X-Attempt-Id`,
  `X-Run-Id`, ground-truth identity) are a documented, closed control channel that does not exist in
  the honeypot's real-world analogue — **the boundary between "what the sensor is told to simulate" and
  "what the sensor would actually observe" is drawn explicitly, in a table, not left implicit in code.**
- Every event lands with its own delivery metadata (`kafka_partition`, `kafka_offset`, `landed_at`)
  appended by the trusted consumer, never trusted from the producer.

### Data governance as a lineage-level control, not a column check

The single most consequential governance decision in `Batcave_IDS` is the **observed/truth
boundary**: everything the honeypot's sensor actually saw is tagged `triage_input` and may reach the
model; everything that constitutes ground truth (villain identity, technique ID, attack outcome) is
tagged `ground_truth` and **may never reach it.** This is not enforced by naming discipline, which a
future contributor can defeat by renaming a column. It is enforced by `assert_no_ground_truth_leakage`,
a test that walks the full dbt lineage graph and fails the build if any model tagged `triage_input` has
a `ground_truth` ancestor anywhere in its history. **A rule enforced at the lineage level survives
refactors that a column-name check does not.** This is the difference between a governance policy and
a governance guarantee, and the project chose to build the guarantee.

The same governance instinct recurs, differently shaped, across the portfolio:

- `SpringfieldTalentPipeline` stores the AI's own assessment of a mock interview (`overallAssessment`)
  and the human recruiter's `RecruiterFeedback` in **separate, never-merged tables** — merging them
  would quietly launder a generated opinion into a human one. `RecruiterFeedback` lives in the
  `pipeline` package rather than `ai`, on purpose: it is the one judgment in the system no model
  produced, and the module boundary says so.
- `Force_Balance_Pipeline`'s AI-generated reference data (planet Force parameters, Jedi attributes) is
  generated once, offline, at temperature zero, human-reviewed, and committed as version-controlled
  seed data with recorded provenance — **never called at runtime.** This is load-bearing, not cosmetic:
  90 days of rolling statistical baselines derive from that backfill, so silently regenerating the
  enrichment would invalidate every downstream statistic without any visible failure. Freezing
  generative output into reviewed, versioned artifacts is the only way to keep a statistical baseline
  trustworthy when part of its input was once a model's guess.
- `Force_Balance_Pipeline`'s intake layer explicitly documents that inferred sensor values "can
  trigger emergencies but never enter baselines" — a probabilistic inference is allowed to raise an
  alarm, and is never allowed to redefine what normal looks like.

---

## 3. Streaming Architecture: Ordering Guarantees Over Convenience

`Batcave_IDS` runs a real ingest → land → transform → serve pipeline on Kafka (Redpanda), and its
design decisions consistently trade convenience for guarantees that hold under failure:

- **One topic, discriminated by event kind — not one topic per kind.** All events for a session share
  the `session_id` partition key, which is the only way to guarantee per-session event ordering across
  every event kind. Splitting into per-kind topics would break exactly the cross-kind join
  (`mart_detection_correlation`) the system depends on. Convenience at the topic level was traded for a
  correctness guarantee at the query level.
- **At-least-once delivery, with idempotent producing deliberately disabled.** Duplicates are made to
  occur on purpose so the downstream deduplication logic is exercised against real duplicate rows
  instead of asserted against a scenario that never happens. A pipeline that has never seen a duplicate
  has not proven its deduplication logic works — it has only proven the logic compiles.
- **Offsets commit only after a successful flush.** A crash between flush and commit replays the batch
  and produces duplicates on the consumer side, by design — deduplication is staging's job, not the
  consumer's, and the consumer is not trusted to be the last word on correctness.
- **Landing partitioned on `received_at`, never on the attacker-supplied `client_ts`.** The only
  trustworthy timestamp in the event envelope is the one the trusted service assigned. A client-supplied
  timestamp is treated as adversarial input from the moment it enters the schema — nullable, permitted
  to be wrong, and never used for anything that ordering or partitioning depends on.

`Force_Balance_Pipeline` applies the same streaming discipline to a physically distributed edge system:
idempotent ingestion via ULID-named immutable files and dedup on `event_id`, exactly-once semantics
through Auto Loader, and explicit handling of event-time skew (`DISCONNECTED` buffering, `BURST`
replay) rather than assuming clean, ordered delivery. **Both systems treat "the network will reorder,
duplicate, and delay events" as the default case to design for, not an edge case to patch around later.**

---

## 4. Cost, Durability, and the Local-First Trade-off

Every data-intensive system in this portfolio makes the same infrastructure trade explicitly:
**a managed cloud service optimizes for a production workload this repository does not have, and costs
durability the repository does need.**

- `Batcave_IDS` chose Redpanda over a managed streaming service and DuckDB over a cloud warehouse for
  one stated reason: **trial resources expire, and an expired dependency makes a portfolio repository
  stop being runnable months after it is written.** A single container with no account and no expiry
  produces real, sub-second delivery semantics; the managed alternatives evaluated had minimum
  buffering windows in the tens of seconds — worse *and* less durable.
- Terraform and Kubernetes were **removed** from `Batcave_IDS` after being evaluated, on the reasoning
  that provisioning infrastructure whose only purpose is hosting a demo is not itself a demonstration
  of infrastructure skill — that work was relocated to a companion repository where infrastructure is
  the actual subject. **Removing a technology because it failed to earn its place in a specific system
  is a stronger engineering signal than including it because it looks impressive on a diagram.**
- The Databricks notebook suite caps total LLM calls per pipeline run (four, in K.A.R.E.N.) as a
  first-class architectural constraint, not an afterthought — the deterministic pre-filtering layer
  exists specifically to shrink the problem before the metered resource is invoked at all, keeping cost
  and latency bounded and predictable by construction rather than by hoping the model stays cheap.
- `Force_Balance_Pipeline` is scoped to run entirely inside free-tier infrastructure limits end to end,
  from the Raspberry Pi edge probe to the Databricks Free Edition lakehouse — a cost ceiling treated as
  an architectural input, not a budget to optimize after the fact.

The consistent judgment: infrastructure is included when the system's own subject matter justifies it,
and excluded — even when it would look more impressive — when it does not.

---

## 5. Structured Output as a Contract, Not a Convention

Every model call in this portfolio that feeds a downstream system is bound to a schema, and the
architecture treats "the model returned valid, well-typed output" as a property to engineer for, not
assume:

- `SpringfieldTalentPipeline` parses LLM output against a JSON schema rather than scraping it from
  prose.
- `SuperHeroOps` requests structured JSON from Groq with **one retry on a malformed response**, and
  injects the safety disclaimer **by code, into every report, after generation** — the model is never
  trusted to include its own disclaimer, because a disclaimer that the model can omit is not a
  disclaimer.
- `Batcave_IDS`'s triage service moved from Groq's plain JSON mode to Gemini's schema-constrained
  structured output specifically to eliminate a measured 27.8% parse-failure rate. The trade this
  surfaced is instructive and is reported rather than hidden: the more reliable, schema-constrained
  model also never answered "unknown" — 0 of 36 sessions, at a flat ~0.84 confidence including on wrong
  answers — while the less reliable model calibrated its uncertainty correctly 8 of 36 times. **Neither
  property is free.** A system that always produces valid output and a system that knows what it
  doesn't know are two different reliability properties, and this portfolio's evaluation harness is
  built to measure both instead of assuming schema conformance implies correctness.
- The Databricks suite locks every enrichment call to `responseMimeType: application/json` so
  downstream parsing is a contract the pipeline can rely on, not a best-effort string match.

---

## 6. Evaluation Against a Deterministic Baseline

A model's output is a claim, and every system that lets a model's output influence a reported result
in this portfolio also runs a non-generative baseline against the same input and reports both.
`Batcave_IDS` states this as an explicit design rule: **if the LLM does not beat a regex on
high-observability techniques, the README says so** — and, in the measured results, the rule-based
baseline does in fact outperform both evaluated models on pattern-matchable evidence. This is not a
failure the project hides; it is the finding the architecture was built to surface. A generative
system that is never measured against a deterministic floor cannot be shown to be worth its cost, and
this repository does not publish an LLM result without that comparison sitting next to it.

---

## 7. System Summaries

### `Batcave_IDS` — Streaming intrusion telemetry and bounded LLM triage
**Trade-off: Detection coverage vs. sensor honesty.** An HTTP-only sensor cannot see every attack
technique — 8 of 23 catalog techniques never produce an observable event at all. Rather than let a
recall metric silently imply otherwise, technique recall is reported **by observability tier**,
converting a bare accuracy number into an honest detection-coverage gap analysis: what a real security
team would actually produce, including an explicit accounting of what the corpus structurally cannot
measure.

### `SpringfieldTalentPipeline` — State-machine-governed pipeline with advisory AI
**Trade-off: Process integrity vs. AI convenience.** Every pipeline-stage transition is legal only if
the single source of truth (`PipelineStateMachineConfig`) says so, and the error response for an
illegal transition is read back out of that same configuration rather than duplicated — the error
message cannot drift from what is actually enforced. The AI-generated fit score and interview never
touch stage legality; they are advisory artifacts a human reads before making a decision the state
machine, not the model, will validate.

### `SuperHeroOps` — Deterministic scoring with an explicit real/synthetic seam
**Trade-off: Narrative usefulness vs. representational honesty.** The application marks the exact point
where real Chicago crime data ends and a fictional scoring model begins with a visible UI banner, not
a code comment — because the boundary between real data and synthetic modeling is a claim the user
needs to see, not an implementation detail.

### `Force_Balance_Pipeline` — Agent action under a code-owned constraint layer
**Trade-off: Agent autonomy vs. deployment safety.** The Yoda agent decides *whether* and *which* Jedi
to deploy, but both automated and manual deployment paths run through the **identical constraint
layer** — human/machine decision parity is architected in, not merely tested for. An agent is free to
reason about which action to take; it is never free to redefine what actions are legal.

### `agentic_AI` suite — Multi-agent orchestration with cost-aware gating
**Trade-off: Responsiveness vs. spend.** Cheap, deterministic checks run before expensive model calls
throughout the suite — a two-stage moderation pattern in Agentic Poet runs a Python check before ever
invoking an LLM. Agent state (Spouse Approval's escalation pipeline) is an explicit, guarded state
machine, not a sequence of ungated model calls.

### `Chord_Chart_Manager` — Offline-first sync with a stated conflict policy
**Trade-off: Availability vs. consistency.** The application is fully usable offline via IndexedDB, and
on reconnection pushes local changes before pulling a fresh server snapshot; a same-song conflict
resolves deterministically in the server's favor. The trade-off — a tablet's concurrent edit can lose —
is stated plainly rather than left for a user to discover.

### Databricks portfolio notebooks — Medallion governance as doctrine
**Trade-off: Platform-specific convenience vs. portability.** Every notebook enforces Bronze→Silver→Gold
separation, per-notebook Unity Catalog schema isolation for clean teardown, and the
deterministic/LLM split as a repeated, independently-applied rule rather than a single shared library —
proving the pattern is a transferable engineering discipline, not a framework dependency. Each notebook
documents its own migration path to Snowflake, BigQuery, Fabric, and AWS, because an architecture that
only works on one vendor's platform is not an architecture; it is a integration.

---

## 8. The Same Law, Applied to the Human in the Loop

Every trade-off above governs what a model is permitted to touch inside a *system*. The `Prompts/`
collection is the same law applied one layer up, at the interaction itself: **AI should expand human
thinking, not replace it.** It is not a separate philosophy bolted onto the engineering above — it is
the same boundary discipline, restated for the case where the "deterministic core" being protected is
a person's own judgment rather than a state machine's transition table.

### The failure mode this repository is built to resist

Every prompt across `healthy-ai/`, `thinking/`, `code-dojo/`, `dev-workflow/`, `training/`, and
`writing/` — six independently designed families — is fighting one named failure mode: **the AI
quietly doing the cognitive work the human was supposed to do.** The instruction-tuned gravity of a
modern LLM pulls toward resolving, converging, and answering. Left unconstrained, that gravity is not
neutral — it erodes exactly the skill the interaction was supposed to build. The organizing test stated
in `thinking/README.md` applies to every prompt in the collection: **what does it hold open, and what
does it refuse to close?** A tool that just answers things well, however helpfully, fails that test —
it is a different, legitimate thing, but it is not this thing.

This is architecture, not tone. Three enforcement mechanisms recur across the collection because a
stated intention ("please don't just give the answer") does not survive contact with a model tuned to
be maximally helpful:

- **The scratchpad as a containment zone.** `code-dojo/` and `health/` both force the model to emit a
  hidden reasoning block before any user-facing output — a "containment zone where the AI is instructed
  to silently diagnose... to itself," venting its fine-tuned urge to over-explain where the user never
  sees it, so the user-facing output can hold strict Socratic discipline. The mechanism is identical to
  `Batcave_IDS`'s ground-truth boundary in spirit: an entire category of content (the model's own
  reasoning, or ground-truth identity) is permitted to exist, but is structurally walled off from the
  channel it must never reach.
- **Separation of roles by module, not by instruction.** `dev-workflow/`'s Coder never sees the tests
  while writing; the Tester writes from the same spec **without ever reading the code.** A single agent
  silently reconciling its own ambiguity is invisible; two agents working independently from one spec
  turn that same ambiguity into a **visible divergence a human can catch.** This is the identical
  engineering move as separating an LLM's generation call from its evaluation call in the Databricks
  Gringotts pipeline (§1) — the *reason stated for both* is the same: a single process reconciling its
  own uncertainty hides the uncertainty; two independent processes expose it.
- **Runtime as final authority over the model, where a runtime exists.** `code-dojo/`'s `code/` edition
  hands verification to an actual test runner — "if the test is green, the objection failed, and the
  dojo concedes" — explicitly because its `chat/` edition, reasoning without execution, can only ever
  produce "a hypothesis," with the human as "the final checker." This is the same hierarchy `Batcave_IDS`
  enforces between the rule-based baseline and the LLM triage result: a claim that hasn't been checked
  against something that cannot be talked out of its answer is provisional, not proven.

### Substrate awareness: the constraint has to survive the model underneath it

`Prompts/readme.md` documents something most prompt libraries never state: **the same constraint
enforces differently depending on the model's alignment substrate.** RLHF-tuned conversational models
fight friction with "sycophantic drift" — quietly stepping back into the cognitive chair to be helpful.
Open-weight models hold architectural fences more literally but degrade faster under long context.
Reasoning models with hidden chains-of-thought can defeat a deliberate multi-agent seam by reconciling
the ambiguity **privately**, before the seam ever does its job. None of this is treated as a reason to
abandon the constraint — it is treated as a reason to test the constraint against **boundary integrity**
(does it spill under pressure, does it silently resolve an ambiguity it should have surfaced, does it
homogenize back to generic assistant prose) rather than against raw output quality. A constraint that
only holds on the model it was written against is not a constraint; it is a lucky prompt.

### Where the two halves of this document meet

Section 1 states the systems-architecture law: a model never decides anything code, money, or access
depends on — it only produces language, bounded by a schema. The `Prompts/` collection states the
human-interaction corollary: a model never does the *thinking* a person is in the session to develop —
it only widens the aperture, bounded by a refusal to converge. Both rules exist for the same reason.
An unbounded generative process optimizes for a plausible-sounding output in the moment; a bounded one
is built to leave behind something durable — a reproducible system state in one case, a sharper human
judgment in the other. The discipline is identical. Only what it is protecting changes.

---

## 9. What This Architecture Deliberately Does Not Do

Stated for the same reason each project states its own limitations: a claim is only credible next to
what it excludes.

- No system in this repository lets a model output alter a state machine's transition table, a
  financial decision, or an access-control boundary at runtime.
- No system trusts client-supplied timing, identity headers, or free-text input for anything a
  security or financial verdict depends on without first passing it through a deterministic, code-owned
  boundary.
- No AI-generated reference data enters a statistical baseline without being frozen, reviewed, and
  version-controlled first.
- No generative output is reported as a capability claim without a deterministic baseline measured
  against the same input, alongside it, in the same table.

This is the discipline the rest of the repository's projects were built to demonstrate, individually,
under different constraints. It holds because it is enforced structurally — in lineage tests, in state
machine configuration, in module boundaries, in schema contracts — not because a README asks a future
contributor to remember it.

---

## 10. Looking for a Plain CRUD App?

Everything above is deliberately weighted toward streaming pipelines, bounded agents, and governed
data. If what's actually wanted is a straightforward, credential-safe CRUD workflow against an
enterprise database, that already exists in [`Shell_Scripts/db_orchestrator.sh`](./Shell_Scripts/README.md#database-orchestrator)
— one script, one consistent interface, running the same create/read/update/delete operations against
Oracle, Teradata, or SQL Server, with credentials read from a permissioned file rather than passed as
command-line arguments.
