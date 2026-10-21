"""agent/dbsql.py against a fake opener and a fake clock: requests, polling, conversion, errors and retries."""
import unittest
from datetime import datetime, timezone

import _support as S
import files_api
from _support import FakeClock, FakeOpener, failed, in_state, succeeded
from agent import dbsql

SQL = "SELECT 1"


def make(script, repeat_last=False):
    opener = FakeOpener(script, repeat_last=repeat_last)
    clock = FakeClock()
    client = dbsql.StatementClient(S.HOST, S.TOKEN, S.WAREHOUSE, opener=opener, sleep=clock.sleep, monotonic=clock.monotonic)
    return client, opener, clock


class Request(unittest.TestCase):
    def setUp(self):
        self.client, self.opener, self.clock = make([(200, succeeded([("n", "INT")], [["1"]]))])
        self.rows = self.client.execute(SQL, {"a": ("x", "STRING"), "b": (None, "DOUBLE"), "c": (True, "BOOLEAN"),
                                               "d": (7, "BIGINT"), "e": (datetime(2026, 10, 12, 18, 23, 54, 691000, tzinfo=timezone.utc), "TIMESTAMP"),
                                               "f": (3, "INT")})
        self.sent = self.opener.requests[0]

    def test_one_post_to_the_statements_endpoint(self):
        self.assertEqual(len(self.opener.requests), 1)
        self.assertEqual(self.sent["method"], "POST")
        self.assertEqual(self.sent["url"], S.HOST + "/api/2.0/sql/statements")

    def test_bearer_header(self):
        self.assertEqual(self.sent["headers"]["authorization"], "Bearer " + S.TOKEN)

    def test_project_user_agent_is_sent(self):
        self.assertEqual(self.sent["headers"]["user-agent"], files_api.UA)

    def test_body_fields(self):
        body = self.sent["body"]
        self.assertEqual(body["warehouse_id"], S.WAREHOUSE)
        self.assertEqual(body["statement"], SQL)
        self.assertEqual(body["wait_timeout"], "30s")
        self.assertEqual(body["on_wait_timeout"], "CONTINUE")
        self.assertEqual(body["disposition"], "INLINE")
        self.assertEqual(body["format"], "JSON_ARRAY")

    def test_parameters_are_typed_strings(self):
        params = {p["name"]: p for p in self.sent["body"]["parameters"]}
        self.assertEqual(params["a"], {"name": "a", "type": "STRING", "value": "x"})
        self.assertEqual(params["d"], {"name": "d", "type": "BIGINT", "value": "7"})
        self.assertEqual(params["f"], {"name": "f", "type": "INT", "value": "3"})

    def test_boolean_is_true_or_false_text(self):
        params = {p["name"]: p for p in self.sent["body"]["parameters"]}
        self.assertEqual(params["c"]["value"], "true")

    def test_timestamp_is_iso_with_z(self):
        params = {p["name"]: p for p in self.sent["body"]["parameters"]}
        self.assertEqual(params["e"]["value"], "2026-10-12T18:23:54.691Z")

    def test_none_is_a_typed_null_without_a_value_key(self):
        params = {p["name"]: p for p in self.sent["body"]["parameters"]}
        self.assertEqual(params["b"], {"name": "b", "type": "DOUBLE"})
        self.assertNotIn("value", params["b"])

    def test_an_unsupported_type_is_refused_before_any_request(self):
        client, opener, _ = make([])
        with self.assertRaises(ValueError):
            client.execute(SQL, {"a": ("x", "BINARY")})
        self.assertEqual(opener.requests, [])

    def test_no_parameters_sends_an_empty_list(self):
        client, opener, _ = make([(200, succeeded([], []))])
        client.execute(SQL)
        self.assertEqual(opener.requests[0]["body"]["parameters"], [])

    def test_rows_are_dicts_by_column_name(self):
        self.assertEqual(self.rows, [{"n": 1}])


class StatementCounter(unittest.TestCase):
    def test_starts_at_zero(self):
        client, _, _ = make([])
        self.assertEqual(client.statements, 0)

    def test_counts_one_per_execute_not_per_http_poll(self):
        client, opener, _ = make([(200, in_state("PENDING")), (200, in_state("RUNNING")), (200, succeeded([("n", "INT")], [["1"]]))])
        client.execute(SQL)
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(client.statements, 1)

    def test_a_failed_statement_is_counted(self):
        client, _, _ = make([(200, failed("PARSE_SYNTAX_ERROR", "bad"))])
        with self.assertRaises(dbsql.StatementFailed):
            client.execute(SQL)
        self.assertEqual(client.statements, 1)

    def test_a_retried_write_counts_each_attempt(self):
        client, _, _ = make([(200, failed("DELTA_CONCURRENT_APPEND", "x")), (200, succeeded([], []))])
        client.write(SQL)
        self.assertEqual(client.statements, 2)

    def test_reads_and_writes_are_counted_together(self):
        client, _, _ = make([(200, succeeded([], [])), (200, succeeded([], []))])
        client.read(SQL)
        client.write(SQL)
        self.assertEqual(client.statements, 2)


class Conversion(unittest.TestCase):
    def rows(self, type_name, value):
        client, _, _ = make([(200, succeeded([("v", type_name)], [[value]]))])
        return client.execute(SQL)[0]["v"]

    def test_boolean_true(self):
        self.assertIs(self.rows("BOOLEAN", "true"), True)

    def test_boolean_false(self):
        self.assertIs(self.rows("BOOLEAN", "false"), False)

    def test_int(self):
        self.assertEqual(self.rows("INT", "42"), 42)

    def test_bigint(self):
        self.assertEqual(self.rows("BIGINT", "9000000000"), 9000000000)

    def test_short_and_long(self):
        self.assertEqual(self.rows("SHORT", "5"), 5)
        self.assertEqual(self.rows("LONG", "6"), 6)

    def test_double(self):
        self.assertEqual(self.rows("DOUBLE", "1.5"), 1.5)

    def test_float_and_decimal(self):
        self.assertEqual(self.rows("FLOAT", "2.5"), 2.5)
        self.assertEqual(self.rows("DECIMAL", "3.25"), 3.25)

    def test_timestamp_is_an_aware_utc_datetime(self):
        value = self.rows("TIMESTAMP", "2026-10-12T18:23:54.691Z")
        self.assertEqual(value, datetime(2026, 10, 12, 18, 23, 54, 691000, tzinfo=timezone.utc))
        self.assertEqual(value.utcoffset().total_seconds(), 0)

    def test_timestamp_with_a_space_and_long_fraction(self):
        value = self.rows("TIMESTAMP", "2026-10-12 18:23:54.691123456")
        self.assertEqual(value, datetime(2026, 10, 12, 18, 23, 54, 691123, tzinfo=timezone.utc))

    def test_timestamp_with_an_offset_is_converted_to_utc(self):
        value = self.rows("TIMESTAMP", "2026-10-12T20:23:54+02:00")
        self.assertEqual(value, datetime(2026, 10, 12, 18, 23, 54, tzinfo=timezone.utc))

    def test_array_is_parsed(self):
        self.assertEqual(self.rows("ARRAY", '["a","b"]'), ["a", "b"])

    def test_map_struct_and_variant_are_parsed(self):
        self.assertEqual(self.rows("MAP", '{"k":1}'), {"k": 1})
        self.assertEqual(self.rows("STRUCT", '{"k":1}'), {"k": 1})
        self.assertEqual(self.rows("VARIANT", '{"k":[1,2]}'), {"k": [1, 2]})

    def test_string_stays_a_string(self):
        self.assertEqual(self.rows("STRING", "coruscant"), "coruscant")

    def test_unknown_type_stays_a_string(self):
        self.assertEqual(self.rows("INTERVAL", "7 days"), "7 days")

    def test_null_stays_none(self):
        for type_name in ("STRING", "INT", "BOOLEAN", "TIMESTAMP", "ARRAY", "DOUBLE"):
            with self.subTest(type_name=type_name):
                self.assertIsNone(self.rows(type_name, None))

    def test_no_data_array_means_no_rows(self):
        response = succeeded([("n", "INT")], [])
        del response["result"]["data_array"]
        client, _, _ = make([(200, response)])
        self.assertEqual(client.execute(SQL), [])

    def test_a_next_chunk_link_raises_result_too_large(self):
        response = succeeded([("n", "INT")], [["1"]])
        response["result"]["next_chunk_internal_link"] = "/api/2.0/sql/statements/st-1/result/chunks/1"
        client, _, _ = make([(200, response)])
        with self.assertRaises(dbsql.ResultTooLarge):
            client.execute(SQL)


class Polling(unittest.TestCase):
    def test_pending_then_running_then_succeeded_polls_every_five_seconds(self):
        client, opener, clock = make([(200, in_state("PENDING")), (200, in_state("RUNNING")), (200, in_state("RUNNING")),
                                      (200, succeeded([("n", "INT")], [["1"]]))])
        self.assertEqual(client.execute(SQL), [{"n": 1}])
        self.assertEqual(clock.sleeps, [5, 5, 5])
        self.assertEqual([r["method"] for r in opener.requests], ["POST", "GET", "GET", "GET"])
        self.assertEqual(opener.requests[1]["url"], S.HOST + "/api/2.0/sql/statements/st-1")

    def test_running_past_fifteen_minutes_is_cancelled_then_raises(self):
        client, opener, clock = make([(200, in_state("RUNNING"))], repeat_last=True)
        with self.assertRaises(dbsql.StatementTimeout):
            client.execute(SQL)
        self.assertEqual(sum(clock.sleeps), 900)
        last = opener.requests[-1]
        self.assertEqual(last["method"], "POST")
        self.assertEqual(last["url"], S.HOST + "/api/2.0/sql/statements/st-1/cancel")

    def test_a_timeout_is_not_retried(self):
        client, opener, _ = make([(200, in_state("RUNNING"))], repeat_last=True)
        with self.assertRaises(dbsql.StatementTimeout):
            client.write(SQL)
        self.assertEqual(sum(1 for r in opener.requests if r["url"].endswith("/statements")), 1)


class Failures(unittest.TestCase):
    def test_failed_statement_carries_code_and_message(self):
        client, _, _ = make([(200, failed("PARSE_SYNTAX_ERROR", "bad sql"))])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        self.assertEqual(caught.exception.error_code, "PARSE_SYNTAX_ERROR")
        self.assertEqual(caught.exception.message, "bad sql")
        self.assertFalse(caught.exception.retryable)

    def test_concurrent_in_the_code_is_retryable(self):
        client, _, _ = make([(200, failed("DELTA_CONCURRENT_APPEND", "x"))])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        self.assertTrue(caught.exception.retryable)

    def test_concurrent_in_the_message_is_retryable(self):
        client, _, _ = make([(200, failed("FAILED", "io.delta.exceptions.ConcurrentAppendException: files were added"))])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        self.assertTrue(caught.exception.retryable)

    def test_canceled_and_closed_states_raise(self):
        for state in ("CANCELED", "CLOSED"):
            with self.subTest(state=state):
                client, _, _ = make([(200, in_state(state))])
                with self.assertRaises(dbsql.StatementFailed):
                    client.execute(SQL)

    def test_429_is_transient(self):
        client, _, _ = make([(429, "slow down")])
        with self.assertRaises(dbsql.TransientHTTPError):
            client.execute(SQL)

    def test_503_is_transient(self):
        client, _, _ = make([(503, "unavailable")])
        with self.assertRaises(dbsql.TransientHTTPError):
            client.execute(SQL)

    def test_400_is_not_retryable(self):
        client, _, _ = make([(400, {"message": "bad request"})])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        self.assertFalse(caught.exception.retryable)

    def test_a_400_carries_the_error_code_and_message_of_the_body(self):
        body = {"error_code": "BAD_REQUEST", "message": "The request could not be processed by the warehouse."}
        client, _, _ = make([(400, body)])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        self.assertIn("HTTP_400", str(caught.exception))
        self.assertIn("BAD_REQUEST", str(caught.exception))
        self.assertIn("The request could not be processed by the warehouse.", str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_a_transient_error_carries_the_detail_too(self):
        client, _, _ = make([(503, {"error_code": "TEMPORARILY_UNAVAILABLE", "message": "try later"})])
        with self.assertRaises(dbsql.TransientHTTPError) as caught:
            client.execute(SQL)
        self.assertIn("TEMPORARILY_UNAVAILABLE", str(caught.exception))

    def test_urls_and_the_host_in_the_body_are_replaced(self):
        message = f"see https://docs.example.invalid/err?q=1 and {S.HOST}/api/2.0/x on example.invalid"
        client, _, _ = make([(400, {"error_code": "BAD_REQUEST", "message": message})])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        text = str(caught.exception)
        self.assertIn("<url>", text)
        self.assertIn("<host>", text)
        self.assertNotIn("example.invalid", text)
        self.assertNotIn("http", text.replace("HTTP", ""))

    def test_a_long_message_is_cut_to_300_characters(self):
        client, _, _ = make([(400, {"error_code": "X", "message": "m" * 1000})])
        with self.assertRaises(dbsql.StatementFailed) as caught:
            client.execute(SQL)
        self.assertLessEqual(len(caught.exception.message), len("HTTP 400: ") + 300)
        self.assertGreater(len(caught.exception.message), 100)

    def test_a_non_json_body_gives_the_status_only(self):
        for body in ("<html>nope</html>", "[1, 2]", {"other": "key"}):
            client, _, _ = make([(400, body)])
            with self.assertRaises(dbsql.StatementFailed) as caught:
                client.execute(SQL)
            self.assertEqual(str(caught.exception), "HTTP_400: HTTP 400", body)

    def test_a_transport_error_is_transient(self):
        client, _, _ = make([S.url_error()])
        with self.assertRaises(dbsql.TransientHTTPError):
            client.execute(SQL)

    def test_a_non_json_body_is_not_retryable(self):
        client, _, _ = make([(200, "<html>")])
        with self.assertRaises(dbsql.StatementFailed):
            client.execute(SQL)

    def test_no_error_message_contains_the_token_or_the_request_body(self):
        cases = [[(200, failed("PARSE_SYNTAX_ERROR", "bad sql"))], [(503, "x")], [(400, "x")], [S.url_error()], [(200, "<html>")]]
        for script in cases:
            with self.subTest(script=str(script)[:40]):
                client, _, _ = make(script)
                with self.assertRaises(dbsql.StatementError) as caught:
                    client.execute("SELECT :secret_marker", {"secret_marker": ("value-NOT-IN-ERRORS", "STRING")})
                self.assertNotIn(S.TOKEN, str(caught.exception))
                self.assertNotIn("value-NOT-IN-ERRORS", str(caught.exception))
                self.assertNotIn("secret_marker", str(caught.exception))


class Retries(unittest.TestCase):
    CONCURRENT = (200, failed("DELTA_CONCURRENT_APPEND", "ConcurrentAppendException"))
    OK = (200, succeeded([("n", "INT")], [["1"]]))

    def test_write_gives_up_after_three_attempts_with_5_then_15_second_waits(self):
        client, opener, clock = make([self.CONCURRENT] * 3)
        with self.assertRaises(dbsql.StatementFailed):
            client.write(SQL)
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(clock.sleeps, [5, 15])

    def test_write_succeeds_on_the_second_attempt(self):
        client, opener, clock = make([self.CONCURRENT, self.OK])
        self.assertEqual(client.write(SQL), [{"n": 1}])
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(clock.sleeps, [5])

    def test_write_succeeds_on_the_third_attempt(self):
        client, opener, clock = make([self.CONCURRENT, self.CONCURRENT, self.OK])
        client.write(SQL)
        self.assertEqual(clock.sleeps, [5, 15])

    def test_a_syntax_error_is_not_retried(self):
        client, opener, clock = make([(200, failed("PARSE_SYNTAX_ERROR", "bad sql"))])
        with self.assertRaises(dbsql.StatementFailed):
            client.write(SQL)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(clock.sleeps, [])

    def test_read_gives_up_after_two_attempts_with_one_5_second_wait(self):
        client, opener, clock = make([(503, "x"), (503, "x")])
        with self.assertRaises(dbsql.TransientHTTPError):
            client.read(SQL)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(clock.sleeps, [5])

    def test_read_recovers_from_a_transient_error(self):
        client, _, clock = make([(503, "x"), self.OK])
        self.assertEqual(client.read(SQL), [{"n": 1}])
        self.assertEqual(clock.sleeps, [5])

    def test_read_does_not_retry_a_non_retryable_error(self):
        client, opener, _ = make([(400, "x")])
        with self.assertRaises(dbsql.StatementFailed):
            client.read(SQL)
        self.assertEqual(len(opener.requests), 1)

    def test_a_transport_error_is_retried(self):
        client, opener, _ = make([S.url_error(), self.OK])
        client.write(SQL)
        self.assertEqual(len(opener.requests), 2)


if __name__ == "__main__":
    unittest.main()
