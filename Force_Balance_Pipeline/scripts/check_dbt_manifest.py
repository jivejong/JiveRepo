"""P8-3 manifest check: the dbt project still has the resources the docs say it has.

Run after `dbt parse`: counts models, seeds, tests, unit tests and sources in warehouse/dbt/target/manifest.json and compares them
with EXPECTED (the Phase 6 handoff: 12 models, 5 seeds, 121 tests, 81 unit tests, 2 sources). Standard library only.
Exit codes: 0 every count matches, 1 a count differs (each mismatch is named on stderr) or the manifest cannot be read.
When the project legitimately gains or loses a resource, change EXPECTED here in the same commit and say why in the log.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "warehouse" / "dbt" / "target" / "manifest.json"

EXPECTED = {"models": 12, "seeds": 5, "tests": 121, "unit_tests": 81, "sources": 2}


def count(manifest):
    """The resource counts of a parsed manifest. Unit tests live in the "unit_tests" section, not in "nodes"."""
    kinds = {}
    for node in manifest.get("nodes", {}).values():
        kind = node.get("resource_type")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "models": kinds.get("model", 0),
        "seeds": kinds.get("seed", 0),
        "tests": kinds.get("test", 0),
        "unit_tests": len(manifest.get("unit_tests", {})),
        "sources": len(manifest.get("sources", {})),
    }


def main(path=DEFAULT_PATH):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"check_dbt_manifest: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    found = count(manifest)
    for key in EXPECTED:
        print(f"{key}: {found[key]} (expected {EXPECTED[key]})")
    wrong = [key for key in EXPECTED if found[key] != EXPECTED[key]]
    for key in wrong:
        print(f"check_dbt_manifest: {key} is {found[key]}, expected {EXPECTED[key]}", file=sys.stderr)
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH))
