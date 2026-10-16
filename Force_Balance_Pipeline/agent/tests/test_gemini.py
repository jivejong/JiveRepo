"""agent/gemini.py against a fake opener and a fake clock: the request, the retries and the blocked/finishReason handling."""
import json
import unittest

import _support as S
from _support import FakeClock, FakeOpener
from agent import gemini
from agent.budget import BudgetExhausted, CallBudget
from agent.prompt import SYSTEM_PROMPT

KEY = "AIza-NOT-A-REAL-KEY-4417"
MODEL = "gemini-3.1-flash-lite"
CONTENTS = [{"role": "user", "parts": [{"text": "hello"}]}]
OK = (200, {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "stand_down", "args": {"rationale": "x"}}}]},
                            "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 5}})


def finish(reason):
    return (200, {"candidates": [{"content": {"role": "model", "parts": [{"text": "t"}]}, "finishReason": reason}]})


def make(script, **options):
    opener = FakeOpener(script)
    clock = FakeClock()
    client = gemini.GeminiClient(KEY, MODEL, opener=opener, sleep=clock.sleep, monotonic=clock.monotonic, **options)
    return client, opener, clock


class Request(unittest.TestCase):
    def setUp(self):
        self.client, self.opener, _ = make([OK])
        self.response = self.client.generate(CONTENTS)
        self.sent = self.opener.requests[0]

    def test_one_post_to_the_model_endpoint(self):
        self.assertEqual(len(self.opener.requests), 1)
        self.assertEqual(self.sent["method"], "POST")
        self.assertEqual(self.sent["url"], f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent")

    def test_the_key_is_in_a_header_and_never_in_the_url(self):
        self.assertEqual(self.sent["headers"]["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, self.sent["url"])

    def test_the_project_user_agent_is_sent(self):
        import files_api
        self.assertEqual(self.sent["headers"]["user-agent"], files_api.UA)

    def test_no_temperature_anywhere_in_the_body(self):
        self.assertNotIn("temperature", json.dumps(self.sent["body"]).lower())

    def test_tool_config_forces_a_tool_call(self):
        self.assertEqual(self.sent["body"]["toolConfig"], {"functionCallingConfig": {"mode": "ANY"}})

    def test_five_function_declarations(self):
        declarations = self.sent["body"]["tools"][0]["functionDeclarations"]
        self.assertEqual(len(declarations), 5)

    def test_generation_config(self):
        self.assertEqual(self.sent["body"]["generationConfig"],
                         {"thinkingConfig": {"thinkingLevel": "low"}, "maxOutputTokens": 2048})

    def test_no_structured_output_settings(self):
        text = json.dumps(self.sent["body"])
        self.assertNotIn("responseJsonSchema", text)
        self.assertNotIn("responseMimeType", text)

    def test_system_instruction_and_contents(self):
        self.assertEqual(self.sent["body"]["systemInstruction"], {"parts": [{"text": SYSTEM_PROMPT}]})
        self.assertEqual(self.sent["body"]["contents"], CONTENTS)

    def test_returns_the_parsed_response(self):
        self.assertEqual(self.response["usageMetadata"], {"promptTokenCount": 5})
        self.assertEqual(self.response["candidates"][0]["finishReason"], "STOP")

    def test_options_are_used(self):
        client, opener, _ = make([OK], thinking_level="high", max_output_tokens=512)
        client.generate(CONTENTS)
        self.assertEqual(opener.requests[0]["body"]["generationConfig"],
                         {"thinkingConfig": {"thinkingLevel": "high"}, "maxOutputTokens": 512})

    def test_a_bad_model_name_is_refused(self):
        with self.assertRaises(ValueError):
            gemini.GeminiClient(KEY, "../evil", opener=FakeOpener([]), sleep=None, monotonic=None)

    def test_a_bad_thinking_level_is_refused(self):
        with self.assertRaises(ValueError):
            gemini.GeminiClient(KEY, MODEL, opener=FakeOpener([]), sleep=None, monotonic=None, thinking_level="extreme")


class Transport(unittest.TestCase):
    def test_429_then_503_is_unavailable_after_one_retry_with_a_10_second_wait(self):
        client, opener, clock = make([(429, "slow"), (503, "down")])
        with self.assertRaises(gemini.GeminiUnavailable):
            client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(clock.sleeps, [10])

    def test_429_then_success(self):
        client, opener, clock = make([(429, "slow"), OK])
        self.assertIn("candidates", client.generate(CONTENTS))
        self.assertEqual(clock.sleeps, [10])

    def test_503_then_success(self):
        client, _, clock = make([(503, "down"), OK])
        client.generate(CONTENTS)
        self.assertEqual(clock.sleeps, [10])

    def test_a_transport_error_is_retried(self):
        client, opener, clock = make([S.url_error(), OK])
        client.generate(CONTENTS)
        self.assertEqual((len(opener.requests), clock.sleeps), (2, [10]))

    def test_two_transport_errors_are_unavailable(self):
        client, _, _ = make([S.url_error(), S.url_error()])
        with self.assertRaises(gemini.GeminiUnavailable):
            client.generate(CONTENTS)

    def test_400_is_not_retried_and_carries_googles_message(self):
        client, opener, clock = make([(400, {"error": {"message": "Invalid JSON payload received"}})])
        with self.assertRaises(gemini.GeminiUnavailable) as caught:
            client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(clock.sleeps, [])
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertIn("Invalid JSON payload received", str(caught.exception))

    def test_an_error_body_without_a_message(self):
        client, _, _ = make([(403, "<html>")])
        with self.assertRaises(gemini.GeminiUnavailable) as caught:
            client.generate(CONTENTS)
        self.assertIn("HTTP 403", str(caught.exception))

    def test_no_error_message_contains_the_key_or_the_request_body(self):
        scripts = [[(429, "x"), (503, "x")], [S.url_error(), S.url_error()], [(400, {"error": {"message": "bad"}})],
                   [finish("MAX_TOKENS"), finish("MAX_TOKENS")], [(200, "<html>"), (200, "<html>")]]
        for script in scripts:
            with self.subTest(script=str(script)[:40]):
                client, _, _ = make(script)
                with self.assertRaises(gemini.GeminiUnavailable) as caught:
                    client.generate([{"role": "user", "parts": [{"text": "body-text-NOT-IN-ERRORS"}]}])
                self.assertNotIn(KEY, str(caught.exception))
                self.assertNotIn("body-text-NOT-IN-ERRORS", str(caught.exception))


class Answers(unittest.TestCase):
    def test_max_tokens_once_then_stop_succeeds(self):
        client, opener, _ = make([finish("MAX_TOKENS"), OK])
        self.assertIn("candidates", client.generate(CONTENTS))
        self.assertEqual(len(opener.requests), 2)

    def test_max_tokens_twice_is_unavailable(self):
        client, opener, _ = make([finish("MAX_TOKENS"), finish("MAX_TOKENS")])
        with self.assertRaises(gemini.GeminiUnavailable):
            client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)

    def test_a_malformed_function_call_finish_is_retried(self):
        client, opener, _ = make([finish("MALFORMED_FUNCTION_CALL"), OK])
        client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)

    def test_other_finish_reasons_are_retried_then_unavailable(self):
        client, _, _ = make([finish("OTHER"), finish("OTHER")])
        with self.assertRaises(gemini.GeminiUnavailable):
            client.generate(CONTENTS)

    def test_non_json_is_retried(self):
        client, opener, _ = make([(200, "<html>"), OK])
        client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)

    def test_no_candidates_is_retried_then_unavailable(self):
        client, opener, _ = make([(200, {}), (200, {"candidates": []})])
        with self.assertRaises(gemini.GeminiUnavailable):
            client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)

    def test_a_candidate_without_parts_is_retried(self):
        client, opener, _ = make([(200, {"candidates": [{"content": {"role": "model"}, "finishReason": "STOP"}]}), OK])
        client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)

    def test_a_json_list_is_retried(self):
        client, opener, _ = make([(200, "[1]"), OK])
        client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 2)

    def test_a_transport_retry_and_a_response_retry_are_independent(self):
        client, opener, clock = make([(429, "x"), finish("MAX_TOKENS"), OK])
        client.generate(CONTENTS)
        self.assertEqual((len(opener.requests), clock.sleeps), (3, [10]))


class ErrorClasses(unittest.TestCase):
    def test_the_two_classes_share_a_base(self):
        self.assertTrue(issubclass(gemini.GeminiTransient, gemini.GeminiUnavailable))
        self.assertTrue(issubclass(gemini.GeminiPermanent, gemini.GeminiUnavailable))
        self.assertFalse(issubclass(gemini.GeminiTransient, gemini.GeminiPermanent))

    def test_twice_429_or_5xx_is_transient(self):
        for script in ([(429, "x"), (429, "x")], [(503, "x"), (500, "x")], [S.url_error(), S.url_error()]):
            with self.subTest(script=str(script)[:30]):
                client, _, _ = make(script)
                with self.assertRaises(gemini.GeminiUnavailable) as caught:
                    client.generate(CONTENTS)
                self.assertIsInstance(caught.exception, gemini.GeminiTransient)
                self.assertEqual(caught.exception.kind, "transient")

    def test_a_400_is_permanent(self):
        client, _, _ = make([(400, {"error": {"message": "bad"}})])
        with self.assertRaises(gemini.GeminiUnavailable) as caught:
            client.generate(CONTENTS)
        self.assertIsInstance(caught.exception, gemini.GeminiPermanent)
        self.assertEqual(caught.exception.kind, "http_400")

    def test_a_401_and_a_404_are_permanent(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                client, _, _ = make([(status, "x")])
                with self.assertRaises(gemini.GeminiUnavailable) as caught:
                    client.generate(CONTENTS)
                self.assertIsInstance(caught.exception, gemini.GeminiPermanent)
                self.assertEqual(caught.exception.kind, f"http_{status}")

    def test_a_second_max_tokens_is_permanent(self):
        client, _, _ = make([finish("MAX_TOKENS"), finish("MAX_TOKENS")])
        with self.assertRaises(gemini.GeminiUnavailable) as caught:
            client.generate(CONTENTS)
        self.assertIsInstance(caught.exception, gemini.GeminiPermanent)
        self.assertEqual(caught.exception.kind, "bad_output")

    def test_a_second_malformed_answer_is_permanent(self):
        client, _, _ = make([(200, "<html>"), (200, "<html>")])
        with self.assertRaises(gemini.GeminiUnavailable) as caught:
            client.generate(CONTENTS)
        self.assertIsInstance(caught.exception, gemini.GeminiPermanent)

    def test_a_first_bad_answer_then_a_good_one_raises_nothing(self):
        client, _, _ = make([finish("MAX_TOKENS"), OK])
        self.assertIn("candidates", client.generate(CONTENTS))

    def test_a_plain_unavailable_has_a_default_kind(self):
        self.assertEqual(gemini.GeminiUnavailable("x").kind, "unavailable")


class Budget(unittest.TestCase):
    def test_a_plain_call_costs_one_unit(self):
        budget = CallBudget(10)
        client, _, _ = make([OK], budget=budget)
        client.generate(CONTENTS)
        self.assertEqual(budget.used, 1)

    def test_a_retried_call_costs_two_units(self):
        budget = CallBudget(10)
        client, opener, _ = make([(429, "slow"), OK], budget=budget)
        client.generate(CONTENTS)
        self.assertEqual((len(opener.requests), budget.used), (2, 2))

    def test_a_response_retry_costs_two_units(self):
        budget = CallBudget(10)
        client, _, _ = make([finish("MAX_TOKENS"), OK], budget=budget)
        client.generate(CONTENTS)
        self.assertEqual(budget.used, 2)

    def test_a_failed_call_is_charged_for_every_attempt(self):
        budget = CallBudget(10)
        client, _, _ = make([(503, "x"), (503, "x")], budget=budget)
        with self.assertRaises(gemini.GeminiUnavailable):
            client.generate(CONTENTS)
        self.assertEqual(budget.used, 2)

    def test_a_retry_that_would_pass_the_cap_is_not_sent(self):
        budget = CallBudget(1)
        client, opener, clock = make([(429, "slow"), OK], budget=budget)
        with self.assertRaises(BudgetExhausted):
            client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 1)

    def test_a_spent_budget_sends_nothing(self):
        budget = CallBudget(1)
        budget.charge()
        client, opener, _ = make([OK], budget=budget)
        with self.assertRaises(BudgetExhausted):
            client.generate(CONTENTS)
        self.assertEqual(opener.requests, [])


class Blocked(unittest.TestCase):
    def test_a_block_reason_raises_model_blocked_without_a_retry(self):
        client, opener, _ = make([(200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}})])
        with self.assertRaises(gemini.ModelBlocked):
            client.generate(CONTENTS)
        self.assertEqual(len(opener.requests), 1)

    def test_blocked_finish_reasons_raise_model_blocked(self):
        for reason in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            with self.subTest(reason=reason):
                client, opener, _ = make([finish(reason)])
                with self.assertRaises(gemini.ModelBlocked):
                    client.generate(CONTENTS)
                self.assertEqual(len(opener.requests), 1)

    def test_the_block_check_comes_before_the_finish_check(self):
        client, _, _ = make([(200, {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": [{"finishReason": "MAX_TOKENS"}]})])
        with self.assertRaises(gemini.ModelBlocked):
            client.generate(CONTENTS)


if __name__ == "__main__":
    unittest.main()
