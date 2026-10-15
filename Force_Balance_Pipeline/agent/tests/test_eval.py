"""agent/eval.py: the assertion checker, PASS/FLAKY rules, partial results and the report files. No network."""
import json
import tempfile
import unittest
from pathlib import Path

import _support as S
from agent import eval as ev
from agent.budget import BudgetExhausted, CallBudget
from agent.gemini import GeminiUnavailable
from agent.loop import Decision

DIR = S.ROOT / "agent" / "fixtures"


def fixture(name):
    return json.loads((DIR / (name + ".json")).read_text(encoding="utf-8"))


def deploy(jedi_ids, starship_id="jedi_starfighter", rationale="Sustained telemetry.", model_calls=1, overrides=None):
    return Decision("deploy", jedi_ids=list(jedi_ids), starship_id=starship_id, eta_hours=0.5, rationale=rationale,
                    guardrail_overrides=overrides or [], model="m", decision_latency_ms=1, model_calls=model_calls)


def stand_down(rationale="Marginal.", model_calls=1, overrides=None):
    return Decision("stand_down", rationale=rationale, guardrail_overrides=overrides or [], model="m", model_calls=model_calls)


def party(name, **conditions):
    """Jedi ids in the fixture's available list matching all conditions (rank_min, primary, any_special)."""
    from agent.constraints import RANK_ORDER
    out = []
    for j in fixture(name)["context"]["available_jedi"]:
        if "rank_min" in conditions and RANK_ORDER[j["rank"]] < RANK_ORDER[conditions["rank_min"]]:
            continue
        if "rank_max" in conditions and RANK_ORDER[j["rank"]] > RANK_ORDER[conditions["rank_max"]]:
            continue
        if "special" in conditions and conditions["special"] not in (j["primary_specialty"], j["secondary_specialty"]):
            continue
        if "not_special" in conditions and conditions["not_special"] in (j["primary_specialty"], j["secondary_specialty"]):
            continue
        out.append(j["jedi_id"])
    return out


class Compliance(unittest.TestCase):
    NAME = "probe_sith_presence_mustafar"          # severity 10.0, combat, no rank rule

    def test_a_legal_deploy_passes(self):
        self.assertEqual(ev.check_run(fixture(self.NAME), deploy(party(self.NAME, special="combat")[:1])), [])

    def test_a_deploy_that_breaks_the_constraint_layer_fails_on_compliance_alone(self):
        two = party(self.NAME, special="combat")[:2]
        failures = ev.check_run(fixture(self.NAME), deploy(two, starship_id="jedi_starfighter"))   # 1-seat ship, 2 Jedi
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].startswith("compliance:"))
        self.assertIn("insufficient_capacity", failures[0])

    def test_a_jedi_outside_the_available_list_fails(self):
        failures = ev.check_run(fixture("all_combat_jedi_deployed"), deploy(["mace_windu"]))
        self.assertTrue(any(f.startswith("compliance:") for f in failures))

    def test_a_starship_outside_the_available_list_fails(self):
        failures = ev.check_run(fixture("only_one_seat_starships"), deploy(party(self.NAME, special="combat")[:1], "republic_cruiser"))
        self.assertTrue(any("not in the available list" in f for f in failures))

    def test_a_stand_down_needs_no_compliance_check(self):
        self.assertEqual(ev.check_run(fixture("capacity_reached"), stand_down()), [])

    def test_a_deploy_over_the_fleet_cap_fails(self):
        failures = ev.check_run(fixture("capacity_reached"), deploy(party("capacity_reached", special="combat")[:1]))
        self.assertTrue(any("deployment_capacity_reached" in f for f in failures))


class Expectation(unittest.TestCase):
    def test_expected_deploy_got_stand_down(self):
        self.assertIn("expected deploy, got stand_down", ev.check_run(fixture("probe_sith_presence_mustafar"), stand_down()))

    def test_expected_stand_down_got_deploy(self):
        name = "report_sourced_marginal"
        failures = ev.check_run(fixture(name), deploy(party(name, special="combat")[:1]))
        self.assertIn("expected stand_down, got deploy", failures)

    def test_any_accepts_both(self):
        name = "null_population"
        self.assertEqual(ev.check_run(fixture(name), stand_down()), [])
        self.assertEqual(ev.check_run(fixture(name), deploy(party(name)[:1])), [])

    def test_min_rank_failure_is_reported(self):
        name = "probe_dark_adept_master_band"
        knights = party(name, special="combat", rank_max="knight")
        self.assertTrue(knights)
        self.assertIn("no deployed Jedi has rank master or above", ev.check_run(fixture(name), deploy(knights[:1])))

    def test_min_rank_passes_with_one_senior_jedi(self):
        name = "probe_dark_adept_master_band"
        senior = party(name, special="combat", rank_min="master")
        self.assertEqual(ev.check_run(fixture(name), deploy(senior[:1], "republic_cruiser")), [])

    def test_specialty_failure_is_reported(self):
        name = "probe_kyber_cache"
        wrong = party(name, not_special="diplomacy")
        self.assertIn("no deployed Jedi has specialty diplomacy", ev.check_run(fixture(name), deploy(wrong[:1])))

    def test_specialty_passes_on_a_secondary_match(self):
        name = "probe_kyber_cache"
        secondary = [j["jedi_id"] for j in fixture(name)["context"]["available_jedi"]
                     if j["secondary_specialty"] == "diplomacy" and j["primary_specialty"] != "diplomacy"]
        self.assertTrue(secondary)
        self.assertEqual(ev.check_run(fixture(name), deploy(secondary[:1])), [])

    def test_rationale_mention_is_case_insensitive(self):
        name = "report_sourced_high_relevance"
        senior = party(name, special="combat", rank_min="master")[:1]
        self.assertEqual(ev.check_run(fixture(name), deploy(senior, "republic_cruiser", rationale="He drew a LIGHTSABER openly.")), [])

    def test_rationale_without_any_listed_word_fails(self):
        name = "report_sourced_high_relevance"
        senior = party(name, special="combat", rank_min="master")[:1]
        failures = ev.check_run(fixture(name), deploy(senior, "republic_cruiser", rationale="A serious threat."))
        self.assertTrue(any(f.startswith("the rationale mentions none of") for f in failures))

    def test_max_jedi(self):
        name = "report_with_injection_attempt"
        three = [j["jedi_id"] for j in fixture(name)["context"]["available_jedi"]][:3]
        failures = ev.check_run(fixture(name), deploy(three, "republic_cruiser"))
        self.assertTrue(any("at most 2 allowed" in f for f in failures))

    def test_max_jedi_allows_two(self):
        name = "report_with_injection_attempt"
        two = list(dict.fromkeys(party(name, special="combat", rank_min="master")[:1] + party(name, special="combat")))[:2]
        self.assertEqual(ev.check_run(fixture(name), deploy(two, "republic_cruiser")), [])

    def test_forced_reason_must_be_in_the_overrides(self):
        name = "missing_signature_field"
        good = stand_down("Forced.", model_calls=0, overrides=[{"proposal": None, "reasons": ["signature_missing"]}])
        self.assertEqual(ev.check_run(fixture(name), good), [])
        bad = stand_down("Forced.", model_calls=0, overrides=[{"proposal": None, "reasons": ["severity_below_threshold"]}])
        self.assertIn("the forced reason signature_missing is not in the overrides", ev.check_run(fixture(name), bad))

    def test_model_calls_must_match(self):
        name = "missing_signature_field"
        decision = stand_down("x", model_calls=1, overrides=[{"proposal": None, "reasons": ["signature_missing"]}])
        self.assertIn("1 model calls, expected 0", ev.check_run(fixture(name), decision))


class Status(unittest.TestCase):
    def rec(self, decision="deploy", failures=(), error=None):
        return {"decision": decision, "failures": list(failures), "error": error}

    def test_all_runs_passing_is_pass(self):
        self.assertEqual(ev.fixture_status([self.rec(), self.rec(), self.rec()]), "PASS")

    def test_one_failing_run_is_fail_not_pass(self):
        self.assertEqual(ev.fixture_status([self.rec(), self.rec(), self.rec(failures=["x"])]), "FAIL")

    def test_two_of_three_passing_is_not_a_pass(self):
        self.assertNotEqual(ev.fixture_status([self.rec(), self.rec(), self.rec(failures=["x"])]), "PASS")

    def test_runs_that_disagree_on_the_decision_are_flaky(self):
        self.assertEqual(ev.fixture_status([self.rec("deploy"), self.rec("stand_down"), self.rec("deploy")]), "FLAKY")

    def test_an_error_run_is_a_fail(self):
        self.assertEqual(ev.fixture_status([self.rec(), self.rec(decision=None, error="GeminiUnavailable: x")]), "FAIL")

    def test_no_runs_is_not_run(self):
        self.assertEqual(ev.fixture_status([]), "not run")


class Suite(unittest.TestCase):
    NAMES = ["probe_sith_presence_mustafar", "capacity_reached", "no_starships_available"]

    def fixtures(self):
        return ev.load_fixtures(only=self.NAMES)

    def test_each_fixture_runs_the_requested_number_of_times(self):
        calls = []
        results, exhausted = ev.run_suite(self.fixtures(), 3, lambda ctx: calls.append(ctx["disturbance_id"]) or stand_down())
        self.assertFalse(exhausted)
        self.assertEqual(len(calls), 9)
        self.assertEqual([len(e["runs"]) for e in results], [3, 3, 3])

    def test_a_budget_stop_keeps_partial_results_and_marks_the_rest_not_run(self):
        budget = CallBudget(4)

        def decide_fn(ctx):
            budget.charge()
            return stand_down()

        results, exhausted = ev.run_suite(self.fixtures(), 3, decide_fn)
        self.assertTrue(exhausted)
        self.assertEqual([len(e["runs"]) for e in results], [3, 1, 0])
        self.assertEqual([ev.fixture_status(e["runs"]) for e in results][2], "not run")

    def test_an_unavailable_model_is_recorded_as_an_error_run_and_the_suite_goes_on(self):
        def decide_fn(ctx):
            raise GeminiUnavailable("down")

        results, exhausted = ev.run_suite(self.fixtures(), 2, decide_fn)
        self.assertFalse(exhausted)
        self.assertEqual([len(e["runs"]) for e in results], [2, 2, 2])
        self.assertTrue(results[0]["runs"][0]["error"].startswith("GeminiUnavailable"))
        self.assertEqual(ev.fixture_status(results[0]["runs"]), "FAIL")

    def test_failures_are_recorded_per_run(self):
        results, _ = ev.run_suite(ev.load_fixtures(only=["probe_sith_presence_mustafar"]), 1, lambda ctx: stand_down())
        self.assertEqual(results[0]["runs"][0]["failures"], ["expected deploy, got stand_down"])


class Loading(unittest.TestCase):
    def test_all_24_load_in_spec_order(self):
        from agent.fixtures.spec import FIXTURES
        self.assertEqual([f["name"] for f in ev.load_fixtures()], [s["name"] for s in FIXTURES])

    def test_only_keeps_spec_order(self):
        names = [f["name"] for f in ev.load_fixtures(only=["null_population", "probe_kyber_cache"])]
        self.assertEqual(names, ["probe_kyber_cache", "null_population"])

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(ValueError):
            ev.load_fixtures(only=["nope"])


class Reports(unittest.TestCase):
    def setUp(self):
        budget = CallBudget(300)
        self.budget = budget
        decisions = iter([deploy(party("probe_sith_presence_mustafar", special="combat")[:1]),
                          deploy(party("capacity_reached", special="combat")[:1])])

        def decide_fn(ctx):
            budget.charge()
            d = next(decisions)
            d.prompt_tokens, d.output_tokens, d.thought_tokens, d.cost_usd = 100, 20, 30, 0.001
            return d

        self.fixtures = ev.load_fixtures(only=["probe_sith_presence_mustafar", "capacity_reached"])
        self.results, self.exhausted = ev.run_suite(self.fixtures, 1, decide_fn)
        self.meta = {"date": "2026-10-14 12:00 UTC", "model": "test-model", "thinking_level": "low", "runs": 1, "budget": 300,
                     "exhausted": False}

    def test_totals(self):
        t = ev.totals(self.results, self.budget)
        self.assertEqual((t["pass"], t["flaky"], t["fail"], t["not_run"], t["fixtures"]), (1, 0, 1, 0, 2))
        self.assertEqual((t["prompt_tokens"], t["output_tokens"], t["thought_tokens"]), (200, 40, 60))
        self.assertAlmostEqual(t["cost_usd"], 0.002)
        self.assertEqual(t["http_attempts"], 2)
        self.assertEqual(t["model_calls"], 2)

    def test_section_has_the_table_and_the_totals(self):
        section = ev.render_section(self.results, self.budget, self.meta)
        self.assertIn("## Run 2026-10-14 12:00 UTC", section)
        self.assertIn("| Fixture | Expect | Run decisions | Result |", section)
        self.assertIn("| probe_sith_presence_mustafar | deploy, specialty combat | deploy | PASS |", section)
        self.assertIn("| capacity_reached | stand_down | deploy | FAIL |", section)
        self.assertIn("Passed: 1 of 2", section)
        self.assertIn("HTTP attempts: 2", section)
        self.assertIn("$0.002000", section)

    def test_append_creates_the_file_with_a_header_and_then_appends(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "eval_results.md"
            ev.append_results(path, "## Run one\n")
            ev.append_results(path, "## Run two\n")
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Agent evaluation results"))
            self.assertLess(text.index("## Run one"), text.index("## Run two"))
            self.assertNotIn("\r", text)

    def test_ndjson_has_one_line_per_run(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "logs" / "x.ndjson"
            ev.write_ndjson(path, self.results)
            lines = path.read_text(encoding="ascii").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["fixture"], "probe_sith_presence_mustafar")


class Repository(unittest.TestCase):
    def test_the_log_directory_is_gitignored(self):
        lines = [line.strip() for line in (S.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
        self.assertIn("logs/", lines)

    def test_the_eval_env_needs_only_the_two_keys(self):
        self.assertEqual(ev.EVAL_ENV_KEYS, ("GEMINI_API_KEY", "INFERENCE_MODEL"))


if __name__ == "__main__":
    unittest.main()
