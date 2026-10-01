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
      .withColumn("_file_modified_ts", F.col("_metadata.file_modification_time"))
      .withColumn("event_time", F.to_timestamp("event_time"))
      .withColumn("schema_version", F.col("schema_version").cast("int"))
      .withColumn("dt", F.to_date("dt"))
      .withColumn("hh", F.col("hh").cast("int"))
      .withColumn("payload", F.expr("try_parse_json(payload)"))
      .select("event_id", "source_id", "source_type", "mode", "scan_id", "sector_id",
              "schema_version", "event_time", "is_synthetic", "synthetic_ingest_ts", "payload",
              "dt", "hh", "_source_file", "_ingest_ts", "_file_modified_ts", "_rescued_data")
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

### The one-time arrival-timestamp backfill (Phase 4)

Run once, after the notebook change above has ingested at least one new file, and only while the ingestion notebook isn't
running (or, once Job 1 exists, while it is paused — Job 1 is not built yet; ingestion is a manual notebook run, doc 05
"Auto Loader ingestion"). The MERGE below and the streaming writer must never hold open transactions against
`force.bronze.events` at the same time.

`read_files()` on a directory path (no wildcard) recurses into the `dt=`/`hh=` subfolders the same way Auto Loader's own
`cloudFiles` reader already does when pointed at the same top-level `LANDING` path — proven by every Phase 2/3 checkpoint
ingesting rows from files nested under those subfolders, and now by this backfill itself (below).

**Recorded result (2026-09-29/30, first attempt).** `_source_file` was assumed to equal `read_files()`'s
`_metadata.file_path` directly, with no normalisation. It matched nothing: dry run `rows_to_update` 0, `files_matched` 0;
the real `MERGE` (`num_affected_rows`) 0. Cause, **derived**: `read_files()` returns `_metadata.file_path` as
`dbfs:/Volumes/force/raw/telemetry/dt=.../hh=.../<file>.ndjson`, but `bronze._source_file` (stamped by the streaming
notebook's own `_metadata.file_path`, doc 03) has no `dbfs:` prefix — the two ingestion paths (`cloudFiles` streaming vs.
ad hoc `read_files()`) format the same underlying column differently. Stripping the prefix from both sides of the join
(a no-op on whichever side doesn't have it) fixed it:

```sql
-- Dry run: the same join the MERGE below uses, counted without writing anything. rows_to_update MUST equal
-- live_rows_null, or STOP -- a mismatch means the path normalisation doesn't cover every case yet.
SELECT count(*) AS live_rows_null FROM force.bronze.events WHERE NOT is_synthetic AND _file_modified_ts IS NULL;

WITH landed AS (
  SELECT DISTINCT regexp_replace(_metadata.file_path, '^dbfs:', '') AS file_path,
         _metadata.file_modification_time AS landed_at
  FROM read_files('/Volumes/force/raw/telemetry/', format => 'text')
)
SELECT count(*) AS rows_to_update, count(DISTINCT l.file_path) AS files_matched
FROM force.bronze.events b
JOIN landed l ON regexp_replace(b._source_file, '^dbfs:', '') = l.file_path
WHERE NOT b.is_synthetic AND b._file_modified_ts IS NULL;
```

Once `rows_to_update = live_rows_null`:

```sql
MERGE INTO force.bronze.events AS tgt
USING (
  SELECT DISTINCT regexp_replace(_metadata.file_path, '^dbfs:', '') AS file_path,
         _metadata.file_modification_time AS landed_at
  FROM read_files('/Volumes/force/raw/telemetry/', format => 'text')
) AS src
ON regexp_replace(tgt._source_file, '^dbfs:', '') = src.file_path
   AND tgt._file_modified_ts IS NULL AND NOT tgt.is_synthetic
WHEN MATCHED THEN UPDATE SET tgt._file_modified_ts = src.landed_at;
```

**Recorded result, corrected run.** `still_null` (the confirm query below) read 9,493 right after the first attempt
above — unchanged, since that attempt affected 0 rows — the same figure the corrected dry run's `rows_to_update` needed
to equal before the real MERGE ran. The corrected dry run's own `rows_to_update` and the corrected MERGE's own
`num_affected_rows` were not captured for this run. Confirm afterward: `SELECT count(*) FROM force.bronze.events WHERE
NOT is_synthetic AND _file_modified_ts IS NULL` — `still_null` 0.

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

**Git Bash on Windows silently breaks the connection.** Found live (2026-09-30): running `dbt` from Git Bash
auto-converts any argument or environment variable that looks like a POSIX path before handing it to the
(native Windows) `dbt.exe` process -- `DATABRICKS_HTTP_PATH` (`/sql/1.0/warehouses/<id>`) becomes something
like `C:/Program Files/Git/sql/1.0/warehouses/<id>`, and every command hangs or fails against a garbage host
path with no clear error (`dbt debug` prints nothing for minutes; the process is alive, just stuck). Confirm
with `dbt debug`'s own "Connection:" block, which echoes `http_path` back -- if it doesn't start with `/`,
this is why. Fix: set `MSYS_NO_PATHCONV=1` in the environment for every `dbt` invocation from Git Bash, or run
`dbt` from PowerShell instead, which doesn't do this conversion at all.

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
├── dbt_project.yml              # +schema per folder below, and the eight Phase 4 vars -- see "Vars" below
├── profiles.yml.example
├── seeds/
│   ├── dim_sector.csv              # SWAPI + AI enrichment, reviewed, frozen
│   ├── dim_jedi.csv                # prequel roster, SWAPI + AI enrichment
│   ├── dim_species.csv
│   ├── dim_starship.csv
│   ├── fault_injection_<period>.csv   # converted from the Pi's fault_injection.jsonl, one seed per reconciliation period
│   └── ENRICHMENT_PROVENANCE.md
├── models/
│   ├── sources.yml               # force.bronze.events, 45-minute freshness
│   ├── staging/
│   │   ├── _staging__models.yml
│   │   ├── _staging__unit_tests.yml
│   │   └── stg_bronze_events.sql   # Jong writes this one by hand (doc 00)
│   ├── silver/
│   │   ├── _silver__models.yml
│   │   ├── _silver__unit_tests.yml
│   │   ├── silver_probe_event.sql    # Jong writes this one by hand (doc 00)
│   │   ├── silver_probe_reading.sql
│   │   ├── silver_force_report.sql
│   │   ├── silver_rejects.sql
│   │   └── silver_source_health.sql  # Jong writes this one by hand (doc 00)
│   └── gold/
│       ├── _gold__models.yml
│       ├── _gold__unit_tests.yml
│       ├── gold_sector_baseline.sql
│       ├── gold_sector_reading.sql
│       ├── gold_disturbance.sql
│       └── gold_deployment.sql       # Phase 6
├── macros/
│   ├── extract_payload.sql      # VARIANT on Databricks; raises on `local` until Phase 8
│   ├── epoch_seconds.sql        # unix_timestamp() on Databricks; raises on `local`, same as extract_payload
│   ├── imbalance_score.sql      # doc 03's composite formula, checked against the doc by test_dbt_doc_parity.py
│   ├── classify_signature.sql   # deterministic CASE on signed z-scores, same parity check
│   ├── deterministic_id.sql     # onset-time + sector hash, ULID-shaped (doc 03: disturbance_id only; deployment_id is Phase 6)
│   ├── generate_schema_name.sql # +schema lands as-is (silver/gold), not concatenated with the target schema
│   └── generic_tests.sql        # expression_is_true, column_is_null -- no dbt_utils dependency
└── tests/
    ├── assert_baseline_probe_only.sql
    ├── assert_cooldown_respected.sql
    └── assert_stealth_never_rejected.sql
```

`assert_imbalance_in_range.sql`, listed here in an earlier draft of this tree, was never built: doc 03's only stated
bound is `imbalance_score >= 0` (03:367), with no upper bound, and that single check is already a column-level test
on `gold_sector_reading.imbalance_score` (`_gold__models.yml`, `expression_is_true: >= 0`). A separate singular test
would duplicate it with no added coverage, so it's dropped from this list rather than built.

### Vars

The eight Phase 4 thresholds/tolerances live in `dbt_project.yml`'s `vars:` block, each commented with the doc 03
line it comes from: `anomaly_threshold` (4.0, 03:239), `emergency_threshold` (5.75, 03:239), `sustained_scans`
(2, 03:310), `cooldown_hours` (2, 03:311), `replay_lag_seconds` (1800, 03:102), `future_tolerance_minutes`
(10, 03:138), `max_age_days` (90, 03:138), `lag_tolerance_seconds` (-2, 03:117). `edge/tests/test_dbt_doc_parity.py`
checks every one of them against doc 03 directly, offline — the same "doc is truth, code fails first" pattern
`test_doc_parity.py` already applies to `forcesim.signatures`. Retuning any of them means editing doc 03 first, in
the same commit as the change here.

### Portability macro

**Phase 4 ships the Databricks branch only** — the Postgres branch, and the rest of the local stack, move to Phase 8 (doc 07's
own fallback, taken). The macro fails loudly on `local` rather than running untested SQL:

```sql
{% macro extract_payload(column, field, type) %}
  {% if target.type == 'databricks' %}
    CAST(try_variant_get({{ column }}, '$.{{ field }}', '{{ type }}') AS {{ type }})
  {% else %}
    {{ exceptions.raise_compiler_error("extract_payload: the Postgres branch is deferred to Phase 8 (doc 07); `local` cannot build silver/gold yet") }}
  {% endif %}
{% endmacro %}
```

(`try_variant_get` rather than a plain `CAST` — a bare `CAST(variant AS DOUBLE)` on a non-numeric value raises under ANSI mode;
`try_variant_get` returns NULL instead, matching bronze's own "malformed becomes NULL" convention above. Whether a numeric
*string* value returns NULL or the parsed number is not asserted yet — establish it against Databricks in the scaffolding
round, not assumed here.)

Every silver model uses this rather than raw syntax. The `local` target, `docker-compose.yml`, `make demo` and the Postgres
`extract_payload` branch move to Phase 8.

### Incremental strategy

Silver models are incremental with `merge` on `event_id`:

```sql
{{ config(
    materialized='incremental',
    unique_key='event_id',
    incremental_strategy='merge'
) }}
```

No `partition_by` — the tables are well under the size where Delta partitioning pays for itself, and partitioning by `dt`
(ingest date, not event date) would fight the recompute window below rather than help it.

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

| Task              | Type                                                                          | Depends on      |
| ----------------- | -------------------------------------------------------------------------------- | --------------- |
| `ingest_bronze`   | Notebook (Auto Loader)                                                        | —               |
| `transform`       | dbt (`dbt build --exclude gold_sector_baseline`, catalog=force, schema=gold) | `ingest_bronze` |
| `publish_serving` | Notebook — **not built yet.** Writes gold aggregates to Postgres, so it waits on the Postgres/local demo stack, deferred to Phase 8 (07:157-158's own fallback, taken in Phase 4). Add it here, `depends_on: transform`, once that stack exists. | `transform`     |

Signature classification and detection live inside the dbt DAG as `gold_sector_reading` and
`gold_disturbance`, so neither needs its own task.

Cadence matters: with a 15-minute scan cycle, a 5-minute schedule would burn quota finding nothing
on two runs out of three. Offset 3 minutes behind the scan so files have landed — **measured, not
guessed, Phase 4 Stage 3a:** under normal `CONNECTED` operation, `docs/PHASE3-RESULTS.md`'s own
landed-file timestamps show files arriving about 3 *seconds* after their scan boundary (the
2026-09-28T19:00:00Z scan landed at 19:00:03Z; the 2026-09-29 checkpoint's :15Z and :30Z scans landed
at :15:03Z and :30:03Z). A 3-minute offset is therefore a 60x margin over the measured normal case,
not a tight fit to it.

`publish_serving` writes gold aggregates to Postgres. If outbound egress to your Postgres host is
blocked, invert it: have a Cloud Run job pull via the SQL Statement Execution API on the same
schedule. The pull direction is preferred anyway — it keeps the work off the Databricks quota.

### Job 2 — `rebuild_baseline` (daily, 03:00 UTC)

| Task               | Type                                                         |
| ------------------ | ------------------------------------------------------------ |
| `rebuild_baseline` | dbt — `dbt run --select gold_sector_baseline --full-refresh` |

Rolling 90-day statistics per planet per channel. **Probe-only** — the model must filter
`source_type = 'probe'`. Excluded from Job 1's `transform` task (above) for exactly this reason: daily rather than per-run
because baselines should be stable within a day; recomputing them every 15 minutes would make z-scores drift under the
detector. 03:00 UTC: a quiet overnight UTC hour, clear of Job 1's continuous :03/:18/:33/:48 cadence — this project has no
timezone of record, so "quiet" means only "doesn't collide with Job 1."

### Deploying Job 1 and Job 2

Both are defined as code: `databricks.yml` (repo root) plus `resources/force_pipeline.job.yml` and
`resources/rebuild_baseline.job.yml`, a Databricks Asset Bundle (DAB) — Databricks' current recommended way to define
jobs as code, chosen over a hand-applied Jobs API JSON file because `databricks bundle deploy` is idempotent (it
updates the same job in place on every deploy) where `databricks jobs create` is not (a second `create` makes a second
job; a hand-rolled approach would need the resulting job id tracked and every change applied via `jobs reset`
instead).

This is a deliberate two-layer split, not a redundancy: the bundle's `databricks bundle deploy` pushes the job
*definitions* (schedule, tasks, which git branch to run) from a developer machine to the workspace, while each job's
own `git_source` (pointed at this repo's `main`, same as the dbt task config below) is what makes the *code those
tasks run* come from Git on every single run — deploying a new job definition and merging a code change to `main` are
two different actions, and only the second one needs no further deploy step. A job run always executes whatever was
on `main` when that run started (`git_snapshot.used_commit` in the run's own API response makes this exact, not
approximate).

`warehouse_id` is the one workspace-specific value the bundle needs; it's a bundle variable with no committed
default, supplied at deploy time only (`BUNDLE_VAR_warehouse_id=<id> databricks bundle deploy`), consistent with
this project's placeholder convention for anything workspace-specific.

**To change either job:** edit the relevant `resources/*.job.yml`, `databricks bundle validate`, then
`databricks bundle deploy` (needs a `BUNDLE_VAR_warehouse_id`-authenticated CLI session; never commit the real
warehouse id). The YAML in the repo is what's deployed — no workspace-UI edits, for the same reason the dbt task
itself pulls from Git rather than a workspace copy (below): someone reading the repo sees the actual deployed
configuration.

**Both jobs were created PAUSED** (`schedule.pause_status: PAUSED`), then unpaused by hand in the workspace UI on
2026-09-30 (Phase 4 Stage 3b) after Job 1's manual run on commit `19cdba2` passed.

**Warning, corrected from an earlier, wrong claim in this section:** `resources/*.job.yml` sets `pause_status`
explicitly, and `databricks bundle deploy` reconciles the *entire* job definition to match the file on every
deploy — it does **not** leave a hand-set pause state alone. An earlier draft of this section claimed the
opposite ("deploying or redeploying the bundle does not change an existing job's pause state"); that was never
actually tested, and is only true for a job whose YAML omits `pause_status` altogether. Because this project's
YAML sets it explicitly, redeploying with a stale `PAUSED` value in the file **would silently re-pause a job an
operator had already turned on by hand** — the exact failure mode this warning exists to prevent. **Both files now
say `UNPAUSED`, matching the live state.** Whoever changes either job going forward must update `pause_status` in
the YAML to match whatever the live job's state should be *before* redeploying, not rely on the workspace UI's
current setting surviving a deploy.

**The manual notebook run (`ingest_bronze`'s notebook, run by hand per "Auto Loader ingestion" above) is retired
now that Job 1 is unpaused** — ingestion, transform, and (once it exists) publish happen on Job 1's own schedule;
do not run the ingestion notebook by hand while Job 1 is unpaused, since both would then be writing to bronze on
overlapping schedules. Job 1's first manual trigger (Phase 4 Stage 3a, 2026-09-30) ran clean end to end:
`ingest_bronze` 83.6s, `transform` 187.9s, both `SUCCESS`, against commit `d923a7c8` (the last commit on `main` at
trigger time — Stage 3a's own changes, including this section, landed in a later commit and were not yet on
`main` when this run fired). Bronze, silver and gold row counts all moved by the identical +4,860 (no rows lost or
gained at any layer); `gold_disturbance` gained 2 new rows from that real backlog (see `docs/ENGINEERING-LOG.md`,
"Stage 3b," for what they are). A second `transform` run against the same data (`dbt build --exclude
gold_sector_baseline`, run locally, same command the task runs) added zero rows at every layer, confirming the
second-run-does-nothing property doc 07's Phase 4 checkpoint asks for.

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
- **Commands:** `dbt deps`, `dbt build` (or `dbt run --select ...` for Job 2), no `--target`. **Deviation, Phase 4
  Stage 3a:** `dbt seed` is NOT in Job 1's or Job 2's commands, despite an earlier draft of this bullet listing it
  generically — seeds are static, enrichment-backed CSVs that change only via Job 4's own explicit `dbt seed
  --full-refresh` (manual trigger only, above); reseeding on every 15-minute Job 1 run would spend warehouse compute
  reproducing identical rows every time.
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

## Phase 3 staging: the desktop broker and the Pi

Committed docs use `<DESKTOP_IP>`, `<PI_IP>` and `<PI_MACHINE_ID>` placeholders — never a real address or machine ID. The
Pi's hostname is not treated as sensitive (a deliberate decision, not an oversight) and may be written directly.
`edge/tests/test_no_sensitive_addresses.py` guards RFC 1918 addresses unconditionally, and, optionally, a machine ID
against an untracked, per-developer list at `~/.force_balance_pipeline/sensitive-strings.txt`: one literal per line,
blank lines and lines starting with `#` ignored, e.g.

```
# ~/.force_balance_pipeline/sensitive-strings.txt
# example only -- replace with this Pi's real machine ID
<the-pi-machine-id>
```

If that file doesn't exist, the hostname/machine-id half of the guard skips with a visible reason rather than silently
passing; the address check runs either way.

In Phase 3 the broker (Mosquitto in Docker) and the bridge (workspace mode) run on the developer's Windows desktop, and the Pi
publishes to the broker over the LAN (both on Ethernet, both with DHCP reservations on the router). There is no TLS: the broker
authenticates with passwords and enforces per-user topic permissions, and the passwords cross the LAN in clear text. TLS and the
e2-micro come together in a later phase. Nothing below is committed with a real address or password.

### The broker on the desktop

1. **Passwords**, in a directory outside the repository. Each command prompts for the password, so none is on a command line; use
   a password manager. Each credential has one home: `probe-01` goes in the Pi's `/etc/force-probe/probe.env`
   (`PROBE_MQTT_PASSWORD`); `force-bridge` goes in the desktop's `.env.mqtt` (`BRIDGE_MQTT_PASSWORD`); `operator` is never stored:
   `edge/probe_ctl.py` asks for it at a prompt (`getpass`), or reads `OPERATOR_MQTT_PASSWORD` from the environment if that is set,
   and never reads it from `.env.mqtt`.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.force_balance_pipeline\mosquitto"
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd -c /work/passwd probe-01
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd /work/passwd force-bridge
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd /work/passwd operator
   ```

2. **The broker**, bound to the desktop's LAN address and to `127.0.0.1`, never to `0.0.0.0`. The config is
   `infra/mosquitto/mosquitto.lan.conf` (`allow_anonymous false`, the password file and `infra/mosquitto/acl` read from the mounted
   directories, persistence and the queue limits of doc 04).

   Mosquitto 2.1.2 refuses to start against a password file mounted straight from Windows (`Unable to open pwfile`, a crash
   loop): the container's `mosquitto` user cannot read a file the Windows bind mount hands it as root-owned. Copy the password
   file into a Docker volume it can read instead, before the first run and again whenever a password changes. **Resolved,
   Phase 4 Stage 3b: the ACL file (`infra/mosquitto/acl`, committed, no secrets) gets the identical treatment in the same
   command** — Mosquitto 2.1.2 raises the same class of warning on a Windows-bind-mounted `acl_file`
   ("future versions will refuse to load this file"), fixed the same way rather than left open:

   ```powershell
   docker volume create force-mosquitto-secrets
   docker run --rm -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/from:ro" -v "<REPO>\Force_Balance_Pipeline\infra\mosquitto:/fromrepo:ro" -v force-mosquitto-secrets:/to eclipse-mosquitto:2 `
     sh -c "cp /from/passwd /to/passwd && cp /fromrepo/acl /to/acl && chown mosquitto:mosquitto /to/passwd /to/acl && chmod 0600 /to/passwd /to/acl"
   ```

   ```powershell
   docker run -d --name force-mosquitto --restart unless-stopped `
     -p <DESKTOP_IP>:1883:1883 -p 127.0.0.1:1883:1883 `
     -v "<REPO>\Force_Balance_Pipeline\infra\mosquitto:/mosquitto/config:ro" `
     -v force-mosquitto-secrets:/mosquitto/secrets:ro `
     -v force-mosquitto-data:/mosquitto/data `
     eclipse-mosquitto:2 mosquitto -c /mosquitto/config/mosquitto.lan.conf
   ```

   `mosquitto.lan.conf`'s own `acl_file` now points at the copy (`/mosquitto/secrets/acl`), not the bind-mounted
   `/mosquitto/config/acl` its committed source lives at — the source of truth stays in the repo; only the deployed path
   changes, same split as the password file. Re-run the copy command above whenever `infra/mosquitto/acl` changes, the
   same discipline the password file already needed.

3. **The firewall**, elevated PowerShell. The rule admits TCP 1883 from the Pi only:

   ```powershell
   New-NetFirewallRule -DisplayName "Force Pi to MQTT" -Direction Inbound -Protocol TCP -LocalPort 1883 `
     -LocalAddress <DESKTOP_IP> -RemoteAddress <PI_IP> -Action Allow -Profile Private
   ```

   Docker Desktop Backend installs its own inbound Allow rules on the Private profile, and Windows combines Allow rules rather
   than picking the most specific one, so those rules override the Pi-only scoping above and open 1883 to the whole LAN. Find and
   disable them — never Block, which would also stop the Pi:

   ```powershell
   Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True | Where-Object { $_.Profile -match "Private" -and
     (Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $_).Program -match "docker" } | Format-Table DisplayName, Profile
   # then, for each DisplayName it lists:
   Disable-NetFirewallRule -DisplayName "<the rule's exact DisplayName>"
   ```

   The broker's own log cannot tell you whether this worked: Docker's port relay presents every client to Mosquitto as
   `172.17.0.1`, so a connection from the LAN and one from the container host look identical there — the check below, a plain TCP
   test from a genuinely separate device, is the only honest one. Docker Desktop can re-create these rules on an update, so
   recheck after one.

4. **Verify that the Pi can connect and nothing else can:**
   - `docker port force-mosquitto` and `netstat -ano | findstr :1883` show `<DESKTOP_IP>` and `127.0.0.1`, no `0.0.0.0` (the
     listening process is Docker's own backend, not `mosquitto.exe` — there is no such thing on Windows).
   - `edge/mqtt_check.py` (each password at a prompt, or from the environment for that run only): the right password connects; a wrong password and an anonymous
     connection are refused; `probe-01` publishing to `force/control/probe-01` is not delivered to a subscriber (the broker
     acknowledges a denied QoS 1 publish, so a subscriber is the only honest test). Run it on the desktop and from the Pi.
   - From a third device on the LAN — an actual TCP probe (`Test-NetConnection <DESKTOP_IP> -Port 1883`), not an MQTT client and
     not the broker log (see The firewall, above) — `TcpTestSucceeded` must be `False`. If it is `True`, a Docker Desktop Backend
     Allow rule is almost always the cause.

5. **The bridge** runs on the desktop in workspace mode as before (doc 04). Its broker credentials come from `.env.mqtt`
   (`BRIDGE_MQTT_USERNAME`, `BRIDGE_MQTT_PASSWORD`, gitignored) or the environment, and its persistent session queues the Pi's
   messages while it is down.

6. **The desktop stays awake and reachable for the tests.** Stop it sleeping (`powercfg /change standby-timeout-ac 0` and
   `powercfg /change hibernate-timeout-ac 0`, and put the values back afterwards); keep Docker Desktop running; keep the VPN in one
   state for the whole checkpoint. A VPN client can block or reroute LAN traffic, so check its "allow local network access"
   setting, then test both ways with the VPN on and off: `Test-NetConnection <PI_IP> -Port 22` from the desktop and
   `nc -vz <DESKTOP_IP> 1883` from the Pi. If the desktop sleeps, the Pi sees an outage.

### The Pi

Raspberry Pi OS Lite, 64-bit, on Ethernet, with an SSH login as `<PI_USER>`.

```bash
sudo apt update && sudo apt install -y git python3-venv sqlite3 nftables
timedatectl status        # "System clock synchronized: yes" and "NTP service: active"
```

**Persistent journal.** journald keeps logs only in `/run/log/journal`, wiped on every reboot — found the hard way, in Phase 3, when
a Pi power loss lost the boot's own logs. `Storage=auto`'s usual rule (persistent if `/var/log/journal` exists, volatile otherwise)
does not apply on this Pi OS image: it ships its own drop-in setting `Storage=volatile` outright, so creating that directory alone
has no effect. Override it with a drop-in of your own, which take precedence in filename order:
```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/90-force-persistent.conf >/dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=100M
EOF
sudo systemctl restart systemd-journald
sudo journalctl --flush
```
Verify: `ls /var/log/journal` shows a machine-id directory (empty means it didn't take — check for another drop-in still forcing
`volatile`, with `systemctl cat systemd-journald | grep Storage` or `find /*/systemd/journald.conf.d`), and `journalctl --disk-usage`
reports non-trivial size. Journals from before this point are not recovered. After the next reboot, `journalctl --list-boots` shows
more than one boot, and the 100M cap keeps it off the SD card's free space regardless of how long the Pi has been up.

`/opt` is root-owned, so create the target directory and hand it to yourself before cloning into it — `deploy.sh` does the same
`mkdir`/`chown` itself and is safe to run again, but the first, manual clone below needs it done first:

```bash
sudo mkdir -p /opt/force-probe
sudo chown "$(id -un)":"$(id -gn)" /opt/force-probe
```

Clone the repository sparsely yourself, at the pinned commit — `infra/pi` is in the cone, so `deploy.sh`, the unit and the env
template travel with the clone and nothing is copied over separately:

```bash
git clone --filter=blob:none --no-checkout https://github.com/jivejong/JiveRepo.git /opt/force-probe/repo
cd /opt/force-probe/repo
git sparse-checkout set --cone Force_Balance_Pipeline/edge Force_Balance_Pipeline/warehouse/dbt/seeds Force_Balance_Pipeline/infra/pi
git checkout <full 40-character commit SHA>
```

Then run the deploy script from inside that clone, as yourself, not with `sudo` — it escalates internally with `sudo` only for the
steps that need it (packages, the service user, the unit, the env file, `daemon-reload`), and reruns the same `mkdir`/`chown` above:

```bash
cd /opt/force-probe/repo/Force_Balance_Pipeline/infra/pi
./deploy.sh <full 40-character commit SHA>
```

It checks out that commit again itself (so it is safe to rerun after a `git pull` to a later SHA), builds the venv in
`/opt/force-probe/venv` from `edge/requirements.txt`, creates the service user, installs `infra/pi/force-probe.service`, and creates
`/etc/force-probe/probe.env` from `infra/pi/probe.env.example` if it is missing (root-owned, mode 0600). Fill it in with
`sudoedit /etc/force-probe/probe.env` (`PROBE_MQTT_HOST`, `PROBE_MQTT_USERNAME`, `PROBE_MQTT_PASSWORD`), then
`sudo systemctl enable --now force-probe`. Logs: `journalctl -u force-probe -f`. The probe logs the commit SHA at start.

The unit orders itself `After=time-sync.target`, but that target waits for a real synchronisation only if
`systemd-time-wait-sync.service` is enabled. The guarantee that nothing is stamped early is the probe's own `NTPSynchronized`
check (doc 04, Clock), not the unit.

The service user owns `/var/lib/force-probe/` (the buffer database, `mode_transitions.jsonl`, `fault_injection.jsonl`), so reading
it from your login needs `sudo`; use `sudo sqlite3 -readonly` for the database so a read cannot touch its WAL files.

### The checkpoint cut

The cut is made on the Pi (doc 07). The rule drops outbound TCP to the broker and a scheduled command removes it after 45 minutes,
scheduled first so a lost SSH session cannot leave it in place. Start it at :16 or :31 past the hour — a few minutes after a
quarter-hour boundary — so the scan taken in the roughly 90 seconds before the probe notices (still labelled `CONNECTED`, doc 02,
but buffered) falls before the cut instead of straddling it:

```bash
sudo systemd-run --on-active=45m --unit=force-cut-restore /usr/sbin/nft delete table inet forcecut
date -u +%FT%TZ | tee -a ~/force-cut.txt
sudo nft add table inet forcecut
sudo nft add chain inet forcecut out '{ type filter hook output priority 0 ; policy accept ; }'
sudo nft add rule inet forcecut out ip daddr <DESKTOP_IP> tcp dport 1883 counter drop
sudo nft list table inet forcecut     # the drop counter rises while the probe retries
sudo tail -n 5 /var/lib/force-probe/mode_transitions.jsonl
```

The rule matches only the broker's port, so the SSH session is unaffected, and it drops rather than rejects, so the probe finds
out the way a real outage does. To stop early: `sudo nft delete table inet forcecut`. The outage the checkpoint queries use is the
`DISCONNECTED` to `BURST` span in `mode_transitions.jsonl`. Run it with `--fault-rate 0` (doc 07).

### Runbook: Stage 3b deploy and two persistence proofs still owed

Two things this phase has built but never actually proven against the real desktop/Pi: that the broker's own disk persistence
(not just a client reconnect) survives a restart, and that the Pi's persistent-journal fix (doc 05, journald section, fixed
2026-09-30) survives an actual reboot, not just a service restart. Both need the desktop and the Pi, so they're a runbook here,
not something this round's code-and-tests-only scope could do — do them together, in one maintenance window.

**1. Prove the broker's own persistence, not just a reconnect, survives a restart.**

```powershell
# on the desktop, bridge NOT running (stop it first if it's running as a service/foreground process)
docker ps --filter name=force-mosquitto           # confirm it's up before starting
```

With the bridge down, let the probe publish a few scans (the broker queues them for the bridge's persistent session,
`clean_session=false`, doc 05's own `max_queued_messages 50000`) — a few minutes is enough. Then:

```powershell
docker restart force-mosquitto                     # the broker process itself restarts, not just a client disconnect
docker logs force-mosquitto --tail 20               # confirm it reloaded from persistence_location, not a cold start
```

Start the bridge. **Proof the restart didn't lose anything:** the files that land for the scans published while the bridge was
down should still appear (check the bridge's own `landed` log lines, or `ls` the telemetry volume), with no gap in `scan_id`s
against what the probe's own log shows it sent. If any of those scans never land, the broker's disk persistence did not survive
the restart as configured — a real finding, not a step to silently skip past.

**2. Deploy this round's publisher fix, and use the same trip to test the journal across a real reboot.**

```bash
# on the Pi, after pulling this round's commit to the clone at /opt/force-probe/repo
cd /opt/force-probe/repo/Force_Balance_Pipeline/infra/pi
./deploy.sh <full 40-character commit SHA>          # the commit containing the publisher.py disconnect-guard fix
journalctl -u force-probe -f                         # confirm it restarts clean on the new code
```

`deploy.sh` restarts the `force-probe` service, which is enough to pick up the new code, but is **not** enough to prove the
persistent-journal fix (`Storage=persistent`, `SystemMaxUse=100M`, doc 05's journald section) survives what it's actually for —
a full power cycle, not a service restart. While already on the Pi for this deploy:

```bash
sudo reboot
# after it comes back and you've reconnected:
journalctl --list-boots                              # more than one boot listed = the journal survived
journalctl -b -1 -u force-probe | tail -20            # the PREVIOUS boot's own force-probe log is still readable
```

This closes the "Persistent-journal fix... not yet confirmed to survive an actual Pi reboot" open item either way: a second
boot listed and the previous boot's log readable is the proof; anything less and the fix needs revisiting, not re-marking as
done.

---

## Local development stack

**Deferred to Phase 8** (doc 07 deviation note: `dbt build` is proven on `prod` only in Phase 4; `extract_payload`'s Postgres
branch raises a compiler error until then). The design below is unchanged, just not built yet.

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
