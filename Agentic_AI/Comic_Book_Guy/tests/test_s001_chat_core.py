from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as cbg_app

client = TestClient(cbg_app.app)


def _mock_genai(monkeypatch, reply_text="Worst. Question. Ever.", exc=None):
    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append({"model": model, "contents": contents, "config": config})
            if exc is not None:
                raise exc
            return SimpleNamespace(text=reply_text)

    monkeypatch.setattr(cbg_app.genai, "Client", lambda: SimpleNamespace(models=FakeModels()))
    return calls


def test_r001_r004_xml_persona_canon_and_paywall_rules(monkeypatch):
    calls = _mock_genai(monkeypatch)
    res = client.post("/api/chat", json={"message": "Thoughts on microservices?", "history": [], "turn": 1})
    assert res.status_code == 200
    instruction = calls[0]["config"].system_instruction
    assert "<persona>" in instruction and "<consulting_paywall>" in instruction
    for token in ("Simpsons", "Star Wars", "Harry Potter", "$300/hour", "$500/hour", "https://www.linkedin.com/company/cbg-retcon/"):
        assert token in instruction


def test_r002_single_bounded_inference_call(monkeypatch):
    calls = _mock_genai(monkeypatch, reply_text="Oh, please. Monoliths predate the Death Star.")
    res = client.post("/api/chat", json={"message": "Monolith or microservices?", "history": [], "turn": 1})
    assert res.status_code == 200
    assert res.json() == {"reply": "Oh, please. Monoliths predate the Death Star.", "error": False, "code": "OK"}
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3.1-flash-lite"
    assert calls[0]["config"].max_output_tokens == 450


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [(RuntimeError("boom"), 502), (TimeoutError("timed out"), 504)],
)
def test_r003_upstream_failure_in_character_fallback(monkeypatch, exc, expected_status):
    _mock_genai(monkeypatch, exc=exc)
    res = client.post("/api/chat", json={"message": "Hello?", "history": [], "turn": 1})
    body = res.json()
    assert res.status_code == expected_status
    assert body["error"] is True
    assert "Traceback" not in body["reply"] and len(body["reply"]) > 20
