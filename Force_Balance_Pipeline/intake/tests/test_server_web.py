"""The page's side of the server: GET /, /static/*, GET /api/reports, the Content-Security-Policy, path traversal, and that the
browser code and the server agree on the numbers and patterns they share."""
import json
import re
import tempfile
import unicodedata
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import _support as S
import intake_server as srv
from _support import NOW, core, report_body, report_ids
from test_server_http import ServerCase, code_of, raw_request
from test_server_main import ENVIRON, argv, log_text, main_clean

WEB = Path(srv.__file__).resolve().parent / "web"
EXPECTED_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SERVED = sorted(p.name for p in WEB.iterdir() if p.is_file() and re.fullmatch(r"[a-z0-9_-]+\.(js|css|html)", p.name))
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8"}


def js_text(name):
    return (WEB / name).read_text(encoding="utf-8")


def js_const(name, constant):
    found = re.search(rf"export const {constant} = (.+?);", js_text(name))
    assert found, f"{constant} not found in {name}"
    return found.group(1)


class PageFilesCase(ServerCase):
    def get(self, path, **kw):
        return raw_request(self.port, "GET", path, **kw)


class PageTests(PageFilesCase):
    def test_the_root_is_index_html_byte_for_byte(self):
        status, headers, payload = self.get("/")
        self.assertEqual((status, headers.get("content-type")), (200, "text/html; charset=utf-8"))
        self.assertEqual(payload, (WEB / "index.html").read_bytes())

    def test_every_page_file_is_served_under_static_with_its_type_and_content(self):
        self.assertEqual(SERVED, ["api.js", "app.js", "identity.js", "index.html", "main.js", "messages.js", "queue.js", "render.js", "store.js",
                                  "style.css", "ulid.js"])
        for name in SERVED:
            if name == "index.html":
                continue
            with self.subTest(name=name):
                status, headers, payload = self.get(f"/static/{name}")
                self.assertEqual((status, headers.get("content-type")), (200, TYPES[Path(name).suffix]))
                self.assertEqual(payload, (WEB / name).read_bytes())

    def test_index_html_is_served_at_the_root_only(self):
        self.assertEqual(self.get("/static/index.html")[0], 404)
        self.assertEqual(self.get("/index.html")[0], 404)

    def test_the_policy_is_exactly_the_decided_one_on_the_page_and_on_every_file(self):
        for path in ["/"] + [f"/static/{n}" for n in SERVED if n != "index.html"]:
            with self.subTest(path=path):
                _, headers, _ = self.get(path)
                self.assertEqual(headers.get("content-security-policy"), EXPECTED_CSP)
                self.assertEqual(headers.get("referrer-policy"), "no-referrer")
                self.assertEqual(headers.get("x-content-type-options"), "nosniff")
                self.assertEqual(headers.get("cache-control"), "no-store")
                self.assertFalse([h for h in headers if h.startswith("access-control-")])

    def test_the_policy_allows_no_inline_or_remote_code(self):
        directives = dict(part.strip().split(" ", 1) for part in srv.PAGE_CSP.split(";"))
        for name in ("script-src", "style-src", "connect-src", "default-src"):
            self.assertEqual(directives[name], "'self'", name)
        self.assertEqual((directives["frame-ancestors"], directives["base-uri"], directives["form-action"]), ("'none'", "'none'", "'self'"))
        for forbidden in ("unsafe-inline", "unsafe-eval", "*", "http:", "https:", "data:" + "text"):
            self.assertNotIn(forbidden, directives["script-src"] + directives["style-src"] + directives["connect-src"])
        self.assertEqual(directives["img-src"], "'self' data:")

    def test_the_html_the_server_sends_loads_nothing_from_outside(self):
        _, _, payload = self.get("/")
        html = payload.decode("utf-8")
        self.assertEqual(re.findall(r"<script\b[^>]*>", html), ['<script type="module" src="/static/main.js">'])
        self.assertNotRegex(html, r"https?://")
        self.assertNotRegex(html, r"\son[a-z]+\s*=")
        self.assertNotRegex(html, r"\sstyle\s*=")

    def test_only_get_is_allowed_on_page_files(self):
        for path in ("/", "/static/main.js", "/static/style.css"):
            for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, headers, _ = raw_request(self.port, method, path)
                    self.assertEqual((status, headers.get("allow")), (405, "GET"))

    def test_a_query_string_is_ignored_and_a_foreign_host_is_refused(self):
        self.assertEqual(self.get("/static/main.js?v=2")[0], 200)
        for path in ("/", "/static/main.js", "/api/reports?sector_id=coruscant"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, host="evil.example")[0], 403)
                self.assertEqual(self.get(path, host=None)[0], 403)

    def test_files_that_are_not_part_of_the_page_are_not_served(self):
        for path in ("/static/package.json", "/package.json", "/static/tests", "/static/tests/", "/static/tests/helpers.js",
                     "/static/tests/queue.test.js", "/tests/helpers.js", "/static/README.md", "/static/.env", "/static/.gitignore",
                     "/static/nothing.js", "/static/main.map", "/static/main.js.map", "/static/queue.test.js"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_the_page_makes_no_call_the_server_does_not_answer(self):
        """Every same-origin URL named in the browser code is a route this server has."""
        urls = {js_const("api.js", c).strip('"') for c in ("SECTORS_URL", "REPORTS_URL", "REPORT_URL")}
        self.assertEqual(urls, {"/api/sectors", "/api/reports", "/api/report"})
        self.assertEqual(self.get("/api/sectors")[0], 200)
        self.assertEqual(self.get("/api/reports?sector_id=coruscant")[0], 200)
        self.assertEqual(raw_request(self.port, "GET", "/api/report")[0], 405)


class PathTraversalTests(PageFilesCase):
    PATHS = [
        "/static/../intake_server.py", "/static/..%2fintake_server.py", "/static/..%2Fintake_server.py", "/static/%2e%2e/intake_server.py",
        "/static/%2E%2E%2Fintake_server.py", "/static/%2e%2e%2fintake_server.py", "/static/..%5cintake_server.py", "/static/%5c..%5cintake_server.py",
        "/static/..\\intake_server.py", "/static/..%c0%afintake_server.py", "/static/%252e%252e/intake_server.py", "/static/....//intake_server.py",
        "/static/./main.js", "/static//main.js", "/static/main.js/", "/static/main.js/.", "/static/main.js/../main.js", "/static/main.js%00.css",
        "/static/main.js\x00", "/static/main.js%00", "/static/main.js%20", "/static/main.js.", "/static/main.js::$DATA", "/static/main.js;x",
        "/static/%6d%61%69%6e.js", "/static/main%2ejs", "/static/MAIN.JS", "/static/Main.js", "/static/main.JS", "/static/STYLE.css",
        "/static/", "/static", "/static/.", "/static/..", "/static/%2e", "/static/%2e%2e", "/static/main", "/static/main.", "/static/.js",
        "/%2e%2e/intake_server.py", "/%2e%2e/%2e%2e/%2e%2e/windows/win.ini", "/../intake_server.py", "/..\\intake_server.py", "//etc/passwd",
        "/static/C:/Windows/win.ini", "/static/C%3a%5cWindows%5cwin.ini", "/static/%5c%5clocalhost%5cc$%5cwindows%5cwin.ini",
        "/static/a/../main.js", "/static/web/main.js", "/web/main.js", "/intake_server.py", "/web/index.html",
    ]

    def test_none_of_these_reaches_a_file_it_should_not(self):
        source_marker = b"class IntakeService"
        for path in self.PATHS:
            with self.subTest(path=path):
                status, _, payload = self.get(path)
                self.assertEqual(status, 404, path)
                self.assertNotIn(source_marker, payload)

    def test_the_request_path_is_never_percent_decoded(self):
        self.assertEqual(self.get("/static/%6d%61%69%6e.js")[0], 404)         # "main.js" spelled in escapes
        self.assertEqual(self.get("/static/main.js")[0], 200)

    def test_absolute_form_request_targets_do_not_reach_files_either(self):
        for target in (f"http://127.0.0.1:{self.port}/static/../intake_server.py", "http://evil.example/static/main.js",
                       f"http://127.0.0.1:{self.port}/static/main.js"):
            with self.subTest(target=target):
                status, _, payload = self.get(target)
                self.assertEqual(status, 404)
                self.assertNotIn(b"class IntakeService", payload)


class StaticSiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def build(self, directory=None):
        """srv.StaticSite(directory), turning an exception where a site is expected into an assertion FAIL."""
        try:
            return srv.StaticSite(directory or self.dir)
        except Exception as e:
            self.fail(f"the page directory was refused: {type(e).__name__}: {e}")

    def make(self, *names):
        for name in names:
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"// {name}".encode())

    def test_only_plain_lowercase_html_js_and_css_files_at_the_top_level_are_taken(self):
        self.make("index.html", "ok.js", "also-ok_1.css", "Bad.JS", "UPPER.js", ".env", ".hidden.js", "notes.md", "data.json", "x.txt", "a b.js",
                  "noext", "two.dots.js", "sub/inner.js", "tests/t.js", "x.map", "main.js.bak", "\u00fcn\u00ef.js")
        (self.dir / "folder.js").mkdir()                   # a directory with a plausible name
        site = self.build()
        self.assertEqual(sorted(site.pages), ["/", "/static/also-ok_1.css", "/static/ok.js"])
        self.assertEqual(site.pages["/"], ("text/html; charset=utf-8", b"// index.html"))
        self.assertEqual(site.pages["/static/ok.js"][0], "text/javascript; charset=utf-8")
        self.assertEqual(site.pages["/static/also-ok_1.css"][0], "text/css; charset=utf-8")

    def test_the_real_directory_gives_the_eleven_files(self):
        self.assertEqual(sorted(self.build(WEB).pages), ["/"] + [f"/static/{n}" for n in SERVED if n != "index.html"])

    def test_a_missing_index_or_directory_is_a_configuration_error(self):
        self.make("ok.js")
        for directory in (self.dir, self.dir / "nowhere"):
            with self.subTest(directory=directory.name):
                try:
                    srv.StaticSite(directory)
                except Exception as e:                      # any other kind of error is a different failure, reported as one
                    self.assertIsInstance(e, core.ConfigError)
                else:
                    self.fail("a page directory without index.html was accepted")

    def test_files_are_read_once_at_start(self):
        self.make("index.html", "ok.js")
        site = self.build()
        (self.dir / "ok.js").write_bytes(b"changed")
        (self.dir / "new.js").write_bytes(b"new")
        self.assertEqual(site.pages["/static/ok.js"][1], b"// ok.js")
        self.assertNotIn("/static/new.js", site.pages)

    def test_the_name_rule_is_matched_in_full(self):
        for name in ("a.js\n", "a.js.", "\na.js", "a.jsx", "a.html5", "a.css ", "a/b.js", "a\\b.js", "..", ".", ""):
            self.assertIsNone(srv.STATIC_NAME_RE.fullmatch(name), repr(name))
        for name in ("a.js", "a-b_c.css", "index.html", "x1.js"):
            self.assertIsNotNone(srv.STATIC_NAME_RE.fullmatch(name), name)

    def test_a_server_serves_what_its_site_holds_and_nothing_else(self):
        self.make("index.html", "ok.js", "secret.json", "notes.md")
        logger = mock.Mock()
        service = mock.Mock()
        server = srv.make_server(service, 0, logger, self.build())
        import threading
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            port = server.server_port
            self.assertEqual(raw_request(port, "GET", "/")[2], b"// index.html")
            self.assertEqual(raw_request(port, "GET", "/static/ok.js")[2], b"// ok.js")
            (self.dir / "ok.js").write_bytes(b"changed after the server started")
            (self.dir / "late.js").write_bytes(b"added after the server started")
            self.assertEqual(raw_request(port, "GET", "/static/ok.js")[2], b"// ok.js")
            self.assertEqual(raw_request(port, "GET", "/static/late.js")[0], 404)
            self.assertEqual(raw_request(port, "GET", "/static/main.js")[0], 404)
            self.assertEqual(raw_request(port, "GET", "/static/secret.json")[0], 404)
            self.assertEqual(raw_request(port, "GET", "/static/notes.md")[0], 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


class MainPageTests(unittest.TestCase):
    def test_a_missing_page_directory_stops_the_process_with_the_configuration_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(srv, "WEB_DIR", Path(tmp) / "nowhere"):
            try:
                code = main_clean(argv(tmp), process_wide=False, publisher=S.FakePublisher(), http=S.CountingModel(), run=lambda server: None,
                                  environ=ENVIRON)
            except Exception as e:
                self.fail(f"main raised {type(e).__name__} instead of returning an exit code")
            text = log_text(tmp)
        self.assertEqual(code, srv.EXIT_CONFIG)
        self.assertIn("page files", text)


class ReportsRouteTests(ServerCase):
    def options(self):
        ticks = iter(NOW + timedelta(seconds=n) for n in range(1, 1000))
        return {"now": lambda: next(ticks)}

    def file(self, n, sector="coruscant", text=None, reporter=None):
        text = text or f"Report number {n}"
        status, _, _ = raw_request(self.port, "POST", "/api/report", {"Content-Type": "application/json"},
                                   report_body(n, reporter=reporter or "a3f2", sector_id=sector, description=text))
        self.assertEqual(status, 200)

    def reports(self, query="sector_id=coruscant", **kw):
        status, headers, payload = raw_request(self.port, "GET", "/api/reports?" + query, **kw)
        return status, headers, payload

    def test_lists_one_planets_reports_newest_first(self):
        self.file(1, text="first")
        self.file(2, sector="tatooine", text="elsewhere")
        self.file(3, text="second")
        self.file(4, text="third")
        status, headers, payload = self.reports()
        self.assertEqual((status, headers.get("content-type")), (200, "application/json; charset=utf-8"))
        self.assertEqual([r["description"] for r in json.loads(payload)], ["third", "second", "first"])
        self.assertEqual([r["description"] for r in json.loads(self.reports("sector_id=tatooine")[2])], ["elsewhere"])

    def test_an_item_carries_what_the_page_shows_and_nothing_that_identifies_the_reporter_or_the_report(self):
        self.file(7, text="Saw Darth Vader", reporter="zq9x1")
        _, _, payload = self.reports()
        (item,) = json.loads(payload)
        self.assertEqual(set(item), {"description", "event_time", "relevance_score", "midichlorian_ppm", "kyber_resonance", "dark_side_activity",
                                     "inference_rationale", "event_id"})
        self.assertEqual(item["event_id"], core.event_id_for(report_ids(7)))
        self.assertEqual(item["description"], "Saw Darth Vader")
        text = payload.decode("utf-8")
        for secret in ("zq9x1", report_ids(7), "web-", "fingerprint", "source_id", "report_id", "reporter"):
            self.assertNotIn(secret, text)

    def test_the_values_are_the_ones_the_reporter_was_shown(self):
        status, _, payload = raw_request(self.port, "POST", "/api/report", {"Content-Type": "application/json"}, report_body(1))
        shown = json.loads(payload)
        (item,) = json.loads(self.reports()[2])
        for key in ("event_time", "relevance_score", "midichlorian_ppm", "kyber_resonance", "dark_side_activity", "inference_rationale", "event_id"):
            self.assertEqual(item[key], shown[key], key)

    def test_descriptions_come_back_verbatim_whatever_they_contain(self):
        texts = ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "caf\u00e9 \u2014 \U0001F311", "line one\nline two\ttabbed",
                 "  padded  ", "\u202eRTL\u202c and \u2028 separator"]
        for n, text in enumerate(texts, 1):
            self.file(n, text=text, reporter=f"user{n}")
        _, _, payload = self.reports()
        self.assertEqual([r["description"] for r in json.loads(payload)], texts[::-1])

    def test_at_most_twenty_are_listed_the_newest(self):
        for n in range(1, 23):
            self.file(n, reporter=f"user{n:04d}")
        rows = json.loads(self.reports()[2])
        self.assertEqual(len(rows), 20)
        self.assertEqual([r["description"] for r in rows], [f"Report number {n}" for n in range(22, 2, -1)])

    def test_a_planet_without_reports_gives_an_empty_list(self):
        self.assertEqual(self.reports("sector_id=naboo")[:1] + (json.loads(self.reports("sector_id=naboo")[2]),), (200, []))

    def test_the_query_must_name_exactly_one_known_planet(self):
        cases = [("", 400, "bad_query"), ("sector_id", 422, "unknown_sector"), ("other=1", 400, "bad_query"),
                 ("sector_id=coruscant&sector_id=tatooine", 400, "bad_query"), ("sector_id=coruscant&x=1", 400, "bad_query"),
                 ("sector_id=", 422, "unknown_sector"), ("sector_id=nowhere", 422, "unknown_sector"), ("sector_id=CORUSCANT", 422, "unknown_sector"),
                 ("sector_id=coruscant%00", 422, "unknown_sector"), ("sector_id=..%2f..%2fetc", 422, "unknown_sector"),
                 ("sector_id=coruscant%0d%0aX-Evil:1", 422, "unknown_sector"), ("sector_id=coruscant%20", 422, "unknown_sector"),
                 ("sector_id=%27%20OR%201%3D1", 422, "unknown_sector"), ("a=1&b=2&c=3&d=4&e=5&f=6", 400, "bad_query")]
        for query, status, code in cases:
            with self.subTest(query=query):
                got, _, payload = self.reports(query)
                self.assertEqual((got, code_of(payload)), (status, code))

    def test_it_reads_the_log_only_so_there_is_no_model_call_and_no_publish(self):
        self.file(1)
        calls, attempts = self.model.calls, list(self.publisher.attempts)
        for _ in range(3):
            self.reports()
            self.reports("sector_id=nowhere")
        self.assertEqual((self.model.calls, self.publisher.attempts), (calls, attempts))

    def test_it_is_get_only_uncacheable_and_host_checked(self):
        self.file(1)
        status, headers, _ = self.reports()
        self.assertEqual((headers.get("cache-control"), headers.get("x-content-type-options")), ("no-store", "nosniff"))
        self.assertFalse([h for h in headers if h.startswith("access-control-")])
        self.assertEqual(self.reports(host="evil.example")[0], 403)
        self.assertEqual(self.reports(host=None)[0], 403)
        self.assertEqual(raw_request(self.port, "POST", "/api/reports?sector_id=coruscant")[0], 405)

    def test_what_the_service_hands_over_survives_a_log_line_with_an_unexpected_response(self):
        log = srv.ReportLog(Path(self.tmp.name) / "odd.ndjson")
        service = srv.IntakeService(sectors=S.core.load_sectors(), api_key=S.KEY, model=S.MODEL, publisher=S.FakePublisher(), report_log=log,
                                    http=S.CountingModel())
        log.append({"report_id": report_ids(1), "event_id": "E1", "source_id": "web-x", "sector_id": "coruscant",
                    "accepted_at": "2026-10-14T15:00:00.000Z", "fingerprint": "f", "description": "odd", "response": "not an object"})
        try:
            answer = service.recent_reports("coruscant")
        except Exception as e:
            self.fail(f"recent_reports raised {type(e).__name__} on an unexpected log entry")
        self.assertEqual(answer.status, 200)
        self.assertEqual(answer.body[0]["description"], "odd")
        self.assertIsNone(answer.body[0]["relevance_score"])
        self.assertEqual(service.recent_reports(None).status, 422)
        self.assertEqual(service.recent_reports(["coruscant"]).status, 422)


class ParityTests(unittest.TestCase):
    """The page applies some of the server's rules first to save a round trip; these keep the two from drifting apart."""

    def test_the_description_limit_is_the_same(self):
        self.assertEqual(int(js_const("messages.js", "MAX_DESCRIPTION_CHARS")), core.MAX_DESCRIPTION_CHARS)
        self.assertRegex(js_text("index.html"), rf'<textarea\b[^>]*\bmaxlength="{core.MAX_DESCRIPTION_CHARS}"')
        self.assertIn(f"0 / {core.MAX_DESCRIPTION_CHARS}<", js_text("index.html"))

    def test_the_reporter_id_pattern_is_the_same(self):
        js = re.search(r"export const REPORTER_RE = /(.+)/;", js_text("identity.js")).group(1)
        self.assertEqual(js, core._REPORTER_RE.pattern)
        self.assertEqual(int(js_const("identity.js", "REPORTER_LENGTH")), 12)
        self.assertRegex("x" * 12, r"^[a-z]{12}$")
        self.assertTrue(4 <= int(js_const("identity.js", "REPORTER_LENGTH")) <= 16)

    def test_the_ulid_pattern_is_the_same(self):
        js = re.search(r"export const ULID_RE = /(.+)/;", js_text("ulid.js")).group(1)
        self.assertEqual(js, core._ULID_RE.pattern)
        samples = [report_ids(1), report_ids(99), "0" * 26, "7" * 26, "8" * 26, "0" * 25, "0" * 27, "0" * 25 + "I", "0" * 25 + "L", "0" * 25 + "O",
                   "0" * 25 + "U", "0" * 25 + "a", "0" * 25 + "\n", "\n" + "0" * 25, "", "0" * 26 + "\n", "01KX" + "Z" * 22]
        for sample in samples:
            self.assertEqual(bool(re.fullmatch(js, sample)), core.is_ulid(sample), repr(sample))

    def test_the_control_character_rule_is_the_same(self):
        js_class = re.search(r"const CONTROL_RE = /(\[.+?\])/;", js_text("api.js")).group(1)
        pattern = re.compile(js_class)
        for code in range(0x110000):
            if 0xD800 <= code <= 0xDFFF:
                continue
            char = chr(code)
            server_rejects = unicodedata.category(char) == "Cc" and char not in "\t\n\r"
            if bool(pattern.fullmatch(char)) != server_rejects:
                self.fail(f"U+{code:04X}: page rule {bool(pattern.fullmatch(char))}, server rule {server_rejects}")

    def test_the_request_fields_the_page_sends_are_the_ones_the_server_reads(self):
        sent = set(re.findall(r"^\s+(sector_id|description|report_id|reporter_id|observed_at)\b", js_text("api.js"), re.M))
        self.assertEqual(sent, {"sector_id", "description", "report_id", "reporter_id", "observed_at"})
        request = core.parse_request(report_body(1, sector_id="coruscant"), core.load_sectors())
        self.assertEqual(request.sector_id, "coruscant")

    def test_what_the_page_reads_from_a_confirmation_is_what_the_server_sends(self):
        shown = set(core.public_response({"event_id": "E", "event_time": "T", "payload": {
            "relevance_score": 1, "midichlorian_ppm": 2, "kyber_resonance": 3, "dark_side_activity": 4, "inference_rationale": "r",
            "inference_model": "m"}}))
        read = set(re.findall(r"response\.([a-z_]+)", js_text("render.js")))
        self.assertTrue(read, "render.js reads no response field")
        self.assertLessEqual(read, shown)

    def test_what_the_page_reads_from_a_recent_item_is_what_the_server_sends(self):
        read = set(re.findall(r"report\.([a-z_]+)", js_text("render.js")))
        sent = {"description", "event_time", "relevance_score", "midichlorian_ppm", "kyber_resonance", "dark_side_activity", "inference_rationale", "event_id"}
        self.assertTrue(read)
        self.assertLessEqual(read, sent)


if __name__ == "__main__":
    unittest.main()
