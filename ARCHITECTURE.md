# Portfolio Architecture

This document is an architectural map of the portfolio, written for engineers evaluating designjudgment rather than users evaluating features.
Every section names a material trade-off and what was rejected, because those choices are the signal.

The repository is a monorepo of independently deployable systems, small utilities, notebooks, and prompt artifacts. Its recurring design principle is narrower and more accurate than a universal ban on model decisions:

> **When a decision changes durable state, money, access, safety, or a measured security outcome,
> the rule that authorizes it is owned by deterministic code.** Models may interpret ambiguity,
> generate language, or propose an action, but code retains the final eligibility, transition,
> constraint, and audit boundary. Interactive demos may use free-form model dialogue where dialogue
> is itself the product, provided it is not silently treated as a trusted system decision.

This is not a stylistic preference. It makes consequential outcomes reproducible and explainable
without pretending that every application in a varied portfolio has the same architecture. The
sections below show where that principle is enforced, where a looser interaction contract is
appropriate, and what protects each boundary. Architecture here evaluates functional requirements
alongside nonfunctional requirements (NFRs): auditability, reliability, security, latency, cost,
durability, portability, consistency, explainability, and operational complexity. Modernization means
applying current data, AI, governance, and delivery patterns to the technology estate at hand, not
reflexively replacing mature Java, .NET, SQL, scripting, or browser-native systems.

---

## 1. The Deterministic/Probabilistic Boundary

**Trade-off: Auditability vs. Fluency.** A deterministic state machine gives an identical answer to an
identical input, forever, and that answer can be replayed and explained. A language model gives a
plausible answer, with variance, and cannot fully explain itself. The systems that make consequential
decisions route the authorization rule to the first kind; interactive applications use the second for
their experience while retaining explicit limits, session controls, or a human decision point.

| System                      | What stays deterministic                                                                                                             | What the model is allowed to touch                                                                                                                    |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SpringfieldTalentPipeline` | Pipeline stage legality (Spring Statemachine), offer accept/decline (arithmetic against BLS wage bands), full-text candidate ranking | Candidate fit narrative and score, parsed against a JSON schema; mock-interview dialogue                                                              |
| `SuperHeroOps`              | Intervention effect percentage (a pure function of powerstats, normalized against the seeded roster)                                 | The prose report interprets that number, never the number itself                                                                                      |
| `Batcave_IDS`               | Threat score, stage-gating, session correlation, technique catalog                                                                   | Attacker attribution and technique reconstruction, but only as a _scored hypothesis_, graded against a rule-based baseline that the model has to beat |
| Databricks notebook suite   | Routing (`Issue → House` lookup table), composite scoring, dimensional pivoting                                                      | Sentiment, summarization, persona-driven writing, qualitative synthesis                                                                               |
| `Chord_Chart_Manager`       | Sync ordering and same-song conflict resolution (server wins)                                                                        | None in the running application; the primary trade-off is offline availability versus concurrent-edit consistency                                     |
| `Agentic_AI`                | Access-code quota checks, call budgets, and the Approval app's escalation state                                                      | Deliberately interactive classification, debate, retrieval-grounded negotiation, and multimodal generation                                            |
| `BBS_Website`               | Client-side interaction state, keyboard/mouse mode, and local navigation                                                             | No model calls; the deliberately dependency-free implementation is itself the architectural choice                                                    |

The pattern generalizes: **wherever a value authorizes a consequential action or is presented as a
measured result, code owns the rule and its audit trail.** In an interactive AI application, a bad
model response can still make the experience poor; it must not bypass the quota, state, eligibility,
or measurement boundary around that experience. A reviewer can locate the boundary by asking, "what
would break if the model returned garbage?" The answer may be "the dialogue is poor," but not "an
unauthorized transition, deployment, or security measurement was accepted."

### Why this beats the alternative

The tempting alternative, letting the model reason over raw state and decide, collapses the moment the
decision needs to be reproduced, audited, or trusted under adversarial input. Two concrete failures
this architecture prevents, both encountered directly during development:

- **Prompted, unconstrained scoring drifts.** `SpringfieldTalentPipeline`'s AI fit-score prompt, before
  explicit scoring bands were imposed, returned the same candidate anywhere from 20 to 70 across
  identical calls; temperature was not the cause, as the model was re-inventing its own scale on every
  invocation. The fix was not a better prompt; it was moving the scale definition into a constraint the
  model cannot renegotiate.
- **Self-grading inflates its own scores.** The Databricks Gringotts pipeline separates the _generation_
  call from the _evaluation_ call into two independent contexts specifically because a model grading
  its own output clusters scores near the top of the range, a documented LLM-as-judge failure mode.
  Generation and evaluation are architecturally forced apart, not just prompted apart.

A deterministic core with a bounded, schema-constrained model attached at the edges is not a
compromise on capability. It is the only architecture in which a wrong model output degrades to "the
prose is unconvincing" instead of "the system did the wrong thing."

---

## 2. Zero-Trust Boundaries: The Sensor Never Decides

`Batcave_IDS` is the clearest expression of a Zero-Trust posture in this portfolio, and its central
rule generalizes to every ingestion point in the repository: **an untrusted producer is a dumb
recorder of what it observed, never an interpreter of what it means.**

The honeypot service is the trust boundary, and its contract is one sentence: it **logs and responds;
it never evaluates, forwards, or executes request content.** Interpretation happens downstream, on
landed data, in a service that the untrusted input can never reach directly. This is enforced
structurally, not by convention:

- The honeypot's `X-Forwarded-For` trust is **off by default**, and only ever honored inside the
  compose-internal network; a service whose entire purpose is receiving attacker traffic does not
  unconditionally trust an attacker-controlled header.
- Attempt correlation, ground-truth villain identity, and technique labels never transit the same
  channel an attacker's traffic does. The simulator's private control headers (`X-Attempt-Id`,
  `X-Run-Id`, ground-truth identity) are a documented, closed control channel that does not exist in
  the honeypot's real-world analogue. **The boundary between "what the sensor is told to simulate" and
  "what the sensor would actually observe" is drawn explicitly, in a table, not left implicit in code.**
- Every event lands with its own delivery metadata (`kafka_partition`, `kafka_offset`, `landed_at`)
  appended by the trusted consumer, never trusted from the producer.

### Governance as code: a lineage-level control, not a column check

The single most consequential governance decision in `Batcave_IDS` is the **observed/truth
boundary**: everything the honeypot's sensor actually saw is tagged `triage_input` and may reach the
model; everything that constitutes ground truth (villain identity, technique ID, attack outcome) is
tagged `ground_truth` and **may never reach it.** This is not enforced by naming discipline, which a
future contributor can defeat by renaming a column. It is enforced by `assert_no_ground_truth_leakage`,
a test that walks the full dbt lineage graph and fails the build if any model tagged `triage_input` has
a `ground_truth` ancestor anywhere in its history. **A rule enforced at the lineage level survives
refactors that a column-name check does not.** This is governance as code: the policy is executable,
tested, and enforced as an architectural guardrail rather than left as contributor guidance. The
project chose a governance guarantee, not merely a governance policy.

The same governance instinct recurs, differently shaped, across the portfolio:

- `SpringfieldTalentPipeline` stores the AI's own assessment of a mock interview (`overallAssessment`)
  and the human recruiter's `RecruiterFeedback` in **separate, never-merged tables**; merging them
  would quietly launder a generated opinion into a human one. `RecruiterFeedback` lives in the
  `pipeline` package rather than `ai`, on purpose; it is the one judgment in the system no model
  produced, and the module boundary says so.

### Interactive telemetry: consent, minimization, and a separate decision seam

`Batcave_IDS` also contains an intentionally adversarial Bat bot interaction delivered through the
console experience. Its boundary is different from triage but just as explicit: the model may write a
free-form dialogue turn, while a small, versioned deterministic extractor alone determines intent
flags, refusal, and whether the conversation continues. A hard 3–5-turn cap cannot be extended by
prompt behavior. The participant first acknowledges that the interaction is simulated and that typed
content is retained locally; raw `user_text` remains out of marts and committed sample data. The
`assert_no_user_text_in_sample` test checks the published sample partition directly. This lets the
project demonstrate social-engineering telemetry without silently turning participant text into a
portable dataset.

---

## 3. Streaming Reliability: Ordering Guarantees Over Convenience

`Batcave_IDS` runs a real ingest → land → transform → serve pipeline on Kafka (Redpanda). Its design
decisions consistently trade convenience for guarantees that hold under failure. The operating
assumption is that distributed systems duplicate, delay, reorder, and occasionally corrupt input;
those are normal conditions to design for, not exceptional cases:

- **One topic, discriminated by event kind, rather than one topic per kind.** All events for a session share
  the `session_id` partition key, which is the only way to guarantee per-session event ordering across
  every event kind. Splitting into per-kind topics would break exactly the cross-kind join
  (`mart_detection_correlation`) the system depends on. Convenience at the topic level was traded for a
  correctness guarantee at the query level.
- **At-least-once delivery, with idempotent producing deliberately disabled.** Duplicates are made to
  occur on purpose so the downstream deduplication logic is exercised against real duplicate rows
  instead of asserted against a scenario that never happens. A pipeline that has never seen a duplicate
  has not proven its deduplication logic works; it has only proven the logic compiles.
- **Offsets commit only after a successful flush.** A crash between flush and commit replays the batch
  and produces duplicates on the consumer side, by design: deduplication is staging's job, not the
  consumer's, and the consumer is not trusted to be the last word on correctness.
- **Landing partitioned on `received_at`, never on the attacker-supplied `client_ts`.** The only
  trustworthy timestamp in the event envelope is the one the trusted service assigned. A client-supplied
  timestamp is treated as adversarial input from the moment it enters the schema: nullable, permitted
  to be wrong, and never used for anything that ordering or partitioning depends on.

---

## 4. FinOps: Cost as an Architectural Constraint

Every data-intensive system in this portfolio makes the same infrastructure trade explicitly:
**a managed cloud service optimizes for a production workload this repository does not have, and can
cost durability the repository does need.** These are cost-aware architecture choices, not a claim of
a formal enterprise FinOps program. Cost is treated as an NFR alongside latency, durability,
portability, and operational complexity: a metered resource must earn its use, and a service must earn
the complexity of operating it.

- `Batcave_IDS` chose Redpanda over a managed streaming service and DuckDB over a cloud warehouse for
  one stated reason: **trial resources expire, and an expired dependency makes a portfolio repository
  stop being runnable months after it is written.** A single container with no account and no expiry
  produces real, sub-second delivery semantics; the managed alternatives evaluated had minimum
  buffering windows in the tens of seconds: worse _and_ less durable.
- Terraform and Kubernetes were **kept out of `Batcave_IDS`'s pipeline** after being evaluated,
  because a single stateless service and a local broker don't need a cluster, and the pipeline runs on
  a laptop with no cloud account. They live only in an optional deployment layer
  (`Batcave_IDS/k8s-data-platform/`, Track C). That layer deploys the same code unchanged to kind and
  to an ephemeral GKE cluster on the GCP Free Trial; infrastructure is its actual subject, and the
  pipeline never depends on it. **Removing a technology from a system where it failed to earn its
  place is a stronger engineering signal than including it because it looks impressive on a
  diagram.**
- The Databricks notebook suite caps total LLM calls per pipeline run (four, in K.A.R.E.N.) as a
  first-class architectural constraint, not an afterthought: the deterministic pre-filtering layer
  exists specifically to shrink the problem before the metered resource is invoked at all, keeping cost
  and latency bounded and predictable by construction rather than by hoping the model stays cheap.

The consistent judgment is that infrastructure is included when the system's own subject matter
justifies it, and excluded, even when it would look more impressive, when it does not.

---

## 5. Structured Output as a Contract, Not a Convention

Model calls whose output is parsed, persisted as a typed record, or consumed by downstream code are
bound to a schema. This is deliberately not imposed on dialogue-first experiences such as interviews,
debates, or the Bat bot, where free-form text is the product and no generated field authorizes a system
action. The architecture treats "the model returned valid, well-typed output" as a property to engineer
for whenever a machine needs to consume it:

- `SpringfieldTalentPipeline` parses LLM output against a JSON schema rather than scraping it from
  prose.
- `SuperHeroOps` requests structured JSON from Groq with **one retry on a malformed response**, and
  injects the safety disclaimer **by code, into every report, after generation**: the model is never
  trusted to include its own disclaimer, because a disclaimer that the model can omit is not a
  disclaimer.
- `Batcave_IDS`'s triage service moved from Groq's plain JSON mode to Gemini's schema-constrained
  structured output specifically to eliminate a measured 27.8% parse-failure rate. The trade this
  surfaced is instructive and is reported rather than hidden: the more reliable, schema-constrained
  model also never answered "unknown": 0 of 36 sessions, at a flat ~0.84 confidence including on wrong
  answers, while the less reliable model calibrated its uncertainty correctly 8 of 36 times. **Neither
  property is free.** A system that always produces valid output and a system that knows what it
  doesn't know are two different reliability properties, and this portfolio's evaluation harness is
  built to measure both instead of assuming schema conformance implies correctness.
- The Databricks suite locks every enrichment call to `responseMimeType: application/json` so
  downstream parsing is a contract the pipeline can rely on, not a best-effort string match.
- `Agentic_AI/No_Cap` uses a constrained verdict vocabulary for a small classifier, while the other
  interactive apps intentionally retain prose, audio, or debate output. The contract follows the
  consumer, not a blanket preference for JSON.

---

## 6. Evaluation Against a Deterministic Baseline

A model's output is a claim; the portfolio applies a deterministic baseline where it makes an
empirical capability claim, rather than pretending that every creative or interactive demo has a
meaningful regex alternative. `Batcave_IDS` states this as an explicit design rule: **if the LLM does not beat a regex on
high-observability techniques, the README says so.** In the measured results, the rule-based baseline
does in fact outperform both evaluated models on pattern-matchable evidence. This is not a
failure the project hides; it is the finding the architecture was built to surface. A generative
system used for detection or classification cannot be shown to be worth its cost without a comparable
floor. This is both evidence-based AI engineering and a FinOps discipline: an expensive probabilistic
component should demonstrate value over the simpler alternative. Conversational systems instead expose
the controls that make their operation inspectable, such as state transitions, quotas, call budgets,
trace data, or stored results, and avoid presenting
their prose quality as a benchmark claim.

---

## 7. System Summaries

### `Batcave_IDS`: Streaming intrusion telemetry and bounded LLM triage

**Trade-off: Detection coverage vs. sensor honesty.** An HTTP-only sensor cannot see every attack
technique: 8 of 23 catalog techniques never produce an observable event at all. Rather than let a
recall metric silently imply otherwise, technique recall is reported **by observability tier**,
converting a bare accuracy number into an honest detection-coverage gap analysis: what a real security
team would actually produce, including an explicit accounting of what the corpus structurally cannot
measure.

### `SpringfieldTalentPipeline`: State-machine-governed pipeline with advisory AI

**Trade-off: Process integrity vs. AI convenience.** Every pipeline-stage transition is legal only if
the single source of truth (`PipelineStateMachineConfig`) says so, and the error response for an
illegal transition is read back out of that same configuration rather than duplicated: the error
message cannot drift from what is actually enforced. The AI-generated fit score and interview never
touch stage legality; they are advisory artifacts a human reads before making a decision the state
machine, not the model, will validate.

### `SuperHeroOps`: Deterministic scoring with an explicit real/synthetic seam

**Trade-off: Narrative usefulness vs. representational honesty.** The application marks the exact point
where real Chicago crime data ends and a fictional scoring model begins with a visible UI banner, not
a code comment, because the boundary between real data and synthetic modeling is a claim the user
needs to see, not an implementation detail.

### `Agentic_AI` suite: Independent interactive agents with cost-aware controls

**Trade-off: Expressiveness vs. operational control.** The five Streamlit apps deliberately keep their
orchestration patterns independent rather than hiding them behind a shared framework: adversarial RAG,
an explicit Approval escalation state machine, a multimodal collaborative pipeline, structured slang
classification, and a debate with independent judges. Shared controls, including user-triggered inference,
per-session quotas, access gates, saved results, and (where relevant) tracing and call budgets, limit
demo cost without presenting those controls as production authentication.

### `Chord_Chart_Manager`: Offline-first sync with a stated conflict policy

**Trade-off: Availability vs. consistency.** The application is fully usable offline via IndexedDB, and
on reconnection pushes local changes before pulling a fresh server snapshot; a same-song conflict
resolves deterministically in the server's favor. The trade-off is that a tablet's concurrent edit can lose;
it is stated plainly rather than left for a user to discover.

### `Data_Engineering` notebooks: Medallion governance and portable patterns

**Trade-off: Platform-specific convenience vs. portability.** The four data-pipeline notebooks enforce
Bronze→Silver→Gold separation, per-notebook Unity Catalog schema isolation for clean teardown, and the
deterministic/LLM split as a repeated, independently-applied rule rather than a single shared library,
proving the pattern is a transferable engineering discipline, not a framework dependency. The suite
documents migration considerations for Snowflake, BigQuery, Fabric, and AWS, making its Databricks
dependencies and their equivalents explicit rather than assuming they disappear.

### `BBS_Website`: Dependency-free, stateful interaction

**Trade-off: Framework convenience vs. a bounded delivery surface.** The BBS experience uses one
compact HTML/CSS/JavaScript shell with a small client-side state machine, keyboard and mouse modes,
Web Audio, and same-origin arcade frames. There is no bundler, router, server, or SMTP service: static
hosting is sufficient, and the contact screen intentionally delegates to the visitor's mail client or
public contact channel instead of creating an unneeded credentials-and-abuse boundary.

### `SQL_Fun` and `Shell_Scripts`: Small tools with explicit operating boundaries

**Trade-off: Generality vs. safe, inspectable scope.** The SQL experiments keep their state inside
scratch-database scripts, while operations scripts target named engines or a user-supplied directory.
The latter favor credential files, dry-run/`-WhatIf` modes, hashing, validation, and quarantine over
opaque automation; the database orchestrator is explicitly a trusted-operator utility, not a public
API.

---

## 8. The Same Law, Applied to the Human in the Loop

Every trade-off above governs what a model is permitted to touch inside a _system_. The `Prompts/`
collection is the same law applied one layer up, at the interaction itself: **AI should expand human
thinking, not replace it.** It is not a separate philosophy bolted onto the engineering above; it is
the same boundary discipline, restated for the case where the "deterministic core" being protected is
a person's own judgment rather than a state machine's transition table.

### The failure mode this repository is built to resist

Every prompt across `healthy-ai/`, `thinking/`, `code-dojo/`, `dev-workflow/`, `training/`, and
`writing/`, six independently designed families, fights one named failure mode: **the AI
quietly doing the cognitive work the human was supposed to do.** The instruction-tuned gravity of a
modern LLM pulls toward resolving, converging, and answering. Left unconstrained, that gravity is not
neutral: it erodes exactly the skill the interaction was supposed to build. The organizing test stated
in `thinking/README.md` applies to every prompt in the collection: **what does it hold open, and what
does it refuse to close?** A tool that just answers things well, however helpfully, fails that test;
it is a different, legitimate thing, but it is not this thing.

This is architecture, not tone. Three enforcement mechanisms recur across the collection because a
stated intention ("please don't just give the answer") does not survive contact with a model tuned to
be maximally helpful:

- **The scratchpad as a containment zone.** `code-dojo/` and `health/` both force the model to emit a
  hidden reasoning block before any user-facing output: a "containment zone where the AI is instructed
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
  Gringotts pipeline (§1): the _reason stated for both_ is the same: a single process reconciling its
  own uncertainty hides the uncertainty; two independent processes expose it.
- **Runtime as final authority over the model, where a runtime exists.** `code-dojo/`'s `code/` edition
  hands verification to an actual test runner: "if the test is green, the objection failed, and the
  dojo concedes," explicitly because its `chat/` edition, reasoning without execution, can only ever
  produce "a hypothesis," with the human as "the final checker." This is the same hierarchy `Batcave_IDS`
  enforces between the rule-based baseline and the LLM triage result: a claim that hasn't been checked
  against something that cannot be talked out of its answer is provisional, not proven.

### Substrate awareness: the constraint has to survive the model underneath it

`Prompts/readme.md` documents something most prompt libraries never state: **the same constraint
enforces differently depending on the model's alignment substrate.** RLHF-tuned conversational models
fight friction with "sycophantic drift": quietly stepping back into the cognitive chair to be helpful.
Open-weight models hold architectural fences more literally but degrade faster under long context.
Reasoning models with hidden chains-of-thought can defeat a deliberate multi-agent seam by reconciling
the ambiguity **privately**, before the seam ever does its job. None of this is treated as a reason to
abandon the constraint; it is treated as a reason to test the constraint against **boundary integrity**
(does it spill under pressure, does it silently resolve an ambiguity it should have surfaced, does it
homogenize back to generic assistant prose) rather than against raw output quality. A constraint that
only holds on the model it was written against is not a constraint; it is a lucky prompt.

### Where the two halves of this document meet

Section 1 states the systems-architecture law: a model never decides anything code, money, or access
depends on; it only produces language, bounded by a schema. The `Prompts/` collection states the
human-interaction corollary: a model never does the _thinking_ a person is in the session to develop;
it only widens the aperture, bounded by a refusal to converge. Both rules exist for the same reason.
An unbounded generative process optimizes for a plausible-sounding output in the moment; a bounded one
is built to leave behind something durable: a reproducible system state in one case, a sharper human
judgment in the other. The discipline is identical. Only what it is protecting changes.

---

## 9. What This Architecture Deliberately Does Not Do

Stated for the same reason each project states its own limitations: a claim is only credible next to
what it excludes.

- No implemented system lets a model output alter a state machine's transition table, a financial
  decision, or an access-control boundary at runtime.
- No system trusts client-supplied timing, identity headers, or free-text input for anything a
  security or financial verdict depends on without first passing it through a deterministic, code-owned
  boundary.
- No AI-generated reference data enters a statistical baseline without being frozen, reviewed, and
  version-controlled first.
- No generative detection or classification result is presented as a measured capability claim without
  an appropriate comparator. Free-form interactive output is presented as an experience, with its
  operational controls and limitations stated separately.

This is the discipline the rest of the repository's projects were built to demonstrate, individually,
under different constraints. It holds because it is enforced structurally, in lineage tests, state
machine configuration, module boundaries, and schema contracts, not because a README asks a future
contributor to remember it.

---

## 10. Looking for a Plain CRUD App?

Everything above is deliberately weighted toward streaming pipelines, bounded agents, and governed
data. If what's actually wanted is a straightforward, credential-safe CRUD workflow against an
enterprise database, that already exists in [`Shell_Scripts/db_orchestrator.sh`](./Shell_Scripts/README.md#database-orchestrator):
one script, one consistent interface, running the same create/read/update/delete operations against
Oracle, Teradata, or SQL Server, with credentials read from a permissioned file rather than passed as
command-line arguments.
