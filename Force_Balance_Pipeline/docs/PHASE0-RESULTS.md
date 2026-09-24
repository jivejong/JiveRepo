# Phase 0 results

Answers to the three open questions in doc 00, recorded from command output only. A cell stays
empty until the output that supports it has been run and pasted in. Nothing here is inferred.

Commands: `python scripts/check_platform.py <preflight|q1|q2|q3|local|cleanup>`.

| # | Question | Answer | Evidence | Fallback if no |
|---|---|---|---|---|
| 1 | Does the `dbt` job task type work on Free Edition, sourced from Git? | **Yes.** | Run 981893542841724: TERMINATED / SUCCESS in 124.9 s from commit `b49a953043e94cfbbda8743359cfb7549a8481e1`. `Running with dbt=1.10.13`, `Registered adapter: databricks=1.9.8`, `Done. PASS=9 TOTAL=9`. See Q1 detail. | Python task shelling out to dbt (doc 07, Phase 0) |
| 2 | Are Unity Catalog external locations to GCS permitted? | **No.** | The Create credential dialog offers only "AWS IAM Role" and "Cloudflare API Token", with no Google Cloud option. Host cloud: aws. 0 `gs://` external locations. Nothing was created. See Q2 detail. | Bridge keeps writing to the UC volume through the Files API (docs 00 and 05) |
| 3 | Does `streaming_table` build and refresh on this workspace with the pinned dbt-databricks? | **Yes, with a stability caveat.** | Build 2 (run 15527873087897) ended TERMINATED / SUCCESS in 772.1 s, but its first attempt failed after 622.42 s (Spark Connect session deleted) and the automatic retry succeeded in 25.84 s. Table type STREAMING_TABLE, last refresh INCREMENTAL, succeeded. Per-file rows: batch_1.ndjson 5, batch_2.ndjson 3. See Q3 detail. | Auto Loader stays as the `ingest_bronze` notebook task (doc 05, job topology) |

A "yes" on Q3 does not move Auto Loader into the dbt DAG. That is a later decision with its own
doc edit.

## Q1 detail: dbt task from Git

Final run, from the script's evidence block:

- run_id 981893542841724, TERMINATED / SUCCESS, empty state message, duration 124.9 s.
- Commit SHA `b49a953043e94cfbbda8743359cfb7549a8481e1`.
- `Running with dbt=1.10.13`, `Registered adapter: databricks=1.9.8`.
- `Done. PASS=9 WARN=0 ERROR=0 SKIP=0 NO-OP=0 TOTAL=9`.
- dbt-core was **not pinned** at that commit: `requirements.txt` pinned only `dbt-databricks`, so
  `1.10.13` is what pip resolved, not a pin. The pin (doc 05) has not been exercised by a run yet.
- The run page URL is left out on purpose: it contains the workspace host and org ID.

Earlier runs:

| Run | Result | Notes |
|---|---|---|
| 874059667196060, 77754742679338 | INTERNAL_ERROR | Task log: `No such file or directory: '/Workspace/Repos/.internal/.../5fc91b1d.../Force_Balance_Pipeline'`. Cause: local `main` was 6 commits ahead of origin, and commit `5fc91b1d` had no project folder. **Invalid runs**, not evidence against Q1. |
| 846253185978169 | TERMINATED / SUCCESS, 112.9 s | Commit `3a444c98`, dbt 1.10.13, databricks 1.9.8, PASS=9 TOTAL=9. `SELECT count(*) FROM force.phase0.phase0_model_b` returned 5. |
| 981893542841724 | TERMINATED / SUCCESS, 124.9 s | Final run above. |

## Q2 detail: external locations to GCS

- Host cloud: aws (inferred from the hostname by the script).
- Storage credentials listed: 1. External locations listed: 1. `gs://` locations: 0.
- Create credential > Storage Credential > Credential Type options: "AWS IAM Role" and
  "Cloudflare API Token". No Google Cloud option.
- Nothing was created. The answer rests on the missing credential type, not on an error from a
  refused attempt.

## Q3 detail: `streaming_table`

Build 2, run 15527873087897, final state TERMINATED / SUCCESS, 772.1 s, commit `3a444c98`.
Recorded per attempt:

- **Attempt 1:** ERROR after 622.42 s: `SparkConnect session ... entered state Some(DELETED)
  before becoming ready`.
- **Attempt 2 (automatic retry):** `OK created sql streaming_table model
  phase0.phase0_streaming_check [OK in 25.84s]`, PASS=1. Profile `target='databricks_cluster'`.

`DESCRIBE TABLE EXTENDED`:

- Type: STREAMING_TABLE
- Last Refresh Type: INCREMENTAL
- Latest Refresh Status: Succeeded
- `pipelines.pipelineId` = `2a7f39e7-3d35-4712-8049-ef03aaaa2eaf`

Per-file rows: `batch_1.ndjson` 5, `batch_2.ndjson` 3. No `_metadata.file_name` column issue was
reported in the pasted output.

Not in the pasted output: build 1's run ID, the row counts after each build, and the dbt versions
in the Q3 log.

## Local dbt against the SQL warehouse (doc 07 step 2)

| Step | Result | Evidence |
|---|---|---|
| `dbt --version` in a repo-local `.venv` | dbt=1.10.13, databricks=1.9.8 | Python 3.10.6 |
| `dbt ls --resource-type model` (toggle off; expect 2 models) | 2 models | |
| `dbt ls --resource-type model --vars ...` (toggle on; expect 3 models) | 3 models | The toggle works, so the streaming gate passed. |
| `dbt debug --target dev` | | Not in the pasted output. |
| `dbt build --target dev` | PASS=9 WARN=0 ERROR=0 in 26.75 s | 1 seed, 1 view, 1 table, 6 tests |

Steps are printed by `python scripts/check_platform.py local`.

## Notes

- `cleanup` drops only `force.phase0` (or just the streaming probe with `--streaming-only`). It
  does **not** drop the `dev_<name>` schema that the local dbt run creates, nor any Q2 storage
  credential or external location. Remove those by hand.
- The pins are `dbt-databricks==1.9.*` and `dbt-core==1.10.13` (doc 05). Only the adapter's
  wildcard resolves at install time, so the versions that actually ran are the ones in the
  `Running with dbt=` and `Registered adapter` lines above, not the pins.
- Git source and dbt project directory for the job runs: see doc 05, "dbt task configuration".
- The serverless `environment_version` defaults to `"5"` (Python 3.12) and can be overridden with
  `--env-version`. Evidence from `.phase0_state.json` before cleanup: the saved `q1` entry for run
  846253185978169 has `"env_version": "5"`. `q3` reused the saved value, and later runs used the
  default `"5"`.
- **Operational lesson:** the dbt task pulls from GitHub, not the local checkout. Two Q1 runs failed
  because local `main` was ahead of origin. Push before running `q1` or `q3`.
- **Script limitation:** for a run the platform retried (Q3 build 2), the evidence block printed
  attempt 1's log lines under the final SUCCESS state. Read the log per attempt.
