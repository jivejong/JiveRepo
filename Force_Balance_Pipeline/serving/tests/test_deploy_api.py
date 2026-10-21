"""Tests for the deploy endpoints of serving/server.py (doc 06 "Manual deployment path"): the CSRF guard, the enabled check, the options hints
and the one-at-a-time rule. The server runs on an ephemeral port in a thread over force_serving_test; deploy.deploy is faked. No warehouse."""
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pg_support
from serving import config, fixtures, server, views

FIXTURE_NOW = datetime(2026, 10, 19, 15, 45, tzinfo=timezone.utc)
CLOCK_NOW = FIXTURE_NOW + timedelta(minutes=15)
CREDS = {"DATABRICKS_HOST": "dbc-test.example.invalid", "DATABRICKS_TOKEN": "t", "DATABRICKS_WAREHOUSE_ID": "w"}
BODY = {"disturbance_id": "FIXD4", "jedi_ids": ["windu"], "starship_id": "arc_170", "rationale": "Because."}


class FakeDeploy:
    def __init__(self, block=None):
        self.calls, self.block, self.entered = [], block, threading.Event()

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        self.entered.set()
        if self.block is not None:
            self.block.wait(10)
        return {"ok": True, "reasons": [], "deployment_id": "DEP1", "statements": 6}


class Recording:
    """A connection wrapper that keeps the SQL of every execute, to show what each session ran first."""
    log = []

    def __init__(self, conn):
        self.conn, self.sql = conn, []
        Recording.log.append(self.sql)

    def execute(self, sql, params=None):
        self.sql.append(sql)
        return self.conn.execute(sql, params)

    def close(self):
        self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


class Web:
    def __init__(self, **kw):
        kw.setdefault("env", dict(config.load_env(), **CREDS))
        kw.setdefault("live_database", pg_support.TEST_DB)
        kw.setdefault("deps_factory", lambda env, database, connect: {})
        kw.setdefault("clock", lambda: CLOCK_NOW)
        self.srv = server.make_server(pg_support.TEST_DB, 0, dist=Path(tempfile.mkdtemp()) / "none", **kw)
        self.port = self.srv.server_port
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
            conn.request(method, path, body=data, headers=headers or {})
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload
        finally:
            conn.close()

    def token(self):
        return json.loads(self.request("GET", "/api/deploy/token")[2])["token"]

    def post(self, body=BODY, origin=True, token=True, content_type="application/json", raw=None, tok=None):
        headers = {"Content-Type": content_type}
        if origin:
            headers["Origin"] = origin if isinstance(origin, str) else self.origin
        if token:
            headers["X-Deploy-Token"] = tok or self.token()
        status, hdrs, payload = self.request("POST", "/api/deploy", body, headers, raw)
        return status, json.loads(payload) if payload else None


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = pg_support.connect_test()
        pg_support.fresh_schema(cls.pg)
        views.apply(cls.pg)
        cls.log = Path(tempfile.mkdtemp()) / "dashboard.log"
        server.configure_logging(cls.log)

    @classmethod
    def tearDownClass(cls):
        cls.pg.close()

    def setUp(self):
        fixtures.load(self.pg, FIXTURE_NOW, reset=True)
        self.webs = []
        self.addCleanup(lambda: [w.stop() for w in self.webs])

    def web(self, enabled=True, **kw):
        if enabled:
            self.pg.execute("DELETE FROM serving.publish_log WHERE kind = 'fixture'")
            self.pg.commit()
        w = Web(**kw)
        self.webs.append(w)
        return w


class Token(ApiTest):
    def test_the_token_is_stable_and_long(self):
        w = self.web()
        first, second = w.token(), w.token()
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 40)
        self.assertNotEqual(first, self.web().token())            # a new process (server) has its own token

    def test_no_cors_header_on_any_response(self):
        w = self.web()
        tok = w.token()
        responses = [w.request("GET", "/api/deploy/token"), w.request("GET", "/api/health"), w.request("GET", "/api/nope"),
                     w.request("OPTIONS", "/api/deploy", headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"}),
                     w.request("POST", "/api/deploy", {"x": 1}, {"Origin": w.origin, "Content-Type": "application/json", "X-Deploy-Token": tok}),
                     w.request("POST", "/api/deploy", {"x": 1}, {"Origin": "http://evil.test", "Content-Type": "application/json"}),
                     w.request("GET", "/api/deploy/options?disturbance_id=FIXD4")]
        for status, headers, _ in responses:
            self.assertFalse([h for h in headers if h.startswith("access-control-")], status)
            self.assertEqual(headers["cache-control"], "no-store")

    def test_options_method_is_405_everywhere(self):
        w = self.web()
        for path in ("/api/deploy", "/api/health", "/", "/api/deploy/token"):
            self.assertEqual(w.request("OPTIONS", path)[0], 405, path)


class Guard(ApiTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeDeploy()
        self.w = self.web(deploy_fn=self.fake)

    def test_a_missing_or_foreign_origin_is_403(self):
        for origin in (False, "http://evil.test", f"http://127.0.0.1:{self.w.port + 1}", "https://127.0.0.1:%d" % self.w.port, "null"):
            self.assertEqual(self.w.post(origin=origin), (403, {"error": "bad_origin"}), origin)
        self.assertEqual(self.fake.calls, [])

    def test_both_loopback_origins_are_accepted(self):
        self.assertEqual(self.w.post(origin=f"http://localhost:{self.w.port}")[0], 200)
        self.assertEqual(self.w.post(origin=f"http://127.0.0.1:{self.w.port}")[0], 200)

    def test_only_json_is_accepted(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "application/jsonx"):
            self.assertEqual(self.w.post(content_type=ctype)[0], 415, ctype)
        self.assertEqual(self.w.post(content_type="application/json; charset=utf-8")[0], 200)
        self.assertEqual(len(self.fake.calls), 1)

    def test_a_wrong_or_missing_token_is_403(self):
        self.assertEqual(self.w.post(token=False), (403, {"error": "bad_token"}))
        self.assertEqual(self.w.post(tok="wrong"), (403, {"error": "bad_token"}))
        self.assertEqual(self.w.post(tok=self.w.token() + "x"), (403, {"error": "bad_token"}))
        self.assertEqual(self.w.post(tok="é"), (403, {"error": "bad_token"}))
        self.assertEqual(self.fake.calls, [])

    def test_the_token_is_compared_in_constant_time(self):
        from unittest import mock
        with mock.patch.object(server.hmac, "compare_digest", wraps=server.hmac.compare_digest) as spy:
            self.assertEqual(self.w.post()[0], 200)
        self.assertTrue(spy.called)

    def test_a_big_body_is_413(self):
        self.assertEqual(self.w.post(raw=b"x" * 9000)[0], 413)
        self.assertEqual(self.w.post(raw=json.dumps(dict(BODY, rationale="y" * 9000)).encode())[0], 413)
        self.assertEqual(self.fake.calls, [])

    def test_the_body_must_be_an_object_with_exactly_the_four_keys(self):
        bad = [dict(BODY, extra=1), {k: v for k, v in BODY.items() if k != "rationale"}, [BODY], "text", 5, None, {}]
        for body in bad:
            self.assertEqual(self.w.post(body=body, raw=json.dumps(body).encode()), (400, {"error": "bad_body"}), body)
        self.assertEqual(self.w.post(raw=b"{not json"), (400, {"error": "bad_body"}))
        self.assertEqual(self.fake.calls, [])

    def test_a_valid_post_passes_the_parsed_request_through(self):
        status, body = self.w.post()
        self.assertEqual((status, body), (200, {"ok": True, "reasons": [], "deployment_id": "DEP1", "statements": 6}))
        request, kwargs = self.fake.calls[0]
        self.assertEqual(request, BODY)
        self.assertEqual(kwargs["now"], CLOCK_NOW)
        self.assertEqual(kwargs["env"]["DATABRICKS_WAREHOUSE_ID"], "w")

    def test_get_on_the_deploy_path_is_not_a_deploy(self):
        self.assertEqual(self.w.request("GET", "/api/deploy")[0], 404)
        self.assertEqual(self.fake.calls, [])

    def test_a_foreign_host_header_is_403_for_a_post_too(self):
        status, _, _ = self.w.request("POST", "/api/deploy", BODY, {"Host": "evil.test", "Origin": self.w.origin, "Content-Type": "application/json",
                                                                    "X-Deploy-Token": self.w.token()})
        self.assertEqual(status, 403)


class OneAtATime(ApiTest):
    def test_a_second_concurrent_post_is_409(self):
        gate = threading.Event()
        fake = FakeDeploy(block=gate)
        w = self.web(deploy_fn=fake)
        results = []
        first = threading.Thread(target=lambda: results.append(w.post()))
        first.start()
        self.assertTrue(fake.entered.wait(10))
        try:
            self.assertEqual(w.post(), (409, {"error": "deploy_in_progress"}))
        finally:
            gate.set()
            first.join(10)
        self.assertEqual(results[0][0], 200)
        self.assertEqual(w.post()[0], 200)                      # and it is free again afterwards
        self.assertEqual(len(fake.calls), 2)


class Enabled(ApiTest):
    def check(self, why, **kw):
        fake = FakeDeploy()
        w = self.web(enabled=(why in (None, "no_credentials")), deploy_fn=fake, **kw)
        meta = json.loads(w.request("GET", "/api/meta")[2])["data"]
        self.assertEqual((meta["deploy_enabled"], meta["deploy_disabled_why"]), (why is None, why))
        status, body = w.post()
        if why is None:
            self.assertEqual(status, 200)
        else:
            self.assertEqual((status, body), (503, {"error": "deploy_disabled", "why": why}))
            self.assertEqual(fake.calls, [])

    def test_the_fixture_database(self):
        self.check("fixture_database", live_database="force_serving")

    def test_fixture_rows(self):
        self.check("fixture_rows")

    def test_missing_credentials(self):
        for key in CREDS:
            env = dict(config.load_env(), **CREDS)
            env[key] = ""
            self.check("no_credentials", env=env)

    def test_enabled_only_when_all_three_hold(self):
        self.check(None)

    def test_a_disabled_server_still_checks_the_token_before_saying_so(self):
        w = self.web(enabled=False, deploy_fn=FakeDeploy())
        self.assertEqual(w.post(token=False)[0], 403)
        self.assertEqual(w.post(origin=False)[0], 403)


class Options(ApiTest):
    def setUp(self):
        super().setUp()
        self.seen = Path(tempfile.mkdtemp()) / "agent_seen_prod.json"
        self.w = self.web(seen_path=self.seen)

    def get(self, did="FIXD4"):
        status, headers, payload = self.w.request("GET", f"/api/deploy/options?disturbance_id={did}")
        return status, json.loads(payload)

    def test_not_open_incidents_are_404(self):
        for did in ("FIXD1", "FIXD5", "NOPE"):
            self.assertEqual(self.get(did)[0], 404, did)
        self.assertEqual(self.get("bad%20id!")[0], 400)
        self.assertEqual(self.w.request("GET", "/api/deploy/options")[0], 400)
        self.assertEqual(self.w.request("GET", "/api/deploy/options?disturbance_id=FIXD4&x=1")[0], 400)

    def test_the_incident_and_all_jedi_and_ships(self):
        status, body = self.get()
        data = body["data"]
        self.assertEqual(status, 200)
        self.assertEqual((data["incident"]["sector_name"], data["incident"]["signature"]), ("Bespin", "civil_unrest"))
        self.assertEqual(len(data["jedi"]), 17)
        self.assertEqual(len(data["starships"]), 13)
        self.assertEqual(sorted(data["jedi"][0]), ["jedi_id", "jedi_name", "primary_specialty", "rank", "secondary_specialty", "unavailable_until"])
        self.assertEqual(sorted(data["starships"][0]), ["crew", "hyperdrive_rating", "passengers", "starship_id", "starship_name", "unavailable_until"])

    def test_unavailable_until_comes_from_open_deploy_rows(self):
        data = self.get()[1]["data"]
        released = {r[0]: r[1] for r in self.pg.execute(
            "SELECT jedi_id, max(released_at) FROM serving.deployment d, jsonb_array_elements_text(d.jedi_ids) AS jedi_id "
            "WHERE d.decision = 'deploy' AND d.released_at > %s GROUP BY jedi_id", (CLOCK_NOW,)).fetchall()}
        self.assertTrue(released)
        by_id = {j["jedi_id"]: j["unavailable_until"] for j in data["jedi"]}
        for jedi_id, until in released.items():
            self.assertEqual(by_id[jedi_id], server.clean(until), jedi_id)
        self.assertEqual(sum(v is not None for v in by_id.values()), len(released))
        ship = {s["starship_id"]: s["unavailable_until"] for s in data["starships"]}
        self.assertIsNotNone(ship["arc_170"])
        self.assertEqual(sum(v is not None for v in ship.values()), self.pg.execute(
            "SELECT count(DISTINCT starship_id) FROM serving.deployment WHERE decision = 'deploy' AND released_at > %s", (CLOCK_NOW,)).fetchone()[0])
        self.assertEqual(data["open_deployments"], self.pg.execute(
            "SELECT count(*) FROM serving.deployment WHERE decision = 'deploy' AND released_at > %s", (CLOCK_NOW,)).fetchone()[0])

    def test_everything_is_available_once_the_releases_have_passed(self):
        w = self.web(seen_path=self.seen, clock=lambda: FIXTURE_NOW + timedelta(days=30))
        data = json.loads(w.request("GET", "/api/deploy/options?disturbance_id=FIXD4")[2])["data"]
        self.assertTrue(all(j["unavailable_until"] is None for j in data["jedi"]))
        self.assertEqual(data["open_deployments"], 0)

    def test_agent_first_seen_is_read_from_the_seen_file(self):
        self.assertIsNone(self.get()[1]["data"]["agent_first_seen"])                 # missing file
        self.seen.write_text('{"FIXD4": "2026-10-19T15:11:00.000Z"}', encoding="utf-8")
        self.assertEqual(self.get()[1]["data"]["agent_first_seen"], "2026-10-19T15:11:00.000Z")
        self.seen.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.get()[1]["data"]["agent_first_seen"])                 # corrupt file
        self.seen.write_text('{"OTHER": "x"}', encoding="utf-8")
        self.assertIsNone(self.get()[1]["data"]["agent_first_seen"])


class ReadOnly(ApiTest):
    def test_read_connections_start_read_only_and_show_it(self):
        Recording.log.clear()
        w = self.web(connect=lambda env, db=None: Recording(config.connect(env, db)), deploy_fn=FakeDeploy())
        w.request("GET", "/api/meta")
        w.request("GET", "/api/deploy/options?disturbance_id=FIXD4")
        w.post(tok=w.token())
        self.assertTrue(Recording.log)
        for sql in Recording.log:
            self.assertEqual(sql[0], "SET TRANSACTION READ ONLY")
        conn = server.open_session(config.load_env(), pg_support.TEST_DB)
        try:
            self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
