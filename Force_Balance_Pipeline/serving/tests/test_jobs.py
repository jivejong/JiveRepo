"""Tests for serving/jobs.py (doc 05 "Serving (Phase 7)": the gate's Jobs API lookup), against a fake transport. No network."""
import unittest
from datetime import datetime, timezone

from fake_http import FakeOpener
from serving import jobs

ENV = {"DATABRICKS_HOST": "example.invalid", "DATABRICKS_TOKEN": "t"}


def run(run_id, end_ms, result="SUCCESS", shape="state"):
    r = {"run_id": run_id, "start_time": end_ms - 1000, "end_time": end_ms}
    if shape == "state":
        r["state"] = {"life_cycle_state": "TERMINATED", "result_state": result}
    else:
        r["status"] = {"state": "TERMINATED", "termination_details": {"code": result}}
    return r


def latest(payload, status=200):
    opener = FakeOpener(lambda m, u, b: (status, payload))
    return jobs.latest_successful_run(ENV, 692051683030737, opener=opener), opener


class Lookup(unittest.TestCase):
    def test_newest_success_wins_and_failed_is_ignored(self):
        got, opener = latest({"runs": [run(5, 5000, "FAILED"), run(4, 4000), run(3, 3000), run(6, 6000, "FAILED")]})
        self.assertEqual(got, (4, datetime.fromtimestamp(4, tz=timezone.utc)))
        self.assertIn("completed_only=true", opener.calls[0][1])
        self.assertIn("limit=25", opener.calls[0][1])

    def test_order_in_the_answer_does_not_matter(self):
        got, _ = latest({"runs": [run(1, 1000), run(2, 9000), run(3, 5000)]})
        self.assertEqual(got[0], 2)

    def test_the_status_only_shape(self):
        got, _ = latest({"runs": [run(7, 7000, "FAILED", "status"), run(8, 8000, "SUCCESS", "status")]})
        self.assertEqual(got[0], 8)

    def test_none_when_nothing_succeeded(self):
        got, _ = latest({"runs": [run(1, 1000, "FAILED"), run(2, 2000, "CANCELED")]})
        self.assertIsNone(got)

    def test_an_object_without_runs_is_none(self):
        try:
            got, _ = latest({"has_more": False})
        except jobs.JobsApiError:
            self.fail("an answer without runs must give None")
        self.assertIsNone(got)

    def test_bad_answers_raise(self):
        for status, payload in ((500, {"runs": []}), (200, b"not json"), (200, [1]), (200, {"runs": "x"})):
            with self.assertRaises(jobs.JobsApiError, msg=str((status, payload))):
                latest(payload, status)

    def test_the_error_message_carries_no_host(self):
        with self.assertRaises(jobs.JobsApiError) as caught:
            latest({"runs": []}, 500)
        self.assertNotIn("example.invalid", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
