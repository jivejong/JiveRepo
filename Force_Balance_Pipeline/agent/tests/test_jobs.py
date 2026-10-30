"""agent/jobs.py: the Job 1 activity check against a fake opener."""
import unittest

import _support as S
from _support import FakeOpener
from agent import jobs


def check(script):
    opener = FakeOpener(script)
    return jobs.job1_active(S.HOST, S.TOKEN, opener=opener), opener


class Job1Active(unittest.TestCase):
    def test_a_listed_run_means_running(self):
        result, _ = check([(200, {"runs": [{"run_id": 1}]})])
        self.assertEqual(result, (True, "job1_running"))

    def test_an_empty_answer_means_idle(self):
        result, _ = check([(200, {})])
        self.assertEqual(result, (False, "idle"))

    def test_an_empty_runs_list_means_idle(self):
        result, _ = check([(200, {"runs": []})])
        self.assertEqual(result, (False, "idle"))

    def test_http_error_skips_the_cycle(self):
        result, _ = check([(500, "boom")])
        self.assertEqual(result, (True, "jobs_api_error"))

    def test_unauthorised_skips_the_cycle(self):
        result, _ = check([(401, "no")])
        self.assertEqual(result, (True, "jobs_api_error"))

    def test_malformed_json_skips_the_cycle(self):
        result, _ = check([(200, "<html>")])
        self.assertEqual(result, (True, "jobs_api_error"))

    def test_a_json_list_skips_the_cycle(self):
        result, _ = check([(200, "[1]")])
        self.assertEqual(result, (True, "jobs_api_error"))

    def test_a_transport_error_skips_the_cycle(self):
        result, _ = check([S.url_error()])
        self.assertEqual(result, (True, "jobs_api_error"))

    def test_url_names_job_1_and_active_only(self):
        _, opener = check([(200, {})])
        request = opener.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertIn("job_id=692051683030737", request["url"])
        self.assertIn("active_only=true", request["url"])
        self.assertTrue(request["url"].startswith(S.HOST + "/api/2.1/jobs/runs/list?"))

    def test_bearer_header_and_user_agent(self):
        _, opener = check([(200, {})])
        self.assertEqual(opener.requests[0]["headers"]["authorization"], "Bearer " + S.TOKEN)
        self.assertIn("user-agent", opener.requests[0]["headers"])


class SharedLookup(unittest.TestCase):
    def test_serving_re_exports_the_same_lookup(self):
        from serving import jobs as serving_jobs
        self.assertIs(serving_jobs.latest_successful_run, jobs.latest_successful_run)
        self.assertIs(serving_jobs.JobsApiError, jobs.JobsApiError)


if __name__ == "__main__":
    unittest.main()
