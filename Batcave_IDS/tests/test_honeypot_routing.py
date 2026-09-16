"""Every route in docs/01-architecture.md's table returns its documented
tier and status, including the wildcard 404. Exercises the real FastAPI app
end to end (TestClient), with Kafka disabled so this stays hermetic.
"""

import os

import pytest

os.environ["HONEYPOT_DISABLE_KAFKA"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from services.honeypot.app import _parse_attempt_id, app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    "path, expected_status",
    [
        ("/", 200),
        ("/login", 401),
        ("/api/v1/status", 200),
        ("/api/v1/users", 200),
        ("/wayne-enterprises/payroll", 403),
        ("/cave/vehicle-bay/status", 200),
        ("/admin", 401),
        ("/cave/archives/case-0047", 200),
        ("/api/v1/protocol/knightfall", 403),
        ("/totally/unknown/path", 404),
    ],
)
def test_route_status_codes(client, path, expected_status):
    response = client.get(path)
    assert response.status_code == expected_status


def test_knightfall_leaks_header_despite_403(client):
    response = client.get("/api/v1/protocol/knightfall")
    assert response.status_code == 403
    assert response.headers.get("x-protocol-status") == "exists"


def test_archives_path_param_echoed(client):
    response = client.get("/cave/archives/case-0047")
    assert response.json()["id"] == "case-0047"


def test_malformed_json_body_is_accepted_not_rejected(client):
    response = client.post(
        "/login", content=b"{not valid json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401  # canned /login response, not a validation error


def test_wildcard_404_still_logged_not_an_error(client):
    response = client.get("/this/does/not/exist")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_parse_attempt_id_present():
    request = _FakeRequest({"x-attempt-id": "b2f1c1e0-0000-0000-0000-000000000000"})
    assert _parse_attempt_id(request) == "b2f1c1e0-0000-0000-0000-000000000000"


def test_parse_attempt_id_absent_is_none():
    request = _FakeRequest({})
    assert _parse_attempt_id(request) is None


def test_first_response_sets_session_cookie(client):
    # A fresh client (no cookie) gets a session minted and set.
    with TestClient(app) as fresh:
        response = fresh.get("/")
        assert "batcave_sid" in response.cookies


def test_cookie_carries_session_across_rotated_identity(client):
    # Same client (carries the cookie) hitting the honeypot with different
    # source IPs stays one session — the cookie, not (ip, ua), is the key.
    with TestClient(app) as c:
        c.get("/", headers={"X-Forwarded-For": "192.0.2.10"})
        token = c.cookies.get("batcave_sid")
        c.get("/api/v1/status", headers={"X-Forwarded-For": "192.0.2.99"})
        assert c.cookies.get("batcave_sid") == token


@pytest.mark.parametrize("method", ["BREW", "PROPFIND", "WHACK", "MEOW"])
def test_absurd_http_methods_are_answered_not_405(client, method):
    # Joker's signature is "occasional absurd HTTP methods" — the honeypot
    # must log and answer them, not bare-405 them (which would publish no
    # event and make the signature invisible).
    response = client.request(method, "/admin")
    assert response.status_code != 405


class _CapturingProducer:
    def __init__(self):
        self.messages = []  # list of (key, value)

    def produce(self, topic, key=None, value=None, callback=None):
        self.messages.append((key, value))

    def poll(self, *a):
        return 0

    def flush(self, *a):
        return 0


def test_pathology_unkeyed_produces_null_key():
    with TestClient(app) as c:
        prod = _CapturingProducer()
        c.app.state.producer = prod
        c.get("/", headers={"X-Sim-Pathology": "unkeyed"})
        assert prod.messages and prod.messages[-1][0] is None


def test_pathology_undeserializable_produces_non_json():
    import json as _json

    with TestClient(app) as c:
        prod = _CapturingProducer()
        c.app.state.producer = prod
        c.get("/", headers={"X-Sim-Pathology": "undeserializable"})
        _, value = prod.messages[-1]
        try:
            _json.loads(value)
            raise AssertionError("undeserializable payload should not parse as JSON")
        except (ValueError, UnicodeDecodeError):
            pass


def test_pathology_duplicate_delivery_produces_two_identical_values():
    import json as _json

    with TestClient(app) as c:
        prod = _CapturingProducer()
        c.app.state.producer = prod
        c.get("/", headers={"X-Sim-Pathology": "duplicate_delivery"})
        assert len(prod.messages) == 2
        v0, v1 = prod.messages[0][1], prod.messages[1][1]
        assert v0 == v1
        assert _json.loads(v0)["event_id"] == _json.loads(v1)["event_id"]


def test_pathology_field_mutations():
    import json as _json

    with TestClient(app) as c:
        prod = _CapturingProducer()
        c.app.state.producer = prod
        c.get(
            "/admin",
            headers={
                "X-Sim-Pathology": (
                    "missing_source_ip,missing_path,schema_drift,clock_skew_negative_response"
                )
            },
        )
        payload = _json.loads(prod.messages[-1][1])
        assert payload["source_ip"] is None
        assert payload["path"] is None
        assert payload["schema_version"] == "v2"
        assert "tls_fingerprint" in payload
        assert payload["response_time_ms"] < 0


def test_run_id_threaded_onto_event():
    import json as _json

    with TestClient(app) as c:
        prod = _CapturingProducer()
        c.app.state.producer = prod
        c.get("/", headers={"X-Run-Id": "run-abc-123"})
        assert _json.loads(prod.messages[-1][1])["run_id"] == "run-abc-123"


def test_healthz_does_not_set_a_session_cookie(client):
    # /healthz is infrastructure, not attacker-facing traffic — it never
    # touches the session tracker or the producer, unlike every other route.
    with TestClient(app) as fresh:
        response = fresh.get("/healthz")
        assert response.status_code == 200
        assert "batcave_sid" not in response.cookies
