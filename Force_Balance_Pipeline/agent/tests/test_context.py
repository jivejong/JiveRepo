"""agent/context.py: the context dict, its JSON round trip and the constraint inputs."""
import json
import unittest
from datetime import datetime, timezone

import _support as S  # noqa: F401  (sets the import paths)
from agent import constraints as c
from agent import context as ctx

DETECTED = datetime(2026, 10, 14, 7, 15, 0, 900000, tzinfo=timezone.utc)


def row(**overrides):
    base = {
        "disturbance_id": "01K4SIGFB41DED3N5O5F1AM", "sector_id": "geonosis", "detected_at": DETECTED, "scan_id": "scan-1",
        "imbalance_score": 8.0, "z_midi": 0.4, "z_kyber": -2.8, "z_dark": 5.2, "signature": "sith_presence", "severity": 16.8,
        "is_report_sourced": False, "report_description": None, "report_relevance": None, "sustained_scans": 3,
        "sector_name": "Geonosis", "system_name": "Geonosis system", "region": "Outer Rim", "population": 100000000000.0,
        "description": "A desert world.", "force_history": "A history.", "channels_present": 3, "disturbances_last_7d": 2,
        "deployments_last_7d": 1, "last_signature": "dark_adept", "open_deployments": 1,
    }
    base.update(overrides)
    return base


def jedi(jedi_id, rank, primary, secondary=None, region="Core Worlds"):
    return {"jedi_id": jedi_id, "jedi_name": jedi_id.title(), "rank": rank, "primary_specialty": primary,
            "secondary_specialty": secondary, "power_rating": 5, "lightsaber_form": "Form I", "notable_for": "x",
            "homeworld_region": region}


def ship(ship_id, rating=1.0, crew=1, passengers=0):
    return {"starship_id": ship_id, "starship_name": ship_id, "starship_class": "x", "hyperdrive_rating": rating, "mglt": None,
            "crew": crew, "passengers": passengers}


JEDI = [jedi("zed", "council_member", "diplomacy"), jedi("amy", "knight", "combat"), jedi("bob", "master", "combat", "stealth"),
        jedi("cat", "padawan", "stealth", "combat", region="Outer Rim")]
SHIPS = [ship("b_ship"), ship("a_ship", 2.0, 4, 6)]


class Fields(unittest.TestCase):
    def test_probe_incident(self):
        built = ctx.build_context(row(), JEDI, SHIPS)
        self.assertEqual(built["evidence_kind"], "probe")
        self.assertEqual(built["signature_specialty"], "combat")
        self.assertEqual(built["channels_present"], 3)
        self.assertEqual(built["z_scores"], {"midi": 0.4, "kyber": -2.8, "dark": 5.2})
        self.assertEqual(built["recent_history"], {"disturbances_last_7d": 2, "deployments_last_7d": 1, "last_signature": "dark_adept"})
        self.assertEqual(built["open_deployments"], 1)
        self.assertEqual(built["sector"]["region"], "Outer Rim")
        self.assertEqual(built["sector"]["sector_name"], "Geonosis")
        self.assertIs(built["is_report_sourced"], False)
        self.assertIsNone(built["report_description"])

    def test_report_incident_has_three_channels_whatever_the_row_says(self):
        built = ctx.build_context(row(is_report_sourced=True, channels_present=1, report_description="A dark figure.",
                                      report_relevance=0.95), JEDI, SHIPS)
        self.assertEqual(built["evidence_kind"], "report")
        self.assertEqual(built["channels_present"], 3)
        self.assertEqual(built["report_description"], "A dark figure.")
        self.assertEqual(built["report_relevance"], 0.95)

    def test_report_incident_with_no_probe_reading_row(self):
        built = ctx.build_context(row(is_report_sourced=True, channels_present=None, report_description="x"), JEDI, SHIPS)
        self.assertEqual(built["channels_present"], 3)

    def test_probe_with_report_keeps_the_probe_channels(self):
        built = ctx.build_context(row(channels_present=2, report_description="A corroborating sighting.", report_relevance=0.8),
                                  JEDI, SHIPS)
        self.assertEqual(built["evidence_kind"], "probe_with_report")
        self.assertEqual(built["channels_present"], 2)

    def test_unclassified_has_no_specialty(self):
        built = ctx.build_context(row(signature="unclassified"), JEDI, SHIPS)
        self.assertIsNone(built["signature_specialty"])

    def test_unknown_signature_has_no_specialty(self):
        self.assertIsNone(ctx.build_context(row(signature="bogus"), JEDI, SHIPS)["signature_specialty"])

    def test_null_population_is_kept_as_null(self):
        built = ctx.build_context(row(population=None), JEDI, SHIPS)
        self.assertIsNone(built["sector"]["population"])

    def test_detected_at_is_iso_with_z(self):
        self.assertEqual(ctx.build_context(row(), JEDI, SHIPS)["detected_at"], "2026-10-14T07:15:00.900Z")


class JediOrder(unittest.TestCase):
    def ids(self, **overrides):
        return [j["jedi_id"] for j in ctx.build_context(row(**overrides), JEDI, SHIPS)["available_jedi"]]

    def test_specialty_match_beats_rank(self):
        # combat incident: bob (master, combat), amy (knight, combat), cat (padawan, secondary combat) match; zed outranks them but does not match
        self.assertEqual(self.ids(), ["bob", "amy", "cat", "zed"])

    def test_a_primary_match_for_another_specialty_goes_first(self):
        self.assertEqual(self.ids(signature="kyber_cache")[0], "zed")   # diplomacy: only zed matches

    def test_no_match_orders_by_rank_then_id(self):
        self.assertEqual(self.ids(signature="force_drain"), ["zed", "bob", "amy", "cat"])

    def test_unclassified_orders_by_rank_then_id(self):
        self.assertEqual(self.ids(signature="unclassified"), ["zed", "bob", "amy", "cat"])

    def test_ties_break_by_jedi_id(self):
        built = ctx.build_context(row(), [jedi("b2", "knight", "combat"), jedi("a1", "knight", "combat")], SHIPS)
        self.assertEqual([j["jedi_id"] for j in built["available_jedi"]], ["a1", "b2"])

    def test_distance_from_sector_uses_the_homeworld_region(self):
        built = {j["jedi_id"]: j for j in ctx.build_context(row(), JEDI, SHIPS)["available_jedi"]}
        self.assertEqual(built["amy"]["distance_from_sector"], 5)     # Core Worlds to Outer Rim
        self.assertEqual(built["cat"]["distance_from_sector"], 1)     # Outer Rim to Outer Rim

    def test_a_missing_homeworld_region_counts_as_the_missing_ring(self):
        built = ctx.build_context(row(), [jedi("lost", "knight", "combat", region=None)], SHIPS)
        self.assertEqual(built["available_jedi"][0]["distance_from_sector"], 2)   # ring 5 against Outer Rim 4

    def test_ships_are_sorted_by_id(self):
        built = ctx.build_context(row(), JEDI, SHIPS)
        self.assertEqual([s["starship_id"] for s in built["available_starships"]], ["a_ship", "b_ship"])


class RoundTrip(unittest.TestCase):
    def test_json_round_trip_is_unchanged(self):
        for r in (row(), row(is_report_sourced=True, report_description="x", report_relevance=0.9), row(population=None)):
            built = ctx.build_context(r, JEDI, SHIPS)
            self.assertEqual(json.loads(json.dumps(built)), built)


class ConstraintInputs(unittest.TestCase):
    def setUp(self):
        built = ctx.build_context(row(open_deployments=2), JEDI, SHIPS)
        self.incident, self.jedi_by_id, self.ships_by_id, self.state = ctx.constraint_inputs(built)

    def test_incident(self):
        self.assertEqual(self.incident, c.Incident("01K4SIGFB41DED3N5O5F1AM", 16.8, "sith_presence", "Outer Rim"))

    def test_fleet_state_has_the_open_count_and_empty_open_sets(self):
        self.assertEqual(self.state, c.FleetState(frozenset(), frozenset(), 2))

    def test_only_available_rows_are_known(self):
        self.assertEqual(set(self.jedi_by_id), {"zed", "amy", "bob", "cat"})
        self.assertEqual(set(self.ships_by_id), {"a_ship", "b_ship"})

    def test_a_jedi_and_a_ship_have_the_constraint_types(self):
        self.assertEqual(self.jedi_by_id["bob"], c.Jedi("bob", "master", "combat", "stealth", "Core Worlds"))
        self.assertEqual(self.ships_by_id["a_ship"], c.Starship("a_ship", 2.0, 4, 6))

    def test_an_unavailable_jedi_is_reported_as_unavailable_by_the_constraint_layer(self):
        reasons = c.check_deploy(self.incident, ["bob", "taken"], "a_ship", self.jedi_by_id, self.ships_by_id, self.state)
        self.assertIn("jedi_unavailable", reasons)

    def test_an_unavailable_ship_is_reported_as_unavailable_by_the_constraint_layer(self):
        reasons = c.check_deploy(self.incident, ["bob"], "taken_ship", self.jedi_by_id, self.ships_by_id, self.state)
        self.assertEqual(reasons, ["starship_unavailable"])

    def test_the_open_count_feeds_the_galaxy_cap(self):
        built = ctx.build_context(row(open_deployments=3), JEDI, SHIPS)
        incident, jedi_by_id, ships_by_id, state = ctx.constraint_inputs(built)
        reasons = c.check_deploy(incident, ["bob"], "a_ship", jedi_by_id, ships_by_id, state)
        self.assertEqual(reasons, ["deployment_capacity_reached"])

    def test_a_null_open_count_is_zero(self):
        built = ctx.build_context(row(open_deployments=None), JEDI, SHIPS)
        self.assertEqual(ctx.constraint_inputs(built)[3].open_deployment_count, 0)


if __name__ == "__main__":
    unittest.main()
