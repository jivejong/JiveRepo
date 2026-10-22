"""ingest/phase7_checkpoint.sql (doc 07, Phase 7 checkpoint): must be read-only, name its placeholders, present p7-1 through p7-5 in order and
cover each claim of the checkpoint (both incidents, A decided by the agent after a cycle, B's rejected attempt wrote nothing, B's later decision,
every row since T0). Offline: nothing here runs SQL."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402

SQL = (S.ROOT / "ingest" / "phase7_checkpoint.sql").read_text(encoding="utf-8")
PLACEHOLDERS = {"sector_a", "sector_b", "t0_utc", "jedi_x"}


def normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", text)).strip()


def blocks():
    """{label: text} by the '-- (p7-N)' comment that introduces each statement."""
    marks = [(m.group(1), m.start()) for m in re.finditer(r"^-- \((p7-\d+)\)", SQL, re.M)]
    ends = [pos for _, pos in marks[1:]] + [len(SQL)]
    return {label: SQL[pos:end] for (label, pos), end in zip(marks, ends)}


class StructureTests(unittest.TestCase):
    def test_the_labels_are_p7_1_to_p7_5_in_order(self):
        self.assertEqual(list(blocks()), [f"p7-{i}" for i in range(1, 6)])

    def test_every_statement_is_read_only(self):
        code = normalise(SQL).upper()
        self.assertNotRegex(code, r"\b(DROP|DELETE|TRUNCATE|INSERT|UPDATE|ALTER|CREATE|GRANT|MERGE|REPLACE)\b")
        for label, block in blocks().items():
            self.assertTrue(normalise(block).upper().startswith("SELECT"), label)

    def test_no_credentials_and_no_host(self):
        for word in ("token", "secret", "password", "databricks.com", "192.168"):
            self.assertNotIn(word, SQL.lower())

    def test_the_placeholders_are_documented_at_the_top(self):
        header = SQL.split("-- (p7-1)")[0]
        for name in PLACEHOLDERS:
            self.assertIn(f"<{name}>", header)

    def test_the_placeholders_used_are_the_documented_ones(self):
        self.assertEqual(set(re.findall(r"<([a-z_0-9]+)>", SQL)), PLACEHOLDERS)

    def test_every_statement_ends_with_a_semicolon(self):
        for label, block in blocks().items():
            self.assertTrue(normalise(block).endswith(";"), label)


class ClaimTests(unittest.TestCase):
    def test_p7_1_looks_for_both_sectors_since_t0(self):
        b = blocks()["p7-1"]
        for needle in ("<sector_a>", "<sector_b>", "detected_at >= TIMESTAMP '<t0_utc>'", "signature", "severity"):
            self.assertIn(needle, b)

    def test_p7_2_checks_the_agent_deployed_a_jedi_after_a_cycle(self):
        b = blocks()["p7-2"]
        for needle in ("<sector_a>", "decided_by", "array_contains(p.jedi_ids, '<jedi_x>')", "INTERVAL 1 HOUR", "p.decision"):
            self.assertIn(needle, b)

    def test_p7_3_counts_user_rows_naming_the_jedi_on_b(self):
        b = blocks()["p7-3"]
        for needle in ("<sector_b>", "p.decided_by = 'user'", "array_contains(p.jedi_ids, '<jedi_x>')", "count(*)"):
            self.assertIn(needle, b)

    def test_p7_4_shows_b_s_decision(self):
        b = blocks()["p7-4"]
        for needle in ("<sector_b>", "p.decided_by", "p.decision"):
            self.assertIn(needle, b)

    def test_p7_5_lists_every_row_since_t0_with_decided_by(self):
        b = blocks()["p7-5"]
        self.assertIn("p.decided_by", b)
        self.assertIn("decided_at >= TIMESTAMP '<t0_utc>'", b)
        self.assertNotIn("sector", b.replace("-- ", ""))


if __name__ == "__main__":
    unittest.main()
