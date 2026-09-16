.PHONY: dev-up dev-down dev-reset attack attack-all separability pathology-check landing-check \
        transform triage eval \
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

transform:
	@echo "make transform: arrives in Phase 5 (dbt transformation layer)." >&2
	@exit 1

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

fmt:
	uv run ruff format .
	uv run ruff check --fix .
	uv run sqlfluff fix transform/models --dialect duckdb || true

test:
	uv run pytest
	@echo "dbt test: arrives in Phase 5 (no dbt models yet)." >&2
