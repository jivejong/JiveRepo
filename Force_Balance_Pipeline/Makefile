# Stub targets. Each is implemented in the phase noted; until then it fails loudly
# so nothing mistakes a stub for a passing check.

.PHONY: demo seed test test-agent test-intake inject

demo:
	@echo "make demo: not implemented (Phase 4: docker-compose + dbt build --target local)"; exit 1

seed:
	@echo "make seed: not implemented (Phase 1: SWAPI fetch + dbt seed)"; exit 1

test:
	@echo "make test: not implemented (Phase 4: dbt test --target local + agent fixtures)"; exit 1

test-agent:
	@echo "make test-agent: not implemented (Phase 6: agent fixture suite)"; exit 1

test-intake:
	@echo "make test-intake: not implemented (Phase 5: intake fixture suite)"; exit 1

# Usage: make inject SIGNATURE=sith_presence PLANET=coruscant
inject:
	@echo "make inject SIGNATURE=$(SIGNATURE) PLANET=$(PLANET): not implemented (Phase 3: control topic)"; exit 1
