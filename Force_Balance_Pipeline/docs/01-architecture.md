# 01 — Architecture

## Component boundaries

The single most important boundary is **what runs inside Databricks Free Edition and what
doesn't**. Free Edition has no inbound streaming endpoint and restricts outbound internet
access, so anything that needs to receive from the outside world or call the outside world
lives elsewhere.

### Outside Databricks

| Component | Host | Responsibility |
|---|---|---|
| Intergalactic probe | Raspberry Pi 3 | Simulates Force readings, four operating modes, store-and-forward buffering |
| Web intake | Any browser | Human reports; model infers sensor-equivalent values |
| Collector bridge | GCP e2-micro (Always Free) | MQTT broker, batcher, **and the report inference endpoint**; writes NDJSON to the UC volume |
| Yoda agent | Cloud Run (scheduled) | Reads incidents, calls Gemini, writes decisions back |
| Jedi Council dashboard | Cloud Run / Vercel | Reads Postgres serving store; manual deployment UI |
| Postgres serving store | GCP e2-micro (same VM) | Gold aggregates, published from Databricks |

### Inside Databricks

| Component | Responsibility |
|---|---|
| Unity Catalog volume | Landing zone for NDJSON files |
| Auto Loader | File discovery, schema inference, exactly-once ingestion into bronze |
| dbt project | bronze → silver → gold transformation, tests, dimension seeds |
| Lakeflow Job | Orchestrates ingest → transform → detect → publish |
| SQL warehouse (2X-Small) | dbt execution target, ad-hoc query, Statement Execution API endpoint |

---

## Databricks Free Edition constraints

These are load-bearing. Every architectural decision below traces back to one of them.

| Constraint | Consequence |
|---|---|
| Serverless compute only, no custom configurations | No cluster tuning; use serverless job compute |
| One SQL warehouse, `2X-Small` | The warehouse is shared between dbt and any query traffic — don't point the dashboard at it |
| Max 5 concurrent job tasks per account | Sequential tasks within one job are fine; parallel jobs are not |
| One active Lakeflow pipeline per pipeline type | Only relevant if a declarative pipeline is added later |
| Quota exhaustion shuts down compute for the rest of the day (or month) | **No continuous streaming.** Use `trigger(availableNow=True)` on a schedule |
| Outbound internet restricted to trusted domains | SWAPI ingestion and any LLM call must happen outside, *or* LinkedIn verification must be completed first |
| Databricks Apps auto-stop after 24 hours | Don't host the dashboard as a Databricks App |
| Accounts may be deleted after prolonged inactivity | Keep the scheduled job running; it counts as activity |
| No account console or account-level APIs | Terraform is limited to workspace-scoped resources |
| Non-commercial use only | Fine for a portfolio; do not use for client work |

**Action required before any implementation:** complete LinkedIn verification in the Databricks
account. It unlocks outbound internet access from serverless compute. Without it, the SWAPI
dimension refresh job cannot reach the API.

---

## Design decisions

### D1 — Batch file ingestion rather than a streaming endpoint

**Decision.** The collector bridge batches events into newline-delimited JSON and writes them to
a Unity Catalog volume. Auto Loader ingests them with `trigger(availableNow=True)` on a
five-minute schedule.

**Why.** Free Edition offers no inbound streaming endpoint, and a continuously-running stream
would exhaust the compute quota.

**Why this is not a downgrade.** `availableNow` uses the identical Structured Streaming
machinery as a continuous trigger — same checkpoint, same exactly-once guarantees, same schema
evolution handling. It processes all available data and stops. Promoting to continuous
execution is a one-line trigger change. Triggered micro-batch is also what a large share of
production lakehouses actually run, because paying for idle compute between event bursts is
rarely worth the latency reduction.

**Thematic bonus.** Batch cadence maps to "how often the Council reviews sector reports."

### D2 — SWAPI as the dimension layer, not a data source

**Decision.** SWAPI data is snapshotted once into `warehouse/dbt/seeds/` as CSV and loaded via
`dbt seed`. It is never called at pipeline runtime.

**Why.** SWAPI is static reference data — roughly 60 planets, 80 people, 37 species, 36
starships. It maps to conformed dimensions, not to a stream. Runtime calls would make the
pipeline depend on third-party availability for no benefit.

**Source choice.** Use `swapi.info` rather than `swapi.dev`. It is served from static files
behind a CDN with no rate limits, and has been more reliable than the Django-based mirror.

### D3 — Deterministic detection, probabilistic response

**Decision.** Detection *and classification* are pure SQL: composite deviation score from signed
z-scores against a rolling 90-day baseline, deterministic signature classification, sustained-scan
requirement, cooldown deduplication. The Yoda agent is invoked only once an incident row exists,
and it receives an already-classified signature rather than inferring one.

**Why.** Putting an LLM in the per-event path makes the pipeline non-deterministic, untestable,
slow, and expensive. Keeping classification deterministic too means the whole chain from raw
telemetry through signature to specialty matching is testable in dbt, and the model handles only
the part that genuinely needs judgment.

### D4 — Serving store split

**Decision.** Gold aggregates are published from Databricks to PostgreSQL. The dashboard reads
Postgres and never queries the lakehouse.

**Why.** A `2X-Small` warehouse should not be answering dashboard refreshes while also running
dbt. Beyond the Free Edition constraint, this is the correct production pattern: a warehouse is
for analytics, a serving store is for applications.

**Direction.** Pull from outside using the SQL Statement Execution API rather than pushing from
inside a notebook. This keeps the work off the Databricks quota and avoids the egress question.

### D5 — dbt Core over Lakeflow declarative pipelines

**Decision.** Transformation is dbt Core with the `dbt-databricks` adapter, orchestrated by the
native `dbt` task type in Lakeflow Jobs, with source code pulled from Git.

**Why.** dbt is a stated learning objective. The models stay portable, which is what makes the
local Postgres demo possible. `dbt test` gives data quality assertions as a first-class,
version-controlled artifact rather than ad-hoc notebook checks. Pulling from Git makes the
GitHub repo the source of truth for production code rather than an export of a workspace
notebook.

**Trade-off accepted.** Lakeflow's lineage graph is a better screenshot. dbt's `docs generate`
output is an acceptable substitute and can be published to GitHub Pages.

### D6 — Local-first development

**Decision.** `docker-compose.yml` runs the entire pipeline against Postgres with no cloud
account required. `make demo` is the single entry point.

**Why.** A repo that someone can run in one command is a categorically better artifact than one
requiring them to provision a workspace. It also gives you a fast iteration loop and
deterministic test data.

**Constraint this imposes.** dbt models must avoid Databricks-only SQL where a portable
equivalent exists. Where a Databricks-specific feature is genuinely needed (`VARIANT`,
`read_files`), isolate it behind a macro with a Postgres branch.

### D7 — AI enrichment is a build-time, frozen artifact

**Decision.** Planet Force parameters, system and region assignments, and the Jedi attribute table
are LLM-generated once (`gemini-3.1-flash-lite`, default temperature), human-reviewed, and
committed as seed CSVs. Never called at runtime.

**Why.** SWAPI's `residents` and `films` arrays are empty for most planets and `population` is
often `"unknown"` — deriving baselines from them yields a galaxy where forty worlds are
statistically dead. Enrichment fixes that properly.

**Why frozen.** The 90-day synthetic backfill is generated from these parameters, and every
rolling baseline and z-score derives from that backfill. Regenerating enrichment with different
output would silently invalidate all historical statistics — no error, just thresholds that no
longer mean what they meant. Regeneration is a migration event. See doc 08.

### D8 — Reports can trigger emergencies, but never touch baselines

**Decision.** Web-submitted reports carry model-inferred sensor values. A report with
`relevance_score >= 0.7` and an inferred score above threshold creates an emergency on its own,
tagged `is_report_sourced = true`. **Inferred values never enter `gold.sector_baseline`.**

**Why permissive.** "Darth Vader is walking the streets" should do something. A system that only
trusts sensors is less interesting and less realistic than one that weighs human reports.

**Why the baseline exclusion is absolute.** If inferred values entered the rolling baseline, one
dramatic description would permanently shift that planet's statistical profile and corrupt every
subsequent z-score. Enforced by a `WHERE source_type = 'probe'` filter and a dbt test. This is the
most important test in the project.

**Consequence.** The agent and the dashboard both see `is_report_sourced`, so they know whether
they are acting on telemetry or on a sighting.

### D9 — Manual and agent deployments share one constraint layer

**Decision.** User-initiated deployments from the dashboard pass through the identical validation
code as agent decisions. `decided_by` is the only difference in the record.

**Why.** A human must not be able to create states the agent cannot reason about — deploying an
already-assigned Jedi, exceeding the concurrent cap. It also means both decision types land in the
same schema with the same `context_snapshot`, which lets the dashboard show them side by side.

### D10 — No LLM on the probe

**Decision.** All inference runs in the cloud. The Raspberry Pi 3 probe runs no model; it
simulates readings and buffers them. Report inference runs on the collector bridge and the Yoda
agent calls Gemini from Cloud Run (see Component boundaries).

**Why.** The Pi 3 has 1 GB of RAM and no accelerator, so it would be slow and memory-bound, and an
on-device model would couple sensor reliability to the heaviest workload in the system. This is a
key difference from the earlier Force Resonance Detection Network (FRDN) project this one
replaces.

---

## Data flow, end to end

1. Probe sweeps all 60 planets every 15 minutes, emitting the full sweep as one burst sharing a
   `scan_id`. Values are mean-reverting random walks seeded from the enrichment parameters, with
   2–3% deliberate fault injection. In `DISCONNECTED` it buffers to SQLite instead; in `BURST` it
   drains that buffer; in `STEALTH` it reports the dark side channel only, hourly.
2. Web intake takes a free-text report plus a planet. The bridge's inference endpoint asks a model
   for three sensor-equivalent values and a relevance score, passing the planet's baseline in for
   scale. Values are clamped in code.
3. Collector bridge accumulates and flushes an NDJSON file per completed scan, or on a 4 MB / 90
   second fallback.
4. Every 15 minutes (offset 3 minutes behind the scan), the Lakeflow Job runs:
   - **Ingest** — Auto Loader reads new files into `bronze.events`, then stops.
   - **Transform** — `dbt build` runs silver and gold plus tests. Signature classification and
     incident detection live inside this DAG.
   - **Publish** — gold aggregates copied to Postgres.
5. Daily, `gold.sector_baseline` rebuilds from the trailing 90 days of **probe-only** data.
6. Every 15 minutes (offset 4 minutes), Cloud Run polls `gold.disturbance` for unprocessed
   incidents. The Yoda agent reasons over context and writes to `gold.deployment`.
7. Dashboard polls Postgres. Users can view by region, system, and planet, and deploy manually
   through the shared constraint layer.

---

## Open items to verify during Phase 0

These could not be confirmed from documentation and should be tested cheaply before the design
depends on them.

- ~~**Is outbound internet access available?**~~ **RESOLVED.** LinkedIn verification is active
  and egress works. Both `swapi.info` and `api.groq.com` return 200 from serverless compute.
  Requires an explicit User-Agent header — see doc 05. The `refresh_dimensions` job can run as a
  real Databricks job; the local-fetch fallback is not needed.
- ~~**Does Free Edition support the `dbt` job task type?**~~ **RESOLVED: yes.** A `dbt` job task
  sourced from Git ran a two-model project, with its seed and tests, on serverless compute.
  Evidence: `docs/PHASE0-RESULTS.md`, Q1.
- ~~**Does Free Edition permit Unity Catalog external locations pointing at GCS?**~~ **RESOLVED:
  no.** The workspace is on AWS and the Create credential dialog offers no Google Cloud
  credential type, so a GCS external location cannot be created. The bridge keeps writing to the
  UC volume through the Files API. Evidence: `docs/PHASE0-RESULTS.md`, Q2.
- ~~**Is the `streaming_table` materialization stable in the pinned `dbt-databricks`
  version?**~~ **RESOLVED: yes, with a stability caveat.** A `streaming_table` model over
  `stream read_files(...)` built and refreshed incrementally, but the first attempt of the refresh
  build failed (a Spark Connect session was deleted before it became ready) and only the automatic
  retry succeeded. Whether Auto Loader moves into the dbt DAG is a separate decision. Evidence:
  `docs/PHASE0-RESULTS.md`, Q3.
- **Is the `materialized_view` materialization stable in the pinned `dbt-databricks`
  version?** Not tested in Phase 0.
