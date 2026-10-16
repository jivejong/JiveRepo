"""agent/distance.py: rings, distance units, ETA and release time (doc 06 "ETA")."""
import csv
import re
import unittest
from datetime import datetime, timezone

import _support as S
from agent import distance as d

DIM_SECTOR = S.ROOT / "warehouse" / "dbt" / "seeds" / "dim_sector.csv"


class Rings(unittest.TestCase):
    def test_every_seed_region_has_a_ring(self):
        with open(DIM_SECTOR, encoding="utf-8", newline="") as f:
            regions = {row["region"] for row in csv.DictReader(f)}
        self.assertTrue(regions)
        self.assertLessEqual(regions, set(d.RINGS))

    def test_every_region_the_sector_seed_accepts_has_a_ring(self):
        text = (S.ROOT / "warehouse" / "dbt" / "seeds" / "_seeds.yml").read_text(encoding="utf-8")
        block = text[text.index("- name: dim_sector"):text.index("- name: dim_jedi")]
        field = block[block.index("- name: region"):]
        field = field[:field.index("- name: is_unknown")]
        accepted = re.findall(r'^\s+- "([^"]+)"\s*$', field, re.M)
        self.assertGreaterEqual(len(accepted), 7)
        self.assertLessEqual(set(accepted), set(d.RINGS))

    def test_inner_rim_is_ring_two(self):
        self.assertEqual(d.ring("Inner Rim"), 2)
        self.assertEqual(d.distance_units("Inner Rim", "Core Worlds"), 3)

    def test_ring_values(self):
        self.assertEqual(d.RINGS, {"Core Worlds": 0, "Colonies": 1, "Inner Rim": 2, "Expansion Region": 2, "Mid Rim": 3, "Outer Rim": 4,
                                   "Wild Space": 5, "Unknown Regions": 5})

    def test_unknown_region_counts_as_the_missing_ring(self):
        self.assertEqual(d.ring("Nowhere"), d.MISSING_REGION_RING)
        self.assertEqual(d.ring(None), 5)


class Distance(unittest.TestCase):
    def test_core_to_outer_rim(self):
        self.assertEqual(d.distance_units("Core Worlds", "Outer Rim"), 5)

    def test_outer_rim_to_core(self):
        self.assertEqual(d.distance_units("Outer Rim", "Core Worlds"), 5)

    def test_same_region_is_one_unit(self):
        self.assertEqual(d.distance_units("Mid Rim", "Mid Rim"), 1)

    def test_missing_region_against_core(self):
        self.assertEqual(d.distance_units(None, "Core Worlds"), 6)

    def test_wild_space_against_unknown_regions(self):
        self.assertEqual(d.distance_units("Wild Space", "Unknown Regions"), 1)


class Eta(unittest.TestCase):
    def test_five_units_at_rating_one(self):
        self.assertAlmostEqual(d.eta_hours(5, 1.0), 0.5)

    def test_one_unit_at_rating_two(self):
        self.assertAlmostEqual(d.eta_hours(1, 2.0), 0.2)

    def test_mission_hours(self):
        self.assertEqual(d.MISSION_HOURS, 24)

    def test_released_at_adds_eta_and_mission_hours(self):
        decided = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(d.released_at(decided, 0.5), datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
