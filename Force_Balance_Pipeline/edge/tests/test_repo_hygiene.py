"""Repo-wide line-ending hygiene: .gitattributes asks for LF in the text files we write, and nothing in the
working tree may hold a CRLF (a tool or an editor on Windows can leave one behind). Also that the two writers of
frozen data (sidecars, the provenance file) emit LF on every OS."""
import fnmatch
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import enrich_common as ec  # noqa: E402

PATTERNS = ("*.py", "*.sql", "*.md", "*.conf", "*.json", "*.yaml", "*.ndjson")
SKIP_DIRS = {".git", ".venv", "node_modules", "target", "logs", "__pycache__", "dbt_packages"}
CRLF = bytes([13, 10])


class GitattributesTests(unittest.TestCase):
    def test_every_pattern_is_text_eol_lf(self):
        lines = [l.split("#")[0].split() for l in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()]
        rules = {l[0]: l[1:] for l in lines if l}
        for pattern in PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertIn("text", rules.get(pattern, []))
                self.assertIn("eol=lf", rules.get(pattern, []))

    def test_frozen_csv_seeds_are_not_forced(self):
        rules = {l.split()[0] for l in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")}
        self.assertNotIn("*.csv", rules)


class WorkingTreeTests(unittest.TestCase):
    def test_no_text_file_holds_a_crlf(self):
        offenders = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if any(fnmatch.fnmatch(name, p) for p in PATTERNS + ("*.yml", "*.sh")):
                    path = Path(dirpath) / name
                    if CRLF in path.read_bytes():
                        offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_frozen_inputs_hash_the_same_as_their_sidecars_record(self):
        """Renormalising line endings must never change a snapshot file: its SHA-256 is in the sidecars."""
        import hashlib
        checked = 0
        for sidecar in (ROOT / "warehouse" / "dbt" / "seeds").glob("*.provenance.json"):
            for name, want in json.loads(sidecar.read_text(encoding="utf-8")).get("inputs_sha256", {}).items():
                got = hashlib.sha256((ROOT / "data" / "swapi_snapshot" / name).read_bytes()).hexdigest()
                self.assertEqual(got, want, f"{sidecar.name}: {name}")
                checked += 1
        self.assertGreaterEqual(checked, 4)


class WriterTests(unittest.TestCase):
    def test_the_sidecar_and_provenance_writers_emit_lf(self):
        with tempfile.TemporaryDirectory() as d:
            path = ec.write_sidecar(d, "dim_x", {"seed": "dim_x.csv", "model_id": "m", "thinking_level": "low",
                                                 "prompt_version": "v", "prompt_hash": "h", "generated_utc": "2026-01-01T00:00:00Z",
                                                 "regeneration_cycles": 0, "rows": 1})
            self.assertNotIn(b"\r", path.read_bytes())
            self.assertTrue(path.read_bytes().endswith(b"}\n"))
            md, count = ec.write_provenance_md(d)
            self.assertEqual(count, 1)
            self.assertNotIn(b"\r", md.read_bytes())

    def test_the_committed_provenance_file_is_what_the_sidecars_render(self):
        seeds = ROOT / "warehouse" / "dbt" / "seeds"
        self.assertEqual((seeds / "ENRICHMENT_PROVENANCE.md").read_bytes(),
                         ec.render_provenance_md(ec.read_sidecars(seeds)).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
