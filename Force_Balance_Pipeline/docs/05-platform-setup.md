# 05 — Platform setup

## Databricks workspace

### Prerequisites

1. Create a Databricks Free Edition account.
2. **Complete LinkedIn verification.** This unlocks outbound internet access from serverless
   compute. Without it the SWAPI dimension refresh cannot reach the API. Do this first.
3. Generate a personal access token for local dbt development and for the collector bridge.
4. Create a Gemini API key in Google AI Studio. Locally it lives in `.env` as `GEMINI_API_KEY`.
   Cloud Run (Yoda agent) and the collector bridge read it from Secret Manager, not from a file.

### Outbound HTTP clients — set a User-Agent

Every outbound request from Databricks compute must send an explicit User-Agent. The default
`Python-urllib/3.x` is rejected by Cloudflare with `403` and body `error code: 1010`, which
looks like blocked egress but isn't — the request reaches the destination CDN and is refused
there on client fingerprint.

```python
UA = "Force_Balance_Pipeline/0.1 (+https://github.com/jivejong/JiveRepo/tree/main/Force_Balance_Pipeline)"
req = urllib.request.Request(url, headers={"User-Agent": UA})
```

Applies to `fetch_swapi.py` and any other outbound client. Identify the project rather than
spoofing a browser — swapi.info is a free community service and a descriptive UA with a repo
link is the correct way to consume it.

**OPEN:** the Gemini SDK sets its own User-Agent. Decide whether to append the project UA through
client options, or whether the SDK's own UA is enough for the Gemini API.

**Verification test.** To confirm egress independently of this issue, request both
`https://swapi.info/api/planets/1` and `https://generativelanguage.googleapis.com/` with a real
User-Agent. Do not test with `pypi.org` alone; it is on the trusted-domain allowlist so package
installs work, and will succeed even when general egress does not.

### Unity Catalog objects

```sql
CREATE CATALOG IF NOT EXISTS force;
CREATE SCHEMA IF NOT EXISTS force.raw;
CREATE SCHEMA IF NOT EXISTS force.bronze;
CREATE SCHEMA IF NOT EXISTS force.silver;
CREATE SCHEMA IF NOT EXISTS force.gold;

CREATE VOLUME IF NOT EXISTS force.raw.telemetry;
CREATE VOLUME IF NOT EXISTS force.raw.checkpoints;
```

The checkpoints volume holds Auto Loader state. Keep it separate from the landing zone.

### SQL warehouse

Free Edition provides one warehouse capped at `2X-Small`. Note its **HTTP path** from the
connection details — dbt needs it.

Set auto-stop to the minimum available. The warehouse should not idle.

---

## Auto Loader ingestion

A notebook or Python job task. Runs, ingests what's available, stops.

```python
from pyspark.sql import functions as F

LANDING = "/Volumes/force/raw/telemetry"
CHECKPOINT = "/Volumes/force/raw/checkpoints/bronze_events"

df = (
    spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "json")
    .option("cloudFiles.schemaLocation", f"{CHECKPOINT}/schema")
    .option("cloudFiles.inferColumnTypes", "false")
    .option("cloudFiles.schemaEvolutionMode", "rescue")
    .option("multiLine", "false")
    .load(LANDING)
)

out = (
    df.withColumn("_source_file", F.col("_metadata.file_path"))
      .withColumn("_ingest_ts", F.current_timestamp())
      .withColumn("event_time", F.to_timestamp("event_time"))
      .withColumn("payload", F.parse_json(F.to_json("payload")))
)

(
    out.writeStream
    .format("delta")
    .option("checkpointLocation", f"{CHECKPOINT}/write")
    .option("mergeSchema", "true")
    .partitionBy("dt")
    .trigger(availableNow=True)
    .toTable("force.bronze.events")
    .awaitTermination()
)
```

Notes:

- `trigger(availableNow=True)` is the Free Edition–compatible trigger. Same streaming semantics,
  processes available data and stops. Switching to `.trigger(processingTime="30 seconds")` for a
  continuous job is the only change needed if you later move to a paid tier.
- `schemaEvolutionMode = "rescue"` puts unexpected fields in a rescued-data column rather than
  failing the stream. Combined with `VARIANT`, new payload fields never break ingestion.
- `awaitTermination()` matters — without it the job task can exit before the batch completes.
- `dt` and `hh` come from the Hive-style path prefixes automatically.

---

## dbt project

### Installation

```bash
pip install -r warehouse/dbt/requirements.txt
```

`warehouse/dbt/requirements.txt` pins both `dbt-databricks==1.9.*` and `dbt-core==1.10.13`.
Pin the versions. Databricks recommends 1.6.0 or greater and recommends pinning so development
and production match. On serverless job compute, set the versions through the **Environment and
Libraries** field on the task, not the dependent-libraries field; the job's serverless
environment installs both pins.

### `profiles.yml`

```yaml
force_balance:
  target: dev
  outputs:
    dev:
      type: databricks
      catalog: force
      schema: dev_<yourname>
      host: "{{ env_var('DATABRICKS_HOST') }}"
      http_path: "{{ env_var('DATABRICKS_HTTP_PATH') }}"
      token: "{{ env_var('DATABRICKS_TOKEN') }}"
      threads: 4
    prod:
      type: databricks
      catalog: force
      schema: gold
      host: "{{ env_var('DATABRICKS_HOST') }}"
      http_path: "{{ env_var('DATABRICKS_HTTP_PATH') }}"
      token: "{{ env_var('DATABRICKS_TOKEN') }}"
      threads: 4
    local:
      type: postgres
      host: localhost
      port: 5432
      user: force
      password: force
      dbname: force
      schema: gold
      threads: 4
```

The `catalog` field enables Unity Catalog's three-level namespace, supported since
`dbt-databricks` 1.1.1. The `local` target is what makes `make demo` work.

Develop against the SQL warehouse. **dbt Python models cannot run on a SQL warehouse** — stay in
SQL and this never comes up.

### Project layout

```
warehouse/dbt/
├── dbt_project.yml
├── profiles.yml.example
├── seeds/
│   ├── dim_sector.csv              # SWAPI + AI enrichment, reviewed, frozen
│   ├── dim_jedi.csv                # prequel roster, SWAPI + AI enrichment
│   ├── dim_species.csv
│   ├── dim_starship.csv
│   └── ENRICHMENT_PROVENANCE.md
├── models/
│   ├── silver/
│   │   ├── _silver__models.yml
│   │   ├── silver_probe_reading.sql
│   │   ├── silver_force_report.sql
│   │   ├── silver_rejects.sql
│   │   └── silver_device_heartbeat.sql
│   ├── gold/
│   │   ├── _gold__models.yml
│   │   ├── gold_sector_baseline.sql
│   │   ├── gold_sector_reading.sql
│   │   ├── gold_disturbance.sql
│   │   └── gold_deployment.sql
│   └── sources.yml
├── macros/
│   ├── extract_payload.sql      # VARIANT on Databricks, JSONB on Postgres
│   ├── classify_signature.sql   # deterministic CASE on signed z-scores
│   └── generate_ulid.sql
└── tests/
    ├── assert_cooldown_respected.sql
    └── assert_imbalance_in_range.sql
```

### Portability macro

The one place the two targets genuinely diverge is payload extraction. Isolate it:

```sql
{% macro extract_payload(column, field, type) %}
  {% if target.type == 'databricks' %}
    CAST({{ column }}:{{ field }} AS {{ type }})
  {% else %}
    CAST({{ column }} ->> '{{ field }}' AS {{ type }})
  {% endif %}
{% endmacro %}
```

Every silver model uses this rather than raw syntax. Keeps the local demo honest.

### Incremental strategy

Silver models are incremental with `merge` on `event_id`:

```sql
{{ config(
    materialized='incremental',
    unique_key='event_id',
    incremental_strategy='merge',
    partition_by=['dt']
) }}
```

`gold_sector_reading` cannot be a simple append. Replayed events from a `BURST` drain land in
historical periods, so affected rows must be recomputed. Use a lookback:

```sql
{% if is_incremental() %}
  WHERE event_time >= (SELECT MIN(event_time) FROM {{ ref('silver_probe_reading') }}
                       WHERE _ingest_ts > (SELECT MAX(_last_built) FROM {{ this }}))
{% endif %}
```

Simpler and safer for this data volume: recompute a trailing 48-hour window on every run. At
this scale the cost is negligible and the correctness is unconditional. Start there; optimize
only if it becomes slow.

---

## Job topology

Free Edition allows 5 concurrent job tasks. **Sequential tasks within one job do not count
against that limit concurrently**, so a four-task linear job is well within budget.

### Job 1 — `force_pipeline` (every 15 minutes, offset +3 from scan)

| Task              | Type                                          | Depends on      |
| ----------------- | --------------------------------------------- | --------------- |
| `ingest_bronze`   | Notebook (Auto Loader)                        | —               |
| `transform`       | dbt (`dbt build`, catalog=force, schema=gold) | `ingest_bronze` |
| `publish_serving` | Notebook                                      | `transform`     |

Signature classification and detection live inside the dbt DAG as `gold_sector_reading` and
`gold_disturbance`, so neither needs its own task.

Cadence matters: with a 15-minute scan cycle, a 5-minute schedule would burn quota finding nothing
on two runs out of three. Offset 3 minutes behind the scan so files have landed.

`publish_serving` writes gold aggregates to Postgres. If outbound egress to your Postgres host is
blocked, invert it: have a Cloud Run job pull via the SQL Statement Execution API on the same
schedule. The pull direction is preferred anyway — it keeps the work off the Databricks quota.

### Job 2 — `rebuild_baseline` (daily, 03:00 UTC)

| Task               | Type                                                         |
| ------------------ | ------------------------------------------------------------ |
| `rebuild_baseline` | dbt — `dbt run --select gold_sector_baseline --full-refresh` |

Rolling 90-day statistics per planet per channel. **Probe-only** — the model must filter
`source_type = 'probe'`. Daily rather than per-run because baselines should be stable within a day;
recomputing them every 15 minutes would make z-scores drift under the detector.

### Job 3 — `maintenance` (weekly)

| Task              | Type                                             |
| ----------------- | ------------------------------------------------ |
| `optimize_vacuum` | SQL — `OPTIMIZE` + `VACUUM` on bronze and silver |

Small-file compaction. Necessary because the bridge produces a file per minute.

### Job 4 — `refresh_dimensions` (manual trigger only)

| Task          | Type                                                   |
| ------------- | ------------------------------------------------------ |
| `fetch_swapi` | Notebook — pull swapi.info with an explicit User-Agent |
| `seed`        | dbt (`dbt seed --full-refresh`)                        |

Manual only, and rarely. SWAPI data doesn't change.

**This job does not regenerate enrichment.** The enriched columns in `dim_sector` and `dim_jedi`
come from committed CSVs produced by `scripts/enrich_*.py`. Regenerating them invalidates the
90-day backfill and every derived baseline — see doc 08. This job refreshes the SWAPI passthrough
columns only, and should be run with care that it does not clobber enriched columns.

### dbt task configuration

- **Source:** Git provider, pointed at `https://github.com/jivejong/JiveRepo`. This project lives in
  that repo as the top-level folder `Force_Balance_Pipeline/`. Not workspace files.
- **Project directory:** `Force_Balance_Pipeline/warehouse/dbt`
- **Commands:** `dbt deps`, `dbt seed`, `dbt build` (no `--target`).
- **Profile:** the task uses the profile Databricks generates, whose only target is
  `databricks_cluster` (verified in the Phase 0 q3 run log). The task's `catalog` and
  `schema` fields decide where models land. The `prod` target in `profiles.yml.example`
  is only for running against prod from a local machine.
- **Serverless environment:** `environment_version` `"5"` (Python 3.12). The packages pinned in
  `warehouse/dbt/requirements.txt` install into it.
- **SQL warehouse:** select the 2X-Small warehouse
- Databricks injects `DBT_ACCESS_TOKEN` for the run-as principal, so no token in the repo.
- Run artifacts — logs, results, manifests, configuration — are archived automatically per run.

Pulling from Git means the repository is the source of truth for production transformation code.
That's a deliberate property: someone reading the repo sees the actual production path.

---

## Local development stack

`docker-compose.yml` brings up:

| Service     | Purpose                                                                         |
| ----------- | ------------------------------------------------------------------------------- |
| `mosquitto` | MQTT broker                                                                     |
| `postgres`  | Stands in for the lakehouse and serves as the serving store                     |
| `bridge`    | Collector, configured to write to a local directory instead of the volume       |
| `loader`    | Watches the local directory, loads NDJSON into `bronze.events` (Postgres JSONB) |
| `probe-sim` | Probe simulator, compressed mode schedule and 1-minute scan cycle               |
| `intake`    | Report inference endpoint (mockable with a stub model for offline demo)         |
| `dashboard` | Jedi Council                                                                    |

`make demo` runs compose up, waits for health, runs `dbt build --target local`, and opens the
dashboard. `make seed` regenerates SWAPI seeds. `make test` runs `dbt test --target local` plus
the agent fixture suite.
