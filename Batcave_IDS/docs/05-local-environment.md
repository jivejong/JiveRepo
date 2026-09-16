# 05 — Local environment

## Principle

The repository must run end to end with Docker and Python, nothing else. No account, no credential,
no service that can expire. The test: someone clones this in 2028 and `make dev-up && make attack`
works.

---

## Docker services

`docker-compose.yml`:

| Service | Image | Port | Purpose |
|---|---|---|---|
| `redpanda` | `redpandadata/redpanda` | 9092 | Kafka-API broker, single node, no ZooKeeper |
| `redpanda-console` | `redpandadata/console` | 8080 | Topic, offset, and consumer lag inspection |
| `honeypot` | built from `services/honeypot/` | 8000 | Fake Batcave endpoints, Kafka producer |
| `consumer` | built from `services/consumer/` | — | Kafka consumer, writes Parquet |

**Phased, not all at once.** `honeypot` and `consumer` don't exist as code until Phase 1 and Phase 4
respectively — listing them in `docker-compose.yml` before then would make `make dev-up` depend on
services that can't build, breaking the Phase 0 checkpoint itself. Phase 0's compose file has only
`redpanda` and `redpanda-console`; the other two are added to the file in their own phases, each
verified against real `docker compose up` output at the time.

Pin image tags rather than using `latest`. A 2028 clone should get the same versions. Verify the tag
actually exists — pull it, don't just write down a version number from memory — before committing
the compose file (Phase 0 did this against the Docker Hub tag API plus an actual `docker pull`).

Mount `./data` into the consumer so landed Parquet appears on the host where DuckDB and dbt can
read it without a copy step.

Health checks on `redpanda` with `depends_on: condition: service_healthy` for the honeypot and
consumer. Without this the producer starts before the broker is ready and the first run fails
confusingly.

### Redpanda configuration

Single-node development mode. Create the topic at startup:

```
rpk topic create attack.events --partitions 3 --replicas 1
rpk topic alter-config attack.events --set retention.ms=86400000
```

Three partitions is deliberate. One partition would make the ordering guarantee trivial and
`assert_session_ordering_preserved` meaningless.

### The Redpanda Console is a portfolio asset

Screenshot it during a burst showing consumer lag rising and recovering. That image communicates
"this is a real streaming system" faster than any paragraph.

---

## Python environment

`uv` with a single `pyproject.toml` and workspace members per service, or one shared environment if
simpler. Key dependencies:

```
confluent-kafka      # NOT kafka-python — librdkafka is the maintained, performant client
pyarrow              # Parquet writing
duckdb
dbt-core
dbt-duckdb
dagster
dagster-dbt
dagster-duckdb
fastapi              # honeypot's HTTP server (added Phase 1, not in the original list — see below)
uvicorn[standard]    # ASGI server for fastapi
httpx                # HTTP client, used by the simulator to drive the honeypot — not the server
pydantic             # event schema validation at the producer boundary
groq
```

Dev: `ruff`, `pytest`, `sqlfluff` with the DuckDB dialect, `pre-commit`.

**`fastapi`/`uvicorn`** weren't in the original dependency list — the honeypot needs an HTTP server
and none was specified. Added Phase 1: Pydantic is already required for event validation, and
FastAPI is built on it, so request validation and event validation share one schema layer.
`http.server` was rejected because it's synchronous and the burst pathology (docs/03) fires a 10x
rate spike that would end up measuring the server's throughput rather than the villain's.

---

## Data directory

```
data/
  villains/                          # committed, vendored akabab subset
  raw/
    attack_events/dt=/hour=/         # gitignored except one sample partition
    attack_runs/
  quarantine/
    undeserializable/
  warehouse.duckdb                   # gitignored
```

**Commit one sample partition** — a few hundred rows of real landed Parquet. A reader can then
inspect actual output, and `dbt run` works immediately after clone without generating traffic.

**A note for anyone cloning this into a different parent directory.** This project lives inside the
`JiveRepo` monorepo, whose root `.gitignore` has an unanchored `data/` rule — git applies it at
every directory level, so it silently swallows this project's `data/` entirely, including the
committed sample partition and vendored villain roster, with no error on `git add`. `Batcave_IDS/
.gitignore` overrides this, un-ignoring each directory level explicitly (git will not descend into
an excluded directory to evaluate rules inside it — a bare `!data/` is not sufficient). If this
project is ever extracted to its own repository, or cloned somewhere with a similarly broad `data/`
rule above it, re-verify with `git check-ignore -v` against a real path in each category (vendored,
sample, generated) rather than assuming the override still applies.

The sample-partition negation is written as a filename convention (`sample-*`) rather than a
directory, deliberately: a `sample/` subdirectory would sit outside the real `dt=`/`hour=` Hive
partition path and break DuckDB's partition-column inference, failing `dbt run` at exactly the thing
the sample exists to enable.
This is a small thing that meaningfully improves the first-visit experience.

---

## Dagster

`orchestration/definitions.py` holds assets, schedules, and resources.

- Load dbt models as assets with `dagster-dbt`. This gives asset-level lineage across the whole
  pipeline with very little code.
- `dagster dev` serves the UI on `:3000`.
- Schedule every 15 minutes, plus a sensor watching `data/raw/` for new partitions.
- DuckDB resource configured to `data/warehouse.duckdb`.

**Screenshot the asset lineage graph for the README.** It is the most legible artifact this project
produces and it communicates the architecture in one image.

Note: DuckDB holds a single-writer lock. Dagster and an open DuckDB CLI session cannot write
concurrently. Document this in the README as a known local constraint rather than letting a reader
hit it unexplained.

---

## Makefile targets

```make
dev-up          docker compose up -d, wait for health, create topic
dev-down        docker compose down (preserve ./data)
dev-reset       docker compose down -v, wipe data/raw except the committed sample-*, rm warehouse.duckdb
attack          run the simulator: VILLAIN=<slug> DURATION=<seconds>
attack-all      run all twelve villains sequentially
separability    run the villain-separability harness (Phase 3 checkpoint)
transform       dbt deps && dbt run && dbt test
triage          score sessions, call LLM (or baseline), write orders + evaluations
eval            print the triage accuracy report
reconcile       print mart_reconciliation
dagster         dagster dev
docs            dbt docs generate && dbt docs serve
fmt             ruff format && ruff check --fix && sqlfluff fix
test            pytest && dbt test
```

Every target must work from a clean clone. Test this before the final commit by cloning into a
fresh directory and running through `make dev-up`, `make attack`, `make transform`, `make eval`.

### Timing model: real budgets, compressible idle gaps

The simulator paces requests in real time so the timing features
(`requests_per_min`, `duration_s`, `inter_request_stddev_ms`) reflect actual behavior. Two knobs
are kept deliberately separate:

- **Request budget** — how many requests a villain makes (speed × durability, docs/03). Always
  preserved. Every volume-derived feature depends on it, so it is never compressed.
- **Idle gaps** — the wall-clock time spent asleep *between* requests. Compressible by a
  `time_scale` factor (1.0 = faithful; smaller = faster iteration). The separability harness defaults
  to a small factor so a full twelve-villain corpus runs in ~1–2 minutes instead of ~20.

Compressing the gaps distorts exactly one feature: `inter_request_stddev_ms`, which *is* the gap
distribution. Below `time_scale ≈ 0.1` the compressed gaps fall under the HTTP round-trip noise
floor (~10 ms) and the feature measures network jitter rather than villain pacing. The harness
labels each run FAITHFUL or compressed and reserves faithful timing for a targeted `--villains`
subset (the Joker/Harley burst-structure pair). **The compression factor is recorded per run on
`attack_runs`** (not just in config) so Phase 5 and Phase 6 can tell, from the data alone, whether a
session's timing was real — and so faithful and compressed runs can coexist in one corpus,
distinguishable by column.

Concurrency was tried to make faithful full-corpus runs affordable (the runs are independent) and
**abandoned**: a `ThreadPoolExecutor` over the runs triggers a fatal C-extension GIL crash on this
platform (`confluent_kafka`/librdkafka and/or httpx). The sequential path the simulator itself uses
is unaffected and runs cleanly; only the harness optimization hit it. So a faithful full corpus is
inherently slow (sum of the runs, idle-gap-dominated), which is why compressed-by-default plus a
faithful subset is the model. Phase 4's pathology corpus, which needs enough volume for 0.5% rates
to fire, should dial `time_scale` up for size rather than expect a fast faithful run.

---

## CI (GitHub Actions)

Free on public repositories, and visible green checks are worth having.

- `ruff check` and `ruff format --check`
- `sqlfluff lint`
- `pytest`
- `dbt compile` and `dbt test` against the committed sample partition
- **`assert_no_ground_truth_leakage`** run explicitly as a named step so a reader sees it in the log

That last one is worth surfacing deliberately. A CI step named for the invariant it protects tells a
reviewer you understood why the invariant mattered.

---

## What is deliberately absent

| Absent | Reason |
|---|---|
| Cloud accounts | Expiry and credentials work against durability |
| Terraform | Nothing here to provision; lives in `k8s-data-platform` |
| Kubernetes | One stateless service does not justify it; lives in `k8s-data-platform` |
| Kubernetes manifests "for completeness" | Manifests for a workload that never needed orchestration read as padding |
| Secrets management tooling | One optional API key; environment variable is the honest answer |

State this in the README. Explaining what you left out, and why, reads as judgment. Most portfolio
projects only explain what they included.
