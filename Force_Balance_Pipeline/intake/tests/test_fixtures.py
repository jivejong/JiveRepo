"""The fixture files themselves (docs/04 "This model needs its own fixture suite"): the doc's rows are all there with the doc's
thresholds, the cases that must cross 5.75 sit on planets where they can, and a malformed fixture is refused."""
import copy
import json
import re
import unicodedata
import unittest
from pathlib import Path

import _support as S
import run_fixtures as rf
from _support import body, core

SECTORS = core.load_sectors()
FIXTURES = rf.load_fixtures(rf.FIXTURES_DIR, SECTORS)
BY_NAME = {fx["name"]: fx for fx in FIXTURES}
README = (rf.FIXTURES_DIR / "README.md").read_text(encoding="utf-8")
EXCLUDED_PLANETS = ("dathomir", "mustafar")


def doc_04_fixture_rows():
    text = (S.ROOT / "docs" / "04-edge-simulators.md").read_text(encoding="utf-8")
    start = text.index("### This model needs its own fixture suite")
    end = text.index("That last one matters", start)
    rows = [line for line in text[start:end].splitlines() if line.startswith("| ")]
    return [tuple(cell.strip() for cell in line.strip("|").split("|", 1)) for line in rows[1:]]       # drop the header


class FixtureFileTests(unittest.TestCase):
    def test_there_are_eleven_fixtures_named_after_their_files(self):
        self.assertEqual(len(FIXTURES), 11)
        self.assertEqual(len(BY_NAME), 11)
        self.assertEqual([fx["name"] for fx in FIXTURES], sorted(fx["name"] for fx in FIXTURES))
        self.assertEqual(sorted(p.stem for p in rf.FIXTURES_DIR.glob("*.json")), sorted(BY_NAME))

    def test_every_row_of_the_doc_04_table_has_a_fixture(self):
        rows = doc_04_fixture_rows()
        self.assertEqual(len(rows), 8)
        descriptions = {rf.description_of(fx) for fx in FIXTURES}
        for description, assertion in rows:
            with self.subTest(description=description):
                if description.startswith('"') and description != '"" (empty)':
                    self.assertIn(description.strip('"'), descriptions)       # the doc's exact words
        self.assertIn("", descriptions)                                       # '"" (empty)'
        self.assertEqual(len(rf.description_of(BY_NAME["rambling_5000_chars"])), 5000)       # '5000-character rambling'
        self.assertIn("Ignore all previous instructions", BY_NAME["injection_on_a_mundane_report"]["description"])   # the injection row

    def test_the_fixtures_carry_the_doc_04_thresholds(self):
        expected = {"rain_today": {"lt": 0.15}, "speeder_broke_down": {"lt": 0.15}, "vader_in_the_market": {"gt": 0.85},
                    "laser_swords": {"gt": 0.8}, "cold_feeling_near_the_temple": {"gt": 0.3, "lt": 0.7},
                    "injection_on_a_mundane_report": {"lt": 0.15}}          # the last one: decision 14, relevance < 0.15
        for name, relevance in expected.items():
            with self.subTest(name=name):
                self.assertEqual(BY_NAME[name]["expect"]["relevance"], relevance)
        self.assertGreater(BY_NAME["vader_in_the_market"]["expect"]["min_sigma"]["dark_side_activity"], 0)

    def test_the_added_cases_carry_their_thresholds(self):
        added = {"injection_on_real_force_content": {"relevance": {"gt": 0.7}, "min_sigma": {"dark_side_activity": 3.0}},
                 "html_and_script_markup": {"relevance": {"lt": 0.15}, "max_abs_sigma": 2.0},
                 "unicode_text": {"relevance": {"lt": 0.15}, "max_abs_sigma": 2.0}}
        for name, expected in added.items():
            with self.subTest(name=name):
                self.assertEqual({k: v for k, v in BY_NAME[name]["expect"].items() if k != "outcome"}, expected)
        self.assertEqual(BY_NAME["vader_in_the_market"]["expect"]["min_sigma"], {"dark_side_activity": 4.0})
        for name in ("rain_today", "speeder_broke_down", "injection_on_a_mundane_report"):
            self.assertEqual(BY_NAME[name]["expect"]["max_abs_sigma"], 2.0)
        self.assertEqual(BY_NAME["empty_description"]["expect"], {"outcome": "rejected", "code": "empty_description", "inference_calls": 0})

    def test_the_doc_04_table_and_the_fixtures_agree_on_the_assertions(self):
        rows = dict(doc_04_fixture_rows())
        self.assertEqual(rows['"It rained today"'], "`relevance < 0.15`")
        self.assertEqual(rows['"Saw Darth Vader walking through the market"'].split(",")[0], "`relevance > 0.85`")
        self.assertEqual(rows['"My speeder broke down"'], "`relevance < 0.15`")
        self.assertEqual(rows['"A cold feeling near the old temple, lights flickered"'], "`0.3 < relevance < 0.7`")
        self.assertEqual(rows['"Two people fighting with laser swords"'], "`relevance > 0.8`")

    def test_the_cases_that_must_cross_5_75_sit_on_planets_where_dark_alone_can_reach_it(self):
        crossing = [fx for fx in FIXTURES if fx.get("must_cross_gold")]
        self.assertEqual(sorted(fx["name"] for fx in crossing), ["injection_on_real_force_content", "vader_in_the_market"])
        for fx in crossing:
            with self.subTest(name=fx["name"]):
                self.assertNotIn(fx["sector_id"], EXCLUDED_PLANETS)
                sector = SECTORS[fx["sector_id"]]
                self.assertLessEqual(sector.dark_baseline + 5 * sector.dark_sigma, 100)       # room under the clamp
                self.assertIn("dark_side_activity", fx["expect"]["min_sigma"])

    def test_no_fixture_at_all_uses_the_two_planets_that_cannot_cross(self):
        self.assertEqual({fx["sector_id"] for fx in FIXTURES} & set(EXCLUDED_PLANETS), set())

    def test_the_two_excluded_planets_really_cannot_reach_it_on_the_dark_channel(self):
        for name in EXCLUDED_PLANETS:
            sector = SECTORS[name]
            self.assertGreater(sector.dark_baseline + 4.07 * sector.dark_sigma, 100)      # z_dark 4.07 alone is score 5.75

    def test_the_readme_names_the_two_planets_and_the_runner(self):
        for name in EXCLUDED_PLANETS:
            self.assertIn(name, README)
        self.assertIn("coruscant-class", README)
        self.assertIn("run_fixtures.py", README)
        self.assertIn("does **not** compute the dbt imbalance score", README)

    def test_every_submittable_fixture_passes_request_validation(self):
        for fx in FIXTURES:
            with self.subTest(name=fx["name"]):
                raw = json.dumps({"sector_id": fx["sector_id"], "description": rf.description_of(fx), "report_id": S.REPORT_ID,
                                  "reporter_id": "fxtr"}).encode("utf-8")
                if fx["expect"]["outcome"] == "rejected":
                    with self.assertRaises(core.RequestError) as caught:
                        core.parse_request(raw, SECTORS)
                    self.assertEqual(caught.exception.code, fx["expect"]["code"])
                else:
                    self.assertEqual(core.parse_request(raw, SECTORS).description, rf.description_of(fx))

    def test_the_added_cases_contain_what_they_claim(self):
        self.assertIn("Darth Vader", BY_NAME["injection_on_real_force_content"]["description"])
        self.assertIn("SYSTEM OVERRIDE", BY_NAME["injection_on_real_force_content"]["description"])
        html = BY_NAME["html_and_script_markup"]["description"]
        self.assertIn("<script>", html)
        self.assertIn("onerror", html)
        text = BY_NAME["unicode_text"]["description"]
        scripts = {unicodedata.name(c).split()[0] for c in text if ord(c) > 127 and c.isalpha()}
        self.assertTrue({"CJK", "ARABIC"} <= scripts, scripts)
        self.assertTrue(any(ord(c) > 0xFFFF for c in text))                              # an emoji outside the BMP
        self.assertFalse(any(unicodedata.category(c) == "Cf" and ord(c) in range(0x202A, 0x202F) for c in text))   # no bidi controls

    def test_the_fixture_files_are_utf8_without_a_bom_and_use_lf(self):
        for path in rf.FIXTURES_DIR.glob("*"):
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), path.name)
            self.assertNotIn(b"\r", raw, path.name)
            raw.decode("utf-8")


class FixtureValidationTests(unittest.TestCase):
    def valid(self):
        return copy.deepcopy(BY_NAME["rain_today"])

    def refuses(self, fx, name="rain_today"):
        with self.assertRaises(rf.FixtureError):
            rf.validate_fixture(fx, name, SECTORS)

    def test_a_valid_fixture_is_accepted(self):
        rf.validate_fixture(self.valid(), "rain_today", SECTORS)

    def test_malformed_fixtures_are_refused(self):
        cases = []
        fx = self.valid(); fx["surprise"] = 1; cases.append(fx)
        fx = self.valid(); fx["name"] = "other"; cases.append(fx)
        fx = self.valid(); fx["source"] = ""; cases.append(fx)
        fx = self.valid(); fx["sector_id"] = "nowhere"; cases.append(fx)
        fx = self.valid(); fx["description_repeat"] = {"unit": "a", "chars": 5}; cases.append(fx)
        fx = self.valid(); del fx["description"]; cases.append(fx)
        fx = self.valid(); fx["description"] = 5; cases.append(fx)
        fx = self.valid(); fx["must_cross_gold"] = "yes"; cases.append(fx)
        fx = self.valid(); fx["expect"]["outcome"] = "blocked"; cases.append(fx)
        fx = self.valid(); fx["expect"]["surprise"] = 1; cases.append(fx)
        fx = self.valid(); fx["expect"]["code"] = "x"; cases.append(fx)
        fx = self.valid(); fx["expect"]["relevance"] = {"gte": 0.1}; cases.append(fx)
        fx = self.valid(); fx["expect"]["relevance"] = {"gt": 0.5, "lt": 0.5}; cases.append(fx)
        fx = self.valid(); fx["expect"]["relevance"] = {"lt": 1.5}; cases.append(fx)
        fx = self.valid(); fx["expect"]["relevance"] = {}; cases.append(fx)
        fx = self.valid(); fx["expect"]["min_sigma"] = {"temperature": 1}; cases.append(fx)
        fx = self.valid(); fx["expect"]["min_sigma"] = {"dark_side_activity": "high"}; cases.append(fx)
        fx = self.valid(); fx["expect"]["max_abs_sigma"] = 0; cases.append(fx)
        fx = self.valid(); fx["expect"]["inference_calls"] = -1; cases.append(fx)
        fx = self.valid(); fx["expect"] = {"outcome": "rejected"}; cases.append(fx)
        fx = self.valid(); fx["expect"] = {"outcome": "rejected", "code": "x", "relevance": {"lt": 0.5}}; cases.append(fx)
        fx = self.valid(); fx["expect"] = "scored"; cases.append(fx)
        for number, fx in enumerate(cases):
            with self.subTest(case=number):
                self.refuses(fx)

    def test_a_bad_repeat_spec_is_refused(self):
        for spec in ({"unit": "", "chars": 5}, {"unit": "a", "chars": 0}, {"unit": "a", "chars": 8001}, {"unit": "a", "chars": True},
                     {"unit": "a"}, {"unit": "a", "chars": 5, "x": 1}, "text"):
            with self.subTest(spec=spec):
                fx = self.valid()
                del fx["description"]
                fx["description_repeat"] = spec
                self.refuses(fx)

    def test_a_file_that_is_not_json_or_a_folder_with_no_fixtures_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(rf.FixtureError):
                rf.load_fixtures(tmp, SECTORS)
            (Path(tmp) / "broken.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(rf.FixtureError):
                rf.load_fixtures(tmp, SECTORS)

    def test_description_repeat_is_cut_to_exactly_the_requested_length(self):
        fx = {"description_repeat": {"unit": "abc ", "chars": 10}}
        self.assertEqual(rf.description_of(fx), "abc abc ab")
        self.assertEqual(rf.description_of({"description_repeat": {"unit": "x" * 7, "chars": 3}}), "xxx")


if __name__ == "__main__":
    unittest.main()
