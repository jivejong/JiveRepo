# Stub targets. Each is implemented in the phase noted; until then it fails loudly
# so nothing mistakes a stub for a passing check.

.PHONY: demo demo-down demo-clean seed test test-agent test-intake inject

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

test:
	@echo "make test: not implemented (Phase 4: dbt test --target local + agent fixtures)"; exit 1

test-agent:
	@echo "make test-agent: not implemented (Phase 6: agent fixture suite)"; exit 1

# Offline: the intake core, the server, the fixture files and the fixture runner's assertion engine, against stub models, and the page's
# JavaScript under node --test with fake stores, fetch and DOM (no npm install, no browser). No network, no key.
# The live fixture run is manual (it calls the real model): intake/fixtures/README.md. The browser check is manual too: intake/MANUAL-CHECKLIST.md.
PYTHON ?= edge/.venv/Scripts/python.exe
NODE ?= node
test-intake:
	$(PYTHON) -m unittest discover -s intake/tests
	$(NODE) --test intake/web/tests/*.test.js

# Usage: make inject SIGNATURE=sith_presence PLANET=coruscant
inject:
	@echo "make inject SIGNATURE=$(SIGNATURE) PLANET=$(PLANET): not implemented (Phase 3: control topic)"; exit 1
