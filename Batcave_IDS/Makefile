.PHONY: dev-up dev-down dev-reset attack attack-all separability pathology-check landing-check \
        transform lint-sql feature-crosscheck sample-partition triage eval \
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
transform:
	cd transform && uv run --project .. dbt deps --profiles-dir .
	cd transform && uv run --project .. dbt build --profiles-dir .

triage:
	@echo "make triage: arrives in Phase 6 (scoring, triage, evaluation)." >&2
	@exit 1

eval:
	@echo "make eval: arrives in Phase 6 (scoring, triage, evaluation)." >&2
	@exit 1

reconcile:
	@echo "make reconcile: arrives in Phase 5 (mart_reconciliation)." >&2
	@exit 1

coverage:
	@echo "make coverage: arrives in Phase 6 (mart_detection_coverage)." >&2
	@exit 1

dagster:
	@echo "make dagster: arrives in Phase 7 (orchestration)." >&2
	@exit 1

docs:
	@echo "make docs: arrives in Phase 5 (dbt docs generate/serve)." >&2
	@exit 1

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
	@echo "dbt test: arrives in Phase 5 (no dbt models yet)." >&2
