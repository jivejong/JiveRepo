"""The Files API client and OAuth M2M token provider (doc 05), against stubs: User-Agent on every
request, token caching and refresh before expiry, 401 handling, overwrite=false, and that no secret leaks.
Nothing here touches the network."""
import base64
import functools
import io
import re
import sys
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
import files_api  # noqa: E402
from files_api import AuthError, FilesApiUploader, TokenProvider, TransportError  # noqa: E402

ROOT = S.ROOT
HOST = "example-workspace.invalid"
CLIENT_ID, CLIENT_SECRET = "cid-1234", "s3cr3t-value-XYZ"


class FakeResponse:
    def __init__(self, status, body=b""):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Stands in for urllib.request.urlopen: records each Request, replies from a script."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        status, body = step
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(body))
        return FakeResponse(status, body)


def token_body(token, expires_in=3600):
    import json
    return json.dumps({"access_token": token, "token_type": "Bearer", "expires_in": expires_in}).encode()


def build(script, clock=None):
    opener = FakeOpener(script)
    http = functools.partial(files_api.http_request, opener=opener)
    clock = clock or S.Clock(1_000_000.0)
    tokens = TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, http=http, clock=clock.now)
    uploader = FilesApiUploader(HOST, "/Volumes/force/raw/telemetry", tokens, http=http)
    return opener, tokens, uploader, clock


class UserAgentTests(unittest.TestCase):
    def test_ua_is_on_every_request_token_exchange_and_put(self):
        opener, tokens, uploader, _ = build([(200, token_body("t1")), (204, b""), (204, b"")])
        uploader.put("dt=2026-08-29/hh=10/a.ndjson", b"x")
        uploader.put("dt=2026-08-29/hh=10/b.ndjson", b"y")
        self.assertEqual(len(opener.requests), 3)
        for request in opener.requests:
            self.assertEqual(request.get_header("User-agent"), files_api.UA)

    def test_ua_is_on_error_and_refresh_paths_too(self):
        opener, tokens, uploader, clock = build([(200, token_body("t1", 400)), (503, b""), (200, token_body("t2")), (204, b"")])
        uploader.put("p", b"x")
        clock.advance(200)  # past expiry - margin: refresh
        uploader.put("p", b"x")
        self.assertGreaterEqual(len(opener.requests), 4)
        for request in opener.requests:
            self.assertEqual(request.get_header("User-agent"), files_api.UA)

    def test_ua_equals_the_string_in_docs_04_and_05(self):
        for doc in ("04-edge-simulators.md", "05-platform-setup.md"):
            text = (ROOT / "docs" / doc).read_text(encoding="utf-8")
            self.assertIn(f'"{files_api.UA}"', text, doc)

    def test_callers_cannot_set_their_own_user_agent(self):
        opener = FakeOpener([(200, b"")])
        with self.assertRaises(ValueError):
            files_api.http_request("GET", "https://x.invalid/", headers={"user-agent": "spoof"}, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_http_request_sends_the_ua_even_with_other_headers(self):
        opener = FakeOpener([(200, b"ok")])
        status, body = files_api.http_request("GET", "https://x.invalid/", headers={"X-Thing": "1"}, opener=opener)
        self.assertEqual((status, body), (200, b"ok"))
        self.assertEqual(opener.requests[0].get_header("User-agent"), files_api.UA)
        self.assertEqual(opener.requests[0].get_header("X-thing"), "1")

    def test_no_other_outbound_http_path_exists_in_the_bridge_or_probe(self):
        """Only files_api.http_request may open a connection, so it is the only place UA has to be right."""
        offenders = []
        for base in (ROOT / "ingest", ROOT / "edge"):
            for path in base.rglob("*.py"):
                if "tests" in path.parts or path.name == "files_api.py" or ".venv" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8")
                if re.search(r"\burlopen\s*\(|urllib\.request\.Request\s*\(|\brequests\.(get|put|post)\b|http\.client", text):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
        source = (ROOT / "ingest" / "bridge" / "files_api.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"urllib\.request\.Request\(", source)), 1)


class TokenTests(unittest.TestCase):
    def test_exchange_request_shape(self):
        opener, tokens, _, _ = build([(200, token_body("abc"))])
        self.assertEqual(tokens.token(), "abc")
        request = opener.requests[0]
        self.assertEqual(request.full_url, f"https://{HOST}/oidc/v1/token")
        self.assertEqual(request.get_method(), "POST")
        basic = base64.b64decode(request.get_header("Authorization").split(" ", 1)[1]).decode()
        self.assertEqual(basic, f"{CLIENT_ID}:{CLIENT_SECRET}")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(parse_qs(request.data.decode()), {"grant_type": ["client_credentials"], "scope": ["files"]})

    def test_the_token_is_cached_across_many_puts(self):
        opener, tokens, uploader, _ = build([(200, token_body("t1"))] + [(204, b"")] * 5)
        for i in range(5):
            uploader.put(f"p{i}", b"x")
        self.assertEqual(tokens.exchanges, 1)
        puts = [r for r in opener.requests if r.get_method() == "PUT"]
        self.assertEqual(len(puts), 5)
        self.assertTrue(all(r.get_header("Authorization") == "Bearer t1" for r in puts))

    def test_refreshed_before_expiry_not_before(self):
        opener, tokens, _, clock = build([(200, token_body("t1", 3600)), (200, token_body("t2", 3600))])
        self.assertEqual(tokens.token(), "t1")
        clock.advance(3299)  # 301 s left: still valid
        self.assertEqual(tokens.token(), "t1")
        self.assertEqual(tokens.exchanges, 1)
        clock.advance(1)     # 300 s left: the refresh margin
        self.assertEqual(tokens.token(), "t2")
        self.assertEqual(tokens.exchanges, 2)

    def test_a_short_lived_token_is_refreshed_every_time_once_inside_the_margin(self):
        opener, tokens, _, clock = build([(200, token_body("t1", 100)), (200, token_body("t2", 100))])
        self.assertEqual(tokens.token(), "t1")   # expires in 100 s, margin 300 s: already inside the margin
        self.assertEqual(tokens.token(), "t2")

    def test_401_invalidates_the_token_and_retries_once_with_a_new_one(self):
        opener, tokens, uploader, _ = build([(200, token_body("old")), (401, b"expired"), (200, token_body("new")), (204, b"")])
        self.assertEqual(uploader.put("p", b"x"), 204)
        puts = [r for r in opener.requests if r.get_method() == "PUT"]
        self.assertEqual([r.get_header("Authorization") for r in puts], ["Bearer old", "Bearer new"])
        self.assertEqual(tokens.exchanges, 2)

    def test_a_second_401_is_returned_not_looped(self):
        opener, tokens, uploader, _ = build([(200, token_body("a")), (401, b""), (200, token_body("b")), (401, b"")])
        self.assertEqual(uploader.put("p", b"x"), 401)
        self.assertEqual(len(opener.requests), 4)

    def test_secrets_never_appear_in_errors(self):
        for script in ([(401, b'{"error":"invalid_client"}')], [(500, b"oops")], [(200, b"not json")],
                       [(200, b'{"no_token": 1}')], [urllib.error.URLError("nope")]):
            opener, tokens, _, _ = build(script)
            with self.assertRaises(AuthError) as cm:
                tokens.token()
            message = str(cm.exception)
            self.assertNotIn(CLIENT_SECRET, message)
            self.assertNotIn(CLIENT_ID, message)
            self.assertNotIn(base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode(), message)

    def test_token_failure_is_an_auth_error_not_a_crash_and_is_not_cached(self):
        opener, tokens, _, _ = build([(500, b""), (200, token_body("ok"))])
        with self.assertRaises(AuthError):
            tokens.token()
        self.assertEqual(tokens.token(), "ok")

    def test_the_bridge_treats_an_auth_failure_as_retryable_and_never_logs_the_secret(self):
        logs = []
        opener, tokens, uploader, _ = build([(500, b""), (200, token_body("ok")), (204, b"")])
        b = bridge.Bridge(uploader, dead_letter_path=Path("unused.ndjson"), clock=S.Clock().now, sleep=lambda s: None,
                          log=logs.append)
        batch = [x for m in S.scan_messages() for x in b.handle_message(m)][0]
        self.assertTrue(b.upload(batch))
        self.assertTrue(logs)
        self.assertFalse(any(CLIENT_SECRET in line for line in logs))


class PutTests(unittest.TestCase):
    def test_url_never_overwrites_and_keeps_the_hive_path(self):
        opener, _, uploader, _ = build([(200, token_body("t")), (204, b"")])
        uploader.put("dt=2026-08-29/hh=10/probe-01-01ABC.ndjson", b"x")
        put = opener.requests[1]
        self.assertEqual(put.get_method(), "PUT")
        self.assertEqual(put.full_url, f"https://{HOST}/api/2.0/fs/files/Volumes/force/raw/telemetry/"
                                       "dt=2026-08-29/hh=10/probe-01-01ABC.ndjson?overwrite=false")
        self.assertEqual(put.get_header("Content-type"), "application/octet-stream")
        self.assertEqual(put.data, b"x")

    def test_host_and_volume_path_are_normalised(self):
        for host, volume in ((f"https://{HOST}/", "/Volumes/force/raw/telemetry/"), (HOST, "Volumes/force/raw/telemetry")):
            up = FilesApiUploader(host, volume, tokens=None)
            self.assertEqual(up.url("a/b.ndjson"), f"https://{HOST}/api/2.0/fs/files/Volumes/force/raw/telemetry/a/b.ndjson?overwrite=false")

    def test_statuses_are_returned_for_the_bridge_to_interpret(self):
        opener, _, uploader, _ = build([(200, token_body("t")), (409, b"exists"), (503, b"")])
        self.assertEqual(uploader.put("p", b"x"), 409)
        self.assertEqual(uploader.put("p", b"x"), 503)

    def test_network_failures_are_transport_errors_without_the_url(self):
        opener, tokens, uploader, _ = build([(200, token_body("t")), TimeoutError("read timed out"),
                                             urllib.error.URLError("dns")])
        with self.assertRaises(TransportError) as cm:
            uploader.put("dt=2026/secret-path", b"x")
        self.assertEqual(str(cm.exception), "TimeoutError")
        with self.assertRaises(TransportError) as cm:
            uploader.put("p", b"x")
        self.assertEqual(str(cm.exception), "URLError")


class TokenScopeTests(unittest.TestCase):
    """The force-bridge secret is scoped to the Files API, so the token request asks for 'files' unless told otherwise."""

    def request_scope(self, **kw):
        opener = FakeOpener([(200, token_body("t"))])
        http = functools.partial(files_api.http_request, opener=opener)
        TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, http=http, **kw).token()
        return parse_qs(opener.requests[0].data.decode())["scope"]

    def test_the_default_scope_is_files(self):
        self.assertEqual(files_api.DEFAULT_SCOPE, "files")
        self.assertEqual(self.request_scope(), ["files"])

    def test_the_scope_is_configurable(self):
        self.assertEqual(self.request_scope(scope="all-apis"), ["all-apis"])
        self.assertEqual(self.request_scope(scope="  files   sql "), ["files sql"])  # space-separated, tidied

    def test_an_empty_scope_is_refused(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, scope=bad)

    def test_the_response_metadata_is_recorded_without_the_token(self):
        import json
        body = json.dumps({"access_token": "SECRET-TOKEN-VALUE", "token_type": "Bearer", "expires_in": 3600, "scope": "files"}).encode()
        opener, tokens, _, _ = build([(200, body)])
        tokens.token()
        self.assertEqual((tokens.granted_scope, tokens.token_type, tokens.expires_in), ("files", "Bearer", 3600.0))
        self.assertEqual(tokens.response_fields, ("access_token", "expires_in", "scope", "token_type"))
        self.assertEqual(tokens.scope, "files")
        for value in (tokens.granted_scope, tokens.token_type, tokens.response_fields):
            self.assertNotIn("SECRET-TOKEN-VALUE", repr(value))

    def test_a_response_without_a_scope_field_is_recorded_as_none(self):
        opener, tokens, _, _ = build([(200, token_body("t"))])
        tokens.token()
        self.assertIsNone(tokens.granted_scope)
        self.assertNotIn("scope", tokens.response_fields)

    def test_a_rejected_scope_is_reported_with_the_oauth_error_and_never_the_secret(self):
        import json
        body = json.dumps({"error": "invalid_scope", "error_description": "The requested scope is not allowed for this secret"}).encode()
        opener, tokens, _, _ = build([(400, body)])
        with self.assertRaises(AuthError) as cm:
            tokens.token()
        message = str(cm.exception)
        for needle in ("HTTP 400", "invalid_scope", "not allowed for this secret", "requested scope 'files'"):
            self.assertIn(needle, message)
        self.assertNotIn(CLIENT_SECRET, message)
        self.assertNotIn(CLIENT_ID, message)

    def test_oauth_error_summary_omits_what_it_cannot_parse(self):
        self.assertEqual(files_api.oauth_error_summary(b"<html>nope</html>"), "")
        self.assertEqual(files_api.oauth_error_summary(b"{}"), "")
        self.assertEqual(files_api.oauth_error_summary(b'{"error": "x"}'), ": x")


class AlreadyExistsTests(unittest.TestCase):
    """The Files API reference does not name the status for an existing path with overwrite=false, so the
    uploader reports 409 for status 409 OR an ALREADY_EXISTS error code. Unverified until the first real double PUT."""

    def test_409_and_already_exists_codes_are_reported_as_409(self):
        import json
        cases = [((409, b""), 409), ((409, b"{}"), 409),
                 ((400, json.dumps({"error_code": "ALREADY_EXISTS"}).encode()), 409),
                 ((400, json.dumps({"error_code": "RESOURCE_ALREADY_EXISTS", "message": "x"}).encode()), 409),
                 ((412, json.dumps({"error_code": "ALREADY_EXISTS"}).encode()), 409)]
        for reply, expected in cases:
            opener, tokens, uploader, _ = build([(200, token_body("t")), reply])
            with self.subTest(reply=reply):
                self.assertEqual(uploader.put("p", b"x"), expected)

    def test_other_errors_keep_their_own_status(self):
        import json
        cases = [(400, json.dumps({"error_code": "INVALID_PARAMETER_VALUE"}).encode()), (400, b"not json"), (400, b""),
                 (403, json.dumps({"error_code": "PERMISSION_DENIED"}).encode()), (404, b""), (500, b""), (503, b"")]
        for status, body in cases:
            opener, tokens, uploader, _ = build([(200, token_body("t")), (status, body)])
            with self.subTest(status=status, body=body):
                self.assertEqual(uploader.put("p", b"x"), status)

    def test_success_statuses_pass_through(self):
        opener, tokens, uploader, _ = build([(200, token_body("t")), (204, b"")])
        self.assertEqual(uploader.put("p", b"x"), 204)

    def test_already_exists_helper(self):
        self.assertTrue(files_api.already_exists(409, b""))
        self.assertFalse(files_api.already_exists(204, b""))
        self.assertFalse(files_api.already_exists(400, b'{"error_code": "PERMISSION_DENIED"}'))


if __name__ == "__main__":
    unittest.main()
