"""scripts/build_starship_seed.py and scripts/starship_roster.py: the parser, the slug, the roster and the committed seed."""
import csv
import unittest

import _support as S
import build_starship_seed as b
import starship_roster as r

SEED = S.ROOT / "warehouse" / "dbt" / "seeds" / "dim_starship.csv"


class ParseCount(unittest.TestCase):
    def test_range_uses_the_upper_bound(self):
        self.assertEqual(b.parse_count("30-165"), 165)

    def test_commas_are_stripped(self):
        self.assertEqual(b.parse_count("47,060"), 47060)

    def test_not_available_is_zero(self):
        self.assertEqual(b.parse_count("n/a"), 0)

    def test_unknown_is_zero(self):
        self.assertEqual(b.parse_count("unknown"), 0)

    def test_empty_is_zero(self):
        self.assertEqual(b.parse_count(""), 0)

    def test_plain_number(self):
        self.assertEqual(b.parse_count("5"), 5)

    def test_a_range_with_commas_uses_the_upper_bound(self):
        self.assertEqual(b.parse_count("1,000-2,500"), 2500)


class Slug(unittest.TestCase):
    def test_lowercase_with_underscores(self):
        self.assertEqual(b.slug("Naboo Royal Starship"), "naboo_royal_starship")

    def test_punctuation_collapses_to_one_underscore(self):
        self.assertEqual(b.slug("Theta-class T-2c shuttle"), "theta_class_t_2c_shuttle")

    def test_no_leading_or_trailing_underscore(self):
        self.assertEqual(b.slug("  -V-wing- "), "v_wing")


class Numbers(unittest.TestCase):
    def test_non_numeric_rating_is_none(self):
        self.assertIsNone(b.parse_float("unknown"))

    def test_rating_is_a_float(self):
        self.assertEqual(b.parse_float("1.8"), 1.8)

    def test_mglt_unknown_is_none(self):
        self.assertIsNone(b.parse_mglt("unknown"))

    def test_mglt_is_an_int(self):
        self.assertEqual(b.parse_mglt("100"), 100)

    def test_a_ship_without_a_rating_is_excluded_and_reported(self):
        record = {"name": "Odd ship", "model": "m", "starship_class": "c", "hyperdrive_rating": "unknown", "MGLT": "10",
                  "crew": "1", "passengers": "0"}
        rows, exclusions = b.build_rows([record])
        self.assertEqual(rows, [])
        self.assertEqual(exclusions[0][0], "Odd ship")


class Roster(unittest.TestCase):
    def test_roster_is_valid_against_the_snapshot(self):
        records, problems = r.verify(r.load_starships())
        self.assertEqual(problems, [])
        self.assertEqual(len(records), len(r.ROSTER))

    def test_no_separatist_sith_or_trade_federation_ship(self):
        names = " ".join(name.lower() for _, name in r.ROSTER)
        for word in ("droid control", "trade federation", "banking clan", "scimitar", "solar sailer", "belbullab"):
            self.assertNotIn(word, names)


class CommittedSeed(unittest.TestCase):
    def fresh(self):
        records, problems = r.verify(r.load_starships())
        self.assertEqual(problems, [])
        rows, _ = b.build_rows(records)
        return b.render(rows)

    def test_committed_csv_equals_a_fresh_build(self):
        self.assertEqual(SEED.read_bytes().decode("utf-8"), self.fresh())

    def test_two_builds_are_identical(self):
        self.assertEqual(self.fresh(), self.fresh())

    def test_csv_has_lf_endings_and_only_ascii(self):
        data = SEED.read_bytes()
        self.assertNotIn(b"\r", data)
        data.decode("ascii")

    def test_header(self):
        self.assertEqual(SEED.read_text(encoding="utf-8").splitlines()[0], ",".join(b.COLUMNS))

    def test_every_rating_is_a_number(self):
        with open(SEED, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows)
        for row in rows:
            float(row["hyperdrive_rating"])


if __name__ == "__main__":
    unittest.main()
