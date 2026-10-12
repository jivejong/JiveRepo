"""Shared test support for the intake core: import paths, a fake clock, a scripted HTTP transport, canned model answers."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "intake"))
sys.path.insert(0, str(ROOT / "ingest" / "bridge"))   # files_api and bridge: the real modules, imported only to check against
sys.path.insert(0, str(ROOT / "edge"))                # forcesim.sectors: an independent loader of the same seed (parity only)

import files_api  # noqa: E402
import intake_core as core  # noqa: E402

NOW = datetime(2026, 10, 14, 15, 0, 0, tzinfo=timezone.utc)
REPORT_ID = "01KX0000000000000000000001"      # 26 Crockford characters, first <= 7
REPORT_ID_2 = "01KX0000000000000000000002"
KEY = "test-key-NOT-A-REAL-KEY-7f3a"
MODEL = "gemini-3.1-flash-lite"


def body(**overrides):
    """A valid browser request body (bytes); keyword arguments override fields, None removes one."""
    data = {"sector_id": "coruscant", "description": "Saw Darth Vader walking through the market.",
            "report_id": REPORT_ID, "reporter_id": "a3f2", "observed_at": "2026-10-14T14:59:00.000Z"}
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return json.dumps(data).encode("utf-8")


def model_answer(**overrides):
    """A generateContent response body (bytes) whose single candidate carries a JSON answer."""
    answer = {"relevance_score": 0.95, "midichlorian_ppm": 26400.0, "kyber_resonance": 30.0,
              "dark_side_activity": 85.0, "inference_rationale": "Named dark side figure."}
    answer.update(overrides)
    return json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(answer)}]}}],
                       "usageMetadata": {"promptTokenCount": 500}}).encode("utf-8")


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t


class ScriptedHttp:
    """Stands in for files_api.http_request. Each entry is (status, body) or an Exception to raise; `advance` moves the
    fake clock by that many seconds per call so the time budget can be exercised. Records every call."""

    def __init__(self, script, clock=None, advance=0.0):
        self.script, self.clock, self.advance = list(script), clock, advance
        self.calls = []
        self.sleeps = []

    def __call__(self, method, url, *, headers=None, data=None, timeout=60, opener=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "data": data, "timeout": timeout})
        if self.clock is not None:
            self.clock.t += self.advance
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.clock is not None:
            self.clock.t += seconds


class FakeResponse:
    def __init__(self, status, payload=b""):
        self.status, self._payload = status, payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Stands in for urllib.request.urlopen underneath the REAL files_api.http_request: records each Request."""

    def __init__(self, status=200, payload=b""):
        self.status, self.payload = status, payload
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self.status, self.payload)


# ---- the intake server (Round 3) ------------------------------------------------------------------------------
import threading  # noqa: E402


def report_ids(n):
    """The n-th distinct valid report id (a ULID)."""
    return core.new_ulid(1_788_000_000_000 + n, n)


def report_body(n=1, reporter="a3f2", **overrides):
    """A valid POST body for report number n from `reporter`; keyword arguments override fields (None removes one)."""
    fields = {"report_id": report_ids(n), "reporter_id": reporter}
    fields.update(overrides)
    return body(**fields)


class CountingModel:
    """Stands in for files_api.http_request towards the model. Counts calls; `respond(call_number)` -> (status, body);
    `gate`, when set, makes every call wait for it first (so a test can hold requests in flight)."""

    def __init__(self, respond=None, gate=None):
        self.respond = respond or (lambda n: (200, model_answer()))
        self.gate, self.calls, self._lock = gate, 0, threading.Lock()

    def __call__(self, method, url, *, headers=None, data=None, timeout=60, opener=None):
        with self._lock:
            self.calls += 1
            number = self.calls
        if self.gate is not None:
            self.gate.wait(10)
        return self.respond(number)


class FakePublisher:
    """Stands in for intake_mqtt.MqttPublisher. `error` makes publish() raise it; every attempt is recorded."""

    def __init__(self, ready=True, error=None):
        self.is_ready, self.error = ready, error
        self.attempts, self.lock = [], threading.Lock()

    def ready(self):
        return self.is_ready

    def publish(self, topic, payload, timeout=10.0):
        with self.lock:
            self.attempts.append({"topic": topic, "payload": payload, "timeout": timeout})
        if self.error is not None:
            raise self.error

    @property
    def published(self):
        return [a for a in self.attempts if self.error is None]
