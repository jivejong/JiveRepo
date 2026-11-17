"""Tests for scripts/check_dbt_manifest.py (P8-3): synthetic manifests, no dbt needed."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_dbt_manifest as cdm  # noqa: E402


def manifest(models=12, seeds=5, tests=121, unit_tests=81, sources=2):
    nodes = {}
    for kind, n in (("model", models), ("seed", seeds), ("test", tests), ("analysis", 3)):
        for i in range(n):
            nodes[f"{kind}.p.{i}"] = {"resource_type": kind}
    return {
        "nodes": nodes,
        "unit_tests": {f"unit_test.p.{i}": {"resource_type": "unit_test"} for i in range(unit_tests)},
        "sources": {f"source.p.{i}": {} for i in range(sources)},
    }


def run(data):
    """(exit code, stdout, stderr) of main() on a manifest file holding `data`."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "manifest.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cdm.main(path)
    return code, out.getvalue(), err.getvalue()


class CheckDbtManifest(unittest.TestCase):
    def test_matching_manifest_passes(self):
        code, out, err = run(manifest())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("models: 12 (expected 12)", out)

    def test_eleven_models_fails_and_names_models(self):
        code, _, err = run(manifest(models=11))
        self.assertEqual(code, 1)
        self.assertIn("models is 11, expected 12", err)
        self.assertNotIn("seeds", err)

    def test_unit_tests_counted_from_unit_tests_section(self):
        data = manifest(unit_tests=80)
        data["nodes"]["unit_test.p.extra"] = {"resource_type": "unit_test"}   # a stray "nodes" entry must not count
        code, _, err = run(data)
        self.assertEqual(code, 1)
        self.assertIn("unit_tests is 80, expected 81", err)
        self.assertEqual(cdm.count(data)["unit_tests"], 80)

    def test_unreadable_manifest_fails(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cdm.main(Path(tempfile.gettempdir()) / "no-such-manifest.json"), 1)


if __name__ == "__main__":
    unittest.main()
