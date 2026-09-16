# CLAUDE.md

Context for Claude Code working in this repository.

---

## ▶ CURRENT POSITION — update this as work proceeds

```
TRACK:  A  (headless core pipeline)
PHASE:  3  (villain behavior and pathologies) — COMPLETE locally (Parts 1 and 2), nothing pushed
NEXT:   Phase 4 (consumer and landing) — fresh session, plan mode first
IN SCOPE:    docs/01, 02, 03, 04, 05, 06, 07
OUT OF SCOPE: docs/08  — no console, no bat bot, no finale, no dashboard
```

**Phase 3 status (paused mid-separability-loop, nothing pushed).** Built and committed: cookie
session derivation (replaced the Phase 1 IP+UA key — rotation broke it; see docs/02), per-run/
per-request identity (RFC 5737 IPs), absurd-method logging, Layer 1 `BehaviorProfile` +
intelligence-aware detection (calibration gap 0.006 over non-detected attempts, INT↔detection
r=-0.957), Layer 1 traffic shaping, Layer 2 signatures, the separability harness (`make
separability`: N-run averaging, effect-size metric, SQL features for Phase 5 to lift), and the
request-budget volume fix (volume now 5-46 attempts, ~6-9x; Croc highest, matching docs/03).

Corrected against real data (docs/03/06/07): pivot_ratio leader is Joker not Ra's, retry leader is
Croc; low-durability villains identifiable by signature not outcome; Ra's discriminator is low
wasted_request_ratio + max_path_tier 4 (a pairing, not one feature). `wasted_request_ratio` redefined
progress-based (r=-0.05 vs error_ratio).

**Separability Part 1 — mostly validated; resolved since the status above:**
- Freeze's response-delay signature landed — he's longest-duration again (via holding, not volume).
- Volume fix made Croc highest `request_count` (docs/03 true, not edited).
- Bane's signature (path concentration) added — it was missing entirely; the mid-stat cluster now
  separates on signature features (Bane/Ivy 1.20, all Freeze pairs 2.7–5.0). The residual
  Bane/Harley/Ivy weakness was a **measurement artifact**: `inter_request_stddev_ms` is flat under a
  compressed clock (gaps below the HTTP noise floor). At faithful timing the Joker/Harley burst pair
  separates (effect ≈ 1.2). Timing model + which features are faithful: docs/05, docs/03.
- **Timing/concurrency, for Phase 4:** faithful full-corpus runs are slow (idle-gap-dominated);
  thread concurrency to speed them up hits a fatal C-extension GIL crash and was abandoned. The
  **sequential path the simulator uses is clean** — `make attack` runs fine; the crash was only the
  harness's threaded optimization. Compressed-by-default + faithful subset is the model; the
  compression factor will be recorded on `attack_runs`. Phase 4's larger pathology corpus should
  dial `time_scale` up for size rather than expect a fast faithful run.

**Separability Part 1 — DONE.** All named checkpoint separations hold (measured, `make
separability`): Riddler/Two-Face on signature features (riddle d≈6, duplicate d≈1.7, outcome flat),
Scarecrow/Penguin and Joker/Harley both out of the closest-10, Joker/Harley validated at faithful
timing (effect ≈1.2), Croc highest request_count + retry_ratio, Freeze longest duration. Closest
remaining pair is Catwoman/Ra's al Ghul (0.77) — an *intended* similarity (both efficient operators
reaching tier 4 in <25 requests), not a failure. Ablation: `error_ratio` load-bearing for 11/66
pairs (strong keep), `wasted_request_ratio` for 2/66 (Riddler pairs — marginal keep, weight lightly
in Phase 6). docs/02 ambiguity pass done (7 features flagged for Phase 5). Optional refinement
recorded, not built: Harley's bimodal burst shape (docs/03/04).

**Part 2 — DONE (verified against real consumed data, `make pathology-check`).** All ten docs/03
pathologies injected into the observed request stream only (never ground truth) via
`services/simulator/pathologies.yml` + `pathologies.py` (single decision-maker; simulator-direct vs
honeypot-executed via the `X-Sim-Pathology` header). Each verified present with a count on a
`runs=12`/`time_scale=0.02`/`seed=0` corpus (144 sessions, 2,592 request events): duplicate_delivery
40, out_of_order 137, unkeyed 24, late_arrival 36, malformed_body 21, undeserializable 2,
missing_source_ip 12, missing_path 16, schema_drift 1141, clock_skew_future 20, clock_skew_negative
24, burst 78; attack_run 144. Clock skew and missing-fields kept as **two counts each** (they diverge
downstream). The three specific checks pass: Two-Face distinct-`event_id` duplicates coexist with and
are distinguishable from identical-`event_id` delivery duplicates; undeserializable emitted now
(consumer quarantine is Phase 4); distinct runs → distinct `session_id`s (Phase 2 merge bug stays
fixed). `attack_runs` published as a 5th `event_kind`, keyed by `run_id`, carrying `villain_slug` +
`timing_compression_factor`. `run_id` now links attempt/request/attack_run events. Reconciliation
(2,442 sent / 2,592 on topic) captured in docs/03; Phase 5's `mart_reconciliation` must reproduce it
(cross-check line added to docs/06 Phase 5). PyYAML declared as a direct dep (pyproject/docs05/CLAUDE);
dep audit found no other gaps. Invented mechanisms documented: `X-Sim-Pathology`/`X-Run-Id` headers
(docs/01 consolidated table), `attack_run` kind (docs/02).

Phases 0, 1, 2, and 3 are all locally complete and verified against real output; none has been
pushed, so **CI green is unconfirmed for all four** — nothing has gone to the public remote yet
(deliberately, per instruction: local first). Do not treat any of them as fully closed until CI is
observed green after that push.

**Phase 1 summary** (full detail: docs/02 "Session derivation", docs/01 header table): gap-based
session derivation (not a fixed time bucket — docs/06 corrected), three invented request headers
(`X-Client-Ts`, `X-Sim-Delay-Ms`, `X-Forwarded-For`).

**Phase 2 checkpoint** (docs/06): run a scripted session, confirm Croc gets exactly the low-gate
techniques and Ra's al Ghul the full catalog, confirm no gate violations, confirm attempts and
their HTTP traffic share an `attempt_id`. Done for real against the live stack (`rpk topic
consume`, cross-checked by hand and programmatically against `gated_techniques`), not asserted from
the code:

- **All 23 ATT&CK IDs verified against live attack.mitre.org** — none wrong. Full table and the
  TA0005 (Defense Evasion → Stealth) rename note: docs/07's commit history.
- **Docs/07's narrative was wrong on both named villains, found by computing gates, not reading
  prose.** Killer Croc stalls at stage 2 (no stage-3 technique's `min_intelligence` is low enough
  for him — 19 vs. a floor of 45) and never reaches `data_local_system`. Ra's al Ghul clears every
  `min_intelligence` gate but fails `privesc_exploit`'s `min_power=40` (his power is 27), so he
  doesn't reach "the full catalog." **Fixed the narrative, not the seed thresholds** — full
  twelve-villain gated-technique table now in docs/07.
- **`produces_traffic` column added** (`techniques.csv`) — whether a technique touches the honeypot
  is fixed per-technique, independent of outcome. Splits `low` observability into two real
  detection postures (8 no-evidence, 1 camouflaged) — carried into docs/07, docs/04, and the README.
- **`detected` outcome defined** — named in the schema, never specified anywhere. Orthogonal to
  success/failure, driven by `noise_generated` (docs/07).
- **`X-Attempt-Id` (request) / `X-Session-Id` (response)** wire formats added to the honeypot —
  the actual attempt-to-request correlation mechanism, verified by hand against real consumed
  Kafka messages (one `attempt` event, its exact matching `request` event(s), same `attempt_id`
  *and* `session_id`).
- **Two Docker bugs found and fixed while bringing the stack up for the checkpoint**: the honeypot
  image was missing `services/common/` (crash-looping, but reported "healthy" because it had no
  real healthcheck — both fixed); Docker's own healthcheck was polluting `attack.events` with
  synthetic traffic once a real healthcheck existed (`/healthz` added, verified 0 messages over 4
  healthcheck cycles).
- **Two open questions handed to Phase 3 with real data attached, not decided here**: whether
  Killer Croc's stage-3 wall is the intended story (full twelve-villain distribution in docs/07);
  per-run `source_ip` variation, needed before `attack-all`'s sequential runs will separate into
  distinct sessions (confirmed empirically — two villains run back-to-back landed in one session).

`k8s-data-platform/` has been moved out to be a sibling of `Batcave_IDS` at the JiveRepo root,
matching its own HANDOFF.md. Resolved.

**Do not build anything from `docs/08-interactive-experience.md` while Track A is in progress**,
even if it would be quick, even if the spec is right there. Track A must run end to end headlessly
first. If a Track A phase seems to require a user interface, it does not — re-read the phase in
`docs/06-implementation-plan.md`.

Track A phases are 0–7 and end at a shippable, public-ready repository. Track B is 8–10. Track C is
an optional cloud landing.

---

## What this project is

A portfolio data engineering project: a streaming pipeline that simulates a five-stage intrusion and
evaluates an LLM's ability to reconstruct it. The Batman theme is presentation. Every architectural
decision must be defensible as production data engineering practice.

**Primary audience:** data engineering team leads and internal recruiters who will *read* this
repository, not run it. Legibility of the artifacts matters more than feature count.

**Design constraint:** Track A runs locally with Docker and Python. No cloud account, no trial
credentials, no service that can expire. This project must still run unchanged in two years.

## Working principles

1. **Verify against real output before building the next layer.** Do not write transformation models
   against an assumed event shape. Emit real events first, inspect them, then build on top. Every
   phase has a checkpoint. Stop at each one.
2. **No happy-path-only code.** The simulator deliberately emits malformed, duplicate, late, and
   out-of-order records. Handling them is the point, not an edge case.
3. **Nothing silently dropped.** Every bad record is deduplicated, quarantined, flagged, or counted,
   and the count is reportable.
4. **Ground truth is sacred.** See the hard constraints below.

## Stack

- **Python 3.11+**, `uv` for dependency management
- **Redpanda** — Kafka-API broker, single container, no ZooKeeper
- **confluent-kafka** — Python client (librdkafka). Not `kafka-python`.
- **pyarrow** — Parquet writing
- **DuckDB** — warehouse, reads Parquet with Hive partitioning
- **dbt-core + dbt-duckdb** — transformations, single target
- **Dagster** (`dagster`, `dagster-dbt`, `dagster-duckdb`) — orchestration
- **Groq** — LLM inference. `GROQ_API_KEY` optional; without it the rule-based baseline runs.
- **FastAPI + uvicorn** — the honeypot's HTTP server (Phase 1). Not in the original spec; added
  because Pydantic (already a dependency) is what FastAPI is built on, so request validation and
  event validation share one schema layer, and because `http.server`'s synchronous model would
  measure the server rather than the villain during the burst pathology's 10x rate spike
  (docs/03).
- **httpx** — HTTP *client* library, used by the simulator (Phase 2+) to drive the honeypot. Not
  to be confused with the server framework above.
- **PyYAML** — reads `services/simulator/pathologies.yml` (Phase 3 Part 2).
- Dev tools: `ruff`, `pytest`, `sqlfluff` (DuckDB dialect), `pre-commit`.

## Repository layout

```
services/
  honeypot/          # HTTP service, fake Batcave endpoints, Kafka producer
  consumer/          # Kafka consumer, batches to partitioned Parquet
  simulator/         # stage machine, villain behavior, pathology injection
  triage/            # LLM triage + evaluation harness
  batbot/            # Track B only
console/             # Track B only
transform/           # dbt project
  models/staging/ intermediate/ marts/
  tests/ macros/
  seeds/villains.csv techniques.csv
orchestration/       # Dagster definitions
data/
  villains/          # vendored akabab subset
  raw/<event_kind>/  # landed Parquet (gitignored except one sample partition)
  quarantine/
  warehouse.duckdb   # gitignored
docs/
docker-compose.yml
Makefile
```

## Conventions

- SQL: lowercase keywords, trailing commas, CTEs over subqueries. One model per file.
- dbt models named `stg_`, `int_`, `fct_`, `dim_`, `mart_`.
- **Every dbt model carries exactly one of the tags `triage_input` or `ground_truth`**, or neither
  if it is a shared dimension. This is not optional — the leakage test depends on it.
- Timestamps stored UTC, suffixed `_at`. Never use a client-supplied timestamp as a watermark.
- Kafka messages keyed by `session_id` so per-session ordering holds within a partition.
- Secrets via environment variables only. `.env.example` committed, `.env` gitignored.
- Commit in small, logical increments. Do not batch a phase into one commit.

## Hard constraints

- **Never let ground truth reach the triage model.** `attack_attempts`, `attack_runs`,
  `int_stage_progression`, and `int_session_features_truth` are ground truth. `attempt_id` is a join
  key for evaluation, stripped from every `triage_input` model. Contaminating the input invalidates
  every number in the repository.
- **Never use `client_ts` for partitioning, ordering, or watermarking.** It is deliberately
  unreliable. Use `received_at`.
- **Never commit `user_text` from bat bot conversations** to the sample partition. Turn metadata and
  intent flags only.
- The honeypot must not proxy, forward, or execute anything from a request body. It logs and returns
  canned responses. It is a fake target, not a real one.
- Track B only: the console never takes over the browser viewport, never requests fullscreen, and
  keeps the `SIMULATION` frame visible at all times including the finale.
- Commit one small sample partition per event kind (a few hundred rows) so a reader can inspect real
  output and `dbt run` works from a clean clone. Gitignore the rest.

## Commands

```bash
make dev-up          # docker compose up: redpanda, console UI, honeypot, consumer
make dev-down        # tear down, preserve data/
make dev-reset       # tear down and wipe generated data/raw + warehouse (spares the committed sample)
make attack VILLAIN=<slug> [TIME_SCALE=<factor>]   # one villain, one run
make attack-all      # all twelve villains sequentially
make landing-check   # verify what the consumer landed (Phase 4 checkpoint)
make transform       # dbt deps && dbt run && dbt test
make triage          # score sessions, call LLM (or baseline), write orders + evaluations
make eval            # attribution + technique reconstruction report
make reconcile       # print mart_reconciliation
make coverage        # print mart_detection_coverage
make dagster         # dagster dev (UI on :3000)
make docs            # dbt docs generate && dbt docs serve
make fmt             # ruff + sqlfluff
make test            # pytest && dbt test
```
