# 06 — Implementation plan

Three tracks. Each ends at a point where the project is complete and showable, so work can stop at
any track boundary without leaving something half-built.

| Track | Phases | Hours | Ends with |
|---|---|---|---|
| **A — Core pipeline, headless** | 0–7 | 45–60 | A complete data engineering project, runnable with `make`, ready to push public |
| **B — Interactive experience** | 8–10 | 25–35 | Terminal console, bat bot, finale, dashboard |
| **C — Cloud landing (optional)** | 11 | 10–20 | Either AWS or DigitalOcean, or skip entirely |

Total if all three: **80–115 hours**. Track A alone: **45–60**.

Every phase has a checkpoint. **Do not begin a phase until the previous checkpoint is satisfied
against real output.**

---

# Track A — Core pipeline (headless)

No user interface beyond the command line. The simulator drives the whole kill chain
programmatically. This is the track that closes the data engineering gap.

## Phase 0 — Scaffolding  (2–4h)

- Repository structure per `CLAUDE.md`
- `pyproject.toml` with `uv`, `ruff`, `pytest`, `sqlfluff`
- `docker-compose.yml`: Redpanda + console, pinned tags, health checks
- `Makefile` with targets stubbed
- `.gitignore`, `.env.example`, pre-commit hooks
- GitHub Actions: lint, pytest, `dbt compile`
- Repository description and topics set

**Checkpoint:** `make dev-up` brings up Redpanda, `rpk topic list` shows `attack.events` with 3
partitions, console reachable on `:8080`, CI green.

## Phase 1 — Honeypot and event envelope  (4–6h)

- Route table with sensitivity tiers
- Pydantic models for the shared envelope and the `request` kind
- Session ID derivation from source IP + UA + **gap since last request** (not a fixed time bucket
  — see docs/02's "Session derivation" section for why, and for the Phase 3 recalibration note)
- Kafka producer keyed by `session_id`, `acks=all`, idempotence off
- Unit tests for routing and session assignment

**Checkpoint:** curl each endpoint, read messages back with `rpk topic consume attack.events`,
inspect the JSON by hand. Every field in the `docs/02-data-model.md` envelope present with the right
type, key equals `session_id`. If reality disagrees with the doc, fix the doc. **Write no dbt model
until this is done.**

## Phase 2 — Technique catalog and stage machine  (6–10h)

- `transform/seeds/techniques.csv` authored, 6–10 techniques per interactive stage
- **Verify every `attack_id` against attack.mitre.org.** The starter table in
  `docs/07-attack-chain.md` is a draft, not a reference.
- `dim_stages` seed
- Stage machine: enter, attempt, resolve, advance or stall
- Gating derived from `min_intelligence` and secondary gates
- Probability model with retry penalty, emitting `computed_probability` and `roll`
- Attempt events published with `event_kind = 'attempt'`

**Checkpoint:** run a scripted session headlessly. Confirm Killer Croc (int 19) is offered exactly
the low-gate techniques and Ra's al Ghul (int 100) the full catalog. Confirm no attempt violates its
gate. Confirm attempts and their generated HTTP requests share an `attempt_id`.

## Phase 3 — Villain behavior and pathologies  (10–14h)

- Vendor the akabab subset; `transform/seeds/villains.csv` committed
- `BehaviorProfile` derived from all six stats (Layer 1, `docs/03-attack-simulation.md`)
- Twelve signatures (Layer 2)
- Retry-versus-pivot decision driven by durability and available alternatives — replaces Phase 2's
  placeholder policy (`services/simulator/session.py`'s `_decide_next_action`: fixed retry cap,
  pivot to next untried technique). Only that function changes; the surrounding plumbing (attempt
  sequencing, event publishing, gating, probability) stays as built.
- `services/simulator/pathologies.yml` with all ten pathologies
- `make attack VILLAIN=<slug>` and `make attack-all`
- `attack_runs` rows written
- **Recalibration items carried from Phase 2, with no empirical basis yet:** the `detected`-outcome
  noise formula (`services/simulator/probability.py`, static `noise_level` only — docs/03's
  intelligence-driven evasion isn't in it) and `dim_stages.difficulty_multiplier`'s values
  (`transform/seeds/stages.csv`, a guessed step). Check both against real attempt outcomes once
  `BehaviorProfile` exists, alongside the powerstat mapping itself.
- **Per-run `source_ip` variation.** Confirmed empirically in Phase 2: two different villains run
  back-to-back from the same process land in the *same* session_id, since gap-based sessionization
  keys on `(source_ip, user_agent)` and the simulator doesn't vary either yet. `make attack-all`
  running twelve villains sequentially needs distinct `source_ip` per run (or enough of a gap
  between runs) or their sessions will merge in the ground truth — this needs a decision before the
  separability checkpoint below can be trusted.
- **Killer Croc never reaches stage 4.** Confirmed for all twelve in Phase 2 (`services/simulator/
  catalog.py`'s `gated_techniques`): he's the only villain with an empty stage-3 set (lowest
  stage-3 `min_intelligence` is 45; he's 19), so he stalls at stage 2 and never attempts
  `deploy_batbot`. Either a coherent story (loud, caught early, contributes two techniques' worth
  of evidence) or a sign stage-3 thresholds need loosening — decide with the real stage-3/4
  distribution in hand (docs/07), not from one villain's case.

**Checkpoint, two parts.**

*Separability:* run all twelve for 120 seconds. Confirm Riddler and Two-Face separate on
`riddle_param_count` and `exact_duplicate_path_pairs`; Scarecrow and Penguin on `error_ratio` and
`distinct_source_ips`; Joker and Harley Quinn on burst structure. Confirm Killer Croc shows the
highest `retry_ratio`, and that Catwoman and Ra's al Ghul both reach tier 4 in under 25 requests
(Ra's al Ghul's discriminator — near-zero wasted requests, not pivot volume; the earlier
"highest `pivot_ratio`" claim was wrong, since a villain who clears stages immediately makes few
pivot decisions — see docs/03). This will loop — budget for it.

**Measure separability as effect size** (centroid distance ÷ pooled within-villain spread), not raw
centroid distance: Phase 6 classifies a single session, not a villain average, so two distant-but-
overlapping distributions are not actually separable. The harness (`make separability`) reports
both, effect size as the headline. Cross-check note for Phase 5: the harness computes these features
in SQL precisely so Phase 5's dbt feature models can lift the same expressions — a divergence
between the two on the same event corpus is a bug in one of them, not an acceptable difference.

*Pathologies:* run with all enabled and verify each actually appears in the data. A pathology
configured but not present means the injection is broken and Phase 5's handling will be untested.

## Phase 4 — Consumer and landing  (6–10h)

- Consumer group, batch flush on 5,000 messages or 30 seconds
- Partitioned Parquet by `event_kind` and `dt`/`hour` from `received_at`
- `kafka_partition`, `kafka_offset`, `landed_at` appended
- **Offsets committed only after successful flush** — at-least-once by design
- Undeserializable messages to `data/quarantine/undeserializable/`, consumer continues
- Consumer restart exercise, documented in `docs/exercises.md`

**Checkpoint:** partitions correct, counts reconcile against `requests_sent` and `attempts_made`.
Kill the consumer mid-batch, restart, confirm the replayed batch produces visible duplicates.

## Phase 5 — dbt transformation layer  (12–16h)

- Sources over landed Parquet with Hive partitioning (`union_by_name = true`); seeds loaded
- staging, intermediate, marts per `docs/02-data-model.md`, including `int_stage_progression` and
  the three analytic marts
- All generic and singular tests, including `assert_technique_gating_respected`,
  `assert_probability_calibration`, `assert_stage_monotonic`, `assert_twoface_duplicates_survive`,
  `assert_session_ordering_preserved`, `assert_no_ground_truth_leakage`
- `mart_reconciliation`
- `make transform`

**Checkpoint:** `dbt run && dbt test` green. Print `mart_reconciliation` and `mart_killchain_funnel`
with real counts. Every pathology visibly accounted for — deduplicated, quarantined, flagged, or
counted. Nothing silently dropped. Commit one sample partition so the project runs from a clean
clone.

`mart_reconciliation` must reproduce the pre-consumer counts the Phase 3 harness
(`services/simulator/pathology_check.py`, `make pathology-check`) already prints on the same corpus
— requests sent by the simulator, request events on the topic, duplicate-delivery copies, invalid
JSON bodies, late arrivals, undeserializable messages, `attack_run` rows. The harness computes them
in Python before the consumer exists; the dbt mart recomputes them after landing and dedupe, and the
two must agree on the same seeded corpus (the seed-driven injection counts are exact; the
timing-derived burst count is approximate). This is the same harness/dbt cross-check the separability
harness sets up for Phase 5's feature models — build the Python number first, make dbt match it.

**Two traps Phase 4's `landing_check.py` (`services/consumer/landing_check.py`) already hit reading
this same layout, both real for dbt sources too:**
- **`union_by_name = true` is required, not optional.** The schema-drift pathology means sibling
  Parquet files in one `dt=/hour=` directory legitimately differ by a column (`tls_fingerprint`);
  DuckDB's default strict read fails across them.
- **`received_at` lands as `TIMESTAMP WITH TIME ZONE`, and DuckDB renders those in the session's
  local zone**, not UTC — `strftime(received_at, '%H')` silently returns the wrong hour anywhere but
  UTC and disagrees with the `hour=` partition value for every row. `SET TimeZone = 'UTC'` on the dbt
  profile/connection, or every `dt`/`hour` comparison against `received_at` is wrong.

## Phase 6 — Scoring, triage, evaluation  (8–12h)

**First item, before anything else: fix deterministic technique selection and re-run the baseline.**
Phase 5 found that 4 of the 23 catalog techniques receive zero attempts in any corpus, because
`services/simulator/session.py` opens a stage with `candidates[0]` and pivots to `untried[0]` —
strict `techniques.csv` row order, no randomness — so runs end before reaching the tail of a stage's
list (stage-4 attempts: 405, 210, 153, 82, 32, 0, 0). One unreachable technique is in each
observability tier, including `deploy_batbot` at `high`.

This is deliberately paid here rather than in Phase 5, because technique coverage is *this* phase's
headline metric and discovering mid-phase that the denominator is wrong is worse than budgeting for
it. Changing selection changes the corpus, so re-run and re-record, in this order:

1. The separability effect sizes (`make separability`) — the Phase 3 baseline and its closest-pair
   findings
2. The pathology counts (`make pathology-check`) — all ten, plus the reconciliation block
3. `mart_reconciliation` — the harness/dbt cross-check below is against the *new* numbers
4. The coverage denominators in `docs/04` — they should become 23 of 23 if the fix works

If the fix does not make all 23 reachable, say so and keep the reachable-denominator framing rather
than quoting a coverage figure over techniques that never appeared.

- `mart_threat_scores` with component breakdown
- Rule-based baseline classifier
- Triage service, prompt v1, strict JSON with repair retry, baseline fallback without an API key
- `fct_intervention_orders`, `fct_triage_evaluations` with exact / top-3 / archetype accuracy
- `make triage`, `make eval`

**Checkpoint:** 30+ sessions across all twelve villains through triage. Print the tiered evaluation
table. Record real accuracy and the baseline comparison — both go in the README regardless of what
they say. Confirm `assert_no_ground_truth_leakage` still passes.

## Phase 7 — Orchestration and presentation  (8–12h)

**This is the shippable milestone. Push it public here.**

- Dagster assets wrapping dbt via `dagster-dbt`, plus `scored_sessions`, `intervention_orders`,
  `triage_evaluations`; schedule and directory sensor
- Architecture diagram at the top of the README
- Dagster asset lineage screenshot
- Redpanda Console screenshot showing consumer lag during a burst
- `mart_reconciliation`, `mart_killchain_funnel`, and the tiered evaluation table, all with real
  numbers
- Decision log with rejected alternatives and why
- Section on what was deliberately left out — Terraform, Kubernetes, cloud — and the reasoning
- Honest limitations: synthetic data, single-node broker, DuckDB single-writer lock
- Roster exclusions noted (Clayface and Mad Hatter absent from the dataset, Man-Bat excluded)
- `docs/exercises.md` complete

**Checkpoint:** clone into a fresh directory. `make dev-up`, `make attack`, `make transform`,
`make eval` all work with only Docker and Python. Then hand the README to someone unfamiliar with
the project — if they cannot describe what it does and which technologies it uses in 30 seconds,
revise it.

---

# Track B — Interactive experience

Presentation layer over models that already exist. Fun to build, and it contributes least to the
data engineering gap, which is exactly why it comes after Track A is shippable.

## Phase 8 — Terminal console  (10–14h)

- Static SPA with a persistent `SIMULATION` frame, present from the first screen
- Lex Luthor welcome message (`docs/08-interactive-experience.md`)
- Villain picker with powerstats and images from the seed
- Per-stage terminal view: available techniques gated by stats, parameters, roll and outcome
  streamed as log lines
- Retry-or-pivot choice surfaced to the user
- Stage transitions

**Checkpoint:** a full run through all four stages produces the same event stream as the headless
simulator. The dbt models need no changes. If they do, the interface is emitting something the
headless path did not, and that divergence should be resolved in favor of the headless contract.

## Phase 9 — Bat bot  (8–12h)

- Blocking consent notice before the chat opens, including the warehouse-storage disclosure
  (exact text in `docs/08-interactive-experience.md`)
- 3–5 turn cap, hard enforced, engagement-driven within that window (docs/08)
- One shared, versioned keyword module producing `extracted_intent_flags`/`refused`/`engaged` for
  both the real-LLM and zero-credential paths — not two separate heuristics
- Separate prompt file, same Gemini model as triage; scripted fallback without an API key
- `botchat_turns` events, two rows per round (docs/02)
- **No new dbt models** — `stg_botchat_turns`, `fct_botchat_turns`, and
  `int_session_features_observed`'s chat features were pre-built dormant in Phase 5. This phase
  lands the first real data through them.
- `assert_no_user_text_in_sample.sql` **upgraded in place** to read the committed sample's
  `chat_turn` Parquet directly, replacing the schema-only check Phase 8 shipped before any
  `chat_turn` data existed to test against

**Checkpoint, in parts:**

- The consent notice cannot be bypassed, and the chat endpoint doesn't silently allow entry without
  it either — checked directly, not assumed from the frontend gate.
- A real conversation, played on purpose (`deploy_batbot` doesn't happen by chance — pick it
  deliberately), with the turn count actually observed varying within `[3, 5]` across at least two
  play-throughs with different engagement.
- The turn cap holds two ways: a deterministic unit test pins `[3, 5]` on adversarial input
  (always-engaged never exceeds 5; always-refused never goes below 3), and the same bound holds on
  real consumed Kafka messages from the sessions just played. Neither substitutes for the other.
- `make transform` green, with `int_session_features_observed`'s three chat features going non-zero
  for the first time and reconciling against what was actually played — not just populating.
- `user_text` containment re-checked against the real model definitions with real data landed: a
  column-inventory query confirms it exists on `stg_botchat_turns` only, nowhere else in the
  warehouse.
- **The sample partition contains no `user_text` — verified two ways, not one.**
  `assert_no_user_text_in_sample` now reads the committed Parquet directly and must pass for the
  real reason (confirmed non-vacuous the way `assert_twoface_test_is_not_vacuous` already is: land a
  test row with `user_text` somewhere *outside* the sample first, confirm the check would have
  caught it). Separately, a one-time hand grep against the real committed sample file, recorded in
  `docs/09-engineering-log.md` — the dbt test is the durable guard, the hand grep is the proof the
  guard is checking the right thing.
- **`bot_text` read by eye, every time the sample is regenerated — not covered by the `user_text`
  check above, and not something a test can cover.** `user_text` redaction says nothing about
  `bot_text`, which a stateful, context-aware bot will naturally echo the player's own words back
  into (docs/02's scope note). Before treating a regenerated sample as final, read every `bot_text`
  value in its committed `chat_turn` file and confirm nothing identifying got echoed back — the
  same kind of hand-check this project already does for everything else that touches the committed
  public artifact (the golden RNG diff, the `user_text` grep itself).

## Phase 10 — Finale and dashboard  (7–9h)

- Counterstrike sequence rendered inside the simulated terminal panel, prefixed
  `[BATCOMPUTER → LUTHOR-RELAY-07]`, never taking over the viewport
- Suspect attributed from the **triage model's prediction**, so a wrong prediction accuses the wrong
  villain
- Batanalytics dashboard: Streamlit or Evidence over DuckDB — kill chain funnel, technique efficacy,
  retry/pivot by archetype, suspect ranking, ATT&CK-mapped remediation
- Dashboard screenshot in the README

**Checkpoint:** run a session where the triage model guesses wrong and confirm the Batcomputer
accuses the wrong villain. That is the intended behavior and the best demo of the evaluation being
real.

---

# Track C — Cloud landing (optional)

Only if you want a cloud name in the portfolio. The project is complete without it. Pick one path,
not both.

## Phase 11 — Option A: AWS  (12–20h)

Closest to what your internal audience runs.

- Terraform: S3 buckets, Glue Data Catalog, IAM, ECR, MSK Serverless **or** self-hosted Redpanda on
  a single small EC2 instance (MSK Serverless is materially more expensive — price it first)
- Consumer and honeypot as containers on ECS Fargate
- Athena over the landed Parquet; dbt target swapped to `dbt-athena`
- Guardrails module applied first: budgets at $5 and $20, alerts, anomaly detection
- Deploy, run, capture evidence into `docs/evidence/`, `terraform destroy`,
  `make verify-destroyed`

**Cost note:** a fresh AWS account created after July 15, 2025 gets $100 at signup plus up to $100
earned. Choose the **Paid plan**, not the Free plan — the Free plan closes the account and deletes
resources when it expires. Do not create the account until you reach this phase.

## Phase 11 — Option B: DigitalOcean  (10–16h)

Cheaper and simpler, and it shares the provider with `k8s-data-platform`.

- Terraform: Droplet or DOKS, Spaces (S3-compatible) for landed Parquet
- Redpanda and the services in Docker Compose on a Droplet, or as workloads on DOKS
- DuckDB reads from Spaces via the httpfs extension, so the warehouse layer is unchanged
- Roughly $12–24 for a bounded month

**Checkpoint, either option:** evidence captured, infrastructure destroyed, billing verified at zero
48 hours later.

---

# Maintenance — scheduled fixes, no track

Work discovered after the fact rather than planned in advance. Not part of the original phase
sequence and not gated behind any track; pick up whenever it's next convenient.

## Phase 12 — `raw_triage_predictions` snapshot identity

**The gap:** `raw_triage_predictions` (`services/triage/store.py`) has no run or snapshot identity —
rows are keyed only on `(session_id, source)`. Re-running triage selection against a corpus that has
grown or changed since a prior run silently conflates old and new predictions in whatever a later
`eval` reads, with no error and no warning. **Confirmed three times now**, most recently self-inflicted
mid-verification, caught only by row-count discipline (baseline and `llm` reporting different `n` for
what should have been one evaluation) rather than by any check the system provides on its own.

Evidence, not restated here — read these in order:
- `docs/09-engineering-log.md`, "`raw_triage_predictions` has no snapshot identity, and it's bitten
  the same evaluation twice" — the first two instances (a Dagster zero-credential check, then a
  Gemini-provider-swap re-verification), both silently extending a pinned 36-session snapshot.
- `docs/09-engineering-log.md`, "Swapping to `gemini-3.1-flash-lite`..." — the third instance, during
  this model swap's own re-verification, where a fresh stratified selection against a grown corpus
  picked 34 of the same 36 sessions plus 2 different ones, leaving stale rows from a superseded
  selection sitting alongside the new ones until caught by a mismatched `n` between `baseline` and
  `llm`.

**Not scoped here on purpose** — this phase exists to put the fix on the board with enough context
that whoever picks it up doesn't have to re-derive the problem from three scattered log entries, not
to pre-decide the shape of the fix (a `run_id`/snapshot column, a separate table, something else).

---

## Sequencing note

Track A is the substance. If time runs short, a complete Track A with a strong Phase 7 is a far
better artifact than all three tracks half-finished. The pipeline, the data quality handling, the
kill chain analytics, and the measured evaluation are what get read.

Push public at the end of Phase 7. Treat Tracks B and C as enhancements to an already-finished
project rather than prerequisites for finishing it.
