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


def test_response_echoes_session_id(client):
    response = client.get("/")
    assert response.headers.get("x-session-id"), "every response must carry X-Session-Id"


def test_healthz_does_not_carry_a_session_id(client):
    # /healthz is infrastructure, not attacker-facing traffic — it never
    # touches the session tracker or the producer, unlike every other route.
    response = client.get("/healthz")
    assert response.status_code == 200
    assert "x-session-id" not in response.headers
