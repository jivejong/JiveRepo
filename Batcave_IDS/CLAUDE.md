# CLAUDE.md

Context for Claude Code working in this repository.

---

## ▶ CURRENT POSITION — update this as work proceeds

```
TRACK:  A  (headless core pipeline)
PHASE:  3  (villain behavior and pathologies) — IN PROGRESS, paused for branch review
NEXT:   finish separability Part 1, then pathologies (Part 2)
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

**Separability Part 1 NOT yet passing — remaining before it can be called done:**
- Wire Mister Freeze's response-delay signature (`X-Sim-Delay-Ms`) so his duration story holds — the
  volume fix made Croc longest-duration, which is a stat-mapping artifact (decided: fix via signature).
- Build the ablation into the harness (effect sizes with `wasted_request_ratio` / `error_ratio`
  removed; report load-bearing pairs).
- docs/02 feature-definition ambiguity pass (2-3 prose-vs-computation mismatches already found;
  likely more) + the wasted+max_path_tier pairing doc edits.
- The generic mid-stat cluster (Bane/Harley/Poison Ivy/Freeze) still overlaps in effect size (<1) —
  open question whether that's acceptable or needs more signature work.
- Then Part 2: pathologies (`pathologies.yml` + injection + verification), `attack_runs`, real
  `make attack`/`attack-all`.

Phases 0, 1, and 2 are all locally complete and verified against real output; none has been pushed,
so **CI green is unconfirmed for all three** — nothing has gone to the public remote yet
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
make attack VILLAIN=<slug> DURATION=<seconds>
make attack-all      # all twelve villains sequentially
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
