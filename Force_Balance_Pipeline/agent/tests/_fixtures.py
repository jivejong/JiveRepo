"""Shared fixtures for the loop, prompt and Gemini tests: a built context and scripted model answers."""
import copy
from datetime import datetime, timezone

import _support as S  # noqa: F401  (sets the import paths)
from agent.context import build_context

DETECTED = datetime(2026, 10, 14, 7, 15, 0, 900000, tzinfo=timezone.utc)


def row(**overrides):
    base = {
        "disturbance_id": "01K4SIGFB41DED3N5O5F1AM", "sector_id": "geonosis", "detected_at": DETECTED, "scan_id": "scan-1",
        "imbalance_score": 8.0, "z_midi": 0.4, "z_kyber": -2.8, "z_dark": 5.2, "signature": "sith_presence", "severity": 8.0,
        "is_report_sourced": False, "report_description": None, "report_relevance": None, "sustained_scans": 3,
        "sector_name": "Geonosis", "system_name": "Geonosis system", "region": "Outer Rim", "population": 100000000000.0,
        "description": "A desert world.", "force_history": "A history.", "channels_present": 3, "disturbances_last_7d": 2,
        "deployments_last_7d": 1, "last_signature": "dark_adept", "open_deployments": 1,
    }
    base.update(overrides)
    return base


def jedi(jedi_id, rank, primary, secondary=None, region="Core Worlds"):
    return {"jedi_id": jedi_id, "jedi_name": jedi_id.title(), "rank": rank, "primary_specialty": primary,
            "secondary_specialty": secondary, "power_rating": 5, "lightsaber_form": "Form I", "notable_for": "x",
            "homeworld_region": region}


def ship(ship_id, rating=1.0, crew=1, passengers=0):
    return {"starship_id": ship_id, "starship_name": ship_id, "starship_class": "x", "hyperdrive_rating": rating, "mglt": None,
            "crew": crew, "passengers": passengers}


JEDI = [jedi("amy", "knight", "combat"), jedi("bob", "master", "combat", "stealth"),
        jedi("cat", "padawan", "stealth", "combat", region="Outer Rim"), jedi("zed", "council_member", "diplomacy")]
SHIPS = [ship("a_ship", 2.0, 4, 6), ship("b_ship", 1.0, 1, 0)]


def context(**overrides):
    return build_context(row(**overrides), copy.deepcopy(JEDI), copy.deepcopy(SHIPS))


# ---- scripted model answers ---------------------------------------------------------------------------------------
def answer(parts, usage=None):
    """A generateContent response whose model content holds these parts."""
    data = {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}
    if usage is not None:
        data["usageMetadata"] = usage
    return data


def call(name, args=None, call_id=None, signature=None):
    part = {"functionCall": {"name": name, "args": args or {}}}
    if call_id is not None:
        part["functionCall"]["id"] = call_id
    if signature is not None:
        part["thoughtSignature"] = signature
    return part


def deploy(jedi_ids, starship_id, rationale="Sustained telemetry on a combat signature warrants a response."):
    return call("deploy", {"jedi_ids": jedi_ids, "starship_id": starship_id, "rationale": rationale})


def stand_down(rationale="The reading is marginal."):
    return call("stand_down", {"rationale": rationale})


class ScriptedClient:
    """Stands in for GeminiClient: returns scripted answers (or raises scripted exceptions) and records what it was sent."""
    model = "test-model"

    def __init__(self, script, repeat_last=False):
        self.script = list(script)
        self.repeat_last = repeat_last
        self.sent = []

    @property
    def calls(self):
        return len(self.sent)

    def generate(self, contents):
        self.sent.append(copy.deepcopy(contents))
        if not self.script:
            raise AssertionError("the scripted client has no answer left")
        item = self.script[0] if (self.repeat_last and len(self.script) == 1) else self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StepClock:
    """monotonic() that advances by step seconds every time it is read."""

    def __init__(self, step=0.25):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now
