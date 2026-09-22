.PHONY: dev-up dev-down dev-reset attack attack-all separability pathology-check landing-check \
        transform lint-sql feature-crosscheck sample-partition triage triage-eval-sample eval \
        reconcile coverage dagster docs fmt test

TOPIC := attack.events

# ── Real (Phase 0) ───────────────────────────────────────────────────────

dev-up:
	docker compose up -d --wait --build
	docker compose exec redpanda rpk topic create $(TOPIC) --partitions 3 --replicas 1 2>/dev/null || true
	docker compose exec redpanda rpk topic alter-config $(TOPIC) --set retention.ms=86400000
	@echo "Redpanda up: broker on localhost:9092, console on http://localhost:8080"

dev-down:
	docker compose down

dev-reset:
	docker compose down -v
	find data/raw -type f ! -name 'sample-*' -delete
	find data/raw -type d -empty -delete
	rm -rf data/quarantine
	rm -f data/warehouse.duckdb

# Run one villain (VILLAIN=<slug>) or all twelve against the honeypot with the
# deliberate pathologies enabled. TIME_SCALE compresses idle gaps (recorded on
# each attack_run). Requires `make dev-up` first.
attack:
	uv run python -m services.simulator --villain $(VILLAIN) --time-scale $(or $(TIME_SCALE),0.05)

attack-all:
	uv run python -m services.simulator --all --time-scale $(or $(TIME_SCALE),0.05)

# ── Stubs — arrive in the phase noted, not yet implemented ─────────────

# Runs all twelve villains, consumes the events back, and reports villain
# separability in normalized feature space (Phase 3 checkpoint, Part 1).
# TIME_SCALE shrinks the real pacing sleeps for fast iteration; normalized
# distances are scale-invariant. Requires `make dev-up` first.
separability:
	uv run python -m services.simulator.separability --time-scale $(or $(TIME_SCALE),0.02)

# Runs a corpus with all pathologies enabled and verifies each one is present
# in real consumed data with a count (Phase 3 checkpoint, Part 2). Requires
# `make dev-up` first.
pathology-check:
	uv run python -m services.simulator.pathology_check --runs $(or $(RUNS),12) --time-scale $(or $(TIME_SCALE),0.02)

# Verifies what the consumer landed: partitions, counts reconciled against
# attack_runs, the two duplicate populations, quarantine, and topic-vs-disk
# agreement (Phase 4 checkpoint). Read-only - run a corpus first.
landing-check:
	uv run python -m services.consumer.landing_check

# Proves the dbt feature models and the separability harness compute identical
# values on the same rows (docs/06: a divergence is a bug in one of them, not
# an acceptable difference). Requires a built warehouse - run `make transform`.
feature-crosscheck:
	uv run python -m services.simulator.feature_crosscheck

# Regenerates the committed sample partition from the current corpus. Needs a
# real corpus on disk (not just the sample) and a built warehouse.
sample-partition:
	uv run python -m services.consumer.sample_partition

# dbt runs from transform/ so the relative paths in profiles.yml
# (../data/warehouse.duckdb) and dbt_project.yml (data_root: ../data) resolve.
# Both are relative to the PROCESS working directory, not to the project file.
# `dbt build` rather than run+test: it loads seeds, builds models and runs each
# model's tests in dependency order, so a failing test stops its dependents
# instead of letting the run finish and reporting the damage afterwards.
# raw_triage_predictions is a dbt SOURCE (written by services/triage/store.py,
# not by dbt itself - dbt can't call the Gemini API or run classification
# logic). fct_intervention_orders+ read it via source(), which requires the
# table to already exist even when empty - ensured here so `make transform`
# works before `make triage` has ever run, on a clean clone.
transform:
	uv run python -c "import duckdb; from services.triage.store import ensure_table; ensure_table(duckdb.connect('data/warehouse.duckdb'))"
	cd transform && uv run --project .. dbt deps --profiles-dir .
	cd transform && uv run --project .. dbt build --profiles-dir .

# Scores + triages sessions at or above the threat threshold: rule-based
# baseline always, LLM too when GEMINI_API_KEY is set (docs/04's zero-
# credential path otherwise). Writes orders, then rebuilds the evaluation
# marts on top of them. Requires `make transform` first.
triage:
	uv run python -m services.triage triage --limit $(or $(LIMIT),60)
	cd transform && uv run --project .. dbt run --profiles-dir . --select fct_intervention_orders+

# Evaluation breadth, not the production trigger: up to N sessions from
# EVERY villain regardless of threat_score, since the threshold alone only
# ever reaches 7 of 12 (docs/02's calibration note). docs/06 Phase 6
# checkpoint: 30+ sessions across all twelve villains.
triage-eval-sample:
	uv run python -m services.triage triage --stratified-per-villain $(or $(N),3)
	cd transform && uv run --project .. dbt run --profiles-dir . --select fct_intervention_orders+

eval:
	uv run python -m services.triage eval

# mart_reconciliation, readable in ten seconds: does every event the simulator
# sent still exist somewhere, and is every pathology accounted for. Requires
# `make transform` first.
reconcile:
	uv run python -c "import duckdb; con = duckdb.connect('data/warehouse.duckdb', read_only=True); rows = con.execute('select ord, metric, value, note from mart_reconciliation order by ord').fetchall(); [print(f'{ord:>3}  {metric:<52} {value:>8}  {note}') for ord, metric, value, note in rows]"

# mart_detection_coverage on its own - the tier/reachable/attempts columns are
# meaningful before any session has been triaged. `make eval` prints the same
# table plus recall by source, once orders exist.
coverage:
	uv run python -m services.triage coverage

# DAGSTER_HOME must be set or dagster.yaml (concurrency limits, run
# coordinator) is silently ignored. UI on :3000; screenshot the asset graph.
dagster:
	DAGSTER_HOME=$(CURDIR)/orchestration/dagster_home uv run dagster dev -m orchestration.definitions -p 3000

docs:
	cd transform && uv run --project .. dbt docs generate --profiles-dir .
	cd transform && uv run --project .. dbt docs serve --profiles-dir .

# sqlfluff uses the dbt templater, which compiles the project and opens the
# warehouse — so it must run from transform/ for the same relative-path reason
# `transform` does. From anywhere else it fails trying to open ../data/.
fmt:
	uv run ruff format .
	uv run ruff check --fix .
	cd transform && uv run --project .. sqlfluff fix models --dialect duckdb || true

lint-sql:
	cd transform && uv run --project .. sqlfluff lint models --dialect duckdb

test:
	uv run pytest
	cd transform && uv run --project .. dbt test --profiles-dir .
