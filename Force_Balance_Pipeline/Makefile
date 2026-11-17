# seed and inject are stubs: each is implemented in the phase noted and fails loudly until then, so nothing mistakes a stub for a passing check.

.PHONY: demo demo-down demo-clean seed inject test test-agent test-edge test-ingest test-intake test-scripts test-serving test-node test-dashboard check-dbt docs-dbt

# make demo: the dashboard on display fixtures in an isolated Postgres (127.0.0.1:5434) and backend (127.0.0.1:8082), doc 07 P8-2.
# DEMO_PYTHON is separate from PYTHON below, which is the edge venv used by test-intake.
ifeq ($(OS),Windows_NT)
DEMO_PYTHON ?= python
else
DEMO_PYTHON ?= python3
endif
demo:
	$(DEMO_PYTHON) scripts/demo.py up

demo-down:
	$(DEMO_PYTHON) scripts/demo.py down

demo-clean:
	$(DEMO_PYTHON) scripts/demo.py clean

seed:
	@echo "make seed: not implemented (Phase 1: SWAPI fetch + dbt seed)"; exit 1

# Test targets (doc 05, "CI (GitHub Actions)"). Every recipe runs from this folder. The interpreters are variables so CI can override
# them (make PYTHON=python SERVING_PYTHON=python DBT=dbt ...). Recipes are single commands or `cd x && y`, which both sh and cmd run.
# The offline suites need no network and no key. The live fixture run for the agent and the intake page's browser check are manual.
ifeq ($(OS),Windows_NT)
PYTHON ?= edge/.venv/Scripts/python.exe
SERVING_PYTHON ?= serving/.venv/Scripts/python.exe
DBT ?= warehouse/dbt/.venv/Scripts/dbt.exe
else
PYTHON ?= edge/.venv/bin/python
SERVING_PYTHON ?= serving/.venv/bin/python
DBT ?= warehouse/dbt/.venv/bin/dbt
endif
NODE ?= node
DBT_PROFILES_DIR ?= warehouse/dbt

# In this order; test-dashboard and check-dbt are separate (npm ci and dbt need installs the others do not).
test: test-agent test-edge test-ingest test-intake test-scripts test-serving test-node

test-agent:
	$(PYTHON) -m unittest discover -s agent/tests

test-edge:
	$(PYTHON) -m unittest discover -s edge/tests

test-ingest:
	$(PYTHON) -m unittest discover -s ingest/tests

test-intake:
	$(PYTHON) -m unittest discover -s intake/tests

test-scripts:
	$(PYTHON) -m unittest discover -s scripts/tests

# Needs the local serving Postgres on 127.0.0.1:5433 and .env.serving (doc 05, "CI (GitHub Actions)").
test-serving:
	$(SERVING_PYTHON) -m unittest discover -s serving/tests

# The intake page's JavaScript under node --test with fake stores, fetch and DOM (no npm install, no browser).
test-node:
	$(NODE) --test intake/web/tests/*.test.js

# Needs `npm ci` in dashboard first. Build first: the "built page" tests only exist when dist/ does, so on a fresh checkout
# testing before building would silently skip 3 of the 47 tests.
test-dashboard:
	cd dashboard && npm run build && npm test

# dbt parse needs no warehouse: the credentials are dummies, only the project's shape is checked (scripts/check_dbt_manifest.py).
check-dbt: export DATABRICKS_HOST = dummy.example.invalid
check-dbt: export DATABRICKS_HTTP_PATH = /sql/1.0/warehouses/dummy
check-dbt: export DATABRICKS_TOKEN = dummy
check-dbt: export POSTGRES_PASSWORD = dummy
check-dbt:
	$(DBT) parse --project-dir warehouse/dbt --profiles-dir $(DBT_PROFILES_DIR)
	$(PYTHON) scripts/check_dbt_manifest.py

# The static dbt docs page (docs.yml publishes warehouse/dbt/target/static_index.html). dbt parse runs first, with the prod target:
# `docs generate --no-compile` does not parse into manifest.json itself, it embeds whatever manifest.json is already in target/
# (a stale dev one would publish the dev schema name). --empty-catalog: no warehouse connection, so no column types.
# The credential values are distinctive on purpose: docs.yml fails if any of the four appears in the page (keep the two in sync).
docs-dbt: export DATABRICKS_HOST = dummy.example.invalid
docs-dbt: export DATABRICKS_HTTP_PATH = /sql/1.0/warehouses/dummy
docs-dbt: export DATABRICKS_TOKEN = fbp-docs-dummy-token
docs-dbt: export POSTGRES_PASSWORD = fbp-docs-dummy-password
docs-dbt:
	$(DBT) parse --project-dir warehouse/dbt --profiles-dir $(DBT_PROFILES_DIR) --target prod
	$(DBT) docs generate --project-dir warehouse/dbt --profiles-dir $(DBT_PROFILES_DIR) --target prod --no-compile --empty-catalog --static

# Usage: make inject SIGNATURE=sith_presence PLANET=coruscant
inject:
	@echo "make inject SIGNATURE=$(SIGNATURE) PLANET=$(PLANET): not implemented (Phase 3: control topic)"; exit 1
