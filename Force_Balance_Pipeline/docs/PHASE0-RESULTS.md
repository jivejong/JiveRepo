# Phase 0 results

Answers to the three open questions in doc 00, recorded from command output only. A cell stays
empty until the output that supports it has been run and pasted in. Nothing here is inferred.

Commands: `python scripts/check_platform.py <preflight|q1|q2|q3|local|cleanup>`.

| # | Question | Answer | Evidence | Fallback if no |
|---|---|---|---|---|
| 1 | Does the `dbt` job task type work on Free Edition, sourced from Git? | | | Python task shelling out to dbt (doc 07, Phase 0) |
| 2 | Are Unity Catalog external locations to GCS permitted? | | | Bridge keeps writing to the UC volume through the Files API (docs 00 and 05) |
| 3 | Does `streaming_table` build and refresh on this workspace with the pinned dbt-databricks? | | | Auto Loader stays as the `ingest_bronze` notebook task (doc 05, job topology) |

Evidence to paste per row:

- **Q1** (`q1`): run ID, state, duration, commit SHA, the `Running with dbt=` and
  `Registered adapter` lines, and the dbt summary line.
- **Q2** (`q2`, then `q2 --verify`): the exact UI error text or success state, and the verify
  output.
- **Q3** (`q3`): run IDs for both builds, row counts after each (5, then 8), and the pipeline ID
  the streaming table created.

A "yes" on Q3 does not move Auto Loader into the dbt DAG. That is a later decision with its own
doc edit.

## Supplementary: doc 07 step 2 (local dbt against the SQL warehouse)

| Step | Result | Evidence |
|---|---|---|
| `dbt --version` in a repo-local `.venv` | | |
| `dbt ls --resource-type model` (toggle off; expect 2 models) | | |
| `dbt ls --resource-type model --vars ...` (toggle on; expect 3 models) | | |
| `dbt debug --target dev` | | |
| `dbt build --target dev --exclude phase0.streaming` | | |

Steps are printed by `python scripts/check_platform.py local`.

## Notes

- `cleanup` drops only `force.phase0` (or just the streaming probe with `--streaming-only`). It
  does **not** drop the `dev_<name>` schema that the local dbt run creates, nor any Q2 storage
  credential or external location. Remove those by hand.
- The pinned version is `dbt-databricks==1.9.*` (doc 05). The wildcard resolves at install time,
  so the version that actually ran is the one in the `Running with dbt=` and
  `Registered adapter` lines above, not the pin.
- Git source and dbt project directory for the job runs: see doc 05, "dbt task configuration".
- If `q3` build 1 fails on the `_metadata.file_name` column, drop that column from
  `phase0_streaming_check.sql`, push, run `cleanup --streaming-only`, and rerun `q3`. Record that
  here as a **column issue**, not as the Q3 answer.
- The serverless `environment_version` is passed with `--env-version` and is omitted otherwise,
  because I could not confirm the accepted values from the docs I checked. Record the value used
  if one was needed.
