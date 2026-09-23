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

### The simulator's private control channel (request headers)

The honeypot reads a set of `X-*` request headers that the simulator uses to drive it. **None of
this exists in a real honeypot** — a real one takes only ordinary HTTP. This is the simulation's
drive channel, and it is the reason the honeypot can stay a dumb, honest sensor while the simulator
owns all the behavior and the deliberate data defects. One table, rather than scattered across the
phase that added each:

| Header | Set by | Purpose |
|---|---|---|
| `X-Forwarded-For` | simulator (per run / per request) | Overrides `source_ip`. Only honored when `HONEYPOT_TRUST_FORWARDED_FOR=true` (default off; on only in `docker-compose.yml`'s honeypot service, reachable only from the compose-internal network). Drives Penguin's per-session IP rotation and the per-run distinct IPs. Trusting a client header unconditionally in a service called a honeypot would be a bad look; the flag keeps it off by default. |
| `X-Client-Ts` | simulator (per request) | Populates `client_ts`, parsed leniently as ISO-8601 (absent/unparseable → `null`). The attacker-supplied, deliberately unreliable timestamp (docs/02). Phase 3's out-of-order and late-arrival pathologies set it to shuffled or hours-old values. |
| `X-Sim-Delay-Ms` | simulator (per request) | Server-side response delay, `await`ed before the response; `response_time_ms` reflects it. Drives Mister Freeze's held-connection signature. Does **not** produce `inter_request_stddev_ms` (that's the simulator's own client-side call pacing, which never touches the honeypot). |
| `X-Attempt-Id` | simulator (per request) | Correlates the HTTP request to the `attempt` event that generated it — the join `mart_detection_correlation` depends on. `null` on a plain curl. |
| `X-Run-Id` | simulator (per run) | Stamps `run_id` on the request event so it joins to the run's `attempt` events and its `attack_runs` row (the ground-truth key). `null` on a plain curl. |
| `X-Sim-Pathology` | simulator (per request) | Comma-separated tokens asking the honeypot to inject a deliberate data pathology into *this* request's event (`unkeyed`, `undeserializable`, `duplicate_delivery`, `missing_source_ip`, `missing_path`, `schema_drift`, `clock_skew_future_received_at`, `clock_skew_negative_response`). Corrupts the observed request stream only, never ground-truth attempt events. See `services/simulator/pathologies.py`. |

### Kafka (Redpanda) — Track A
Single container, Kafka API, no ZooKeeper.

- Topic `attack.events`, 3 partitions, replication factor 1
- Keyed by `session_id`, guaranteeing per-session ordering within a partition. Cross-session
  ordering is not guaranteed, which is correct and worth stating in the README.
- Retention 24 hours, ample for replay during development
- Redpanda Console on `:8080` for topics, offsets, and consumer lag

Five `event_kind` values share the topic: `request`, `attempt`, `chat_turn`, `counterstrike`, and
`attack_run` (Phase 3 — ground truth, docs/02).

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
Reads observed session features, calls Gemini for schema-constrained structured JSON (originally Groq;
swapped for operational reasons, see docs/04's Configuration section and docs/09), writes intervention
orders and two sets of evaluation rows: villain attribution and technique reconstruction.
Specification: `docs/04-llm-triage.md`.

### Orchestration (`orchestration/`) — Track A
Dagster. Assets rather than tasks:

- `raw_events` — observable source asset over the Parquet landing zone
- dbt models as assets via `dagster-dbt`
- `mart_threat_scores` → `triage/raw_triage_predictions` (Python: baseline, or baseline+LLM with a
  key) → `fct_intervention_orders` → `fct_triage_evaluations` + `fct_technique_evaluations`

**Naming note.** This section originally named the last four assets `scored_sessions`,
`intervention_orders`, `triage_evaluations`, and `technique_evaluations` — conceptual names written
before the dbt layer existed. By Phase 7 those are dbt models
(`mart_threat_scores`/`fct_intervention_orders`/`fct_triage_evaluations`/`fct_technique_evaluations`),
and the model names are what Dagster and the Makefile's `--select` strings actually use. Aliasing the
Dagster asset keys back to the original conceptual names would desync the UI from both the `.sql`
filenames and the Makefile — worse legibility, the opposite of the point — so the models stand as
named and this note explains the drift instead. The lineage screenshot bands the graph by
`group_name` (`raw`/`staging`/`intermediate`/`marts`/`triage`, plus the three seeds each showing as
their own singleton group) so it still reads at a glance. The four evaluation marts
(`fct_intervention_orders`, `fct_triage_evaluations`, `fct_technique_evaluations`,
`mart_detection_coverage`) share the `marts` group with every other mart rather than a separate
band — they live in the same dbt folder, and `group_name` follows the folder.

`raw_events` is not free lineage the way the dbt-model assets are: the Parquet landing zone is read
through a macro (`transform/macros/raw_events.sql` inlines `read_parquet()`), not a dbt source, so
`dagster-dbt`'s translator sees no dependency on it by default. `orchestration/definitions.py`
injects the dependency explicitly for the six models that read the macro.

A Python step sits in the middle of the dbt graph, not after it: `fct_intervention_orders` reads
both a dbt model and a dbt *source* (`raw_triage_predictions`) written by `services/triage/`, since
dbt cannot call the LLM API or run the baseline classifier itself. The asset graph is split into two
`@dbt_assets` definitions at that boundary (matching the Makefile's own `fct_intervention_orders+`
selector) rather than one, which would cycle through the source.

Schedule every 15 minutes plus a directory sensor (cursor-diff on a digest of `data/raw/`'s files,
not an mtime poll, so an unchanged directory never triggers a run). `dagster dev` on `:3000`.
Screenshot the asset lineage graph for the README — it is the most legible artifact the project
produces.

**Zero-credential requirement, verified rather than assumed**: the whole graph — dbt upstream, the
triage asset, dbt evaluation — materializes end to end with `GEMINI_API_KEY` genuinely absent (not
just unset for one call; `.env` itself removed for the test). The triage asset runs the rule-based
baseline in that case and the run still succeeds, which is what makes the clean-clone checkpoint
meaningful. Re-verified after the Groq→Gemini provider swap, not assumed to carry over unchanged.

### Console, bat bot, finale, dashboard — Track B
Presentation over models that already exist. Specification: `docs/08-interactive-experience.md`.
Not built until Track A is complete and pushed.

**Two pieces, not one.** `console/` is a static SPA (no framework) — but a browser cannot reach
Kafka, cannot hold the honeypot's session cookie, and cannot set the identity headers docs/01's
control-channel table above marks server-side-only. `services/console/` (Phase 8, FastAPI) is the
backend that actually drives the stage machine: it holds one `StageMachine`
(`services/simulator/machine.py`) per browser session, calling the same resumable core the headless
autopilot (`services/simulator/session.py`) drives, and exposes it over a small HTTP API the SPA
calls. Port `:8090`, run via `make console` (needs `make dev-up` and the honeypot already up, same
as `make attack`). No new compose service in Phase 8.

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
              Triage service → Gemini LLM                                      │
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
