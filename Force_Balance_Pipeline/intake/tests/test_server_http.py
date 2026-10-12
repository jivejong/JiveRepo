"""The HTTP layer, over real sockets on 127.0.0.1 with the model and the broker stubbed: routes, the host / origin / content-type
checks that keep a web page in the user's browser from using this local service, size limits, headers, and what reaches the log."""
import json
import logging
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import _support as S
import intake_server as srv
from _support import CountingModel, FakePublisher, KEY, MODEL, core, report_body, report_ids

SECTORS = core.load_sectors()


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def raw_request(port, method="GET", path="/", headers=None, body=b"", host="default", send_length=True, half_close=False):
    """One HTTP/1.0-style request over a plain socket. `host`: "default" -> 127.0.0.1:port, None -> no Host header, else verbatim.
    Returns (status, {lower-case header: value}, body bytes)."""
    sent = {}
    if host == "default":
        sent["Host"] = f"127.0.0.1:{port}"
    elif host is not None:
        sent["Host"] = host
    if send_length and (body or method == "POST"):
        sent["Content-Length"] = str(len(body))
    sent.update(headers or {})
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in sent.items() if v is not None) + "\r\n"
    chunks = []
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        try:
            sock.sendall(head.encode("latin-1") + body)
            if half_close:
                sock.shutdown(socket.SHUT_WR)
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        except (socket.timeout, ConnectionError):
            pass                                     # no (complete) answer: reported as status 0, so the caller's assertion FAILS
    blob = b"".join(chunks)
    if not blob.startswith(b"HTTP/"):
        return 0, {}, b""
    head_bytes, _, payload = blob.partition(b"\r\n\r\n")
    lines = head_bytes.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    return status, {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}, payload


def code_of(payload):
    """The error code in a JSON answer, or None if the answer is not that."""
    try:
        return json.loads(payload).get("error")
    except (ValueError, AttributeError):
        return None


def post_report(port, body, **kwargs):
    headers = {"Content-Type": "application/json"}
    headers.update(kwargs.pop("headers", {}))
    return raw_request(port, "POST", "/api/report", headers, body, **kwargs)


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logger = logging.getLogger(f"test-intake-{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.records = ListHandler()
        self.logger.addHandler(self.records)
        self.publisher, self.model = FakePublisher(), CountingModel()
        self.service = srv.IntakeService(sectors=SECTORS, api_key=KEY, model=MODEL, publisher=self.publisher,
                                         report_log=srv.ReportLog(Path(self.tmp.name) / "r.ndjson"), http=self.model,
                                         sleep=lambda _: None, log=self.logger.info, **self.options())
        self.server = srv.make_server(self.service, 0, self.logger)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def options(self):
        return {}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class RoutesTests(ServerCase):
    def test_the_server_listens_on_127_0_0_1_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(srv.BIND_HOST, "127.0.0.1")

    def test_the_port_cannot_be_shared_and_handler_threads_do_not_keep_the_process_alive(self):
        self.assertIs(srv.IntakeHTTPServer.allow_reuse_address, False)         # on Windows SO_REUSEADDR lets a second process share a port
        self.assertIs(srv.IntakeHTTPServer.daemon_threads, True)

    def test_a_report_is_filed_over_http(self):
        status, headers, payload = post_report(self.port, report_body(1))
        self.assertEqual(status, 200)
        answer = json.loads(payload)
        self.assertEqual(answer["event_id"], core.event_id_for(report_ids(1)))
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(len(self.publisher.attempts), 1)

    def test_a_charset_on_the_content_type_is_fine(self):
        status, _, _ = post_report(self.port, report_body(1), headers={"Content-Type": "Application/JSON; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_sectors_lists_the_60_planets(self):
        status, headers, payload = raw_request(self.port, "GET", "/api/sectors")
        rows = json.loads(payload)
        self.assertEqual((status, len(rows)), (200, 60))
        self.assertEqual([r["sector_name"].lower() for r in rows], sorted(r["sector_name"].lower() for r in rows))
        self.assertTrue(all(set(r) == {"sector_id", "sector_name"} for r in rows))

    def test_the_root_serves_a_placeholder_page_with_a_locked_down_policy(self):
        status, headers, payload = raw_request(self.port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertIn(b"POST /api/report", payload)
        self.assertNotIn(b"<script", payload)

    def test_a_query_string_does_not_change_the_route(self):
        self.assertEqual(raw_request(self.port, "GET", "/api/sectors?x=1")[0], 200)
        self.assertEqual(raw_request(self.port, "GET", "/?x=1")[0], 200)

    def test_unknown_paths_are_404_and_wrong_methods_405_with_allow(self):
        self.assertEqual(raw_request(self.port, "GET", "/nope")[0], 404)
        self.assertEqual(raw_request(self.port, "GET", "/api/reports")[0], 404)
        for method, path, allow in (("GET", "/api/report", "POST"), ("POST", "/api/sectors", "GET"), ("POST", "/", "GET"),
                                    ("PUT", "/api/report", "POST"), ("DELETE", "/api/report", "POST"), ("OPTIONS", "/api/report", "POST"),
                                    ("PATCH", "/api/sectors", "GET"), ("HEAD", "/", "GET")):
            with self.subTest(method=method, path=path):
                status, headers, _ = raw_request(self.port, method, path)
                self.assertEqual((status, headers["allow"]), (405, allow))

    def test_errors_are_json_with_an_error_code_and_a_message(self):
        status, headers, payload = post_report(self.port, report_body(1, description=""))
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(payload), {"error": "empty_description", "message": "the description is empty"})

    def test_every_answer_is_uncacheable_and_has_no_cors_headers_and_no_python_version(self):
        answers = [raw_request(self.port, "GET", "/"), raw_request(self.port, "GET", "/api/sectors"), raw_request(self.port, "GET", "/nope"),
                   post_report(self.port, report_body(1)), post_report(self.port, b"{"), raw_request(self.port, "OPTIONS", "/api/report")]
        for status, headers, _ in answers:
            self.assertEqual(headers.get("cache-control"), "no-store")
            self.assertEqual(headers.get("x-content-type-options"), "nosniff")
            self.assertFalse([h for h in headers if h.startswith("access-control-")], headers)
            self.assertEqual(headers.get("server"), "ForceIntake/0.1")

    def test_retry_after_travels_as_a_header(self):
        self.publisher.error = srv.intake_mqtt.PublishError("timeout", "x")
        status, headers, payload = post_report(self.port, report_body(1))
        self.assertEqual((status, headers["retry-after"], code_of(payload)), (503, "15", "publish_timeout"))


class BrowserSafetyTests(ServerCase):
    """A page in the user's browser can reach 127.0.0.1; these checks stop it using the service."""

    def test_a_request_with_a_foreign_or_missing_host_is_refused(self):
        for host in ("evil.example", "evil.example:8080", f"127.0.0.1", f"192.168.0.5:{self.port}", f"localhost:{self.port + 1}", None):
            with self.subTest(host=host):
                self.assertEqual(raw_request(self.port, "GET", "/api/sectors", host=host)[0], 403)
                self.assertEqual(post_report(self.port, report_body(1), host=host)[0], 403)
        self.assertEqual(self.publisher.attempts, [])

    def test_localhost_by_name_is_accepted(self):
        self.assertEqual(raw_request(self.port, "GET", "/api/sectors", host=f"localhost:{self.port}")[0], 200)
        self.assertEqual(raw_request(self.port, "GET", "/api/sectors", host=f"LOCALHOST:{self.port}")[0], 200)

    def test_a_foreign_origin_is_refused_and_the_report_is_not_filed(self):
        for origin in ("http://evil.example", "https://127.0.0.1:%d" % self.port, "null", f"http://127.0.0.1:{self.port + 1}", ""):
            with self.subTest(origin=origin):
                status, _, payload = post_report(self.port, report_body(1), headers={"Origin": origin})
                self.assertEqual((status, code_of(payload)), (403, "bad_origin"))
        self.assertEqual((self.model.calls, self.publisher.attempts), (0, []))

    def test_the_services_own_origin_is_accepted(self):
        for origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            with self.subTest(origin=origin):
                status, _, _ = post_report(self.port, report_body(len(origin)), headers={"Origin": origin})
                self.assertEqual(status, 200)

    def test_only_json_is_accepted_so_a_cross_site_form_or_text_post_cannot_file_a_report(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "application/jsonp", "", None):
            with self.subTest(content_type=content_type):
                status, _, payload = post_report(self.port, report_body(1), headers={"Content-Type": content_type})
                self.assertEqual((status, code_of(payload)), (415, "unsupported_media_type"))
        self.assertEqual(self.model.calls, 0)

    def test_the_service_never_answers_a_cors_preflight(self):
        status, headers, _ = raw_request(self.port, "OPTIONS", "/api/report", {"Origin": "http://evil.example",
                                                                               "Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 405)
        self.assertFalse([h for h in headers if h.startswith("access-control-")])


class BodyTests(ServerCase):
    def test_a_missing_or_chunked_length_is_411(self):
        self.assertEqual(post_report(self.port, b"", send_length=False)[0], 411)
        status, _, payload = post_report(self.port, b"", send_length=False, headers={"Transfer-Encoding": "chunked"})
        self.assertEqual((status, code_of(payload)), (411, "length_required"))
        # chunked even with a Content-Length and a perfectly good body: still refused
        status, _, payload = post_report(self.port, report_body(1), headers={"Transfer-Encoding": "chunked"})
        self.assertEqual((status, code_of(payload)), (411, "length_required"))
        self.assertEqual(self.publisher.attempts, [])

    def test_a_malformed_length_is_400(self):
        for value in ("abc", "-1", "1.5", "\u00b2", " ", "0x10"):
            with self.subTest(value=value):
                self.assertEqual(post_report(self.port, b"", send_length=False, headers={"Content-Length": value})[0], 400)

    def test_an_oversize_body_is_413_without_being_read(self):
        status, _, payload = post_report(self.port, b"", send_length=False, headers={"Content-Length": str(core.MAX_BODY_BYTES + 1)})
        self.assertEqual((status, code_of(payload)), (413, "body_too_large"))
        status, _, _ = post_report(self.port, report_body(1))
        self.assertEqual(status, 200)

    def test_a_body_at_the_limit_is_read_and_judged_by_the_core(self):
        status, _, payload = post_report(self.port, b"x" * core.MAX_BODY_BYTES)
        self.assertEqual((status, code_of(payload)), (400, "not_json"))

    def test_a_body_that_ends_early_is_400(self):
        status, _, payload = post_report(self.port, b"{}", send_length=False, headers={"Content-Length": "100"}, half_close=True)
        self.assertEqual((status, code_of(payload)), (400, "short_body"))

    def test_a_stalled_client_is_dropped_after_the_request_timeout(self):
        self.assertEqual(srv.REQUEST_TIMEOUT_S, 30)
        self.assertEqual(srv.make_handler(self.service, self.logger).timeout, 30)


class RobustnessTests(ServerCase):
    def test_an_unexpected_error_is_a_500_that_reveals_nothing_and_is_logged(self):
        def explode(body):
            raise RuntimeError("SECRET-DETAIL")
        self.service.handle_report = explode
        status, _, payload = post_report(self.port, report_body(1))
        self.assertEqual((status, code_of(payload)), (500, "internal_error"))
        self.assertNotIn(b"SECRET-DETAIL", payload)
        self.assertTrue(any("unexpected error handling a report" in line for line in self.records.lines))
        self.assertEqual(raw_request(self.port, "GET", "/api/sectors")[0], 200)                 # and the server carries on

    def test_many_reports_at_once_are_all_filed(self):
        results = []
        threads = [threading.Thread(target=lambda n=n: results.append(post_report(self.port, report_body(n, reporter=f"rep{n:02d}"))[0]))
                   for n in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(results, [200] * 8)
        self.assertEqual((self.model.calls, len(self.publisher.attempts)), (8, 8))

    def test_the_log_never_holds_a_description_a_body_or_the_key(self):
        post_report(self.port, report_body(1, description="Saw a figure in black robes, SECRET-TEXT"))
        post_report(self.port, report_body(1, description="Saw a figure in black robes, SECRET-TEXT"))
        post_report(self.port, report_body(2, description=""))
        raw_request(self.port, "GET", "/nope?token=SECRET-QUERY")
        text = "\n".join(self.records.lines)
        self.assertIn("POST /api/report -> 200", text)
        self.assertIn("published", text)
        for secret in (KEY, "SECRET-TEXT", "black robes", "SECRET-QUERY", "web-a3f2 "):
            self.assertNotIn(secret, text)


class CapsOverHttpTests(ServerCase):
    def options(self):
        return {"per_reporter_cap": 1}

    def test_a_reporter_over_the_cap_gets_429_with_retry_after(self):
        self.assertEqual(post_report(self.port, report_body(1))[0], 200)
        status, headers, payload = post_report(self.port, report_body(2))
        self.assertEqual((status, code_of(payload)), (429, "reporter_daily_cap"))
        self.assertGreater(int(headers["retry-after"]), 0)


if __name__ == "__main__":
    unittest.main()
