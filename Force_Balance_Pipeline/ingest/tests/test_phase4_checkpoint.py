"""ingest/phase4_checkpoint.sql (doc 07, Phase 4; the plan file's own "Phase 4 checkpoint" table): must be
read-only, name its placeholders, present p4-0 through p4-12 in order, and cover every checkpoint claim (baseline
probe-only, dedup, both known replay windows, the 4 backfill emergencies, the slow riser, STEALTH, replay
recompute, the two control-topic injection checks, housekeeping, fault reconciliation). Offline: nothing here
runs SQL."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402

SQL = (S.ROOT / "ingest" / "phase4_checkpoint.sql").read_text(encoding="utf-8")


def normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", text)).strip()


def blocks():
    """{label: text} by the '-- (p4-N)' comment that introduces each statement group."""
    marks = [(m.group(1), m.start()) for m in re.finditer(r"^-- \((p4-\d+)\)", SQL, re.M)]
    ends = [pos for _, pos in marks[1:]] + [len(SQL)]
    return {label: SQL[pos:end] for (label, pos), end in zip(marks, ends)}


class StructureTests(unittest.TestCase):
    def test_the_labels_are_p4_0_to_p4_12_in_order(self):
        self.assertEqual(list(blocks()), [f"p4-{i}" for i in range(13)])

    def test_every_statement_is_read_only(self):
        self.assertNotRegex(normalise(SQL).upper(), r"\b(DROP|DELETE|TRUNCATE|INSERT|UPDATE|ALTER|CREATE|GRANT)\b")
        # MERGE is excluded above only because "full outer join ... merge" never appears literally as the SQL
        # keyword MERGE anywhere in this file -- confirmed separately, since the blanket word check above can't
        # tell MERGE-the-verb from MERGE-the-noun in a comment.
        self.assertNotRegex(normalise(SQL).upper(), r"\bMERGE\s+INTO\b")

    def test_no_credentials_and_no_host(self):
        for word in ("token", "secret", "password", "databricks.com", "192.168"):
            self.assertNotIn(word, SQL.lower())

    def test_the_placeholders_are_documented_at_the_top(self):
        header = SQL.split("-- (p4-0)")[0]
        for placeholder in ("<baseline_computed_at>", "<replay_window_start_utc>", "<replay_window_end_utc>",
                             "<inject_ts_utc>", "<inject_sector>", "<fault_seed_table>"):
            self.assertIn(placeholder, header)

    def test_the_placeholders_used_are_the_documented_ones(self):
        used = set(re.findall(r"<([a-z_]+)>", SQL))
        self.assertEqual(used, {"baseline_computed_at", "replay_window_start_utc", "replay_window_end_utc",
                                 "inject_ts_utc", "inject_sector", "fault_seed_table"})

    def test_p4_0_is_informational_not_a_query(self):
        # The only block with no SELECT at all -- doc 07's own p4-0 criterion is a job run's output, not SQL.
        self.assertNotIn("SELECT", blocks()["p4-0"].upper())


class ClaimTests(unittest.TestCase):
    def test_p4_1_baseline_probe_only_uses_delta_time_travel_not_a_plain_timestamp_filter(self):
        b = blocks()["p4-1"]
        self.assertIn("TIMESTAMP AS OF", b)
        self.assertIn("gold_sector_baseline", b)
        self.assertIn("sample_count", b)
        # the known, documented reason plain event_time filtering doesn't hold at arbitrary later times
        self.assertIn("is_replayed=true rows whose arrival predated the gold build", b)

    def test_p4_2_dedup_uses_a_window_not_bare_equality_and_expects_13_47(self):
        b = blocks()["p4-2"]
        self.assertIn("2026-09-27T23:44:55Z", b)
        self.assertIn("2026-09-27T23:45:10Z", b)
        self.assertIn("false -> 13, true -> 47", b)

    def test_p4_3_and_p4_4_use_the_real_outage_bounds_and_expect_180_and_60_replayed(self):
        b3, b4 = blocks()["p4-3"], blocks()["p4-4"]
        self.assertIn("2026-09-29T18:14:16.192Z", b3)
        self.assertIn("2026-09-29T19:29:47.517Z", b3)
        self.assertIn("n=300, replayed=180", b3)
        self.assertIn("2026-09-29T14:19:05.866Z", b4)
        self.assertIn("2026-09-29T15:01:57.431Z", b4)
        self.assertIn("n=180, replayed=60", b4)

    def test_p4_5_names_all_four_backfill_emergencies_with_their_days_and_signatures(self):
        b = blocks()["p4-5"]
        for sector, day, signature in (("tatooine", "2026-07-18", "sith_presence"),
                                        ("dantooine", "2026-08-05", "nexus_awakening"),
                                        ("kamino", "2026-08-25", "force_drain"),
                                        ("coruscant", "2026-09-15", "civil_unrest")):
            self.assertIn(sector, b)
            self.assertIn(day, b)
            self.assertIn(signature, b)
        self.assertIn("sustained_scans >= 2", b)

    def test_p4_6_checks_mon_cala_mean_and_zero_disturbances(self):
        b = blocks()["p4-6"]
        self.assertIn("mon_cala", b)
        self.assertIn("avg(imbalance_score)", b)
        self.assertIn("mon_cala_disturbances", b)
        self.assertIn("mean < 4.0, 0 disturbances", b)

    def test_p4_7_checks_stealth_channels_present_and_rejects_with_a_window_not_bare_equality(self):
        b = blocks()["p4-7"]
        self.assertIn("2026-09-28T18:59:55Z", b)
        self.assertIn("2026-09-28T19:00:10Z", b)
        self.assertIn("channels_present", b)
        self.assertIn("stealth_rows_in_rejects", b)

    def test_p4_8_checks_no_duplicate_event_ids_after_a_replay(self):
        b = blocks()["p4-8"]
        self.assertIn("count(DISTINCT event_id)", b)
        self.assertIn("<replay_window_start_utc>", b)
        self.assertIn("<replay_window_end_utc>", b)

    def test_p4_9_checks_sith_presence_and_sustained_scans(self):
        b = blocks()["p4-9"]
        self.assertIn("sith_presence", b)
        self.assertIn("sustained_scans", b)
        self.assertIn("<inject_sector>", b)
        self.assertIn("<inject_ts_utc>", b)

    def test_p4_10_checks_the_cooldown_window_is_two_hours(self):
        b = blocks()["p4-10"]
        self.assertIn("dateadd(hour, 2,", b)
        self.assertIn("Expected: 1.", b)

    def test_p4_11_compares_silver_probe_event_against_bronze_housekeeping_rows(self):
        b = blocks()["p4-11"]
        self.assertIn("silver_probe_event", b)
        self.assertIn("payload:kind::string IS NOT NULL", b)
        self.assertIn("housekeeping_rows_in_rejects", b)

    def test_p4_12_full_outer_joins_the_fault_seed_against_rejects_on_event_id(self):
        b = blocks()["p4-12"]
        self.assertIn("FULL OUTER JOIN force.silver.silver_rejects r ON r.event_id = f.event_id", b)
        self.assertIn("<fault_seed_table>", b)
        self.assertIn("expected_reject_reason", b)
        self.assertIn("fault_not_rejected", b)
        self.assertIn("reason_mismatches", b)

    def test_the_fault_seed_columns_match_what_the_converter_actually_writes(self):
        # p4-12 only joins on and compares event_id/expected_reject_reason -- those must be exactly the columns
        # scripts/faultlog_to_seed.py writes, not a guess at a seed schema that doesn't exist yet.
        converter = (S.ROOT / "scripts" / "faultlog_to_seed.py").read_text(encoding="utf-8")
        self.assertIn('FIELDNAMES = ("event_id", "expected_reject_reason")', converter)


if __name__ == "__main__":
    unittest.main()
