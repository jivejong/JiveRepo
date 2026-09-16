"""assert_no_ground_truth_leakage — the invariant the whole project rests on.

The triage model must reconstruct *who* attacked and *what techniques they
used* from what the honeypot's sensor saw. Ground truth exists only to score
that reconstruction. If any model tagged `triage_input` can reach a
`ground_truth` model through its lineage, the model is being handed the answer
and every accuracy number in Phases 6 and 7 is worthless — while still looking
entirely plausible.

**A lineage test, not a column test** (docs/02). A column-name check is defeated
by renaming: `select villain_slug as cluster_label` passes a column check and
leaks exactly as badly. So this walks `depends_on` in target/manifest.json
transitively.

Run as a NAMED CI step so a reader sees the invariant in the log
(.github/workflows/batcave-ids.yml), and it needs only a parsed project, not a
populated warehouse.

Two layers on purpose:
  - the real manifest, which is what actually protects the project
  - synthetic manifests with known-bad and known-good lineage, which keep the
    checker itself honest when someone refactors it in a later phase. A checker
    that silently stopped detecting anything would pass the first layer forever.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSFORM = REPO_ROOT / "transform"
MANIFEST = TRANSFORM / "target" / "manifest.json"

TRIAGE_TAG = "triage_input"
TRUTH_TAG = "ground_truth"

# Named explicitly in docs/02 in addition to the tag check, so that dropping a
# tag by accident cannot quietly open the boundary.
FORBIDDEN_MODELS = {
    "stg_attack_attempts",
    "stg_attack_runs",
    "int_stage_progression",
    "int_session_features_truth",
}


def _load_manifest() -> dict:
    """The real manifest, parsing the project first if needed.

    Deliberately never skips. A skipped leakage test in CI looks identical to a
    passing one in the summary line, which is the one failure mode this file
    cannot afford.
    """
    if not MANIFEST.exists():
        subprocess.run(
            ["uv", "run", "--project", "..", "dbt", "parse", "--profiles-dir", "."],
            cwd=TRANSFORM,
            check=True,
            capture_output=True,
        )
    assert MANIFEST.exists(), f"no manifest at {MANIFEST} even after `dbt parse`"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _ancestors(node_id: str, nodes: dict[str, dict]) -> set[str]:
    """Every upstream node, transitively. Iterative rather than recursive so a
    cycle can't blow the stack before it can be reported."""
    seen: set[str] = set()
    queue = list(nodes.get(node_id, {}).get("depends_on", {}).get("nodes", []))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(nodes.get(current, {}).get("depends_on", {}).get("nodes", []))
    return seen


def find_leaks(manifest: dict) -> list[str]:
    """Every triage_input model that can reach ground truth. Empty means clean."""
    nodes = manifest.get("nodes", {})
    leaks: list[str] = []

    for node_id, node in nodes.items():
        if node.get("resource_type") != "model" or TRIAGE_TAG not in node.get("tags", []):
            continue
        for ancestor_id in _ancestors(node_id, nodes):
            ancestor = nodes.get(ancestor_id, {})
            ancestor_name = ancestor.get("name", ancestor_id)
            if TRUTH_TAG in ancestor.get("tags", []):
                leaks.append(f"{node.get('name')} [{TRIAGE_TAG}] -> {ancestor_name} [{TRUTH_TAG}]")
            elif ancestor_name in FORBIDDEN_MODELS:
                leaks.append(
                    f"{node.get('name')} [{TRIAGE_TAG}] -> {ancestor_name} (forbidden model)"
                )
    return sorted(leaks)


# --- layer 1: the real project ------------------------------------------


def test_no_triage_input_model_descends_from_ground_truth():
    leaks = find_leaks(_load_manifest())
    assert not leaks, "GROUND TRUTH LEAKAGE:\n  " + "\n  ".join(leaks)


def test_every_model_is_tagged_deliberately():
    """docs/02 / CLAUDE.md: exactly one of triage_input or ground_truth, or
    neither for a shared dimension. Both tags at once is incoherent."""
    nodes = _load_manifest().get("nodes", {})
    for node in nodes.values():
        if node.get("resource_type") != "model":
            continue
        tags = set(node.get("tags", []))
        assert not (TRIAGE_TAG in tags and TRUTH_TAG in tags), (
            f"{node.get('name')} is tagged both {TRIAGE_TAG} and {TRUTH_TAG}"
        )


def test_the_known_ground_truth_models_are_actually_tagged():
    """The tag check and the named-model check must agree. If a model in
    FORBIDDEN_MODELS exists but lost its ground_truth tag, the tag half of this
    test would stop protecting everything downstream of it."""
    nodes = _load_manifest().get("nodes", {})
    by_name = {n.get("name"): n for n in nodes.values() if n.get("resource_type") == "model"}
    for name in FORBIDDEN_MODELS:
        if name in by_name:
            assert TRUTH_TAG in by_name[name].get("tags", []), f"{name} lost its {TRUTH_TAG} tag"


def test_the_triage_input_side_is_not_empty():
    """A checker with nothing to check passes trivially. If every triage_input
    tag were dropped, the leakage test above would go green while protecting
    nothing."""
    nodes = _load_manifest().get("nodes", {})
    tagged = [
        n.get("name")
        for n in nodes.values()
        if n.get("resource_type") == "model" and TRIAGE_TAG in n.get("tags", [])
    ]
    assert tagged, f"no models tagged {TRIAGE_TAG} — the boundary would be unenforced"


# --- layer 2: the checker itself ----------------------------------------


def _synthetic(*models: tuple[str, list[str], list[str]]) -> dict:
    """Build a minimal manifest: (name, tags, parent names)."""
    return {
        "nodes": {
            f"model.test.{name}": {
                "name": name,
                "resource_type": "model",
                "tags": tags,
                "depends_on": {"nodes": [f"model.test.{p}" for p in parents]},
            }
            for name, tags, parents in models
        }
    }


def test_checker_rejects_a_direct_leak():
    manifest = _synthetic(
        ("stg_attack_runs", [TRUTH_TAG], []),
        ("mart_threat_scores", [TRIAGE_TAG], ["stg_attack_runs"]),
    )
    assert find_leaks(manifest), "a direct ground_truth parent must be caught"


def test_checker_rejects_a_leak_three_levels_up():
    """The realistic shape: nobody joins villain_slug directly into a triage
    model. It arrives through two innocuous-looking intermediates."""
    manifest = _synthetic(
        ("stg_attack_runs", [TRUTH_TAG], []),
        ("int_run_summary", [], ["stg_attack_runs"]),
        ("int_session_enriched", [], ["int_run_summary"]),
        ("mart_threat_scores", [TRIAGE_TAG], ["int_session_enriched"]),
    )
    leaks = find_leaks(manifest)
    assert leaks, "a transitive ground_truth ancestor must be caught"
    assert "stg_attack_runs" in leaks[0]


def test_checker_rejects_a_forbidden_model_even_when_its_tag_was_removed():
    """Belt and braces: the named-model list must still bite if someone drops
    the tag, which is the easiest way to accidentally open the boundary."""
    manifest = _synthetic(
        ("int_session_features_truth", [], []),
        ("mart_threat_scores", [TRIAGE_TAG], ["int_session_features_truth"]),
    )
    assert find_leaks(manifest), "forbidden model must be caught without its tag"


def test_checker_accepts_a_clean_lineage():
    """It must not fire on everything — a checker that always fails gets
    disabled, and then protects nothing."""
    manifest = _synthetic(
        ("stg_attack_events", [TRIAGE_TAG], []),
        ("stg_villains", [], []),
        ("int_session_features_observed", [TRIAGE_TAG], ["stg_attack_events", "stg_villains"]),
        ("mart_threat_scores", [TRIAGE_TAG], ["int_session_features_observed"]),
        ("stg_attack_runs", [TRUTH_TAG], []),
        ("int_session_features_truth", [TRUTH_TAG], ["stg_attack_runs"]),
    )
    assert find_leaks(manifest) == []


def test_checker_allows_ground_truth_models_to_use_shared_dimensions():
    """The boundary is one-directional. A ground_truth model reading a shared
    dimension is normal and must not be reported."""
    manifest = _synthetic(
        ("stg_villains", [], []),
        ("int_session_features_truth", [TRUTH_TAG], ["stg_villains"]),
    )
    assert find_leaks(manifest) == []


def test_checker_survives_a_dependency_cycle():
    """A malformed manifest must fail loudly, not hang."""
    manifest = _synthetic(
        ("a", [TRIAGE_TAG], ["b"]),
        ("b", [], ["a"]),
    )
    assert find_leaks(manifest) == []


@pytest.mark.parametrize("missing_parent", ["model.test.does_not_exist"])
def test_checker_tolerates_a_dangling_reference(missing_parent):
    manifest = _synthetic(("solo", [TRIAGE_TAG], []))
    manifest["nodes"]["model.test.solo"]["depends_on"]["nodes"] = [missing_parent]
    assert find_leaks(manifest) == []
