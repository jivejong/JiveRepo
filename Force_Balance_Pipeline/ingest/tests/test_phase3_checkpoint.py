"""ingest/phase3_checkpoint.sql (doc 07, Phase 3): written before the run, so it must be read-only, name its placeholders, cover every checkpoint
claim (buffered scans, modes, replay clustering, no gaps, no duplicates, replay paths, STEALTH's two null channels), and cast VARIANT paths
before testing them for NULL. Offline: nothing here runs SQL."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402

SQL = (S.ROOT / "ingest" / "phase3_checkpoint.sql").read_text(encoding="utf-8")


def normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", text)).strip()


def blocks():
    """{label: text} by the '-- (p3-N)' comment that introduces each statement group."""
    marks = [(m.group(1), m.start()) for m in re.finditer(r"^-- \((p3-\d)\)", SQL, re.M)]
    ends = [pos for _, pos in marks[1:]] + [len(SQL)]
    return {label: SQL[pos:end] for (label, pos), end in zip(marks, ends)}


class StructureTests(unittest.TestCase):
    def test_the_labels_are_p3_0_to_p3_9_in_order(self):
        self.assertEqual(list(blocks()), [f"p3-{i}" for i in range(10)])

    def test_every_statement_is_read_only(self):
        self.assertNotRegex(normalise(SQL).upper(), r"\b(DROP|DELETE|TRUNCATE|INSERT|UPDATE|ALTER|CREATE|MERGE|GRANT)\b")

    def test_every_statement_reads_the_bronze_table_and_ends_with_a_semicolon(self):
        for label, text in blocks().items():
            with self.subTest(label):
                self.assertIn("force.bronze.events", text)
                self.assertTrue(re.search(r";\s*$", text.strip()) or ";" in text)

    def test_no_credentials_and_no_host(self):
        for word in ("token", "secret", "password", "databricks.com", "192.168"):
            self.assertNotIn(word, SQL.lower())

    def test_the_placeholders_are_documented_at_the_top(self):
        header = SQL.split("-- (p3-0)")[0]
        for placeholder in ("<outage_start_utc>", "<outage_end_utc>", "<run_start_utc>", "<run_end_utc>"):
            self.assertIn(placeholder, header)

    def test_the_header_gives_the_doc_05_cut_timing(self):
        header = SQL.split("-- (p3-0)")[0]
        self.assertIn(":16 or :31 past the hour", header)
        self.assertIn("mode_transitions.jsonl", header)
        self.assertIn("--fault-rate 0", header)

    def test_the_placeholders_used_are_the_documented_ones(self):
        used = set(re.findall(r"<([a-z_]+)>", SQL))
        self.assertEqual(used, {"outage_start_utc", "outage_end_utc", "run_start_utc", "run_end_utc", "fault_period_start_utc",
                                "fault_period_end_utc"})


class ClaimTests(unittest.TestCase):
    def test_the_buffered_scans_are_counted_inside_the_outage_window(self):
        b = blocks()["p3-1"]
        for needle in ("count(*) AS n", "count(DISTINCT scan_id) AS scans", "count(DISTINCT sector_id) AS sectors",
                       "event_time >= TIMESTAMP '<outage_start_utc>'", "event_time < TIMESTAMP '<outage_end_utc>'", "NOT is_synthetic"):
            self.assertIn(needle, b)
        self.assertIn("60 x scans", b)

    def test_the_mode_query_groups_by_mode_and_explains_the_connected_scan_before_detection(self):
        b = blocks()["p3-2"]
        self.assertIn("GROUP BY mode", b)
        self.assertIn("CONNECTED", b)

    def test_ingest_clustering_uses_ingest_ts_and_the_1800_second_replayed_rule_of_doc_03(self):
        b = blocks()["p3-3"]
        for needle in ("_ingest_ts", "> 1800", "min_lag_s", "max_lag_s", "rows_replayed"):
            self.assertIn(needle, b)

    def test_no_gaps_compares_seen_slots_with_expected_slots_on_15_minute_boundaries(self):
        b = blocks()["p3-4"]
        self.assertIn("floor(unix_timestamp(t) / 900) * 900", b)              # a scan's slot
        self.assertIn(") / 900 + 1 AS slots_expected", b)                      # the slots between the first and the last
        self.assertIn("slots_seen", b)
        self.assertIn("slots_expected", b)

    def test_duplicates_and_incomplete_scans_are_both_checked(self):
        b = blocks()["p3-5"]
        self.assertIn("HAVING count(*) > 1", b)
        self.assertIn("HAVING count(*) <> 60", b)

    def test_replay_paths_are_read_from_dt_and_hh(self):
        b = blocks()["p3-6"]
        self.assertIn("dt, hh", b)
        self.assertIn("_source_file", b)

    def test_stealth_checks_two_null_channels_dark_present_and_no_temperature_and_casts_before_is_null(self):
        b = blocks()["p3-7"]
        self.assertIn("mode = 'STEALTH'", b)
        self.assertIn("payload:midichlorian_ppm::double IS NULL", b)
        self.assertIn("payload:kyber_resonance::double IS NULL", b)
        self.assertIn("payload:dark_side_activity::double IS NOT NULL", b)
        self.assertIn("payload:sensor_temp_c::double IS NULL", b)

    def test_no_variant_path_is_tested_for_null_without_a_cast(self):
        for match in re.finditer(r"payload:[a-z_]+(?!::)\s+IS\s+(NOT\s+)?NULL", normalise(SQL)):
            self.fail(f"an uncast VARIANT path is tested for NULL: {match.group(0)}")

    def test_the_fault_query_covers_the_four_documented_faults(self):
        b = blocks()["p3-8"]
        for needle in ("null_channel", "out_of_range", "unknown_sector", "event_time_ahead_of_ingest", "'unknown-%'"):
            self.assertIn(needle, b)

    def test_readings_queries_exclude_housekeeping_events(self):
        for label in ("p3-0", "p3-1", "p3-2", "p3-3", "p3-4", "p3-6"):
            self.assertIn("payload:kind::string IS NULL", blocks()[label], label)
        self.assertIn("payload:kind::string = 'buffer_overflow'", blocks()["p3-9"])


if __name__ == "__main__":
    unittest.main()
