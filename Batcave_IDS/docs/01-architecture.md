# 01 — Architecture

## Component responsibilities

### Simulator (`services/simulator/`) — Track A
Drives the whole kill chain headlessly. Three jobs: translate powerstats into traffic shape, run the
stage machine selecting and resolving techniques, and inject the deliberate data pathologies.

Writes one `attack_runs` row per run recording the ground-truth villain. Publishes `attempt` events
and drives the honeypot to produce the corresponding `request` traffic.

Specification: `docs/03-attack-simulation.md` (behavior) and `docs/07-attack-chain.md` (stage
machine, catalog, probability model).

### Honeypot (`services/honeypot/`) — Track A
HTTP service serving plausible-looking Batcave endpoints with canned responses. Publishes one
`request` event per request to `attack.events`, keyed by `session_id`.

| Path | Tier | Response |
|---|---|---|
| `/` | 0 | Generic landing page |
| `/login` | 1 | 401 with a hint that the username exists |
| `/api/v1/status` | 1 | 200, benign JSON |
| `/api/v1/users` | 2 | 200, partial list — enumeration target |
| `/wayne-enterprises/payroll` | 2 | 403 |
| `/cave/vehicle-bay/status` | 2 | 200, partial data |
| `/admin` | 3 | 401 |
| `/cave/archives/<id>` | 3 | 200, data payload |
| `/api/v1/protocol/knightfall` | 4 | 403, leaks a header suggesting it exists |
| `*` | 0 | 404, still logged |

When the simulator runs a technique, the honeypot emits that technique's `detection_signature` as
ordinary traffic carrying the same `attempt_id`. Two views of one action.

The honeypot **logs and responds**. It never evaluates, forwards, or executes request content.

Producer: `acks=all`, `enable.idempotence=false` (deliberately — duplicates are wanted), key =
`session_id`, value = JSON.

**Request headers the honeypot reads — wire formats left open by the spec, decided in Phase 1:**

| Header | Purpose | Behavior |
|---|---|---|
| `X-Client-Ts` | Populates `client_ts` | Parsed leniently as ISO-8601. Absent or unparseable → `null`. This is the attacker-supplied, deliberately unreliable timestamp docs/02 describes — Phase 1 just needed *a* wire format for it to exist; Phase 3's pathologies are what make it lie. |
| `X-Sim-Delay-Ms` | Server-side response delay | `await`ed before the response is built; `response_time_ms` honestly reflects it. Feeds Mister Freeze's long-response signature (docs/03) — read fresh per request, so the simulator can vary it request by request, not just per session. Does **not** produce `inter_request_stddev_ms` — that's the simulator's own client-side call pacing and never touches the honeypot. |
| `X-Forwarded-For` | Overrides `source_ip` | Only honored when `HONEYPOT_TRUST_FORWARDED_FOR=true` (default off; set `true` only in `docker-compose.yml`'s honeypot service, reachable only from the compose-internal network). Unconditional trust of a client header in a service called a honeypot would be a bad look in a public repo, even with nothing real behind it. Exists so Penguin's per-session IP rotation (Phase 3) has a real mechanism without new honeypot code then. |

### Kafka (Redpanda) — Track A
Single container, Kafka API, no ZooKeeper.

- Topic `attack.events`, 3 partitions, replication factor 1
- Keyed by `session_id`, guaranteeing per-session ordering within a partition. Cross-session
  ordering is not guaranteed, which is correct and worth stating in the README.
- Retention 24 hours, ample for replay during development
- Redpanda Console on `:8080` for topics, offsets, and consumer lag

Four `event_kind` values share the topic: `request`, `attempt`, `chat_turn`, `counterstrike`.

### Consumer (`services/consumer/`) — Track A
Consumer group `attack-events-writer`. Batches and writes partitioned Parquet.

- Flush on 5,000 messages or 30 seconds
- `data/raw/<event_kind>/dt=YYYY-MM-DD/hour=HH/part-<uuid>.parquet`
- Partition values from `received_at`, never `client_ts`
- **Offsets committed only after a successful flush.** A crash between flush and commit replays the
  batch and produces duplicates. That is intentional; deduplication belongs in staging.
- Undeserializable messages go to `data/quarantine/undeserializable/` with the offset recorded, and
  the consumer continues rather than halting

### Warehouse (DuckDB) — Track A
Single file, `data/warehouse.duckdb`, reading landed Parquet with Hive partitioning.

### Transform (`transform/`) — Track A
dbt with `dbt-duckdb`. Three layers plus analytic and evaluation marts. The central structural rule
is the observed / truth boundary — see `docs/02-data-model.md`.

### Triage (`services/triage/`) — Track A
Reads observed session features, calls Groq for structured JSON, writes intervention orders and two
sets of evaluation rows: villain attribution and technique reconstruction.
Specification: `docs/04-llm-triage.md`.

### Orchestration (`orchestration/`) — Track A
Dagster. Assets rather than tasks:

- `raw_events` — source asset observing the Parquet directories
- dbt models as assets via `dagster-dbt`, giving lineage for free
- `scored_sessions` → `intervention_orders` → `triage_evaluations` + `technique_evaluations`

Schedule every 15 minutes plus a directory sensor. `dagster dev` on `:3000`. Screenshot the asset
lineage graph for the README — it is the most legible artifact the project produces.

### Console, bat bot, finale, dashboard — Track B
Presentation over models that already exist. Specification: `docs/08-interactive-experience.md`.
Not built until Track A is complete and pushed.

---

## Data flow

```
Simulator ──── stage machine ────► attempt events ─────────┐
    │                                                      │
    │ drives                                               │
    ▼                                                      ▼
Honeypot ───── request events ────────────────► Kafka: attack.events
                                                 (3 partitions, keyed by session_id)
Bat bot (Track B) ── chat_turn events ─────────►         │
                                                          ▼
                                                   Consumer service
                                              (batch → partitioned Parquet,
                                               offsets after flush)
                                                          │
                                                          ▼
                                        data/raw/<event_kind>/dt=/hour=/
                                                          │
                                                          ▼
                                              DuckDB ◄── dbt
                                                          │
                        ┌─────────────────────────────────┴────────────────────┐
                        ▼                                                      ▼
            OBSERVED  [triage_input]                          GROUND TRUTH  [ground_truth]
     stg_attack_events, stg_botchat_turns                 stg_attack_attempts, stg_attack_runs
     int_session_features_observed                        int_session_features_truth
     mart_threat_scores                                   int_stage_progression
                        │                                                      │
                        ▼                                                      │
              Triage service → Groq LLM                                        │
                        │                                                      │
                        ▼                                                      │
             fct_intervention_orders ──────────── joined for scoring ──────────┤
                                                                               ▼
                                                          fct_triage_evaluations
                                                          fct_technique_evaluations
                                                          mart_detection_coverage

                        All of the above orchestrated by Dagster
```

The vertical split is the whole point. The model sees only the left side. The right side exists to
score it. `assert_no_ground_truth_leakage` walks the dbt manifest and fails if any `triage_input`
model has a `ground_truth` ancestor.

---

## Technology decisions

Document each in the README with its rationale. Reviewers read decision rationale as a proxy for
judgment.

### Kafka, not a managed cloud streaming service
Redpanda runs as one container with no account and no expiry. Kafka appears in far more data
engineering job descriptions than any vendor streaming service, the semantics are real rather than
simulated, and delivery is sub-second. Managed alternatives had minimum buffering windows measured
in tens of seconds.

### DuckDB, not a cloud warehouse
Trial warehouses expire, which means the repository stops being runnable. DuckDB reads the landed
Parquet directly, needs no server, and produces identical SQL semantics for everything here. The
migration path is stated in the README rather than performed.

### At-least-once, handled downstream
Idempotent producing is deliberately disabled so duplicates actually occur and the deduplication
logic is exercised rather than theoretical.

### One topic, four event kinds
A discriminated envelope keeps ordering guarantees intact across all events for a session, since
they share the `session_id` key. Separate topics per kind would break per-session ordering across
kinds, which is exactly what `mart_detection_correlation` depends on.

### MITRE ATT&CK, not invented technique names
The catalog is being authored either way, so real technique IDs cost nothing and turn the project
from a game into a detection-engineering exercise. Verify every ID against attack.mitre.org.

### Observability tiers on techniques
An HTTP sensor cannot see every technique. Modeling that explicitly is what makes the technique
reconstruction metric honest, and it produces a detection coverage gap analysis rather than a bare
accuracy number.

### Dagster, not Airflow
Native dbt integration gives asset-level lineage with little glue, and the asset graph is far more
legible to a reader than a DAG of shell tasks. Airflow appears in `k8s-data-platform`, so both are
represented across the portfolio.

### Infrastructure work lives elsewhere
Terraform and Kubernetes were removed because nothing here needed them. A single stateless service
and a local broker do not justify a cluster, and provisioning infrastructure whose only purpose is
hosting a demo is not a demonstration of infrastructure skill. That work moved to
`k8s-data-platform`, where it is the subject.

Say this in the README. Removing a technology because it did not earn its place is a stronger signal
than including it because it looks good.

---

## Rejected — do not add these

| Rejected | Reason |
|---|---|
| Any cloud account in Track A | Expiry, credentials, and cost all work against durability |
| Snowflake | 30-day trial; repository stops running when it lapses |
| Terraform, Kubernetes | Nothing here to provision; moved to `k8s-data-platform` |
| Spark | DuckDB handles this volume on a laptop; Spark would be theater |
| A vector database | Catalog-scale similarity does not justify the dependency |
| Separate Kafka topics per event kind | Breaks per-session ordering across kinds |
| Live superheroapi.com calls | Endpoint is slow and unreliable; the dataset is vendored |
| ZooKeeper-based Kafka | Redpanda removes the dependency |
