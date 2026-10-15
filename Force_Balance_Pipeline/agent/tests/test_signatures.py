"""agent/signatures.py against doc 03 "Signature classification", edge/forcesim/signatures.py and the Jedi seed."""
import ast
import csv
import unittest

import _support as S
from agent import signatures as sg

DOC03 = S.ROOT / "docs" / "03-data-model.md"
EDGE_SIGNATURES = S.ROOT / "edge" / "forcesim" / "signatures.py"
DIM_JEDI = S.ROOT / "warehouse" / "dbt" / "seeds" / "dim_jedi.csv"


def doc_table():
    """[(signature, specialty)] in document order, from the table under '### Signature classification'."""
    lines = DOC03.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| `signature` | Pattern"))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append((cells[0].strip("`"), cells[2].strip("`")))
    return rows


class DocParity(unittest.TestCase):
    def test_signature_list_equals_the_doc_in_order(self):
        self.assertEqual(tuple(name for name, _ in doc_table()), sg.SIGNATURES)

    def test_specialties_equal_the_doc_without_unclassified(self):
        expected = {name: spec for name, spec in doc_table() if name != "unclassified"}
        self.assertEqual(expected, sg.SPECIALTY)

    def test_unclassified_has_no_specialty_rule(self):
        self.assertNotIn("unclassified", sg.SPECIALTY)


class EdgeParity(unittest.TestCase):
    def test_specialty_equals_the_edge_simulator(self):
        # edge/forcesim/signatures.py uses package-relative imports, so it cannot be loaded by file path alone; read its
        # SPECIALTY literal from the source instead (test-only, nothing is imported from edge).
        tree = ast.parse(EDGE_SIGNATURES.read_text(encoding="utf-8"))
        literal = next(node.value for node in tree.body
                       if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SPECIALTY" for t in node.targets))
        self.assertEqual(ast.literal_eval(literal), sg.SPECIALTY)


class JediCoverage(unittest.TestCase):
    def test_every_specialty_has_a_jedi_with_it_as_primary_or_secondary(self):
        with open(DIM_JEDI, encoding="utf-8", newline="") as f:
            jedi = list(csv.DictReader(f))
        for specialty in set(sg.SPECIALTY.values()):
            with self.subTest(specialty=specialty):
                self.assertTrue(any(specialty in (j["primary_specialty"], j["secondary_specialty"]) for j in jedi))


class EvidenceKind(unittest.TestCase):
    def test_probe(self):
        self.assertEqual(sg.evidence_kind(False, None), "probe")

    def test_probe_with_report(self):
        self.assertEqual(sg.evidence_kind(False, "Saw a dark figure in the market."), "probe_with_report")

    def test_report(self):
        self.assertEqual(sg.evidence_kind(True, "Saw a dark figure in the market."), "report")

    def test_report_without_a_description_is_still_a_report(self):
        self.assertEqual(sg.evidence_kind(True, None), "report")

    def test_empty_description_on_a_probe_incident_counts_as_a_report(self):
        self.assertEqual(sg.evidence_kind(False, ""), "probe_with_report")


if __name__ == "__main__":
    unittest.main()
