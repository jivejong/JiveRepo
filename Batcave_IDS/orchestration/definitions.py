"""Dagster orchestration layer (docs/01, docs/05, docs/06 Phase 7).

Two `@dbt_assets` definitions instead of one, because a Python step sits in
the middle of the dbt graph: `fct_intervention_orders` reads both
`ref('mart_threat_scores')` and `source('triage', 'raw_triage_predictions')`,
and that source is written by `services/triage/` (dbt cannot call the
Gemini API or run the baseline classifier). A single `@dbt_assets` over the
whole project would create a cycle through that source. Split at the same
boundary the Makefile already uses (`--select fct_intervention_orders+`):

    dbt_upstream (everything except fct_intervention_orders+)
        -> triage_predictions (Python: baseline, or baseline+LLM with a key)
        -> dbt_evaluation (fct_intervention_orders+)

The wiring is automatic, not manual: dagster-dbt's `default_asset_key_fn`
maps a dbt source to `AssetKey([source_name, name])`, so the Python asset
below is declared at exactly `AssetKey(["triage", "raw_triage_predictions"])`
- the same key `dbt_evaluation`'s `fct_intervention_orders` depends on
through the `source()` call. No translator override needed for that edge.
Verified disjoint and exhaustive against `transform/target/manifest.json`:
4 nodes in the downstream selection, 31 in the complement, 35 total, and all
43 dbt tests attach to the upstream group.

Zero-credential path (docs/04): without `GEMINI_API_KEY`, the triage asset
runs the rule-based baseline only, and the graph still materializes end to
end - verified via `dagster asset materialize --select '*'` with the key
genuinely unset, not merely absent from one call.
"""

from pathlib import Path

import duckdb
from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    Config,
    DataVersion,
    Definitions,
    ObserveResult,
    RunRequest,
    ScheduleDefinition,
    asset,
    define_asset_job,
    observable_source_asset,
    sensor,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from services.triage.__main__ import DEFAULT_LIMIT, run_triage
from services.triage.store import ensure_table

REPO_ROOT = Path(__file__).parent.parent
DBT_PROJECT_DIR = REPO_ROOT / "transform"
WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
RAW_DIR = REPO_ROOT / "data" / "raw"

dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, profiles_dir=DBT_PROJECT_DIR)
dbt_project.prepare_if_dev()

RAW_EVENTS_MACRO = "macro.batcave_ids.raw_events"
TRIAGE_SOURCE_KEY = AssetKey(["triage", "raw_triage_predictions"])


def _raw_dir_fingerprint() -> str:
    """Digest over every landed Parquet file's (path, size, mtime) - used by
    both the observable source asset and the sensor below, so a file that
    hasn't changed never triggers a run regardless of how often either
    polls."""
    if not RAW_DIR.exists():
        return "empty"
    listing = sorted(
        (str(p.relative_to(RAW_DIR)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in RAW_DIR.rglob("*.parquet")
    )
    return str(hash(tuple(listing)))


class BatcaveDbtTranslator(DagsterDbtTranslator):
    """Bands the lineage graph by layer for the README screenshot, and wires
    `raw_events` as an upstream dep for the models that read it directly.

    The Parquet landing zone (`data/raw/`) is read through a macro
    (`transform/macros/raw_events.sql` inlines `read_parquet()`), not a dbt
    source, so dagster-dbt sees no dependency on it by default - this is the
    one edge that isn't free. Six staging/marts models depend on the macro
    (confirmed via `depends_on.macros` in the manifest); each gets an
    explicit dep on the `raw_events` source asset here.
    """

    def get_asset_spec(self, manifest, unique_id, project):
        spec = super().get_asset_spec(manifest, unique_id, project)
        resource_props = self.get_resource_props(manifest, unique_id)
        fqn = resource_props.get("fqn", [])
        layer = fqn[1] if len(fqn) > 1 else "other"
        spec = spec.replace_attributes(group_name=layer)
        macros = resource_props.get("depends_on", {}).get("macros", [])
        if RAW_EVENTS_MACRO in macros:
            spec = spec.merge_attributes(deps=[AssetKey("raw_events")])
        return spec


dagster_dbt_translator = BatcaveDbtTranslator()


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select="fqn:*",
    exclude="fct_intervention_orders+",
    name="dbt_upstream",
    dagster_dbt_translator=dagster_dbt_translator,
    pool="duckdb_writer",
)
def dbt_upstream(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select="fct_intervention_orders+",
    name="dbt_evaluation",
    dagster_dbt_translator=dagster_dbt_translator,
    pool="duckdb_writer",
)
def dbt_evaluation(context: AssetExecutionContext, dbt: DbtCliResource):
    # `run`, not `build`: all 43 dbt tests attach to upstream models, so
    # there is nothing to test in this selection - matches the Makefile's
    # own `triage`/`triage-eval-sample` targets, which use `dbt run` here.
    yield from dbt.cli(["run"], context=context).stream()


@observable_source_asset(key="raw_events", group_name="raw")
def raw_events() -> ObserveResult:
    """The Parquet landing zone (`data/raw/<event_kind>/dt=/hour=/`),
    observed rather than materialized - Dagster doesn't write this, the
    consumer service does."""
    return ObserveResult(data_version=DataVersion(_raw_dir_fingerprint()))


class TriageConfig(Config):
    limit: int = DEFAULT_LIMIT
    stratified_per_villain: int | None = None


@asset(
    key=TRIAGE_SOURCE_KEY,
    deps=[AssetKey("mart_threat_scores")],
    group_name="triage",
    pool="duckdb_writer",
)
def triage_predictions(context: AssetExecutionContext, config: TriageConfig) -> None:
    """Writes `raw_triage_predictions` (`services/triage/store.py`) - the one
    table in the warehouse written by Python rather than dbt, and the dbt
    source `dbt_evaluation`'s `fct_intervention_orders` reads. Calls the same
    entry point `make triage` does; no separate implementation.

    Runs the baseline only when `GEMINI_API_KEY` is unset (docs/04's documented
    zero-credential path) - this asset never requires a key to materialize.
    """
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("SET TimeZone = 'UTC'")
    try:
        ensure_table(con)
        run_triage(con, config.limit, config.stratified_per_villain)
    finally:
        con.close()
    context.log.info("triage_predictions materialized")


all_assets = [raw_events, dbt_upstream, triage_predictions, dbt_evaluation]

batcave_pipeline_job = define_asset_job(name="batcave_pipeline", selection=AssetSelection.all())

batcave_schedule = ScheduleDefinition(job=batcave_pipeline_job, cron_schedule="*/15 * * * *")


@sensor(job=batcave_pipeline_job, minimum_interval_seconds=60)
def raw_events_sensor(context):
    """Triggers a full pipeline run when `data/raw/` changes. Cursor-diff,
    not mtime-poll: comparing the same digest `raw_events` observes means a
    re-run with unchanged data is a no-op regardless of firing frequency.
    There is no `partitions_def` on the dbt assets (they are full-refresh
    tables, not partitioned by `dt=/hour=`), so this always requests the
    whole graph rather than a specific partition - partitioning 35 models to
    match the Parquet layout wasn't judged worth it here (docs/06)."""
    digest = _raw_dir_fingerprint()
    if digest == context.cursor:
        return
    context.update_cursor(digest)
    yield RunRequest(run_key=digest)


defs = Definitions(
    assets=all_assets,
    schedules=[batcave_schedule],
    sensors=[raw_events_sensor],
    jobs=[batcave_pipeline_job],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
