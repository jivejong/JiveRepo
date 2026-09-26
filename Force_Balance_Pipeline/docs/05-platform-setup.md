# 05 — Platform setup

## Databricks workspace

### Prerequisites

1. Create a Databricks Free Edition account.
2. **Complete LinkedIn verification.** This unlocks outbound internet access from serverless
   compute. Without it the SWAPI dimension refresh cannot reach the API. Do this first.
3. Generate a personal access token for local dbt development. The bridge does not use it.
4. Create the bridge's credential: a service principal `force-bridge`, an OAuth secret for it, and
   grants that let it write the landing volume and nothing else. The menu names follow Databricks'
   OAuth machine-to-machine documentation; Free Edition may differ slightly.

   1. **Create it in the account.** Account console, User management, Service principals, Add service
      principal, named `force-bridge`. (A workspace admin can instead use Settings, Identity and
      access, Service principals, Manage, Add service principal.)
   2. **Add it to the workspace.** Account console, Workspaces, your workspace, Permissions, Add
      permissions, `force-bridge`, role User. In the workspace, Settings, Identity and access, Service
      principals, Manage, `force-bridge`, Configuration tab: it lists the entitlements; leave the
      defaults.
   3. **Generate an OAuth secret.** The same page, Secrets tab, Generate secret. Set a lifetime (at
      most 730 days). Databricks secrets can be scoped: the scopes are fixed when the secret is
      generated and cannot be changed afterward, so choose the **`files`** scope (the scope that covers
      the Files API) for this one, not `all-apis`. **The secret is shown once**: copy it together with
      the client ID, then click Done. Account admins and workspace admins can both generate one.
   4. **Find the application ID.** The client ID shown with the secret is the service principal's
      application ID, a UUID; the service principal's page shows it too. Grants take this UUID, not the
      display name: Databricks describes a service principal as "represented by its applicationId
      value" in Unity Catalog grants.
   5. **Grant, as a user who can grant on these objects** (for example the owner of `force`). Replace
      `<application-id>` with the UUID from step 4 and keep the backticks:

      ```sql
      GRANT USE CATALOG ON CATALOG force TO `<application-id>`;
      GRANT USE SCHEMA ON SCHEMA force.raw TO `<application-id>`;
      GRANT READ VOLUME, WRITE VOLUME ON VOLUME force.raw.telemetry TO `<application-id>`;
      ```

      Creating files in a volume needs `USE CATALOG`, `USE SCHEMA`, `READ VOLUME` and `WRITE VOLUME`.
      Nothing is granted on `force.raw.checkpoints`, `bronze`, `silver`, `gold` or the SQL warehouse.
   6. **Store the credentials in `.env.bridge`**, which is gitignored: copy `.env.bridge.example` and
      fill in `DATABRICKS_HOST`, `BRIDGE_DATABRICKS_CLIENT_ID` (the application ID) and
      `BRIDGE_DATABRICKS_CLIENT_SECRET`. The file holds only those three keys. The bridge reads it by
      default (`--env-file` names another path), values already in the process environment win, and it
      refuses a file that contains `DATABRICKS_TOKEN`. On the e2-micro the same three values come from
      Secret Manager.
   7. **Check the grant is narrow** (Phase 2, first workspace run). The bridge exchanges the client ID
      and secret at `/oidc/v1/token` (client credentials, sent with the project User-Agent) and asks for
      the scope `files`; `--oauth-scope` changes it, for example to `all-apis` with an unscoped secret.
      A token is valid for one hour, and the bridge caches it and refreshes it before it expires. With that token, list the telemetry volume through the Files API: it succeeds. List
      `force.raw.checkpoints`: it fails with a permission error. A `bronze` query fails too. Fallback,
      only if an OAuth secret cannot be generated: a personal access token owned by the service
      principal.

   **Verified against the workspace (2026-09-26, `ingest/workspace_check.py`, scope `files`):**
   - The token response has the fields `access_token`, `token_type`, `expires_in` (3600 s) and
     `scope`, and its `scope` is `files`, the scope that was requested.
   - A PUT with `overwrite=false` to a new path returns **204**. The same PUT again returns **409**
     with error code `ALREADY_EXISTS` and the message "The file being created already exists."
     The Files API reference does not name this status; the bridge treats 409, or an
     `ALREADY_EXISTS` error code, as "already exists".
   - Listing the telemetry volume succeeds. Listing `force.raw.checkpoints` returns 403
     `PERMISSION_DENIED` (no `READ VOLUME`). A call to the SQL warehouses API returns 403 "Provided
     OAuth token does not have required scopes: sql", so the files scope is enforced.
5. Create a Gemini API key in Google AI Studio. Locally it lives in `.env` as `GEMINI_API_KEY`.
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

**Gemini calls.** Gemini is called over its REST API through `http_request()`, with structured
output, and not through the Google SDK. The SDK sets its own User-Agent; going through
`http_request()` keeps one explicit project User-Agent on every outbound call, the same code path
as every other client in this repo. Structured output is `responseMimeType` +
`responseJsonSchema`; the `responseFormat` shape was rejected by the API (probe, 2026-09-24).
Exact item-count constraints (`minItems`/`maxItems`) are also rejected: at 59 items, and at 17 when
combined with a 17-value enum. Counts are enforced in code instead (probe steps 12, 15-18).

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
    .option("cloudFiles.schemaHints", "is_synthetic BOOLEAN, synthetic_ingest_ts TIMESTAMP")
    .option("cloudFiles.schemaEvolutionMode", "rescue")
    .option("multiLine", "false")
    .load(LANDING)
)

out = (
    df.withColumn("_source_file", F.col("_metadata.file_path"))
      .withColumn("_ingest_ts", F.current_timestamp())
      .withColumn("event_time", F.to_timestamp("event_time"))
      .withColumn("schema_version", F.col("schema_version").cast("int"))
      .withColumn("dt", F.to_date("dt"))
      .withColumn("hh", F.col("hh").cast("int"))
      .withColumn("payload", F.expr("try_parse_json(payload)"))
      .select("event_id", "source_id", "source_type", "mode", "scan_id", "sector_id",
              "schema_version", "event_time", "is_synthetic", "synthetic_ingest_ts", "payload",
              "dt", "hh", "_source_file", "_ingest_ts", "_rescued_data")
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
- The code above is `ingest/autoloader_bronze.py`; a test keeps the two identical. The `select` lists
  the columns in the order of doc 03's `bronze.events` table.

### First run, by hand

Do this after the bridge has landed files in `force.raw.telemetry` (`ingest/verify_landing.py`
confirms they are there). Run everything below as yourself, the owner of `force`, not as
`force-bridge`.

1. **Create the Git folder.** In the workspace sidebar choose Workspace, then Create, then Git
   folder. Set the Git repository URL to `https://github.com/jivejong/JiveRepo`, the Git provider to
   GitHub, and the name to `JiveRepo`. Optionally turn on sparse checkout with the cone pattern
   `Force_Balance_Pipeline/ingest`, so only that folder is cloned. Click Create Git folder. A public
   repository needs no credentials; for a private one, first link your GitHub account (Settings,
   Linked accounts) with a token that can read it. Check that the folder is on branch `main` at the
   latest commit; use the branch menu's Pull if it is behind.
2. **Open the notebook.** In the Git folder, open
   `Force_Balance_Pipeline/ingest/autoloader_bronze.py`. Its first line, `# Databricks notebook source`,
   makes Databricks open it as a notebook.
3. **Attach compute.** Choose Serverless in the compute menu (the only kind Free Edition has). In the
   Environment side panel pick the newest environment version; the project's jobs use `5`. If the run
   fails with `try_parse_json` not found, the environment is too old.
4. **Run all.** The last cell starts the stream, ingests the landed files and stops: it finishes in
   about a minute with no error. Running it again is safe and adds nothing while no new file has
   landed.
5. **Run the first query below**, in the SQL editor or a notebook SQL cell.

### The first query: does `payload` hold real numbers?

**Recorded result (2026-09-26, first run).** The original `payload` line,
`parse_json(to_json(payload))`, failed at analysis with `DATATYPE_MISMATCH.INVALID_JSON_SCHEMA`:
`to_json(payload)` received the input schema `STRING`, but it needs a struct, array, map or variant.
With `inferColumnTypes = false`, Auto Loader hands the nested `payload` object over as a STRING of raw
JSON. The notebook therefore parses that string directly with `try_parse_json(payload)`. The result is
a VARIANT whose numbers keep their JSON types, and a malformed payload becomes NULL in bronze instead of
failing the stream, so a NULL payload is where a bad event shows up. Check the fixed line before
anything else is built on the table:

```sql
SELECT event_id, sector_id,
       typeof(payload)                  AS payload_type,
       schema_of_variant(payload)       AS payload_schema,
       payload:midichlorian_ppm::double AS midi,
       payload:battery_pct::int         AS battery
FROM force.bronze.events
WHERE NOT is_synthetic
ORDER BY event_time
LIMIT 5;

SELECT schema_of_variant_agg(payload) AS payload_schema_all_rows FROM force.bronze.events;

SELECT count(*) AS null_payloads FROM force.bronze.events WHERE payload IS NULL;
```

- **Pass:** `payload_type` is `variant`, every field in `payload_schema` is numeric (`BIGINT`,
  `DECIMAL(p,s)` or `DOUBLE`; JSON numbers with a decimal point may show as `DECIMAL`), and
  `null_payloads` is 0.
- **Fail:** any field typed `STRING` (numbers stored as text), the whole payload typed `STRING`, or any
  NULL payload (`try_parse_json` returns NULL for a malformed one). On a fail, do the reset below and
  bring the failing output back, so the `payload` line is fixed in this doc and in the notebook
  together.

The rest of the checkpoint queries are in `ingest/phase2_checkpoint.sql`.

### Reset, only if the first query fails

Run these yourself. The order matters, and both steps are needed:

```sql
DROP TABLE IF EXISTS force.bronze.events;
```

then delete the Auto Loader checkpoint, in a notebook cell:

```python
dbutils.fs.rm("/Volumes/force/raw/checkpoints/bronze_events", True)
```

or with the Databricks CLI:

```
databricks fs rm -r dbfs:/Volumes/force/raw/checkpoints/bronze_events
```

Check both are gone: `SHOW TABLES IN force.bronze;` lists no `events`, and
`databricks fs ls dbfs:/Volumes/force/raw/checkpoints` (or `LIST '/Volumes/force/raw/checkpoints'`
in SQL) lists no `bronze_events`. Then rerun the notebook once the fix is in.

- Drop the table **and** delete the checkpoint. With the table gone but the checkpoint kept, Auto
  Loader believes the files were already processed and the new table stays empty. With the checkpoint
  gone but the table kept, every file is ingested a second time and the table has duplicates. The
  inferred schema lives in `bronze_events/schema`, so deleting the checkpoint directory is also what
  makes the schema be inferred again.
- **Do not delete anything in `/Volumes/force/raw/telemetry`.** Those files are the source; a fresh
  checkpoint reads all of them again.
- Do not try to run this as `force-bridge`: it has no access to `force.raw.checkpoints` or `bronze`,
  by design.

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
      password: "{{ env_var('POSTGRES_PASSWORD') }}"
      dbname: force
      schema: gold
      threads: 4
```

The `catalog` field enables Unity Catalog's three-level namespace, supported since
`dbt-databricks` 1.1.1. The `local` target is what makes `make demo` work.

The local Postgres password is read from `POSTGRES_PASSWORD` and has no default. Whatever starts
the local Postgres container must use the same value.

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

Small-file compaction. Necessary because the bridge produces a file per scan (96 a day) plus a file
per 90 seconds of report traffic.

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
