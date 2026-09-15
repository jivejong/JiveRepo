# CLAUDE.md

Context for Claude Code working in this repository.

---

## ▶ CURRENT POSITION — update this as work proceeds

```
TRACK:  A  (headless core pipeline)
PHASE:  0  (scaffolding)
IN SCOPE:    docs/01, 02, 03, 04, 05, 06, 07
OUT OF SCOPE: docs/08  — no console, no bat bot, no finale, no dashboard
```

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
make dev-reset       # tear down and wipe data/raw + warehouse
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
