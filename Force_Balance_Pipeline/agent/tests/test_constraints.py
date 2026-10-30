"""agent/constraints.py: every rule of doc 06 "Constraint layer", one test per case."""
import unittest

import _support as S  # noqa: F401  (sets the import paths)
from agent import constraints as c
from agent.constraints import FleetState, Incident, Jedi, Starship

KNIGHT = Jedi("knight_a", "knight", "combat", None, "Core Worlds")
KNIGHT_B = Jedi("knight_b", "knight", "combat", "diplomacy", "Mid Rim")
KNIGHT_C = Jedi("knight_c", "knight", "stealth", None, "Outer Rim")
KNIGHT_D = Jedi("knight_d", "knight", "stealth", None, "Outer Rim")
KNIGHT_E = Jedi("knight_e", "knight", "stealth", None, "Outer Rim")
COUNCIL = Jedi("council_a", "council_member", "investigation", None, "Core Worlds")
ODD_RANK = Jedi("odd_a", "jedi_lord", "combat", None, None)
ALL_JEDI = {j.jedi_id: j for j in (KNIGHT, KNIGHT_B, KNIGHT_C, KNIGHT_D, KNIGHT_E, COUNCIL, ODD_RANK)}

BIG_SHIP = Starship("big", 1.0, 3, 6)
TINY_SHIP = Starship("tiny", 1.0, 1, 0)
ALL_SHIPS = {s.starship_id: s for s in (BIG_SHIP, TINY_SHIP)}

FREE = FleetState(frozenset(), frozenset(), 0)


def incident(severity=8.0, signature="sith_presence"):
    return c.Incident("inc-1", severity, signature, "Core Worlds")


def check(inc=None, jedi=("knight_a",), ship="big", state=FREE):
    return c.check_deploy(inc or incident(), list(jedi), ship, ALL_JEDI, ALL_SHIPS, state)


class LegalDeploy(unittest.TestCase):
    def test_legal_combat_deploy_has_no_reasons(self):
        self.assertEqual(check(), [])


class ForcedStandDown(unittest.TestCase):
    def test_severity_at_the_floor_is_not_forced(self):
        self.assertIsNone(c.forced_stand_down(incident(severity=4.40)))
        self.assertEqual(check(incident(severity=4.40)), [])

    def test_severity_just_below_the_floor_is_forced(self):
        self.assertEqual(check(incident(severity=4.39)), ["severity_below_threshold"])

    def test_missing_severity_is_forced(self):
        self.assertEqual(check(incident(severity=None)), ["severity_below_threshold"])

    def test_missing_signature_is_forced(self):
        self.assertEqual(check(incident(signature=None)), ["signature_missing"])

    def test_unknown_signature_is_forced(self):
        self.assertEqual(check(incident(signature="bogus")), ["signature_missing"])

    def test_forced_reason_is_the_only_reason_even_with_other_violations(self):
        state = FleetState(frozenset({"knight_a"}), frozenset({"big"}), 3)
        self.assertEqual(check(incident(severity=1.0), jedi=(), ship="nope", state=state), ["severity_below_threshold"])

    def test_severity_is_checked_before_signature(self):
        self.assertEqual(check(incident(severity=1.0, signature=None)), ["severity_below_threshold"])


class Rank(unittest.TestCase):
    def test_severity_at_the_master_line_needs_no_rank(self):
        self.assertEqual(check(incident(severity=12.0)), [])

    def test_above_the_line_with_only_knights_is_rejected(self):
        self.assertIn("insufficient_rank", check(incident(severity=12.01)))

    def test_above_the_line_with_a_council_member_passes(self):
        self.assertEqual(check(incident(severity=12.01, signature="nexus_awakening"), jedi=("council_a",)), [])

    def test_unknown_rank_counts_as_below_master(self):
        self.assertIn("insufficient_rank", check(incident(severity=15.0), jedi=("odd_a",)))

    def test_one_senior_jedi_in_the_party_is_enough(self):
        self.assertEqual(check(incident(severity=15.0), jedi=("knight_a", "council_a")), [])


class Specialty(unittest.TestCase):
    def test_primary_match_passes(self):
        self.assertEqual(check(incident(signature="sith_presence"), jedi=("knight_a",)), [])

    def test_secondary_match_passes(self):
        self.assertEqual(check(incident(signature="kyber_cache"), jedi=("knight_b",)), [])

    def test_no_match_is_rejected(self):
        self.assertEqual(check(incident(signature="kyber_cache"), jedi=("knight_a",)), ["specialty_mismatch"])

    def test_unclassified_needs_no_match(self):
        self.assertEqual(check(incident(signature="unclassified"), jedi=("knight_b",), state=FREE), [])


class Party(unittest.TestCase):
    def test_four_distinct_jedi_pass(self):
        self.assertEqual(check(incident(signature="unclassified"), jedi=("knight_a", "knight_b", "knight_c", "knight_d")), [])

    def test_five_distinct_jedi_are_too_many(self):
        reasons = check(incident(signature="unclassified"), jedi=("knight_a", "knight_b", "knight_c", "knight_d", "knight_e"))
        self.assertEqual(reasons, ["party_too_large"])

    def test_an_empty_party_is_rejected(self):
        self.assertEqual(check(incident(signature="unclassified"), jedi=()), ["no_jedi"])

    def test_a_repeated_id_is_rejected(self):
        self.assertEqual(check(jedi=("knight_a", "knight_a")), ["duplicate_jedi"])


class Availability(unittest.TestCase):
    def test_a_jedi_with_an_open_deployment_is_unavailable(self):
        state = FleetState(frozenset({"knight_a"}), frozenset(), 1)
        self.assertEqual(check(state=state), ["jedi_unavailable"])

    def test_an_unknown_jedi_id_is_unavailable(self):
        self.assertIn("jedi_unavailable", check(jedi=("ghost",)))

    def test_a_ship_with_an_open_deployment_is_unavailable(self):
        state = FleetState(frozenset(), frozenset({"big"}), 1)
        self.assertEqual(check(state=state), ["starship_unavailable"])

    def test_an_unknown_ship_id_is_unavailable(self):
        self.assertEqual(check(ship="ghost"), ["starship_unavailable"])


class UnavailableParty(unittest.TestCase):
    """Review finding 4: rank and specialty are judged on the whole party, so an unknown or deployed Jedi suppresses them."""

    def test_a_deployed_council_member_is_not_called_unranked(self):          # the 2026-10-29 case: plo_koon, deployed, on civil_unrest
        council = Jedi("plo", "council_member", "investigation", "combat", "Outer Rim")
        jedi = dict(ALL_JEDI, plo=council)
        state = FleetState(frozenset({"plo"}), frozenset({"big"}), 3)
        reasons = c.check_deploy(incident(severity=13.19, signature="civil_unrest"), ["plo"], "big", jedi, ALL_SHIPS, state)
        self.assertEqual(reasons, ["jedi_unavailable", "starship_unavailable", "deployment_capacity_reached"])

    def test_a_deployed_jedi_in_a_mixed_party_suppresses_both_checks(self):
        state = FleetState(frozenset({"knight_a"}), frozenset(), 0)
        reasons = c.check_deploy(incident(severity=15.0, signature="kyber_cache"), ["knight_a", "knight_c"], "big", ALL_JEDI, ALL_SHIPS, state)
        self.assertEqual(reasons, ["jedi_unavailable"])

    def test_an_unknown_jedi_suppresses_both_checks(self):
        self.assertEqual(check(incident(severity=15.0, signature="kyber_cache"), jedi=("ghost",)), ["jedi_unavailable"])

    def test_an_available_party_is_still_judged(self):
        self.assertEqual(check(incident(severity=15.0, signature="kyber_cache"), jedi=("knight_a",)), ["insufficient_rank", "specialty_mismatch"])


class FleetCapacity(unittest.TestCase):
    def test_two_open_deployments_still_allow_a_third(self):
        self.assertEqual(check(state=FleetState(frozenset(), frozenset(), 2)), [])

    def test_three_open_deployments_are_the_limit(self):
        self.assertEqual(check(state=FleetState(frozenset(), frozenset(), 3)), ["deployment_capacity_reached"])


class ShipCapacity(unittest.TestCase):
    def test_one_seat_holds_one_jedi(self):
        self.assertEqual(check(ship="tiny"), [])

    def test_one_seat_cannot_hold_two_jedi(self):
        self.assertEqual(check(incident(signature="unclassified"), jedi=("knight_a", "knight_b"), ship="tiny"),
                         ["insufficient_capacity"])


class Order(unittest.TestCase):
    def test_reasons_come_back_in_the_specified_order(self):
        state = FleetState(frozenset({"knight_a"}), frozenset({"tiny"}), 3)
        reasons = c.check_deploy(incident(severity=15.0, signature="kyber_cache"),
                                 ["knight_a", "knight_a", "knight_c", "knight_d", "knight_e", "ghost"], "tiny",
                                 ALL_JEDI, ALL_SHIPS, state)
        self.assertEqual(reasons, ["duplicate_jedi", "party_too_large", "jedi_unavailable", "starship_unavailable",
                                   "deployment_capacity_reached", "insufficient_capacity"])

    def test_the_party_quality_reasons_keep_their_place_for_an_available_party(self):
        state = FleetState(frozenset(), frozenset({"tiny"}), 3)
        reasons = c.check_deploy(incident(severity=15.0, signature="kyber_cache"), ["knight_a", "knight_c"], "tiny",
                                 ALL_JEDI, ALL_SHIPS, state)
        self.assertEqual(reasons, ["starship_unavailable", "deployment_capacity_reached", "insufficient_rank", "specialty_mismatch",
                                   "insufficient_capacity"])

    def test_no_jedi_comes_first(self):
        state = FleetState(frozenset(), frozenset(), 3)
        self.assertEqual(check(jedi=(), state=state)[:2], ["no_jedi", "deployment_capacity_reached"])

    def test_each_reason_appears_at_most_once(self):
        reasons = check(jedi=("ghost", "ghost2", "knight_a", "knight_a"))
        self.assertEqual(len(reasons), len(set(reasons)))

    def test_every_returned_reason_is_known(self):
        state = FleetState(frozenset({"knight_a"}), frozenset({"tiny"}), 3)
        for inc in (incident(severity=15.0, signature="kyber_cache"), incident(severity=1.0), incident(signature="x")):
            for reason in c.check_deploy(inc, ["knight_a", "knight_a", "ghost"], "tiny", ALL_JEDI, ALL_SHIPS, state):
                self.assertIn(reason, c.REASONS)

    def test_the_reason_list_has_the_agent_reasons(self):
        self.assertIn("agent_exhausted_retries", c.REASONS)
        self.assertIn("agent_no_decision", c.REASONS)


class Constants(unittest.TestCase):
    def test_rank_order(self):
        self.assertEqual(c.RANK_ORDER, {"padawan": 0, "knight": 1, "master": 2, "council_member": 3, "grand_master": 4})
        self.assertEqual(c.MASTER_RANK, 2)

    def test_limits(self):
        self.assertEqual((c.SEVERITY_FLOOR, c.MASTER_SEVERITY, c.MAX_CONCURRENT_DEPLOYMENTS, c.MAX_PARTY), (4.40, 12.0, 3, 4))


if __name__ == "__main__":
    unittest.main()
