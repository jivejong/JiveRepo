"""ingest/workspace_check.py against a stub workspace: what it asks for, that it always cleans up, that it stops
without a token, that a too-broad grant fails it, and that no token, secret, host or client id reaches the
output. Offline."""
import functools
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _support as S  # noqa: E402
import files_api  # noqa: E402
import workspace_check  # noqa: E402
from files_api import FilesApiUploader, TokenProvider  # noqa: E402

HOST = "dbc-secret-workspace-12345.cloud.example.invalid"
CLIENT_ID, CLIENT_SECRET, TOKEN = "11111111-2222-3333-4444-555555555555", "s3cr3t-value-XYZ", "TOKEN-VALUE-ABC123"
VOLUME = "/Volumes/force/raw/telemetry"


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StubWorkspace:
    """Routes each request to a handler(method, path, query, body) -> (status, body) | Exception, and records it."""

    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def __call__(self, request, timeout=None):
        url = urlparse(request.full_url)
        self.requests.append(request)
        result = self.handler(request.get_method(), url.path, url.query, request.data)
        if isinstance(result, Exception):
            raise result
        status, body = result
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(body))
        return FakeResponse(status, body)

    def calls(self):
        return [(r.get_method(), urlparse(r.full_url).path) for r in self.requests]


def token_reply(scope="files", **over):
    body = {"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600, "scope": scope}
    body.update(over)
    return 200, json.dumps({k: v for k, v in body.items() if v is not None}).encode()


def well_behaved(scope="files", second_put=(409, b'{"error_code":"ALREADY_EXISTS","message":"file exists"}'),
                 checkpoints=(403, b'{"error_code":"PERMISSION_DENIED","message":"denied"}'),
                 sql=(403, b'{"error_code":"PERMISSION_DENIED","message":"insufficient scope"}')):
    state = {"puts": 0, "dir_has_check": False}

    def handler(method, path, query, body):
        if path == "/oidc/v1/token":
            return token_reply(scope)
        if path.startswith("/api/2.0/fs/directories/Volumes/force/raw/checkpoints"):
            return checkpoints
        if path == "/api/2.0/sql/warehouses":
            return sql
        if method == "GET" and path == f"/api/2.0/fs/directories{VOLUME}":
            contents = [{"name": "_connectivity_check", "is_directory": True}] if state["dir_has_check"] else []
            return 200, json.dumps({"contents": contents}).encode()
        if method == "PUT" and "/_connectivity_check/" in path:
            state["puts"] += 1
            if state["puts"] == 1:
                state["dir_has_check"] = True
                return 204, b""
            return second_put
        if method == "HEAD":
            return 200, b""
        if method == "DELETE" and path.endswith("/_connectivity_check"):
            state["dir_has_check"] = False
            return 204, b""
        if method == "DELETE":
            return 204, b""
        return 404, b"{}"
    return handler


def run(handler, scope="files"):
    workspace = StubWorkspace(handler)
    http = functools.partial(files_api.http_request, opener=workspace)
    tokens = TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, scope=scope, http=http, clock=S.Clock(1_000_000.0).now)
    out = io.StringIO()
    failures = workspace_check.run_checks(tokens, tokens.host, VOLUME, http, out=out, now=lambda: 1_788_000_000.0)
    return failures, out.getvalue(), workspace


class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self.failures, self.out, self.ws = run(well_behaved())

    def test_everything_behaves_as_expected(self):
        self.assertEqual(self.failures, [], self.out)

    def test_the_granted_scope_and_token_metadata_are_reported(self):
        self.assertIn("requested scope: 'files'", self.out)
        self.assertIn("granted scope in the response: 'files'", self.out)
        self.assertIn("token_type='Bearer' expires_in=3600s", self.out)
        self.assertIn("response fields: access_token, expires_in, scope, token_type", self.out)

    def test_the_calls_in_order_and_nothing_extra(self):
        methods_paths = self.ws.calls()
        self.assertEqual([m for m, _ in methods_paths],
                         ["POST", "GET", "PUT", "PUT", "HEAD", "DELETE", "DELETE", "GET", "GET", "GET"])
        self.assertEqual(methods_paths[0], ("POST", "/oidc/v1/token"))
        self.assertTrue(methods_paths[1][1].endswith(VOLUME))
        self.assertTrue(methods_paths[-2][1].endswith("/checkpoints"))
        self.assertEqual(methods_paths[-1], ("GET", "/api/2.0/sql/warehouses"))

    def test_uploads_never_overwrite_and_use_the_underscore_directory(self):
        puts = [r for r in self.ws.requests if r.get_method() == "PUT"]
        self.assertEqual(len(puts), 2)
        for r in puts:
            self.assertTrue(r.full_url.endswith("?overwrite=false"), r.full_url)
            self.assertIn("/_connectivity_check/force-bridge-check-", r.full_url)
        self.assertEqual(puts[0].full_url, puts[1].full_url)

    def test_the_already_exists_status_is_recorded_for_doc_05(self):
        self.assertIn("-> 409 (ALREADY_EXISTS; file exists)", self.out)
        self.assertIn("the status the bridge must treat as 'already exists'", self.out)

    def test_cleanup_deletes_the_file_then_the_directory_and_confirms_nothing_is_left(self):
        deletes = [p for m, p in self.ws.calls() if m == "DELETE"]
        self.assertTrue(deletes[0].endswith(".txt"))
        self.assertTrue(deletes[1].endswith("/_connectivity_check"))
        self.assertIn("_connectivity_check is not left behind in the volume", self.out)

    def test_every_request_carries_the_ua_and_the_bearer_token(self):
        for r in self.ws.requests:
            self.assertEqual(r.get_header("User-agent"), files_api.UA)
            if urlparse(r.full_url).path != "/oidc/v1/token":
                self.assertEqual(r.get_header("Authorization"), f"Bearer {TOKEN}")

    def test_no_token_secret_host_or_client_id_in_the_output(self):
        for secret in (TOKEN, CLIENT_SECRET, HOST, CLIENT_ID, "dbc-secret-workspace"):
            self.assertNotIn(secret, self.out)


class TokenTests(unittest.TestCase):
    def test_a_response_without_a_scope_field_is_reported_as_such(self):
        def handler(method, path, query, body):
            return token_reply(scope=None) if path == "/oidc/v1/token" else well_behaved()(method, path, query, body)
        failures, out, _ = run(handler)
        self.assertEqual(failures, [])
        self.assertIn("granted scope: the response has no 'scope' field", out)

    def test_a_granted_scope_that_differs_from_the_request_is_flagged(self):
        failures, out, _ = run(well_behaved(scope="files sql"))
        self.assertIn("granted differs from requested: requested 'files', granted 'files sql'", out)

    def test_a_rejected_token_request_stops_everything_else(self):
        def handler(method, path, query, body):
            return 400, json.dumps({"error": "invalid_scope", "error_description": f"scope not allowed for {CLIENT_ID}"}).encode()
        failures, out, ws = run(handler)
        self.assertEqual(len(ws.requests), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("invalid_scope", out)
        self.assertIn("stopping: without a token", out)
        for secret in (CLIENT_SECRET, CLIENT_ID, HOST):
            self.assertNotIn(secret, out)

    def test_the_requested_scope_is_the_one_configured(self):
        failures, out, ws = run(well_behaved(scope="all-apis"), scope="all-apis")
        self.assertIn("requested scope: 'all-apis'", out)
        self.assertIn(b"scope=all-apis", ws.requests[0].data)


class CleanupTests(unittest.TestCase):
    def test_cleanup_runs_when_a_later_check_fails(self):
        base = well_behaved()
        seen = {"puts": 0}

        def handler(method, path, query, body):
            if method == "PUT":
                seen["puts"] += 1
                if seen["puts"] == 2:
                    return urllib.error.URLError("connection reset")
            return base(method, path, query, body)
        failures, out, ws = run(handler)
        self.assertTrue(failures)
        self.assertEqual([m for m, _ in ws.calls()].count("DELETE"), 2, "the file and the directory are still deleted")

    def test_cleanup_runs_even_when_something_unexpected_blows_up_mid_run(self):
        """An exception that is not a network error (a bug, an interrupt) must not leave the test file behind."""
        base = well_behaved()
        seen = {"puts": 0}

        def handler(method, path, query, body):
            if method == "PUT":
                seen["puts"] += 1
                if seen["puts"] == 2:
                    raise RuntimeError("unexpected")
            return base(method, path, query, body)
        workspace = StubWorkspace(handler)
        http = functools.partial(files_api.http_request, opener=workspace)
        tokens = TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, http=http, clock=S.Clock(1_000_000.0).now)
        with self.assertRaises(RuntimeError):
            workspace_check.run_checks(tokens, tokens.host, VOLUME, http, out=io.StringIO(), now=lambda: 1_788_000_000.0)
        self.assertEqual([m for m, _ in workspace.calls()].count("DELETE"), 2, "the file and the directory are still deleted")

    def test_cleanup_runs_when_the_metadata_check_fails(self):
        base = well_behaved()

        def handler(method, path, query, body):
            return (500, b"{}") if method == "HEAD" else base(method, path, query, body)
        failures, out, ws = run(handler)
        self.assertTrue(failures)
        self.assertEqual([m for m, _ in ws.calls()].count("DELETE"), 2)

    def test_nothing_is_deleted_when_nothing_was_uploaded(self):
        base = well_behaved()

        def handler(method, path, query, body):
            return (403, b'{"error_code":"PERMISSION_DENIED","message":"no write"}') if method == "PUT" else base(method, path, query, body)
        failures, out, ws = run(handler)
        self.assertTrue(any("PUT" in f for f in failures))
        self.assertNotIn("DELETE", [m for m, _ in ws.calls()])
        self.assertNotIn("HEAD", [m for m, _ in ws.calls()])

    def test_a_directory_left_behind_is_a_failure(self):
        base = well_behaved()

        def handler(method, path, query, body):
            if method == "DELETE" and path.endswith("/_connectivity_check"):
                return 204, b""  # says deleted, but the listing below still shows it
            if method == "GET" and path == f"/api/2.0/fs/directories{VOLUME}":
                return 200, json.dumps({"contents": [{"name": "_connectivity_check"}]}).encode()
            return base(method, path, query, body)
        failures, out, ws = run(handler)
        self.assertTrue(any("not left behind" in f for f in failures))


class NarrowGrantTests(unittest.TestCase):
    def test_a_readable_checkpoints_volume_fails_the_check(self):
        failures, out, _ = run(well_behaved(checkpoints=(200, b'{"contents": []}')))
        self.assertTrue(failures)
        self.assertIn("grant is broader than doc 05 allows", out)

    def test_a_404_on_checkpoints_counts_as_denied(self):
        failures, out, _ = run(well_behaved(checkpoints=(404, b"{}")))
        self.assertEqual(failures, [])

    def test_a_non_files_api_that_answers_is_information_not_a_failure(self):
        failures, out, _ = run(well_behaved(sql=(200, b'{"warehouses": []}')))
        self.assertEqual(failures, [])
        self.assertIn("this secret is not scoped to files only", out)

    def test_a_denied_non_files_api_is_reported_as_expected(self):
        failures, out, _ = run(well_behaved())
        self.assertIn("denied, as expected for a files-only scope", out)


class RedactionTests(unittest.TestCase):
    def test_error_text_that_echoes_identifiers_is_redacted(self):
        leaky = (403, json.dumps({"error_code": "PERMISSION_DENIED", "message": f"{CLIENT_ID} on {HOST} with {CLIENT_SECRET}"}).encode())
        failures, out, _ = run(well_behaved(checkpoints=leaky, sql=leaky))
        for secret in (CLIENT_ID, HOST, CLIENT_SECRET):
            self.assertNotIn(secret, out)
        self.assertIn("<redacted>", out)

    def test_local_dir_mode_is_refused(self):
        with self.assertRaises(SystemExit):
            workspace_check.main(["--local-dir", "x"])


if __name__ == "__main__":
    unittest.main()
