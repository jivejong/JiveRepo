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
   Cloud Run (Yoda agent) and the collector bridge read it from Secret Manager, not from a file. Phase 6 runs the agent locally as a Windows scheduled task; Cloud Run is an optional later stage (doc 06, "Runtime").

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
│   ├── sources.yml               # force.bronze.events (freshness is monitored outside dbt: Monitoring)
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

### Job 1 — `force_pipeline` (every hour, offset +3 from scan)

| Task              | Type                                                                          | Depends on      |
| ----------------- | -------------------------------------------------------------------------------- | --------------- |
| `ingest_bronze`   | Notebook (Auto Loader)                                                        | —               |
| `transform`       | dbt (`dbt build --exclude gold_sector_baseline`, catalog=force, schema=gold) | `ingest_bronze` |

Signature classification and detection live inside the dbt DAG as `gold_sector_reading` and
`gold_disturbance`, so neither needs its own task.

Cadence matters: with a 15-minute scan cycle, a 5-minute schedule would burn quota finding nothing
on two runs out of three. Offset 3 minutes behind the scan so files have landed — **measured, not
guessed, Phase 4 Stage 3a:** under normal `CONNECTED` operation, `docs/PHASE3-RESULTS.md`'s own
landed-file timestamps show files arriving about 3 *seconds* after their scan boundary (the
2026-09-28T19:00:00Z scan landed at 19:00:03Z; the 2026-09-29 checkpoint's :15Z and :30Z scans landed
at :15:03Z and :30:03Z). A 3-minute offset is therefore a 60x margin over the measured normal case,
not a tight fit to it.

**Cadence changed 15 -> 30 min, Stage 4b, then 30 -> 60 min, Stage 4c** (both `docs/ENGINEERING-LOG.md`): the Free
Edition serverless quota was exhausted on two separate days under the original 15-minute (96-run) cadence — see
"Stage 3d" and "Stage 4a". Halving to 30 minutes (Stage 4b) was then itself measured for 3 days
(`ingest/job1_quota_watch.sql`) and still exhausted the quota every one of those 3 days. From that measurement
(**inference**, not confirmed against a published Databricks number): exhaustion is **silent** (no run is even
created once the day's budget is spent, not a failed run with a visible error); the daily reset is **inferred**
to land between 03:03Z and 03:18Z (Job 2 succeeded at 03:18Z all 3 days — see "Job 2," below — and the 03:03Z Job 1
run never appeared, any of the 3 days); the daily transform budget is **inferred** at roughly 3.4-3.8 cumulative hours.
Going hourly (Stage 4c) halves the run count again, bringing cumulative transform time to roughly 2-2.5 hours/day
at observed per-run durations — real margin under the inferred budget. Still **provisional**: the scan cycle
itself stays 15 minutes unchanged, so an hourly run now processes roughly four scans' worth of new rows instead
of one.

`publish_serving` is not a Job 1 task. Databricks serverless cannot reach the desktop, so it runs locally as `ForceServe` (see "Serving (Phase 7)").

### Job 2 — `rebuild_baseline` (daily, 03:40 UTC)

| Task               | Type                                                         |
| ------------------ | ------------------------------------------------------------ |
| `rebuild_baseline` | dbt — `dbt run --select gold_sector_baseline --full-refresh` |

Rolling 90-day statistics per planet per channel. **Probe-only** — the model must filter
`source_type = 'probe'`. Excluded from Job 1's `transform` task (above) for exactly this reason: daily rather than per-run
because baselines should be stable within a day; recomputing them every Job 1 run would make z-scores drift under the
detector. 03:40 UTC (moved from 03:00, Stage 3d, then 03:10 -> 03:18, Stage 4b, then 03:18 -> 03:40, Stage 4c — see
`docs/ENGINEERING-LOG.md`): a quiet overnight UTC hour, clear of Job 1's `:03` cadence — this project has no timezone
of record, so "quiet" means only "doesn't collide with Job 1." Job 1 going hourly (Stage 4c) means "mid-gap between
Job 1 runs" no longer constrains this job's placement the way it did at 30-minute cadence; the binding constraint is
now the quota reset itself. The Stage 4b quota-watch **inferred** the daily reset lands between 03:03Z and 03:18Z —
Job 2 succeeded at 03:18Z all 3 observed days (the evidence for that upper bound in the first place), meaning its
old start time sat right at the edge of the inferred window, not safely clear of it. 03:40 sits a full 22 minutes
past that inferred edge, with real margin. Job 2's own run takes about 17 minutes (03:18Z-03:35Z observed) — longer
than any single Job 1 transform task; **inference**, not confirmed, but this may itself include a warehouse-start
stall like the one found in the Oct 3 11:03Z/11:33Z Job 1 runs (`docs/ENGINEERING-LOG.md`'s Job 1 cadence open
item, above), rather than reflecting `gold_sector_baseline`'s own query cost.

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
default, supplied at deploy time only, through the `BUNDLE_VAR_warehouse_id` environment variable (set from the workspace, as below,
never by pasting a placeholder), consistent with this project's placeholder convention for anything workspace-specific.

**To change either job:** edit the relevant `resources/*.job.yml`, `databricks bundle validate`, then
`databricks bundle deploy` (needs a `BUNDLE_VAR_warehouse_id`-authenticated CLI session; never commit the real
warehouse id). The YAML in the repo is what's deployed — no workspace-UI edits, for the same reason the dbt task
itself pulls from Git rather than a workspace copy (below): someone reading the repo sees the actual deployed
configuration.

**Setting `BUNDLE_VAR_warehouse_id` — and the failed deploy of 2026-10-11.** The first S2 deploy failed: `jobs/reset` returned HTTP 400
`"<id>" is not a valid endpoint id` for **both** jobs (nothing changed; zero files uploaded). Cause: `BUNDLE_VAR_warehouse_id` held the
literal text `<id>` at user level and in the session; older terminals still had the real value, which hid it. The live jobs and the
warehouse were fine. The repo's own text had shown the variable as `BUNDLE_VAR_warehouse_id=<id>` (here and in `databricks.yml`), a
form that can be copied and run as it stands; that is the likely origin and it has been replaced. Set it from the workspace, with a
guard that refuses anything that is not a real id (Free Edition has exactly one warehouse; with several, choose by name):

```powershell
$id = (databricks warehouses list -o json | ConvertFrom-Json).id
if (@($id).Count -ne 1 -or "$id" -notmatch '^[0-9a-f]{16}$') { throw "expected one 16-character warehouse id, got: $id" }
setx BUNDLE_VAR_warehouse_id $id            # then open a NEW terminal: setx does not change the one you are in
# In the new terminal, confirm both the saved value and the session value are real (True/False only, nothing printed):
[Environment]::GetEnvironmentVariable('BUNDLE_VAR_warehouse_id', 'User') -match '^[0-9a-f]{16}$'
$env:BUNDLE_VAR_warehouse_id -match '^[0-9a-f]{16}$'
```

If a deploy ever fails with `is not a valid endpoint id`, check both of those first. A terminal opened before a bad `setx` keeps the old
value, so the same deploy can pass in one window and fail in another.

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

**`VACUUM`'s retention on `silver_probe_reading` must stay at least 1 day (Delta's own default is 7, via
`delta.deletedFileRetentionDuration` / the `RETAIN n HOURS` clause) and never be shortened with
`VACUUM ... RETAIN 0 HOURS` or `spark.databricks.delta.retentionDurationCheck.enabled=false`.**
`ingest/phase4_checkpoint.sql`'s p4-1 depends on Delta time travel (`TIMESTAMP AS OF`) against
`silver_probe_reading`'s state as of `gold_sector_baseline`'s own `computed_at`, which can be hours old by the
time anyone runs it (`docs/ENGINEERING-LOG.md`, "Stage 3c"); a shorter retention would let `VACUUM` physically
delete the file versions that query needs, breaking the checkpoint silently rather than loudly.

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
  Stage 3a:** the literal subcommand `dbt seed` is never invoked directly by either job's commands, despite an
  earlier draft of this bullet listing it generically. This bullet originally claimed that meant seeds were
  excluded from Job 1 entirely -- **wrong, found Stage 4c:** `dbt build` selects seeds by default along with
  models and tests, so every single scheduled Job 1 run was reseeding `gold.dim_jedi`/`gold.dim_sector`/
  `gold.phase0_seed` from scratch regardless, reproducing identical rows every run and spending quota for
  nothing -- confirmed directly in captured run logs (`"OK loaded seed file gold.dim_jedi"` etc., every run).
  Job 1's `transform` command now explicitly excludes `resource_type:seed` (`force_pipeline.job.yml`) to actually
  achieve what this bullet always intended: seeds load only via Job 4's own explicit `dbt seed --full-refresh`
  (manual trigger only, above), including the fault-log seed converter's output.
- **Unit tests excluded from Job 1, Stage 4c:** the same default-selection gap applied to unit tests -- `dbt build`
  runs them (against mock fixture data, never the warehouse's real tables) on every scheduled run too, which
  checks nothing about live data and exists only to catch a code change before it ships. Job 1's `transform`
  command now also excludes `test_type:unit`; unit tests are run in `--target dev` before a commit instead (every
  round in this project's own history has done exactly that).
- **`--indirect-selection cautious`, same round:** proving the above with `dbt ls` (same `--exclude`) before
  changing the command found that a plain `--exclude resource_type:seed` drops more than seed self-checks -- dbt's
  default (eager) indirect selection also drops any test with a seed as just ONE of several parents, including
  `relationships_silver_probe_reading_sector_id__sector_id__ref_dim_sector_` (a real referential-integrity check
  on `silver_probe_reading`'s live data against `dim_sector`, not a seed self-check). `--indirect-selection
  cautious` only drops a test when ALL its parents are excluded, which keeps that one test -- but as a single
  global setting it also loosens the unrelated `gold_sector_baseline` exclusion (`assert_baseline_probe_only` has
  `gold_sector_baseline` as only one of two parents too, and would otherwise come back testing a table this job
  never rebuilds); `assert_baseline_probe_only` is re-excluded explicitly, by name, to cancel exactly that one
  side effect. Proven, not assumed: old (`--exclude gold_sector_baseline` alone) selected 10 models, 3 seeds, 72
  data tests, 30 unit tests; the final command selects 10 models, 0 seeds, 44 data tests (28 seed-only tests
  dropped, the 1 cross-reference test kept, `assert_baseline_probe_only` still excluded), 0 unit tests.
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

### Serving (Phase 7)

- `ForceServe` scheduled task: hourly at :17 UTC; it first waits for `logs\agent.lock` to be free (up to 10 minutes), so the pull follows the agent's cycle while the warehouse is still on; skips the 03:xx UTC hour, `IgnoreNew`, runs `serving/` from the working tree with `serving\.venv` (its own venv; `edge/requirements.txt` is also installed on the Pi, so serving dependencies stay out of it), lock `logs\serve.lock`. Registered with `infra/desktop/register-serve-task.ps1`.
- Gate: it asks the Jobs API for Job 1's latest successful run (free) and pulls only when that run is newer than the last publish's.
- Reads, all read-only SELECTs: incremental with upsert on `event_id` and 30 days kept in Postgres (the first run backfills 7): `gold_sector_reading` (watermark `_gold_built_at`), `silver_probe_reading` lag columns and `silver_rejects` (watermark `arrival_ts`). Full pulls each run: `gold_disturbance` (the daily baseline rebuild re-scores it), `gold.deployment`, `gold_sector_baseline`, the dim tables, and `silver_source_health` (one snapshot kept per publish, so the panel has history). Aggregations (ingest lag histogram, rejection rate) are Postgres views.
- Results larger than one chunk are fetched through the Statement API's result-chunk endpoint, through `files_api.http_request`; `agent/dbsql.py` is not changed.
- Postgres: Docker container `force-postgres`, pinned major version, bound to 127.0.0.1:5433 (5432 is published by another project's stopped container and claimed by the dbt `local` target, which Phase 7 does not use), database `force_serving`, schema `serving`, credentials in `.env.serving` (gitignored).
- Dashboard backend: `python -m serving.server` on 127.0.0.1:8081, read-only GET API over the serving views (it never queries the lakehouse); `--database force_serving_fixture` for UI work against fixtures.
- Dashboard frontend: `dashboard/` (React, Vite, Recharts; exact versions in package-lock.json). Build with `npm run build` in dashboard/; the backend serves dashboard/dist. `npm run dev` proxies /api to 127.0.0.1:8081 for development (no CSP there; the CSP applies to the built app).
- Cost: the first live pull (2026-10-20, the 7-day backfill with the dims due) took 31 statements and 58 seconds and wrote 83,024 rows; a steady run is 7 statements (10 when the dims are due), measured by the tests. The live steady number comes after the next scheduled run.

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
   and never reads it from `.env.mqtt`. `web-intake` (the report intake process, Phase 5) goes in the desktop's `.env.intake.mqtt`
   (`INTAKE_MQTT_PASSWORD`, gitignored; template `.env.intake.mqtt.example`).

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.force_balance_pipeline\mosquitto"
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd -c /work/passwd probe-01
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd /work/passwd force-bridge
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd /work/passwd operator
   docker run --rm -it -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 mosquitto_passwd /work/passwd web-intake
   ```

   **If the interactive prompt does not work** (it failed twice in Phase 5 with a hash mismatch, cause unknown, which is why this form is
   recommended), read the password from the credentials file instead. Mount `.env.intake.mqtt` read-only and let the container take
   the password out of it, for a user that is **added to or changed in the existing file** (no `-c`: `-c` creates a new file and
   would erase every other user):

   ```powershell
   docker run --rm -v "<REPO>\Force_Balance_Pipeline\.env.intake.mqtt:/in/env:ro" -v "$env:USERPROFILE\.force_balance_pipeline\mosquitto:/work" eclipse-mosquitto:2 sh -c 'mosquitto_passwd -b /work/passwd web-intake "$(sed -n "s/^INTAKE_MQTT_PASSWORD=//p" /in/env)"'
   ```

   The password is read inside the container, so it is on no host command line and in no extra file. The file must hold the value
   unquoted and with LF line endings (the intake's own reader also accepts quotes; this `sed` does not strip them). It was run live on
   `force-mosquitto` on 2026-10-11, at about 22:40 to 22:43 EDT: it printed `Updating password for user web-intake`, and
   `intake/acl_check.py` then passed. Re-run the volume copy above afterwards, as for any password change.


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

   **Adding `web-intake` to a running broker (Phase 5).** The ACL line (`user web-intake`, `topic write force/telemetry/web-intake`) is
   committed in `infra/mosquitto/acl`, but nothing reads it until the volume copy above is re-run and the broker restarts (the bind
   mount is not what Mosquitto reads). Steps: set the password (the `web-intake` command in step 1, no `-c`, so the other users are
   kept); re-run the volume copy command; `docker restart force-mosquitto`, a graceful restart, so the broker saves its state
   (`persistence true`); outside the minutes :58 to :05 of each quarter hour, because a scan arrives at :00 of each 15 minutes and the
   probe, the bridge and the intake process all reconnect. Then check: `docker logs` shows no ACL or password warning; the bridge
   logs `connected (session present: True)`; the next scan lands 60 of 60. MQTT 5 is what makes a wrong ACL visible: Mosquitto
   answers a denied QoS 1 publish with a PUBACK carrying "Not authorized", which the intake publisher reports as a refused publish.

   **Reloading without a restart (done live 2026-10-11; checked again 2026-10-12 on a throwaway broker).** After the volume copy above,
   `docker kill -s HUP force-mosquitto` makes Mosquitto re-read the password file and the ACL (its log prints `Reloading config`)
   **without dropping connected clients**. On the live broker, on 2026-10-11 at about 22:40 to 22:43 EDT, it logged `Reloading config`
   and no client disconnected. On a disposable container built like this one (the repo's `mosquitto.lan.conf`, authenticated), a
   client that stayed connected throughout saw no disconnect, a new user was refused before the HUP and accepted after it, and a new
   ACL line took effect (a write to a topic the ACL forbids was answered "Not authorized" under MQTT 5). So a password or ACL change
   does not need the graceful restart above, nor the :58 to :05 window. The checks covered the password file and the ACL only: a
   changed `mosquitto.lan.conf` still needs the restart.

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

### Broker persistence and the bridge session

The 2026-10-09/10 outage (`docs/ENGINEERING-LOG.md`, "Stage 4d — Phase 4 close") delivered about 22 hours of data intact on a
manual bridge restart. That worked because of a handful of broker and bridge settings that were correct but asserted nowhere.
This section lists them with their effective values, the headroom they give, the ways data can still be lost silently, and how
to check the live broker. `ingest/tests/test_broker_config.py` fails if any value here changes in the repo.

**Where the broker reads its configuration.** `infra/mosquitto/mosquitto.lan.conf` is *bind-mounted* from the repo working tree
(the `docker run` above: `-v "<REPO>\Force_Balance_Pipeline\infra\mosquitto:/mosquitto/config:ro"`, started with
`mosquitto -c /mosquitto/config/mosquitto.lan.conf`), not copied into a volume as the password and ACL files are. An edit to the
file is therefore visible to the container at once, takes effect at the broker's next start, and goes live on whatever restarts
the container first — including an unplanned reboot. Edit it deliberately, and restart the broker on purpose.

**Effective values.** Defaults are from `mosquitto.conf(5)` (mosquitto.org/man/mosquitto-conf-5.html); "not stated" means that
page does not say.

| Setting | Effective value | Source | Mosquitto default |
|---|---|---|---|
| `persistence` | `true` | `mosquitto.lan.conf:29` | `false` — "If enabled, connection, subscription and message data will be written to disk in mosquitto.db" |
| `persistence_location` | `/mosquitto/data/` — the `force-mosquitto-data` volume | `mosquitto.lan.conf:30`; doc 05 `docker run` (`-v force-mosquitto-data:/mosquitto/data`) | the current directory |
| `persistence_file` | not set, so `mosquitto.db` (`/mosquitto/data/mosquitto.db`) | — | `mosquitto.db` |
| `autosave_interval` | `10` seconds (was `30` until the broker-conf commit) | `mosquitto.lan.conf:31` | `1800` seconds; `0` saves only on exit or on SIGUSR1 |
| `autosave_on_changes` | not set, so `autosave_interval` is a number of seconds | — | not stated; the page says that when it is false the interval is seconds |
| `max_queued_messages` | `50000`, per client | `mosquitto.lan.conf:32` | `1000`; the page does not say what happens to messages over the limit |
| `max_queued_bytes` | not set, so no byte limit | — | `0` (no maximum); "Once this limit has been reached, subsequent messages will be silently dropped" (not stated whether logged) |
| `persistent_client_expiration` | `10d` (was `7d`) | `mosquitto.lan.conf:33` | never expire; units `s h d w m y` (`m` is month) |
| `queue_qos0_messages` | not set, so `false` (irrelevant: all traffic is QoS 1) | — | `false` |
| `max_inflight_messages` | `180` (was unset, so `20`; the page says it applies globally). **It must be live before the `manual_ack` bridge is deployed** ("Acknowledging after landing", below) | `mosquitto.lan.conf:34` | `20`; "Set to 0 for no maximum" |
| Bridge client id | `force-bridge` | `ingest/bridge/bridge.py:69` (`DEFAULT_CLIENT_ID`) | — |
| Bridge session | `clean_session=False` | `bridge.py:449` (`build_client`) | — |
| Bridge subscription | `force/telemetry/#` at QoS 1, re-subscribed on every connect | `bridge.py:471` | — |

The values above are the ones in the repo, and the running broker has used them since it was restarted on 2026-10-11 (02:46:35Z
and 02:48:52Z): the container saw `autosave_interval 10`, `persistent_client_expiration 10d` and `max_inflight_messages 180`
through the bind mount, and the autosave rhythm in its log changed from about every 31 s to about every 11 s. (The broker reads
`mosquitto.lan.conf` only when it starts, so a conf edit is not live until the next restart.)

**Headroom (derived).** The probe publishes one event per planet per scan: 60 planets (the `dim_sector` seed, 60 sectors) every
900 s (`edge/probe/runtime.py:43`, `DEFAULT_INTERVAL`), so 60 × 3600 / 900 = **240 events an hour**. The test computes this from
those constants rather than hard-coding it.

- The queue fills at 50,000 / 240 = 208.3 hours = **8.68 days** (the comment in `mosquitto.lan.conf` and doc 04's flag table
  both say ~8.7 days, derived).
- The session expires after `10d` = 240 hours, longer than the 208.3-hour fill time, so the queue limit is reached before the
  session expires and a bridge that is away for days hits `max_queued_messages` first. It used to be `7d` = 168 hours, which
  made the expiry the binding limit: after seven days of the bridge being away the broker would have discarded the session and
  everything queued for it (up to 168 × 240 = 40,320 events, derived), and the Pi had already deleted each of those rows when the
  broker acknowledged it (`edge/probe/buffer.py:3`: a row is "deleted only when its QoS 1 PUBACK arrives"), so nothing would
  have been left to resend. The test `test_session_expiry_outlasts_the_time_the_queue_takes_to_fill` asserts the margin.
- Mosquitto counts the expiry from the moment the client is disconnected ("if they do not reconnect within a certain time frame");
  this was read from the man page, not tested.

**Ways data can still be lost silently.** Each is bounded, and none is visible in Job 1 or in the broker log unless stated.

| Path | What is lost | Exposure (derived) |
|---|---|---|
| Broker killed between autosaves (host crash, Docker Desktop killed, power) | Messages queued since the last save: the broker has already acknowledged them, so the Pi deleted them. A graceful stop saves on exit (per the `autosave_interval` entry) | At most `autosave_interval` seconds of arrivals: with `10`, up to one scan's 60 events, and only if the crash lands within 10 s after a scan burst while the bridge is down (10 / 900 = 1.1 % of the time; it was 30 / 900 = 3.3 % at `30`). Messages delivered to a connected bridge are not in this window; they are in the next row |
| Queue past `max_queued_messages` | Messages over the limit. What the broker does with them is not documented on the man page (only `max_queued_bytes` is said to drop "silently"), so assume a silent drop, unlogged | After 8.68 days with no bridge; this now comes before the session expires |
| Session expiry (`persistent_client_expiration`) | The whole session and queue | After 10 days with no bridge, when the queue has already been full for 1.3 days (derived: 240 h − 208.3 h); at most the 50,000 events it holds. (At the old `7d`: up to 40,320 events, derived: 168 h × 240 an hour) |
| Bridge killed after receiving a message but before its file is uploaded | Events held in the bridge's memory. paho acknowledges a message as soon as `on_message` returns (paho 2.1.0, `client.py:4147-4152`, unless `manual_ack` is set), so the broker has already discarded it and the Pi deleted its row | Events are held up to `DEFAULT_MAX_SECONDS` = 90 s (`bridge.py:66`) plus upload retries of up to 1+2+4+8+16 = 31 s; a complete scan flushes at once, so typically a fraction of one scan, at most about 60 events. **Closed: the `manual_ack` bridge has run since 2026-10-11 02:51:52Z** ("Acknowledging after landing", below) |

What the 2026-10-09/10 outage showed: the persistence file was necessary (derived). The desktop crashed at 15:20:44Z and the
broker container started fresh at 2026-10-09T15:26:20Z (`docker inspect` StartedAt, read on the evening of 2026-10-10;
`RestartCount 0` only means Docker's own restart policy did not restart it — the Docker VM came back after the reboot and started
it). A new broker process starts with empty memory, so the `force-bridge` session — its subscription to `force/telemetry/#` — can
only have come from `mosquitto.db` on the `force-mosquitto-data` volume, restored at that start. Without it nothing would have
been subscribed to the topic after 15:26:20Z; the Pi, which reconnected at 15:26:23Z, would have had its QoS 1 publishes
acknowledged and dropped (no subscriber), and it deletes a row on acknowledgement: about 22 hours of data lost. Instead the queue
grew in memory (saved to disk every 30 s) against a session restored from disk, and the manual bridge restart collected it. That
account is an inference from the timeline (no broker log from that start was kept), but the mechanism has since been observed
directly: each of the two broker restarts of 2026-10-11 logged `Restored 1 clients` and `Restored 1 subscriptions`, and the
bridge then connected with `session present: True` ("Checking the live broker", below).

**Acknowledging after landing (`manual_ack`).** The bridge acknowledges a message (MQTT PUBACK) only after its batch has landed or
its lines are in the dead-letter file, so the broker's persistent session holds every event until it is safe and a bridge killed
at any moment loses nothing. It builds the client with `manual_ack=True` (`bridge.py`, `build_client`; paho 2.1.0 then does not
acknowledge when `on_message` returns, `client.py:4147-4152`) and sends `ack(mid, qos)` itself. The rules, each with a test in
`ingest/tests/test_bridge_ack.py`:

- **In arrival order.** "It MUST send PUBACK packets in the order in which the corresponding PUBLISH packets were received"
  (MQTT 3.1.1, MQTT-4.6.0-2). Batches complete out of order, so an acknowledgement is released only when every earlier message is
  safe too.
- **From one thread.** paho 2.1.0 has no `_out_packet_mutex`: `ack()` reaches `_packet_queue`, which appends to an unguarded deque
  (`client.py:3776`) that paho's loop thread also pops (`:3163-3177`). So the uploader never calls `ack()`; it marks messages safe,
  and `run()`'s main loop, the one other thread that touches the client, sends the released PUBACKs.
- **Per connection.** On every connect the bridge forgets the acknowledgements it owed on the previous connection: a PUBACK for
  a message id from an old connection could name a different message on the new one. If the broker kept the session
  (`session present: True`) it redelivers everything unacknowledged, in order, with DUP set (MQTT-4.4.0-1, MQTT-4.6.0-1), so what
  is still buffered is dropped and arrives again. A batch already uploading when the connection drops lands anyway, its
  acknowledgements are suppressed, and the broker redelivers its messages, so they land a second time. That duplicate is
  accepted and counted (`redelivered=` in the bridge's summary line).
- **Killed after landing, before acknowledging.** The same: the broker redelivers and the events land again, in a new file. The
  bridge does not deduplicate (doc 04); bronze keeps both copies (doc 03) and silver deduplicates on `event_id`.
- **Dead letters.** An unreadable message, or a batch that exhausted its upload retries, is acknowledged after its line is
  written to `logs/bridge_dead_letter.ndjson`. If that write itself fails three times (1 s, then 2 s apart), the message is not
  acknowledged and the bridge stops with exit code 4; the scheduled task restarts it and the broker redelivers.
- **Shutdown.** Buffered events are uploaded and acknowledged while the connection is still up, and DISCONNECT is queued behind
  the last PUBACKs; paho's loop thread writes its queue empty before it exits (`client.py:2302-2306`). Killing the process instead
  (`Stop-ScheduledTask`, which the windowless task needs, since there is no console to send Ctrl+C to) is safe: whatever was
  unacknowledged is redelivered.

*The in-flight window.* Holding acknowledgements means the broker may have to send a whole scan before the first one is
released. Mosquitto's `max_inflight_messages` is "The maximum number of outgoing QoS 1 or 2 messages that can be in the process
of being transmitted simultaneously" and "Defaults to 20". A scan is 60 events, so at 20 the broker sends 20, waits, and the
bridge has no complete scan to flush: nothing lands until the bridge's 90 s timer fires, then 20 events as a 20-line file, and so
on, about 270 s and three files per scan (derived from the flush rules); a 22-hour backlog would drain at roughly 800 events an
hour against 240 arriving (derived). `ingest/e2e_local.py --scenario stall20` shows the first of these against a default broker.
The fix is two settings that must be changed together: the broker window `max_inflight_messages 180` (three scans: one
uploading while the next arrives, one of slack; bounded, about 72 KB of events) and the bridge's `--max-pending 120`, which
flushes everything once 120 events are buffered without a complete scan, so that partial scans or events with no `scan_id`
cannot wait for the timer. `--max-pending` plus one scan must not exceed the broker window; `ingest/tests/test_broker_config.py`
asserts that for both broker confs. MQTT v5's Receive Maximum was considered and rejected: as I understand it, it can only lower
what the broker sends, and v5 changes session semantics for no gain here. An unlimited window (`0`) would let a 50,000-message
queue flood the bridge; the bounded window also gives back-pressure when uploads are slow.

*Rollout (in this order; the broker window first).* A `manual_ack` bridge against the default window stalls, so:
1. The broker-conf commit (`max_inflight_messages 180`, `autosave_interval 10`, `persistent_client_expiration 10d`) is in the
   repo; restart the broker gracefully (`docker restart force-mosquitto`) so it reads it, and check the persistence log and
   `session present: True` as described under "Checking the live broker".
2. Deploy the bridge. **`Stop-ScheduledTask ForceBridge` stops the OLD bridge, which still acknowledges on receipt, so do it just
   after a scan has landed (about :01 past :00/:15/:30/:45)**: stopped mid-scan it loses what it holds, up to one scan. The
   repetition trigger starts the new bridge within 5 minutes, or wait until no bridge process remains and then
   `Start-ScheduledTask ForceBridge` (see "Restarting the bridge by hand", under "Bridge auto-start": a Start straight after the Stop
   races the old process's exit).
3. Watch `logs\bridge.log`: `received=` and `files=` rise by 60 and 1 per scan, `buffer_depth` returns to 0, every file has 60
   lines, `redelivered=` stays 0 outside a deliberate kill, and bronze shows 60 of 60 for the first hour's scans.
4. Roll back by reverting the bridge: unacknowledged messages are redelivered to the old bridge, so nothing is lost.

*Done 2026-10-11* (`docs/ENGINEERING-LOG.md`, "Phase 5", Round 3b): the old bridge was stopped at 02:51:14Z, between scans, and the
new one connected at 02:51:52Z. The first scan after it landed as one 60-line file (03:00:05Z). Overnight, a scheduled DISCONNECTED
window ended in a BURST (backlog 660) and the bridge landed 11 files of exactly 60 lines in 11 seconds (06:27:43–06:27:54Z); at
the old window of 20 they would have been 20-line files. 28 of 28 real scans over 02:15–09:00Z were 60 of 60 with 0 duplicate
rows.

**How the monitoring bounds these** (S1, bridge liveness; see "Monitoring", below — deployed 2026-10-11). An alert after about
30 minutes without the bridge makes the two multi-day paths (queue overflow, session expiry) unreachable in practice, because
someone is told long before day 7. It does nothing for the two instantaneous paths (a broker kill between autosaves, a bridge kill
before upload): each loses at most about one scan, and S1 does not see it. Those are bounded by `autosave_interval` and, once
deployed, `manual_ack`.

**What changed in the broker-conf commit.** `autosave_interval 30` → `10` (the exposure window above shrinks to 10 / 900 =
1.1 %); `persistent_client_expiration 7d` → `10d` (240 hours, longer than the 208.3-hour fill time, so the queue limit, not the
expiry, is reached first); `max_inflight_messages` (unset, so 20) → `180`, which the `manual_ack` bridge needs (above); and the
conf comment corrected to "about 8.7 days". `ingest/tests/test_broker_config.py` asserts all of it. The conf is bind-mounted
into the live container, so a broker restart is what makes it live. The local-dev conf, `infra/mosquitto/mosquitto.conf`, also
says 180: the live container never reads it (doc 05's `docker run` starts `mosquitto -c /mosquitto/config/mosquitto.lan.conf`),
but the throwaway broker of `ingest/e2e_local.py` does.

**Checking the live broker** (read-only; run by hand — the repo's tests cannot see the container):
- `docker inspect force-mosquitto --format "{{json .Mounts}}"` — the repo's `infra\mosquitto` directory mounted at
  `/mosquitto/config` read-only, plus the `force-mosquitto-secrets` and `force-mosquitto-data` volumes.
- `docker exec force-mosquitto ls -l /mosquitto/data` — `mosquitto.db` present; its modification time advances while messages
  flow.
- After a deliberate (graceful) restart, `docker logs force-mosquitto --since <a couple of minutes before>`. On the two restarts of
  2026-10-11 (02:46:35Z and 02:48:52Z) the live log showed, on the way down, `mosquitto version 2.1.2 terminating` followed by
  `Saving in-memory database to /mosquitto/data//mosquitto.db.` (log timestamps 1791686795 and 1791686932), and on the way up:
  ```
  Restored 0 base messages
  Restored 0 retained messages
  Restored 1 clients
  Restored 1 subscriptions
  Restored 0 client messages
  ```
  `Restored 1 clients` and `Restored 1 subscriptions` are the `force-bridge` session coming back from `mosquitto.db`; `0 client
  messages` because the queue was empty when the broker stopped. (These are the lines as pasted from the live log; they replace an
  earlier guess at the wording.)
- `Client force-bridge [(null):0] disconnected: session taken over` is logged at each start, against the restored offline session,
  just before the bridge connects. **Verified 2026-10-11** (both restarts): it is the resumption of the persistent session, not a
  second client.
- The bridge's own log after a restart: `disconnected: …`, then `connected (session present: True …)` a few seconds after the
  broker is back. The Pi logged `link_lost` and `link_restored` around each restart with a backlog of 0, and the SQL check over
  02:15–03:00Z found 4 scans, all 60 of 60, with 0 duplicate rows.
- **What this does and does not prove.** It proves the *session* (the client and its subscription) persists across a restart.
  Queued-message persistence across a restart was **not** tested: both restarts logged `Restored 0 client messages` because the
  queue was empty. (A different thing *is* now proven live: on 2026-10-11 the broker held three scans for the offline `force-bridge`
  session during a 41-minute bridge outage, and delivered them when the bridge reconnected at 21:04:48Z, without the Pi buffering
  anything. That is queuing in the broker's memory for an offline session, not surviving a broker restart.) The test that would
  show it is: disable the task and stop the bridge (safe now that it acknowledges after landing; disable first, or the 5-minute
  tick restarts it), wait for the process to exit, let two scans publish, restart the broker, enable and start the task, and
  check that both scans land.

**A Windows shutdown or reboot stops the broker ungracefully.** Observed in the reboot test of 2026-10-11: at the shutdown
(17:50:44Z) the broker logged both clients as `connection closed by client` and **no `terminating` line**, because Windows stops
Docker Desktop hard and the container never receives its stop signal. The broker therefore does not get its exit save, and after
the next start it restored its state (`Restored 1 clients`, `Restored 1 subscriptions`, 18:04:37Z) from the **last autosave**. A
graceful `docker restart` saves on exit, so it is not representative of a reboot. On a reboot the exposure is the first row of the
loss table: up to `autosave_interval` (now 10 s) of arrivals, not zero.

### Bridge auto-start

The bridge runs as a Windows Scheduled Task, `ForceBridge`, registered by `infra/desktop/register-bridge-task.ps1`. The design
answers the 2026-10-09 outage (`docs/ENGINEERING-LOG.md`, "Phase 5 Round 2"): after a reboot the old task opened a console window
at logon, someone closed it (exit `0xC000013A`), and a second, independent failure sat behind that one — the bridge exited with
code 1 if the broker was not reachable at start, and the broker container came up about 4m40s after the task did.

| Property | Setting | Why |
|---|---|---|
| Interpreter | `edge\.venv\Scripts\pythonw.exe` | No console window to close by mistake. `pythonw.exe` has no stdout or stderr, so the bridge writes its own log file (below) |
| Triggers | the logon of the current user (`-AtLogOn -User DOMAIN\name`), and a repetition every 5 minutes with no end | The logon trigger starts it; the repetition restarts it if it ever exits. The task's `RestartCount` (999, a minute apart) only covers a failure to *start* the process, not one that starts and later exits |
| Overlap | `MultipleInstances = IgnoreNew` | A repetition tick while the bridge is running does nothing |
| Missed ticks | `StartWhenAvailable = True` | A tick missed while the machine was off or asleep runs when it is back |
| Time limit | none (`ExecutionTimeLimit` 0) | The bridge is meant to run for weeks |
| Principal | the current user, `Interactive`, `Limited` | Standard rights, no stored password |

**Starting before the broker is up.** The bridge no longer exits when the broker is unreachable. It connects with
`connect_async` and `loop_start`, and paho's loop thread makes the first attempt and every retry, backing off 1, 2, 4 … up to 60 s
(`reconnect_delay_set(1, 60)`; paho 2.1.0, `client.py`: `_thread_main` calls `loop_forever(retry_first_connection=True)`, checked
against the installed source and by a test against a port nothing listens on). A failed attempt is logged at most once a minute.
Ctrl+C is handled from the moment the process starts, so it stops the bridge cleanly instead of killing it.

**Logs.** `logs/bridge.log` (gitignored; `--log-file`): a rotating file, 5 files of up to 5 MB, UTC timestamps
(`2026-10-10T15:26:16.123Z INFO …`). Everything the bridge prints, everything written to stderr, and any uncaught exception
(main thread or another thread) goes there; a console stream is added only when there is a console. Dead-lettered events stay in
`logs/bridge_dead_letter.ndjson`. Paths are absolute, derived from the script's location, never the working directory.

Reading the log while the bridge runs is safe: `Get-Content -Wait logs\bridge.log` (or an editor, or a scanner) holds the file
open without delete sharing, which makes Windows refuse the rename a rotation needs. The bridge then keeps appending to the
current file, which grows past 5 MB meanwhile, and tries to rotate again a minute later, so no line is lost and rotation resumes
by itself once the reader lets go. A failing log write (a full disk, say) never stops the bridge: it is reported once a minute to
the original stderr, or dropped silently when there is none (`pythonw.exe`), and never logged through the logger itself.

**One bridge at a time.** The bridge connects with the fixed client id `force-bridge` (above: "a persistent session queues the Pi's
messages while it is down"), so two running at once would take the session from each other, repeatedly, with no error from either
side. Three layers: the task's `IgnoreNew`; `register-bridge-task.ps1` refuses to register or start a task while a bridge process
exists (a real `Win32_Process` scan for `bridge.py` in the command line of `python.exe` or `pythonw.exe` — a bridge started from a
venv shows as two PIDs, the launcher and the interpreter); and an exclusive lock on `logs/bridge.lock` (`--lock-file`) taken before
the MQTT connect and held for the life of the process. The operating system drops the lock when the holder dies, so a crash never
leaves it stale. A second bridge logs `another bridge holds …` and exits with code 3, without creating an MQTT client.

**Elevation: not required — proven 2026-10-10.** In a non-elevated PowerShell, registering a task whose logon trigger names the
current user (`New-ScheduledTaskTrigger -AtLogOn -User DOMAIN\name`) with principal `Interactive`/`Limited` succeeded; the same
trigger *without* `-User` (an "any user logs on" trigger — what this script created before) failed with "Access is denied". The
script therefore always passes `-User`, and registration is done in an ordinary window. (The earlier text here said no elevation
was needed, "Checked, Stage 3d"; that check was not backed by a test, and the task it had registered carried the any-user trigger.)
Removing a task that was registered *elevated* with the any-user trigger is expected to need an elevated shell (not tested).

**A reboot with no logon starts nothing — a decision, not an oversight.** The task runs in the interactive session, so it starts at
logon, and so does the broker (derived from the 2026-10-09 timeline: bridge task 15:21:40Z, broker container 15:26:20Z). A reboot nobody logs in after (a
Windows Update restart overnight, say) starts neither the broker nor the bridge, and the Pi's messages wait in its own buffer. No
auto-logon is configured. The Phase 5 bridge-liveness signal (S1, see "Monitoring", below; deployed 2026-10-11) is what turns that case into
an alert.

**Docker Desktop must start when you sign in.** The broker container has `--restart unless-stopped`, but it only comes back once
Docker Desktop itself is running, so Docker Desktop's setting *Settings > General > "Start Docker Desktop when you sign in to your
computer"* must be on. It is, and it works unattended (the reboot test of 2026-10-11), but the start is slow and variable: the
broker was up about 4m40s after logon on 2026-10-09 (container 15:26:20Z, bridge task 15:21:40Z) and about 12.5 minutes after logon
on 2026-10-11 (logon 17:51:48Z, Docker Desktop 18:04:27Z, broker 18:04:37Z). That is slow, not a fault. The bridge's retry
absorbs it: it logged `cannot reach the MQTT broker at …:1883 … retrying, at most 60 s apart` once a minute from logon without
exiting, and connected 3 s after the broker (18:04:40Z, `session present: True`); the Pi reconnected at 18:05:00Z, and the 18:00Z
scan it had buffered landed at 18:05:02Z. A Windows shutdown also stops the broker ungracefully (above).

**Check the autostart after every Windows update or reboot (it was found off on 2026-10-14).** The desktop rebooted at 02:36:32 EDT (06:36:32Z) with
Docker Desktop's "start when you sign in" setting off, although it had autostarted in the 2026-10-11 test; when or why it changed is unknown. Nothing
started the broker. The bridge task started at sign-in, about 09:13 EDT, and retried as designed, once a minute, until Docker was started by hand at
about 13:25 EDT (17:25Z); the setting is now on. Scans stopped landing for about eleven hours, and none was lost: the Pi buffered them and replayed them
in BURST from 17:27Z, whole 60-line scans about 0.5 s apart. After any update or restart, check both that Docker Desktop setting and Windows'
own list, *Settings > Apps > Startup*, where "Docker Desktop" should be On; and confirm that the next scan lands. Alerts for that outage were
raised by S1 and S3 but not delivered, because the email integration had failed (Engineering Log, Phase 5 findings): confirm delivery too.

```powershell
# from Force_Balance_Pipeline\infra\desktop, in an ordinary (NON-elevated) PowerShell
.\register-bridge-task.ps1 -MqttHost <DESKTOP_IP_or_broker_host>              # registers only
.\register-bridge-task.ps1 -MqttHost <DESKTOP_IP_or_broker_host> -StartNow    # registers and starts it now
```

No secrets or real addresses live in the script or in the registered task's own definition: `-MqttHost` (or
`$env:BRIDGE_MQTT_HOST`) is required with no default, and the bridge's own MQTT credentials still come from `.env.mqtt` at the repo
root (above), read by the bridge process itself at startup — the task's command line never carries them. `-VenvPath` defaults to
`edge\.venv`; `-User` defaults to the current user. Un-register with `Unregister-ScheduledTask -TaskName ForceBridge` (prompts for
confirmation; stop the running bridge process first, the same way you would before closing a manually-started one).

**Restarting the bridge by hand: stop, wait, then start.** `Stop-ScheduledTask` returns before the process has exited. On 2026-10-11 a
`Stop-ScheduledTask; Start-ScheduledTask` pair raced: the new instance reached the lock at 19:38:00.476Z and exited with code 3
(`another bridge holds …`), 177 ms before the old instance wrote its last summary line at 19:38:00.653Z; the repetition tick at
19:39:44Z then started the bridge cleanly, 1 min 44 s late. **The lock did its job: it prevented two bridges from running at once**,
and the cost was only that delay. To avoid it, wait until no bridge process remains before starting:

```powershell
Stop-ScheduledTask ForceBridge
# Stop-ScheduledTask returns before the process has exited. Wait (up to 60 s) until no bridge.py process remains:
$deadline = (Get-Date).AddSeconds(60)
while ((Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object CommandLine -like '*bridge.py*') -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
# This must print nothing; if it prints a process, do not start another bridge, find out why it is still there:
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object CommandLine -like '*bridge.py*'
Start-ScheduledTask ForceBridge
```

(Leaving the start to the 5-minute repetition tick is also fine, and needs no waiting.)

Every cmdlet that can fail carries an explicit `-ErrorAction Stop`, so the "Registered…" message can only print after the
registration actually succeeded. The running-bridge guard fails closed: a failed process-list query stops the script rather than
being read as "nothing is running".

**Tested:** `infra/desktop/register-bridge-task.Tests.ps1` (Pester 3.4.0, ships with Windows PowerShell 5.1) fakes the process list
for the guard and asserts every property of the task definition above — the user-named logon trigger, the 5-minute repetition with
an empty (indefinite) duration, the principal, each setting, `pythonw.exe` — by *building* the definition without registering
anything (`New-BridgeTaskDefinition`). `ingest/tests/test_bridge_resilience.py` covers the bridge side: first-connect retry (including
a real paho client against a closed port), the backoff, rate-limited failure logging, SIGINT before the connect, the log file with
no console, uncaught-exception capture, and the lock. Run Pester with `Invoke-Pester infra\desktop\register-bridge-task.Tests.ps1`.
What these cannot show is that Task Scheduler *accepts* the empty-duration repetition and starts the task unattended after a reboot.
That was checked by hand, and both held (`docs/ENGINEERING-LOG.md`, "Phase 5 Round 2", "Live install and results"): on 2026-10-10 a
throwaway task with the same triggers registered unelevated with a `PT5M` interval and no duration, and the real task restarted a
killed bridge on its first repetition tick; on 2026-10-11 a reboot with nobody touching anything started the bridge at logon with no
console window, it retried the broker once a minute until Docker was up, and the buffered scan landed 22 s after it connected. A
repetition tick that finds the bridge running is ignored (Task Scheduler event 322, `MultipleInstances IgnoreNew`).

### Monitoring

Nothing noticed the 22-hour bridge outage of 2026-10-09/10: Job 1 stayed green throughout, and the same class of silent failure
(a healthy-looking pipeline not receiving new data) is how Databricks quota exhaustion shows up, because no run is created at all
(`docs/ENGINEERING-LOG.md`, Stage 4c). Freshness is therefore monitored **outside** dbt and outside the desktop, by three signals
that each send a ping to an external dead-man's-switch, [Healthchecks.io](https://healthchecks.io) (hosted, free tier, email
notifications only). A check that stops receiving pings goes late and then down, and that silence is the alarm: nothing has to be
alive to report a failure, and the alerting shares no failure domain with the desktop or with Databricks. This replaces the
45-minute `bronze.events` freshness test that used to be in doc 03 and `sources.yml`, which would have tripped between hourly Job 1
runs and in every scheduled DISCONNECTED window.

**Status (2026-10-12): all three signals are deployed and proven; S3's negative test passed on 2026-10-12.**
- **S1 and S3** are implemented in `ingest/bridge/monitor.py`, wired into `bridge.py`, and deployed: the new bridge started at
  19:39:44Z, logged `monitoring pings enabled for S1, S3` and connected with `session present: True`. S1 has pinged every 5 minutes
  since 15:39 EDT (19:39Z), each request carrying the project User-Agent. S3 has pinged roughly every 15 minutes since about 19:45Z,
  one per landed scan, including at 21:04Z after the S1-proof restart (from the check's own log).
- **S2** is deployed (the bundle variable `s2_ping_email`, below) and its first ping arrived after Job 1's 21:03Z run succeeded.
- **Proven:** S1 went down on a real outage, at exactly period plus grace, and came back up on its first ping; the notification
  email was proven on `fbp-test` (below).
- **Passed (2026-10-12):** S3's negative test. `fbp-s3-data-landed` stayed up through the 2026-10-12 00:00 to 12:00Z DISCONNECTED window
  (checked at about 10:30 EDT). It can: period plus grace (15 minutes + 3 hours 45 minutes) exceeds the 3-hour maximum window.

| Signal | What it says | Sent by | Ping | Check | Period | Grace | Alert after |
|---|---|---|---|---|---|---|---|
| **S1** | the bridge is alive and connected | `bridge.py` (`monitor.py`) | every 5 min while `client.is_connected()`, data or not | `fbp-s1-bridge-alive` | 5 min | 25 min | 30 min of silence |
| **S2** | Job 1 is running and succeeding | Databricks, on each successful Job 1 run (design below) | once per success | `fbp-s2-job1-success` | 1 h | 1 h | 2 h of silence |
| **S3** | data is landing | `bridge.py` (`monitor.py`) | after an upload that landed at least 1 line, at most one per 5 min | `fbp-s3-data-landed` | 15 min | 3 h 45 min | 4 h of silence |
| **S4** | the Yoda agent cycle ran, or skipped on purpose | `agent/run.py` (Phase 6) | at the end of every cycle, skipped cycles included; `/fail` on a crash | `fbp-s4-agent` | 1 h | 2 h | 3 h of silence |
| **S5** | the `ForceServe` puller ran, or was gated on purpose (Phase 7) | `serving/` | at the end of every run, a gated no-op run included; `/fail` on an error | `fbp-s5-serving` | 1 h | 2 h | 3 h of silence |
| (test) | exercises the alert path only | by hand | — | `fbp-test` | 1 min | 1 min | 2 min |

"Grace" here is the Healthchecks.io grace time and the alert comes after period plus grace, i.e. **total silence** (decision, Q-B1).

**S4 (Phase 6).** The agent runs hourly at :15 (the `ForceAgent` task), so its check has a period of 1 hour and a grace of 2 hours. Every cycle pings it, including a cycle that skips the 03:xx UTC hour or an active Job 1 run, because a skipped cycle means the agent is alive; a crashed cycle pings the `/fail` URL. The URL is `AGENT_PING_S4_URL` in `.env.agent`. The check is created by hand when the task is registered (Phase 6 R6b).

**S5 (Phase 7).** `ForceServe` runs hourly at :17 UTC, so `fbp-s5-serving` has a period of 1 hour and a grace of 2 hours. Every run pings it, including a run gated because Job 1 has no newer successful run; an error pings `/fail`.

**Rule (decided, Phase 7):** every check has both the email integration and a push integration attached, and delivery is proven with `fbp-test`. The email integration failed again on 2026-10-18 and 10-19 (no alert reached Jong during the agent outage).

**What Healthchecks.io does** (from its own documentation, read 2026-10-11).
- *Ping URLs:* `https://hc-ping.com/<uuid>` for success, with `/start`, `/fail`, `/log` and `/<exit-status>` variants, or
  `https://hc-ping.com/<ping-key>/<slug>`; methods HEAD, GET and POST; a good answer is HTTP 200 `OK`
  ([Pinging API](https://healthchecks.io/docs/http_api/)). No special request header is required, and the page does not say how
  `User-Agent` is used.
- *Rate:* "Please do not ping a single check more than 5 times per minute" (same page). S1 and S3 send at most one ping per five
  minutes each.
- *States:* a check is *late* when its success signal "is due but has not arrived yet" and the grace time has not elapsed, and
  *down* once the grace time has passed, which is when the alert is sent ([documentation index](https://healthchecks.io/docs/),
  [cron guide](https://healthchecks.io/docs/monitoring_cron_jobs/): "notifies you as soon as a ping does not arrive on time").
  For a simple schedule a check becomes late when its period passes. Account settings can repeat the email every hour or day while
  any check stays down (same cron guide).
- *Email pings:* "any email received at the displayed address" counts as a success signal unless keyword filters are set
  ([Email pinging](https://healthchecks.io/docs/email/)). The address has the form `<uuid>@hc-ping.com` and is shown on the check's
  Email tab (confirmed 2026-10-11, from the owner's check).
- *Free (Hobbyist) plan:* "Monitor 20 jobs" and "100 log entries per job" ([pricing](https://healthchecks.io/pricing/)); four checks
  are in use.
- **Unconfirmed:** the minimum and maximum period and grace time; the free plan's team-member and notification limits (the pricing page states none); and whether the service reads
  `User-Agent`.

**Why these numbers (derived).**
- *S1, 30 minutes.* The longest normal gap in S1 pings is a reboot. In the reboot test of 2026-10-11 the shutdown was at 17:50:44Z
  and the bridge was connected again at 18:04:40Z, 13 min 56 s later, because Docker Desktop took about 12.5 minutes to start.
  30 minutes covers that with a margin and still catches a bridge, desktop or broker that stays down. S1 stops when the MQTT client is
  disconnected, so it also fires when the broker or Docker is down even though the bridge process is up.
- *S2, 2 hours.* Job 1 runs hourly at :03 (cron `0 3 * * * ?`), takes about 6 minutes normally (median transform 338–358 s observed)
  and up to about 18–20 minutes after a backlog, with a 40-minute task timeout. A period of 1 h plus 1 h grace alerts after two
  consecutive hourly runs fail to succeed, so one slow or late run never alerts. Quota exhaustion, where no run is created for
  hours, alerts within 2 hours.
- *S3, 4 hours.* Scans are every 15 minutes (hourly in STEALTH), but the probe's scheduled DISCONNECTED windows last 1–3 hours
  (`edge/probe/modes.py:55`, `min_hours=1.0, max_hours=3.0`), two a day, one per half of the UTC day, and nothing lands during one;
  its buffer arrives as a BURST when it ends. So the longest normal silence is about 3 hours plus up to one scan interval, under 4.
  Two windows can touch at the half-day boundary and merge; a Monte Carlo over the schedule's own rule puts a merged silence over
  4 hours at about 2 in 10,000 per boundary (roughly one false alert in seven years; the longest merged silence found was about 6.1
  hours). S3 is silent by design during a window while S1 keeps pinging, which is how to tell the two apart.

**What each signal catches, and what it does not.**
- **S1** catches: the bridge process down, the desktop off, asleep or rebooting for over 30 minutes, Docker or the broker down, no
  network. It does *not* see data stop (a bridge that is connected but receiving nothing keeps pinging) or uploads failing.
- **S2** catches: Job 1 not running (quota exhaustion) or not succeeding, the workspace down. It does *not* see stale bronze: Job 1
  succeeds on empty input.
- **S3** catches: nothing landing although the bridge may be up (the Pi or its link down, the probe stopped, the Databricks Files API
  failing, **the OAuth secret lapsed**). It does *not* see partial scans, duplicates, silver or gold problems, or wrong values, and it
  is silent through a scheduled DISCONNECTED window by design.
- *Reading them together:* S1 down, others late → the desktop, Docker, broker or bridge. S1 up, S3 late → data is not arriving
  (check whether a scheduled window is running, then the Pi). S1 up, S3 late, bridge log full of `gave up on …` → uploads failing.
  S2 down alone → Databricks.
- **The OAuth secret** for `force-bridge` expires 2026-12-25 13:59Z. When it lapses every upload fails and is dead-lettered, but MQTT
  stays connected (S1 keeps pinging) and Job 1 keeps succeeding on empty input (S2 keeps pinging); only **S3** fires. Derived: the
  last good upload is at most about 1 hour after expiry (the cached token lives up to `expires_in`, default 3,600 s) and S3 then
  needs 4 hours, so the alert comes roughly 5 hours after expiry, about 18:00–19:15Z on 2026-12-25.

**Where the ping URLs live.** A ping URL is credential-like (whoever holds it can mark a check up), so it is never committed, printed,
logged or put in an error message.
- S1 and S3: `BRIDGE_PING_S1_URL` and `BRIDGE_PING_S3_URL`, in `.env.bridge` (gitignored; `.env.bridge.example` has empty
  placeholders) or the process environment (which wins). Only workspace mode reads them; a `--local-dir` run, an e2e or a test never
  pings. A missing key disables that signal with one warning that names the key and never its value; a value that is not an `https://`
  URL is treated as missing.
- S2: the check's email-ping address is the bundle variable `s2_ping_email` (`databricks.yml`), supplied at deploy time through
  `BUNDLE_VAR_s2_ping_email`, set the same way as `BUNDLE_VAR_warehouse_id`. `fbp-test`'s address is a session environment variable
  for a test. Neither is ever committed.
- **The order mattered:** before the bridge ran this code, `.env.bridge` could not contain the ping keys, because the previous
  `read_env_file` refused any key outside the three Databricks keys and the bridge would have exited at its next start. The new
  bridge started with the keys present (2026-10-11 19:39:44Z).

**How a ping is sent, and what it can never do.** `monitor.py` queues the ping (a queue of 16; a full queue drops the ping), and one
daemon thread sends it through `files_api.http_request`, the repo's one outbound HTTP path, with no headers: the only header is the
project User-Agent, and there is no Databricks session or credential in reach. The timeout is 10 s. Every failure is caught; a
failing ping is logged at most once every 15 minutes, described by status or exception type only, and never touches the MQTT, upload
or acknowledgement paths.

**S2: how Job 1 pings (deployed 2026-10-11).** Job 1 has a job-level `email_notifications.on_success` entry
(`resources/force_pipeline.job.yml`) whose address is the S2 check's email-ping address, the bundle variable `s2_ping_email`
(`databricks.yml`). It is success only: a failed or skipped run sends nothing, and the silence is the alarm. Job 2 is untouched.
Databricks sends the email itself, so the compute needs no outbound access and no secret scope.
- *Evidence.* The Databricks documentation lists "Successful completion" as a notification event for email addresses and says an
  administrator creates system destinations ([Job notifications](https://docs.databricks.com/aws/en/jobs/notifications)). It does
  not say whether the recipient must be a workspace user, and the Free Edition limitations page
  ([Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)) says nothing about
  notifications, webhooks or secrets. So it was tested: **on 2026-10-11 a throwaway serverless job with `on_success` email to the
  `fbp-test` check's address (`<uuid>@hc-ping.com`) produced an email ping on `fbp-test`**; the job and its workspace folder were
  deleted afterwards. Job emails to an external address are therefore delivered on Free Edition. The webhook-destination route was
  not needed and not tried (it would need an administrator-created destination and outbound access to `hc-ping.com`, which the
  Free Edition page, "restricted to a limited set of trusted domains", leaves unconfirmed).
- *No default, on purpose.* The variable has no `default`, so `databricks bundle validate` and `deploy` fail with `no value assigned to
  required variable s2_ping_email` instead of deploying an empty notification (checked 2026-10-11; validate exits 1).
- *Precedence.* The Databricks CLI takes a variable's value from the first of: a `--var` option, a `BUNDLE_VAR_` environment variable,
  `variable-overrides.json`, a target's `variables` mapping, then the `default`
  ([Bundle variables](https://docs.databricks.com/aws/en/dev-tools/bundles/variables)). Checked on this CLI (v1.17.0) on
  2026-10-11: with both set, `--var` won over `BUNDLE_VAR_s2_ping_email`.
- *Every successful run pings,* manual runs included, and a run that exhausts the quota never starts, which is what S2 is for.
- *Deploy* (the owner runs this; `bundle deploy` reconciles the whole definition of both jobs, and Job 2 is unchanged). **This is a
  template: replace the whole quoted string with the real address from the S2 check's Email tab** (its form is
  `<uuid>@hc-ping.com`, with the real uuid). Pasting it as it stands would deploy a notification to an address that does not exist,
  and the checks below would catch it. Make sure `BUNDLE_VAR_warehouse_id` is a real id first (see "Setting
  `BUNDLE_VAR_warehouse_id`", under "Job topology").
  ```powershell
  # Once. setx only affects NEW terminals. Replace the quoted string with the real address:
  setx BUNDLE_VAR_s2_ping_email "<the address from the S2 check's Email tab>"
  # In a new terminal, from Force_Balance_Pipeline\ (True/False only, nothing printed):
  $env:BUNDLE_VAR_s2_ping_email -match '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}@hc-ping\.com$'     # must be True
  databricks bundle validate        # "Validation OK!"; without the variable: "no value assigned to required variable s2_ping_email"
  databricks bundle deploy
  ```
  The first S2 deploy, on 2026-10-11, failed for a different reason (a placeholder in `BUNDLE_VAR_warehouse_id`); it was fixed and
  the second deploy succeeded.
- *Read-only check that the deployed jobs have it, without printing the address:*
  ```powershell
  function Count-Notified($job, $kind) { @($job.settings.email_notifications.$kind | Where-Object { $_ }).Count }
  $j1 = databricks jobs get 692051683030737 -o json | ConvertFrom-Json
  "Job 1: on_success=$(Count-Notified $j1 'on_success') (expect 1), on_failure=$(Count-Notified $j1 'on_failure') (expect 0)"
  "Job 1 address is a real hc-ping.com address: $([bool](@($j1.settings.email_notifications.on_success) -match '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}@hc-ping\.com$'))"
  $j2 = databricks jobs get 38074271722750 -o json | ConvertFrom-Json
  "Job 2: on_success=$(Count-Notified $j2 'on_success') (expect 0)"
  ```
  Result on 2026-10-11: Job 1 `on_success=1`, `on_failure=0`, a hc-ping.com address; Job 2 `on_success=0`. After Job 1's 21:03Z run
  succeeded, `fbp-s2-job1-success` showed its first ping.

**Setting Healthchecks.io up** (the order matters; the 2026-10-11 outage test found the third step missing).
1. *Create the four checks* with the periods and grace times in the table: `fbp-s1-bridge-alive` (5 min / 25 min),
   `fbp-s2-job1-success` (1 h / 1 h), `fbp-s3-data-landed` (15 min / 3 h 45 min) and the test check `fbp-test` (1 min / 1 min). Copy each
   check's ping URL (S1, S3) or Email-tab address (S2, `fbp-test`) from the check itself.
2. *Confirm the account's Email integration:* open the confirmation email Healthchecks sent and follow its link. An unconfirmed
   integration sends nothing.
3. *Enable that integration on every check* (each check has its own integrations list). Creating the integration does not attach it.
4. *Prove delivery with the short-grace test check before trusting any alert:* ping `fbp-test` once, then stop. About 2 minutes later it
   goes down, and the "down" email must arrive. All four checks share the integration, so one delivered email proves the path.

**What went wrong the first time.** During the S1 outage proof (below) S1 went down at exactly the right moment and **no email was
sent**. The account's Email integration had not been confirmed or attached; the check's own event log showed the status changes but
no notification. It was fixed and enabled on all four checks, and delivery was then proven on `fbp-test` (the "down" email arrived at
17:21 EDT, 21:21Z). So S1's *own* outage produced no email; the alert path was proven afterwards, on the shared integration.

**How the alerts were proven** (2026-10-11 unless noted; each step needed the owner's OK).
1. *The alert path:* `fbp-test` was taken through a down cycle and the "down" email arrived at 21:21Z (above).
2. *S1, for real.* ForceBridge was disabled and stopped at about 20:23:30Z (the bridge's last summary line was 20:23:26Z). S1's last
   ping was at 20:19Z, and Healthchecks marked it **down at 20:49Z: exactly period plus grace, 30 minutes**. The bridge was restored
   at 21:04:48Z (`session present: True`) and S1 went back **up on its first ping** (21:04Z). The three scans published during the
   outage (20:30, 20:45 and 21:00Z) landed at 21:04:49–21:04:50Z as 60-line, regular-sized files. **The Pi did not buffer them: the
   broker held them for the offline persistent session** (the first live proof of broker-queued delivery; previously only 2026-10-09).
   *Procedure for repeating it:* `Disable-ScheduledTask ForceBridge` **first** (otherwise the 5-minute repetition tick restarts the
   bridge within 5 minutes and there is no outage), then `Stop-ScheduledTask ForceBridge`, then wait until no bridge process remains
   (the command under "Restarting the bridge by hand"), then leave it for 35 minutes; expect S1 late about 5 minutes after its last
   ping and down at about 30, with an email (once the integration is attached). To restore: `Enable-ScheduledTask ForceBridge`, then
   `Start-ScheduledTask ForceBridge`; S1 returns to up on its first ping. A stop is safe at any moment, because messages are
   acknowledged only after landing.
3. *S3, cadence:* in the check's log, pings arrive roughly every 15 minutes since about 19:45Z, one per landed scan, including at
   21:04Z after the S1-proof restart (the at-most-one-per-5-minutes throttle is unit-tested). *S3, silence:* **passed 2026-10-12**: across the
   00:00 to 12:00Z DISCONNECTED window (checked at about 10:30 EDT) `fbp-s3-data-landed` never went down; period plus grace (4 hours)
   exceeds the 3-hour maximum window.
4. *S2:* the throwaway job's email reached `fbp-test` (2026-10-11), and after the real deploy `fbp-s2-job1-success` showed its first ping
   after the 21:03Z run. A quota-exhaustion alert itself has not been observed; it follows from the
   design (no run is created, so no success email, so the check goes down after 2 hours) and from the delivery proof above.
5. *The disabled paths:* with the keys absent the bridge logs one warning per start and runs normally. This is covered by unit tests;
   it was not exercised live.

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
steps that need it (packages, the service user, the unit, the env file, `daemon-reload`, and — if `force-probe` is already
running, a redeploy, below — the restart itself), and reruns the same `mkdir`/`chown` above:

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

**Redeploying (the service already enabled and running).** `deploy.sh` restarts `force-probe` itself in this case, and then
verifies the restart actually happened before it exits 0 — it checks `systemctl show -p ExecMainStartTimestamp,MainPID` against
the time the script itself started, and the checked-out commit against the SHA given, failing loudly (exit 1) if either doesn't
match. **Found live, 2026-10-06, and this is exactly why the verification exists:** `deploy.sh` installed a new unit file
(`--fault-rate 1.0`) and ran `daemon-reload` successfully — `systemctl cat` showed the new `ExecStart`, `NeedDaemonReload=no` —
but the already-running process was never restarted, silently staying on the previous commit's `--fault-rate 0` (PID unchanged,
original start time unchanged) until a manual `sudo systemctl restart force-probe`. Installing a new unit file and reloading
systemd changes what it would run *next time*; it does nothing to a process already forked from the old one. On a brand new,
never-enabled install, `deploy.sh` still does **not** start the service — `probe.env` is still just the template at that point,
and starting it would crash-loop on missing MQTT credentials; that first start stays the manual `sudo systemctl enable --now
force-probe` step above.

**The first deploy after a change to `deploy.sh` itself runs the previous script — run it twice.** The clone is detached
(`git checkout --detach`), so you start the new commit by running `./deploy.sh <new SHA>` from the *old* checkout. bash is
already reading the old file when that checkout replaces it on disk, so the rest of that run executes the old script's logic
(observed 2026-10-10: the first deploy after a `deploy.sh` change ran the previous script; the mechanism described here is
derived from reading `deploy.sh`, not observed). The new unit file *is* still installed, because it is read after the
checkout; only the script's own steps are stale. After any commit that changes `deploy.sh`, run the same command a second
time: the second run executes the new script. A permanent fix (re-exec the new script after the checkout, guarded against
loops) is an open item in `docs/ENGINEERING-LOG.md`.

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

### Runbook: Stage 3b deploy and two persistence proofs — both done, 2026-10-01

Two things this phase had built but never actually proven against the real desktop/Pi: that the broker's own disk persistence
(not just a client reconnect) survives a restart, and that the Pi's persistent-journal fix (doc 05, journald section, fixed
2026-09-30) survives an actual reboot, not just a service restart. **Both proofs ran in the 2026-09-30/10-01 maintenance
window and both passed** — see `docs/ENGINEERING-LOG.md`, "Stage 3b deploy and proofs," for the evidence. The steps below are
kept as the runbook for next time either needs re-proving (a Mosquitto image upgrade, a journald config change), not because
either is still outstanding.

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

**Correction, 2026-10-06:** this instruction's own claim that `deploy.sh` restarts `force-probe` was wrong at the time it was
written — `deploy.sh` never restarted an already-running service until Stage 4d's fix ("Redeploying," above). It's unconfirmed
whether this specific redeploy actually picked up the publisher.py fix at the time, or silently kept running the pre-fix code
the same way the fault-rate deploy later did; `journalctl -u force-probe -f`'s own command below would show the actual PID/start
time either way, but nothing here recorded whether anyone checked it at the time. `deploy.sh` now restarts and verifies this
itself, so a future redeploy can't repeat it unnoticed. Even with the restart, it's **not** enough to prove the
persistent-journal fix (`Storage=persistent`, `SystemMaxUse=100M`, doc 05's journald section) survives what it's actually for —
a full power cycle, not a service restart. While already on the Pi for this deploy:

```bash
sudo reboot
# after it comes back and you've reconnected:
journalctl --list-boots                              # more than one boot listed = the journal survived
journalctl -b -1 -u force-probe | tail -20            # the PREVIOUS boot's own force-probe log is still readable
```

This closes the "Persistent-journal fix... not yet confirmed to survive an actual Pi reboot" open item: a second boot listed
and the previous boot's log readable is the proof. **Proven 2026-10-01 — and by a stronger event than planned:** the Pi was
accidentally unplugged (a real, uncontrolled power loss, not a graceful `sudo reboot`) during this same window, and
`journalctl --list-boots` afterward showed 3 boots total (the one that ended in the unplug, the recovery boot, and the
deliberate test reboot that followed), with the unplug boot's own `force-probe` log still fully readable. A real power cut is
the harder case `Storage=persistent` exists for; it passed that case along with the planned graceful-reboot test.

### Runbook: C3/C4, the control-topic injection checkpoints

Doc 07's own Phase 4 checkpoint bullets: "Trigger `sith_presence` via the control topic. Within two scans a `gold.disturbance`
row appears with the correct `signature` and `sustained_scans >= 2`" (C3), and "Trigger two disturbances in one sector 30
minutes apart. One incident row — cooldown works" (C4). The commands below are `probe_ctl.py`'s own documented syntax
(`edge/probe_ctl.py`'s own docstring; doc 04:91-98) — no new flags invented.

**(a) C3 — one injection.** Pick any sector other than Mustafar or Dathomir (doc 04, their dark baseline clamps at 100 and
can't reach the emergency threshold via a spike — doc 03's own "Resolved, 2026-09-30" note on `gold.disturbance` has the
unclamped-direction nuance, irrelevant here since this is a spike, not a dip). Fire it a minute or two before a `:00/:15/:30/:45`
scan boundary, so that boundary (`T`) is the injection's first (`ramp`) scan:

```bash
python edge/probe_ctl.py --host <DESKTOP_IP> inject <sector_id> sith_presence
```

(It prompts for the `operator` MQTT password — `getpass`, never echoed, never read from `.env.mqtt`; or set
`OPERATOR_MQTT_PASSWORD` first.) Defaults apply: `ramp=2, hold=4, decay=3` scans (doc 04:96). **What to note:** the wall-clock
boundary of the scan right after you send it (`T`, that is `onset_event_time` *if* `T` itself already qualifies) and the
sector you chose.

**Timed against Job 1 (`:03`, 3 minutes behind every fourth scan — Stage 4c; was `:03/:33` at Stage 4b, and
`:03/:18/:33/:48` at Stage 3d's original correction, same reasoning throughout, doc 07's C3 bullet too).** Onset
(the first scan actually at or above 5.75) is not a fixed ramp/hold boundary — a real run (naboo, 2026-10-01,
injected at `T`, under the 15-minute Job 1 cadence that existed then) crossed threshold on `T+15`, still inside
the default 2-scan `ramp`, not only once `hold` began. **Under healthy Job 1 operation at the current hourly
cadence, expect `sustained_scans = 2` and the `gold.disturbance` row to appear on the first Job 1 run at or after
onset's second qualifying scan** — since Job 1 now builds only once per hour (every fourth scan), that can be up
to ~60 minutes after that scan, not the ~15-30 minutes an earlier cadence would give; the real naboo run (onset
`T+15`) would have given `detected_at` ≈ `T+30` only under the ORIGINAL 15-minute cadence — expect somewhat later
now. Watch the actual scores (`gold_sector_reading`, not just the countdown) to find onset for your own run
rather than assuming a fixed count.
**Separately, `detected_at` can run much later than onset if Job 1 misses a cycle** — it is `max(event_time)` of the run as
of whenever `gold_disturbance` last rebuilt (`gold_disturbance.sql`'s own definition), not the onset, so a missed build
reports whatever was the LAST qualifying scan by the time a build finally ran, with `sustained_scans` to match. This is
exactly what happened in the real run above: a quota outage (`ingest/job1_quota_watch.sql`; docs/ENGINEERING-LOG.md,
"Stage 3d") meant no build ran between `T+3` and a manual catch-up at `T+75`, so the row that finally appeared showed
`sustained_scans = 5` and `detected_at` at the run's last scan, not its second.

```sql
-- p4-9, filled in: <inject_sector> = the sector you chose, <inject_ts_utc> = T above (the injection's first scan boundary)
SELECT sector_id, detected_at, signature, sustained_scans
FROM force.gold.gold_disturbance
WHERE sector_id = '<inject_sector>' AND detected_at >= TIMESTAMP '<inject_ts_utc>'
ORDER BY detected_at LIMIT 1;
-- Expected: 1 row, signature = 'sith_presence', sustained_scans >= 2, detected_at appearing on the first Job 1 run at
-- or after onset's second qualifying scan (up to ~60 minutes after it, at the current hourly cadence) under a
-- healthy pipeline -- see phase4_checkpoint.sql's own p4-9 comment for the real 2026-10-01 result (75 minutes, not ~30,
-- due to a Job 1 outage, not the detection logic, and timed under the since-changed 15-minute cadence).
```

**(b) C4 — two SEPARATE runs, both inside the 2-hour cooldown, same sector.** Redesigned, Stage 3d: the original version
(two injections 30 minutes apart with their defaults) left how `forcesim` handles a second injection landing mid-episode
undefined, since the first episode's own `ramp+hold+decay` (2+4+3 = 9 scans, ~2h15m with defaults) was still active when
the second would fire. This version shortens the first episode with `probe_ctl.py`'s own documented flags (`--hold`,
`--decay` — `edge/probe_ctl.py`'s docstring and `argparse` block; doc 04:96, "may override them (`ramp`, `hold`, `decay`)"
— no undocumented flag invented) so it fully completes, with at least one confirmed below-threshold scan, before the
second injection fires — genuinely two separate runs, not one continued episode.

**Lessons from the actual runs (attempt 1, dagobah, void; p4-10, bespin, succeeded):**
- **Set `T` on the hour.** Job 1 is now hourly at `:03`. If `T` isn't on the hour, `T+60` (episode 1's nominal decay scan)
  can land just after the `:03` run that would have covered it, costing a full extra hour's wait before you can even
  check it. `T` on the hour means `T+60` is also on the hour, and the very next `:03` run (3 minutes later) covers it.
- **Confirm `control_applied` in `probe_events.jsonl` within a minute of each inject, before assuming it landed.**
  Attempt 1 (dagobah, injection 1 applied 2026-10-05T21:59:49Z) went void because injection 2 was simply never sent —
  nothing wrong with the probe or the schedule, an operator step was missed. Check the log, don't assume the command
  succeeded just because `probe_ctl.py` printed `sent to ...` (that confirms the broker acknowledged the publish, not
  that the probe applied it).
- **If `T+60`'s own decay scan still qualifies (`imbalance_score >= 5.75`), don't fire injection 2 at `T+75` as planned
  — wait one more scan to re-confirm, then fire at `T+90`.** This happened for real in the bespin run: `T+60` (15:00Z)
  scored 5.777, still just over threshold, so decay hadn't actually finished; `T+75` (15:15Z) came in at 3.945, clearly
  below, and injection 2 fired at `T+90` (15:30Z) — one scan of margin past the scan that actually confirmed it, not
  past the nominal `T+60`.
- **Date-stamp every query — don't reuse an earlier day's.** `<inject_ts_utc>` needs the full date every time; a C4
  attempt run on a different day than planned (as both of these were) makes a bare time-of-day placeholder silently
  wrong.
- **The final check must cover `detected_at >= T` with no upper bound**, not `< T + 2 hours` as originally written —
  below. A delayed or re-attempted run can push `detected_at` past a fixed window and produce a false "0 incidents."
- **`journalctl --since` needs an explicit `UTC`, or it's interpreted in the Pi's own local time.** The Pi's system
  clock runs EDT; every `T` in this runbook is UTC. `journalctl -u force-probe --since "2026-10-06 14:00 UTC"`, not
  `--since "2026-10-06 14:00"` (which would be read as 14:00 EDT, 4 hours off).

```bash
# injection 1, at boundary T
python edge/probe_ctl.py --host <DESKTOP_IP> inject <sector_id> sith_presence --hold 2 --decay 1
```

`--hold 2` is doc 04:114's own documented minimum ("injection holds at least 2 scans, because a disturbance needs 2
consecutive scans above the threshold"); `--decay 1` is a fast, single-scan return toward baseline, to keep episode 1 as
short as possible: `ramp` (2, default) + `hold` (2) + `decay` (1) = 5 scans, `T` through `T+60`. **Watch `gold_sector_reading`
for `T+60`'s own score to confirm it has genuinely dropped below 5.75 before firing the second injection** — doc 04 doesn't
give the exact decay curve shape, so this is a check, not an assumption. Once confirmed:

```bash
# injection 2, at boundary T+75 (one scan of margin past the confirmed-below-threshold scan) -- T+90 instead if T+60
# itself still qualified and T+75 was the scan that actually confirmed below-threshold (see "Lessons," above)
python edge/probe_ctl.py --host <DESKTOP_IP> inject <sector_id> sith_presence --hold 2 --decay 1
```

**What to note:** both injection times, `T+60`'s confirmed below-threshold score (the gap that makes these two runs, not
one), and episode 2's own onset the same way as C3. Episode 2's onset (`T+75` at the earliest) is well inside episode 1's
2-hour cooldown (`T+30`-to-`T+90`-ish detected_at at the current hourly Job 1 cadence — see C3's note above on the
wider range this cadence now gives — +120 minutes = `T+150`-to-`T+210`ish) — `T+75` stays comfortably inside that window
either way; the margin just isn't as wide as the original 15-minute-cadence estimate implied.

```sql
-- p4-10, filled in the same way; <inject_ts_utc> = T (injection 1's own boundary, full date -- don't reuse an earlier
-- day's). No upper bound on detected_at: a delayed or re-attempted run can push it past a fixed +2h window and
-- produce a false "0 incidents" -- see "Lessons," above.
SELECT count(*) AS incidents_in_window
FROM force.gold.gold_disturbance
WHERE sector_id = '<inject_sector>'
  AND detected_at >= TIMESTAMP '<inject_ts_utc>';
-- Expected: 1 (the cooldown, var('cooldown_hours') = 2, suppresses episode 2's onset -- doc 03, "Late-replayed onset
-- inside an existing cooldown"). Check cooldown_conflict on that one row too, same query plus the column --
-- **decided, "definition B", Stage 4a (doc 03, docs/ENGINEERING-LOG.md "Stage 4a"): cooldown_conflict is true only
-- for a REPLAYED onset older (by event_time) than an incident already recorded for the same sector -- an ordinary,
-- in-order second onset suppressed by the cooldown, which is exactly what episode 2's live control-topic injection
-- is here, is the cooldown working as designed, not a conflict. Expected for this runbook: cooldown_conflict =
-- false on the one incident row.**
```

Run both p4-9 and p4-10 after a Job 1 `transform` run has completed following the relevant onset's own qualifying scans —
Job 1's own per-run log (`ingest/job1_quota_watch.sql`) or the workspace UI's run history has the exact completion time if
the hourly `:03` schedule isn't precise enough to tell by wall clock alone, and confirm no run was missed in between
(the same quota risk C3's real run hit).

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
