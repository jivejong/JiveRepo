"""ingest/phase8_outage_check.sql (doc 07, Phase 8): must be read-only, hold exactly four statements labelled p8-1 to p8-4 in order, use the
2026-11-02 to 2026-11-17 window in each, and cover each claim of the check (scans and events per day against the 15-minute cadence, duplicate
event ids, the largest gap and every gap over 20 minutes, and arrival lag per day). Offline: nothing here runs SQL."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402

SQL = (S.ROOT / "ingest" / "phase8_outage_check.sql").read_text(encoding="utf-8")


def strip_comments(text):
    return re.sub(r"^\s*--[^\n]*\n?", "", text, flags=re.M)


def normalise(text):
    return re.sub(r"\s+", " ", strip_comments(text)).strip()


def blocks():
    """{label: text} by the '-- (p8-N)' comment that introduces each statement; a statement runs to the next label (or the end)."""
    marks = [(m.group(1), m.start()) for m in re.finditer(r"^-- \((p8-\d+)\)", SQL, re.M)]
    ends = [pos for _, pos in marks[1:]] + [len(SQL)]
    return {label: SQL[pos:end] for (label, pos), end in zip(marks, ends)}


class StructureTests(unittest.TestCase):
    def test_the_labels_are_p8_1_to_p8_4_in_order(self):
        self.assertEqual(list(blocks()), [f"p8-{i}" for i in range(1, 5)])

    def test_there_are_exactly_four_statements(self):
        statements = [s for s in strip_comments(SQL).split(";") if s.strip()]
        self.assertEqual(len(statements), 4)
        for label, block in blocks().items():
            self.assertEqual(normalise(block).count(";"), 1, label)
            self.assertTrue(normalise(block).endswith(";"), label)

    def test_every_statement_starts_with_select_or_with(self):
        for label, block in blocks().items():
            self.assertRegex(normalise(block).upper(), r"^(SELECT|WITH)\b", label)

    def test_every_statement_is_read_only(self):
        code = normalise(SQL).upper()
        self.assertNotRegex(code, r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|COPY|GRANT)\b")

    def test_no_credentials_and_no_host(self):
        for word in ("token", "secret", "password", "databricks.com", "192.168"):
            self.assertNotIn(word, SQL.lower())

    def test_there_are_no_placeholders(self):
        self.assertEqual(re.findall(r"<[a-z_0-9]+>", SQL), [])


class WindowTests(unittest.TestCase):
    def test_every_statement_uses_the_two_window_literals(self):
        for label, block in blocks().items():
            code = normalise(block)
            with self.subTest(label=label):
                self.assertIn("event_time >= TIMESTAMP '2026-11-02T00:00:00Z'", code)
                self.assertIn("event_time < TIMESTAMP '2026-11-17T00:00:00Z'", code)

    def test_no_other_date_is_a_window_bound(self):
        self.assertEqual(sorted(set(re.findall(r"TIMESTAMP '([^']+)'", SQL))), ["2026-11-02T00:00:00Z", "2026-11-17T00:00:00Z"])

    def test_every_statement_reads_live_probe_events_only(self):
        for label, block in blocks().items():
            code = normalise(block)
            with self.subTest(label=label):
                self.assertIn("FROM force.bronze.events", code)
                self.assertIn("source_type = 'probe' AND NOT is_synthetic", code)


class ClaimTests(unittest.TestCase):
    def test_p8_1_compares_per_day_scans_and_events_with_the_cadence(self):
        code = normalise(blocks()["p8-1"])
        for needle in ("min(event_time) AS t", "GROUP BY scan_id", "CAST(t AS DATE) AS day_utc", "96 AS expected_scans", "5760 AS expected_events",
                       "count(*) - 96 AS scans_vs_expected", "sum(n) - 5760 AS events_vs_expected", "count_if(n <> 60) AS scans_not_60",
                       "payload:kind::string IS NULL"):
            self.assertIn(needle, code)

    def test_p8_2_counts_event_ids_with_more_than_one_row_and_gives_examples(self):
        code = normalise(blocks()["p8-2"])
        for needle in ("GROUP BY event_id", "HAVING count(*) > 1", "count(*) AS duplicate_event_ids", "slice(collect_list(event_id), 1, 5) AS examples"):
            self.assertIn(needle, code)

    def test_p8_3_finds_the_largest_gap_and_every_gap_over_20_minutes(self):
        code = normalise(blocks()["p8-3"])
        for needle in ("lag(t) OVER (ORDER BY t) AS gap_start", "row_number() OVER (ORDER BY minutes DESC)", "WHERE minutes > 20 OR rank_by_length = 1",
                       "rank_by_length = 1 AS is_largest", "/ 60, 2) AS minutes"):
            self.assertIn(needle, code)
        self.assertNotIn("mode <>", code)   # nothing is excluded, STEALTH included

    def test_p8_4_measures_arrival_per_event_day_from_ingest_and_from_landing(self):
        code = normalise(blocks()["p8-4"])
        for needle in ("CAST(event_time AS DATE) AS day_utc", "min(_ingest_ts) AS first_ingest", "unix_timestamp(_ingest_ts) - unix_timestamp(event_time)",
                       "unix_timestamp(_file_modified_ts) - unix_timestamp(event_time)", "/ 3600",
                       "round(percentile_approx((unix_timestamp(_ingest_ts) - unix_timestamp(event_time)) / 3600, 0.5), 2) AS ingest_median_h",
                       "round(percentile_approx((unix_timestamp(_file_modified_ts) - unix_timestamp(event_time)) / 3600, 0.5), 2) AS landed_median_h",
                       "AS ingest_min_h", "AS ingest_max_h", "AS landed_min_h", "AS landed_max_h"):
            self.assertIn(needle, code)


if __name__ == "__main__":
    unittest.main()
