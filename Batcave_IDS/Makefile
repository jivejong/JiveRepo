.PHONY: dev-up dev-down dev-reset attack attack-all transform triage eval \
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
	rm -f data/warehouse.duckdb

# ── Stubs — arrive in the phase noted, not yet implemented ─────────────

attack:
	@echo "make attack: arrives in Phase 2-3 (simulator, stage machine, villain behavior)." >&2
	@exit 1

attack-all:
	@echo "make attack-all: arrives in Phase 3 (runs all twelve villains sequentially)." >&2
	@exit 1

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
