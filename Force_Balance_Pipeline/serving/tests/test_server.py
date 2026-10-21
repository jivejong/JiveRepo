"""Tests for serving/server.py (doc 05 "Serving (Phase 7)": "Dashboard backend"): the server on an ephemeral port in a thread, over
force_serving_test with the fixtures loaded, and a temp dist folder. Nothing leaves 127.0.0.1."""
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pg_support
import psycopg
from serving import api_queries, config, fixtures, server, views

NOW = datetime(2026, 10, 19, 15, 45, tzinfo=timezone.utc)
JS_HASHED = "console.log('app');"


def make_dist():
    dist = Path(tempfile.mkdtemp()) / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text(JS_HASHED, encoding="utf-8")
    (dist / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    (dist / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (dist / "notes.txt").write_text("no", encoding="utf-8")
    return dist


class Running:
    def __init__(self, dist):
        self.srv = server.make_server(pg_support.TEST_DB, 0, dist=dist, env=config.load_env())
        self.port = self.srv.server_port
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        finally:
            conn.close()

    def api(self, path):
        status, headers, body = self.get(path)
        return status, json.loads(body)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = pg_support.connect_test()
        pg_support.fresh_schema(cls.pg)
        views.apply(cls.pg)
        fixtures.load(cls.pg, NOW, reset=True)
        cls.log = Path(tempfile.mkdtemp()) / "dashboard.log"
        server.configure_logging(cls.log)
        cls.dist = make_dist()
        cls.web = Running(cls.dist)

    @classmethod
    def tearDownClass(cls):
        cls.web.stop()
        cls.pg.close()


class Api(ServerTest):
    def test_health_and_meta(self):
        self.assertEqual(self.web.api("/api/health"), (200, {"data": {"ok": True}}))
        status, body = self.web.api("/api/meta")
        data = body["data"]
        self.assertEqual((status, data["publish"]["kind"], data["publish"]["status"], data["database"]), (200, "fixture", "ok", pg_support.TEST_DB))
        self.assertTrue(data["server_time"].endswith("Z"))
        self.assertTrue(data["publish"]["finished_at"].endswith("Z"))

    def test_galaxy_has_sixty_planets_in_region_then_name_order(self):
        _, body = self.web.api("/api/galaxy")
        data = body["data"]
        self.assertEqual(len(data), 60)
        keys = [(r["region"], r["sector_name"]) for r in data]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual({r["band"] for r in data}, {"normal", "anomaly", "emergency"})

    def test_planet(self):
        status, body = self.web.api("/api/planet/geonosis")
        data = body["data"]
        self.assertEqual(status, 200)
        self.assertEqual(data["sector"]["sector_id"], "geonosis")
        self.assertEqual(len(data["baseline"]), 3)
        times = [r["event_time"] for r in data["readings"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), 288)
        self.assertEqual(sorted(data["readings"][0]), sorted(["event_time", "source_type", "midichlorian_ppm", "kyber_resonance",
                                                               "dark_side_activity", "z_midi", "z_kyber", "z_dark", "imbalance_score",
                                                               "signature", "is_replayed"]))
        self.assertEqual([i["disturbance_id"] for i in data["incidents"]], ["FIXD1"])

    def test_planet_input_checks(self):
        for bad in ("Tatooine", "a;b", "a" * 65, ""):
            self.assertEqual(self.web.get("/api/planet/" + bad)[0], 400, bad)
        self.assertEqual(self.web.get("/api/planet/nope")[0], 404)
        self.assertEqual(self.web.get("/api/planet/geonosis/extra")[0], 400)

    def test_signatures_source_health_lag_and_rejections(self):
        self.assertEqual([r["signature"] for r in self.web.api("/api/signatures")[1]["data"]],
                         ["civil_unrest", "dark_adept", "force_drain", "nexus_awakening", "sith_presence"])
        health = self.web.api("/api/source-health")[1]["data"]
        self.assertEqual(len(health["sources"]), 1)
        self.assertEqual(len(health["scans"]), 96)
        stamps = [s["scan_event_time"] for s in health["scans"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual([b["bin_order"] for b in self.web.api("/api/ingest-lag")[1]["data"]], list(range(1, 11)))
        rej = self.web.api("/api/rejections")[1]["data"]
        hours = [r["hour"] for r in rej["rate"]]
        self.assertTrue(hours and hours == sorted(hours))
        self.assertTrue(rej["by_reason"])

    def test_incidents_order_filter_and_limits(self):
        data = self.web.api("/api/incidents")[1]["data"]
        self.assertEqual([r["disturbance_id"] for r in data], ["FIXD4", "FIXD1", "FIXD3", "FIXD2", "FIXD5"])
        self.assertEqual([r["disturbance_id"] for r in self.web.api("/api/incidents?status=decided")[1]["data"]], ["FIXD1", "FIXD3", "FIXD2"])
        self.assertEqual(len(self.web.api("/api/incidents?limit=2")[1]["data"]), 2)
        self.assertEqual(self.web.api("/api/incidents?status=open&limit=200")[0], 200)
        for bad in ("limit=0", "limit=201", "limit=x", "limit=-1", "limit=", "status=nope", "status=open&status=decided", "other=1"):
            self.assertEqual(self.web.get("/api/incidents?" + bad)[0], 400, bad)

    def test_deployments(self):
        data = self.web.api("/api/deployments")[1]["data"]
        self.assertEqual([r["deployment_id"] for r in data], ["FIXDEP1", "FIXDEP3", "FIXDEP2", "FIXDEPO"])
        self.assertEqual(len(self.web.api("/api/deployments?limit=1")[1]["data"]), 1)
        for bad in ("limit=0", "limit=201", "limit=x"):
            self.assertEqual(self.web.get("/api/deployments?" + bad)[0], 400, bad)
        self.assertEqual(self.web.api("/api/deployments")[1]["data"][2]["capacity_reached"], True)

    def test_unknown_api_path(self):
        self.assertEqual(self.web.get("/api/nope")[0], 404)
        self.assertEqual(self.web.get("/api")[0], 404)


class Guards(ServerTest):
    def test_it_binds_to_loopback_only(self):
        self.assertEqual(self.web.srv.server_address[0], "127.0.0.1")

    def test_only_get_and_head(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, headers, _ = self.web.get("/api/health", method)
            self.assertEqual(status, 405, method)
            self.assertIn("get", headers["allow"].lower())
        self.assertEqual(self.web.get("/api/health", "HEAD")[0], 200)

    def test_a_foreign_host_header_is_refused(self):
        self.assertEqual(self.web.get("/api/health", headers={"Host": f"evil.test:{self.web.port}"})[0], 403)
        self.assertEqual(self.web.get("/", headers={"Host": "127.0.0.1"})[0], 403)
        self.assertEqual(self.web.get("/api/health", headers={"Host": f"localhost:{self.web.port}"})[0], 200)

    def test_the_session_is_read_only(self):
        conn = server.open_session(config.load_env(), pg_support.TEST_DB)
        try:
            self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("INSERT INTO serving.watermark VALUES ('x', now())")
        finally:
            conn.close()

    def test_a_query_error_is_a_plain_500_and_the_log_has_no_url(self):
        with mock.patch.object(api_queries, "GALAXY", 'SELECT * FROM "https://x.example/y"'):
            status, body = self.web.api("/api/galaxy")
        self.assertEqual((status, body), (500, {"error": "internal"}))
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("UndefinedTable", text)
        self.assertIn("<url>", text)
        self.assertNotIn("http", text.lower())


class Static(ServerTest):
    def test_index_and_hashed_asset(self):
        status, headers, body = self.web.get("/")
        self.assertEqual((status, headers["content-type"], headers["cache-control"]), (200, "text/html; charset=utf-8", "no-store"))
        status, headers, body = self.web.get("/assets/app-abc123.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/javascript")
        self.assertEqual(headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(body.decode(), JS_HASHED)
        self.assertEqual(self.web.get("/assets/app.css")[1]["content-type"], "text/css")
        self.assertEqual(self.web.get("/logo.svg")[1]["content-type"], "image/svg+xml")

    def test_paths_that_are_not_listed_never_serve(self):
        for path in ("/../serving/config.py", "/assets/%2e%2e/x", "/assets\\x"):
            self.assertEqual(self.web.get(path)[0], 400, path)
        for path in ("/notlisted.js", "/assets/./app.css", "/assets//app.css", "/notes.txt", "/assets/", "/dist/index.html"):
            self.assertEqual(self.web.get(path)[0], 404, path)

    def test_a_missing_dist_gives_503_on_the_root(self):
        other = Running(Path(tempfile.mkdtemp()) / "no-dist")
        try:
            status, headers, body = other.get("/")
            self.assertEqual((status, body), (503, b"dashboard not built"))
            self.assertIn("default-src 'self'", headers.get("content-security-policy", ""))
            self.assertEqual(other.get("/assets/app.js")[0], 404)
            self.assertEqual(other.api("/api/health")[0], 200)
        finally:
            other.stop()


class Headers(ServerTest):
    def test_security_headers_on_html_api_and_error_responses(self):
        cases = [self.web.get("/"), self.web.get("/api/health"), self.web.get("/api/nope"), self.web.get("/api/incidents?limit=0"),
                 self.web.get("/nope.js"), self.web.get("/api/health", "POST"), self.web.get("/api/health", headers={"Host": "evil.test"}),
                 self.web.get("/assets/app-abc123.js")]
        for status, headers, _ in cases:
            self.assertIn("default-src 'self'; script-src 'self'; style-src 'self'", headers.get("content-security-policy", ""), status)
            self.assertIn("object-src 'none'", headers.get("content-security-policy", ""))
            self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""))
            self.assertEqual(headers["x-content-type-options"], "nosniff", status)
            self.assertEqual(headers["referrer-policy"], "no-referrer", status)

    def test_api_responses_are_not_cached(self):
        self.assertEqual(self.web.get("/api/health")[1]["cache-control"], "no-store")
        self.assertEqual(self.web.get("/api/nope")[1]["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
