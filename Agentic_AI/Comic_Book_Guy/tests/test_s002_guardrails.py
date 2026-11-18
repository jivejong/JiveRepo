from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as cbg_app

client = TestClient(cbg_app.app)


@pytest.fixture(autouse=True)
def reset_rate_limits():
    cbg_app.RATE_LIMIT_BUCKETS.clear()


def _track_genai(monkeypatch):
    calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="Ok.")

    monkeypatch.setattr(cbg_app.genai, "Client", lambda: SimpleNamespace(models=FakeModels()))
    return calls


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [("   ", "EMPTY_INPUT"), ("x" * 601, "INPUT_TOO_LONG")],
)
def test_r005_input_empty_and_length_boundary(monkeypatch, message, expected_code):
    calls = _track_genai(monkeypatch)
    res = client.post("/api/chat", json={"message": message, "history": [], "turn": 1})
    assert res.status_code == 400
    assert res.json()["error"] is True and res.json()["code"] == expected_code
    assert calls == []


def test_r006_multipart_upload_rejected(monkeypatch):
    calls = _track_genai(monkeypatch)
    res = client.post("/api/chat", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 415
    assert res.json()["error"] is True and res.json()["code"] == "UPLOAD_PROHIBITED"
    assert calls == []


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"message": "Hi", "history": [], "turn": 11}, "SESSION_TURN_LIMIT"),
        (
            {
                "message": "Hi",
                "history": [{"role": "user" if i % 2 == 0 else "model", "content": "m"} for i in range(9)],
                "turn": 1,
            },
            "INVALID_HISTORY",
        ),
    ],
)
def test_r007_session_turn_and_history_bounds(monkeypatch, payload, expected_code):
    calls = _track_genai(monkeypatch)
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 400
    assert res.json()["error"] is True and res.json()["code"] == expected_code
    assert calls == []


def test_r008_ip_rate_limit_blocks_21st_request(monkeypatch):
    calls = _track_genai(monkeypatch)
    headers = {"x-forwarded-for": "203.0.113.42"}
    for _ in range(20):
        assert client.post("/api/chat", json={"message": "Hi", "history": [], "turn": 1}, headers=headers).status_code == 200
    res = client.post("/api/chat", json={"message": "Hi", "history": [], "turn": 1}, headers=headers)
    assert res.status_code == 429
    assert res.json()["error"] is True and res.json()["code"] == "RATE_LIMITED"
    assert len(calls) == 20
