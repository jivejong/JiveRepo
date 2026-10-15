"""agent/loop.py: the tool loop against a scripted client. One test per case."""
import unittest

import _support as S  # noqa: F401  (sets the import paths)
from _fixtures import ScriptedClient, StepClock, answer, call, context, deploy, stand_down
from agent import loop
from agent.gemini import GeminiUnavailable, ModelBlocked
from agent.budget import BudgetExhausted, CallBudget


def run(script, ctx=None, budget=None, repeat_last=False, clock=None):
    budget = budget or CallBudget(20)
    client = ScriptedClient(script, repeat_last=repeat_last, budget=budget)
    decision = loop.decide(ctx or context(), client, budget, monotonic=clock or StepClock())
    return decision, client


USAGE = {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 300}


class HistoryReplay(unittest.TestCase):
    def test_signed_content_is_replayed_exactly(self):
        first = answer([{"thought": True, "text": "thinking"},
                        call("get_disturbance_context", {"disturbance_id": "x"}, signature="SIG-ONE"),
                        call("list_available_starships", signature="SIG-TWO")])
        second = answer([deploy(["amy"], "a_ship")])
        _, client = run([first, second])
        replayed = client.sent[1][1]
        self.assertEqual(replayed, first["candidates"][0]["content"])
        self.assertEqual([p.get("thoughtSignature") for p in replayed["parts"]], [None, "SIG-ONE", "SIG-TWO"])
        self.assertEqual(replayed["parts"][0], {"thought": True, "text": "thinking"})

    def test_unsigned_content_is_replayed_exactly(self):
        first = answer([call("get_disturbance_context", {"disturbance_id": "x"})])
        _, client = run([first, answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(client.sent[1][1], first["candidates"][0]["content"])
        self.assertNotIn("thoughtSignature", client.sent[1][1]["parts"][0])

    def test_the_first_turn_is_the_user_message(self):
        _, client = run([answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(len(client.sent[0]), 1)
        self.assertEqual(client.sent[0][0]["role"], "user")
        self.assertTrue(client.sent[0][0]["parts"][0]["text"].startswith("Disturbance to assess."))

    def test_the_function_response_turn_follows_the_model_turn(self):
        first = answer([call("list_available_starships")])
        _, client = run([first, answer([deploy(["amy"], "a_ship")])])
        self.assertEqual([t["role"] for t in client.sent[1]], ["user", "model", "user"])


class ParallelCalls(unittest.TestCase):
    def setUp(self):
        first = answer([call("get_disturbance_context", {"disturbance_id": "x"}, call_id="c1", signature="S1"),
                        call("list_available_jedi", {"sector_id": "geonosis", "signature": "sith_presence"}, call_id="c2")])
        _, self.client = run([first, answer([deploy(["amy"], "a_ship")])])
        self.turn = self.client.sent[1][2]

    def test_one_user_turn_with_two_responses_in_order(self):
        self.assertEqual(self.turn["role"], "user")
        self.assertEqual([p["functionResponse"]["name"] for p in self.turn["parts"]],
                         ["get_disturbance_context", "list_available_jedi"])

    def test_ids_are_copied(self):
        self.assertEqual([p["functionResponse"].get("id") for p in self.turn["parts"]], ["c1", "c2"])

    def test_a_call_without_an_id_gets_a_response_without_one(self):
        _, client = run([answer([call("list_available_starships")]), answer([deploy(["amy"], "a_ship")])])
        self.assertNotIn("id", client.sent[1][2]["parts"][0]["functionResponse"])


class InfoTools(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        first = answer([call("get_disturbance_context"), call("list_available_jedi"), call("list_available_starships")])
        self.decision, self.client = run([first, answer([deploy(["amy"], "a_ship")])], ctx=self.ctx)
        self.responses = [p["functionResponse"]["response"] for p in self.client.sent[1][2]["parts"]]

    def test_context_answer_omits_the_lists(self):
        self.assertNotIn("available_jedi", self.responses[0])
        self.assertNotIn("available_starships", self.responses[0])
        self.assertEqual(self.responses[0]["signature"], "sith_presence")

    def test_jedi_answer(self):
        self.assertEqual(self.responses[1], {"jedi": self.ctx["available_jedi"]})

    def test_starship_answer(self):
        self.assertEqual(self.responses[2], {"starships": self.ctx["available_starships"]})

    def test_info_calls_are_not_rejections(self):
        self.assertEqual(self.decision.guardrail_overrides, [])
        self.assertEqual(self.decision.model_calls, 2)


class AcceptedDeploy(unittest.TestCase):
    def setUp(self):
        self.decision, self.client = run([answer([deploy(["amy", "cat"], "a_ship", "  Strong telemetry.  ")], usage=USAGE)])

    def test_decision_fields(self):
        d = self.decision
        self.assertEqual((d.decision, d.jedi_ids, d.starship_id), ("deploy", ["amy", "cat"], "a_ship"))
        self.assertEqual(d.rationale, "Strong telemetry.")
        self.assertEqual(d.model, "test-model")
        self.assertEqual(d.model_calls, 1)
        self.assertEqual(d.guardrail_overrides, [])

    def test_eta_is_the_slowest_party_member(self):
        # amy: Core Worlds to Outer Rim = 5 units, cat: Outer Rim to Outer Rim = 1 unit; ship rating 2.0
        self.assertAlmostEqual(self.decision.eta_hours, 1.0)

    def test_usage_and_cost(self):
        d = self.decision
        self.assertEqual((d.prompt_tokens, d.output_tokens, d.thought_tokens), (1000, 200, 300))
        self.assertAlmostEqual(d.cost_usd, 1000 * 0.25e-6 + 500 * 1.50e-6)

    def test_latency_is_the_model_time_in_milliseconds(self):
        self.assertEqual(self.decision.decision_latency_ms, 250)

    def test_missing_usage_counts_as_zero(self):
        d, _ = run([answer([deploy(["amy"], "a_ship")])])
        self.assertEqual((d.prompt_tokens, d.output_tokens, d.thought_tokens, d.cost_usd), (0, 0, 0, 0.0))

    def test_usage_adds_up_over_calls(self):
        d, _ = run([answer([call("list_available_starships")], usage=USAGE), answer([deploy(["amy"], "a_ship")], usage=USAGE)])
        self.assertEqual((d.prompt_tokens, d.output_tokens, d.thought_tokens), (2000, 400, 600))
        self.assertEqual(d.decision_latency_ms, 500)


class StandDown(unittest.TestCase):
    def test_accepted_stand_down(self):
        d, _ = run([answer([stand_down("  Marginal reading.  ")], usage=USAGE)])
        self.assertEqual((d.decision, d.jedi_ids, d.starship_id, d.eta_hours), ("stand_down", [], None, None))
        self.assertEqual(d.rationale, "Marginal reading.")
        self.assertEqual(d.guardrail_overrides, [])
        self.assertEqual(d.model, "test-model")

    def test_empty_rationale_is_a_rejection(self):
        d, _ = run([answer([call("stand_down", {"rationale": "   "})]), answer([stand_down()])])
        self.assertEqual(d.guardrail_overrides, [{"proposal": {"rationale": "   "}, "reasons": ["invalid_arguments"]}])
        self.assertEqual(d.model_calls, 2)

    def test_missing_args_is_a_rejection(self):
        d, _ = run([answer([call("stand_down")]), answer([stand_down()])])
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["invalid_arguments"])


class Rejections(unittest.TestCase):
    def test_rejected_then_accepted(self):
        d, client = run([answer([deploy(["zed"], "a_ship")]), answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.decision, "deploy")
        self.assertEqual(d.jedi_ids, ["amy"])
        self.assertEqual(d.model_calls, 2)
        self.assertEqual(len(d.guardrail_overrides), 1)
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["specialty_mismatch"])
        self.assertEqual(d.guardrail_overrides[0]["proposal"]["jedi_ids"], ["zed"])
        response = client.sent[1][2]["parts"][0]["functionResponse"]["response"]
        self.assertEqual(response, {"accepted": False, "reasons": ["specialty_mismatch"]})

    def test_three_rejections_end_in_agent_exhausted_retries(self):
        d, _ = run([answer([deploy(["zed"], "a_ship")])], repeat_last=True)
        self.assertEqual(d.decision, "stand_down")
        self.assertEqual(d.model_calls, 3)
        self.assertEqual([o["reasons"] for o in d.guardrail_overrides],
                         [["specialty_mismatch"]] * 3 + [["agent_exhausted_retries"]])
        self.assertIsNone(d.guardrail_overrides[-1]["proposal"])
        self.assertEqual(d.rationale, "Stand-down by the agent loop: agent_exhausted_retries.")

    def test_two_rejections_then_an_acceptance_still_deploys(self):
        d, _ = run([answer([deploy(["zed"], "a_ship")]), answer([deploy(["zed"], "a_ship")]), answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.decision, "deploy")
        self.assertEqual(len(d.guardrail_overrides), 2)

    def test_invalid_arguments_count_as_a_rejection(self):
        d, client = run([answer([call("deploy", {"jedi_ids": "amy", "starship_id": "a_ship", "rationale": "x"})]),
                         answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["invalid_arguments"])
        response = client.sent[1][2]["parts"][0]["functionResponse"]["response"]
        self.assertEqual(response, {"accepted": False, "reasons": ["invalid_arguments"]})

    def test_a_missing_field_is_invalid_arguments(self):
        d, _ = run([answer([call("deploy", {"jedi_ids": ["amy"], "rationale": "x"})]), answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["invalid_arguments"])

    def test_a_non_string_jedi_id_is_invalid_arguments(self):
        d, _ = run([answer([call("deploy", {"jedi_ids": [7], "starship_id": "a_ship", "rationale": "x"})]),
                    answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["invalid_arguments"])

    def test_an_over_long_rationale_is_invalid_arguments(self):
        d, _ = run([answer([deploy(["amy"], "a_ship", "x" * 1001)]), answer([deploy(["amy"], "a_ship", "x" * 1000)])])
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["invalid_arguments"])
        self.assertEqual(d.decision, "deploy")

    def test_unknown_tool_counts_as_a_rejection(self):
        d, client = run([answer([call("launch_missiles", {"x": 1})]), answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.guardrail_overrides, [{"proposal": {"name": "launch_missiles"}, "reasons": ["unknown_tool"]}])
        response = client.sent[1][2]["parts"][0]["functionResponse"]["response"]
        self.assertEqual(response, {"accepted": False, "reasons": ["unknown_tool"]})

    def test_a_forbidden_pair_of_reasons_is_reported_together(self):
        d, _ = run([answer([deploy(["zed", "zed"], "ghost_ship")]), answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.guardrail_overrides[0]["reasons"], ["duplicate_jedi", "starship_unavailable", "specialty_mismatch"])


class OneDecisionPerTurn(unittest.TestCase):
    def test_only_the_first_accepted_terminal_call_counts(self):
        d, _ = run([answer([deploy(["amy"], "a_ship"), deploy(["bob"], "b_ship"), stand_down()])])
        self.assertEqual((d.decision, d.jedi_ids), ("deploy", ["amy"]))
        self.assertEqual(d.guardrail_overrides, [])

    def test_a_rejected_call_before_an_accepted_one_is_counted(self):
        d, _ = run([answer([deploy(["zed"], "a_ship"), deploy(["amy"], "a_ship")])])
        self.assertEqual(d.jedi_ids, ["amy"])
        self.assertEqual(len(d.guardrail_overrides), 1)

    def test_an_accepted_stand_down_ends_the_turn(self):
        d, _ = run([answer([stand_down("Marginal."), deploy(["amy"], "a_ship")])])
        self.assertEqual(d.decision, "stand_down")
        self.assertEqual(d.guardrail_overrides, [])


class NoToolCall(unittest.TestCase):
    def test_one_nudge_then_agent_no_decision(self):
        d, client = run([answer([{"text": "I think we should stand down."}])], repeat_last=True)
        self.assertEqual(d.decision, "stand_down")
        self.assertEqual(d.model_calls, 2)
        self.assertEqual(d.guardrail_overrides, [{"proposal": None, "reasons": ["agent_no_decision"]}])
        self.assertEqual(d.rationale, "Stand-down by the agent loop: agent_no_decision.")
        self.assertEqual(client.sent[1][-1], {"role": "user", "parts": [{"text": "Call exactly one of deploy or stand_down now."}]})

    def test_a_nudge_that_works(self):
        d, client = run([answer([{"text": "hmm"}]), answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(d.decision, "deploy")
        self.assertEqual(d.model_calls, 2)
        self.assertEqual(d.guardrail_overrides, [])

    def test_the_model_turn_is_replayed_before_the_nudge(self):
        first = answer([{"text": "hmm"}])
        _, client = run([first, answer([deploy(["amy"], "a_ship")])])
        self.assertEqual(client.sent[1][1], first["candidates"][0]["content"])


class ForcedStandDown(unittest.TestCase):
    def test_low_severity_makes_no_model_call(self):
        d, client = run([answer([deploy(["amy"], "a_ship")])], ctx=context(severity=4.0), repeat_last=True)
        self.assertEqual(client.calls, 0)
        self.assertEqual(d.decision, "stand_down")
        self.assertIsNone(d.model)
        self.assertIsNone(d.decision_latency_ms)
        self.assertEqual(d.model_calls, 0)
        self.assertEqual(d.rationale, "Forced stand-down by the constraint layer: severity_below_threshold.")
        self.assertEqual(d.guardrail_overrides, [{"proposal": None, "reasons": ["severity_below_threshold"]}])
        self.assertEqual((d.prompt_tokens, d.output_tokens, d.thought_tokens, d.cost_usd), (0, 0, 0, 0.0))

    def test_missing_signature_makes_no_model_call(self):
        d, client = run([answer([deploy(["amy"], "a_ship")])], ctx=context(signature=None), repeat_last=True)
        self.assertEqual(client.calls, 0)
        self.assertEqual(d.guardrail_overrides, [{"proposal": None, "reasons": ["signature_missing"]}])

    def test_a_forced_stand_down_does_not_use_the_budget(self):
        budget = CallBudget(1)
        run([answer([stand_down()])], ctx=context(severity=1.0), budget=budget)
        self.assertEqual(budget.used, 0)


class Blocked(unittest.TestCase):
    def test_blocked_input_stands_down(self):
        try:
            d, _ = run([ModelBlocked("promptFeedback.blockReason")])
        except Exception as e:
            self.fail(f"a blocked answer must not raise: {e!r}")
        self.assertEqual(d.decision, "stand_down")
        self.assertEqual(d.rationale, "The model declined to assess this input.")
        self.assertEqual(d.guardrail_overrides, [{"proposal": None, "reasons": ["blocked_by_model"]}])
        self.assertEqual(d.model_calls, 1)
        self.assertEqual(d.model, "test-model")


class Unavailable(unittest.TestCase):
    def test_unavailable_is_raised_and_nothing_is_decided(self):
        with self.assertRaises(GeminiUnavailable):
            run([GeminiUnavailable("down")])

    def test_unavailable_after_a_first_answer_is_still_raised(self):
        with self.assertRaises(GeminiUnavailable):
            run([answer([call("list_available_starships")]), GeminiUnavailable("down")])


class Budgets(unittest.TestCase):
    def test_a_run_budget_of_one_allows_exactly_one_call(self):
        budget = CallBudget(1)
        client = ScriptedClient([answer([call("list_available_starships")])], repeat_last=True, budget=budget)
        with self.assertRaises(BudgetExhausted):
            loop.decide(context(), client, budget, monotonic=StepClock())
        self.assertEqual(client.calls, 1)
        self.assertEqual(budget.used, 1)

    def test_a_budget_of_one_allows_one_complete_decision(self):
        budget = CallBudget(1)
        try:
            d, client = run([answer([deploy(["amy"], "a_ship")])], budget=budget)
        except BudgetExhausted:
            self.fail("the one call the budget allows must be able to finish a decision")
        self.assertEqual((d.decision, client.calls, budget.used), ("deploy", 1, 1))

    def test_a_spent_budget_makes_no_call(self):
        budget = CallBudget(1)
        budget.charge()
        client = ScriptedClient([answer([deploy(["amy"], "a_ship")])], budget=budget)
        with self.assertRaises(BudgetExhausted):
            loop.decide(context(), client, budget, monotonic=StepClock())
        self.assertEqual(client.calls, 0)

    def test_six_calls_per_incident_then_agent_no_decision(self):
        d, client = run([answer([call("list_available_starships")])], repeat_last=True)
        self.assertEqual(client.calls, 6)
        self.assertEqual(d.model_calls, 6)
        self.assertEqual(d.guardrail_overrides, [{"proposal": None, "reasons": ["agent_no_decision"]}])

    def test_the_budget_is_shared_across_incidents(self):
        budget = CallBudget(3)
        for _ in range(3):
            run([answer([deploy(["amy"], "a_ship")])], budget=budget)
        with self.assertRaises(BudgetExhausted):
            run([answer([deploy(["amy"], "a_ship")])], budget=budget)

    def test_constants(self):
        self.assertEqual((loop.MAX_RETRIES, loop.MAX_NUDGES, loop.MAX_MODEL_CALLS_PER_INCIDENT), (2, 1, 6))
        self.assertEqual((loop.MAX_MODEL_CALLS_PER_RUN, loop.MAX_MODEL_CALLS_PER_EVAL_RUN), (20, 300))
        self.assertEqual((loop.PRICE_INPUT_PER_M, loop.PRICE_OUTPUT_PER_M), (0.25, 1.50))


if __name__ == "__main__":
    unittest.main()
