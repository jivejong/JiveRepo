"""Deliberate data pathologies (docs/03-attack-simulation.md).

Pathologies corrupt the OBSERVED stream only — the honeypot's request events,
never ground-truth attempt events or attack_runs. The simulator is the single
decision-maker (it owns the run and its RNG); execution is split:

- **simulator-direct** — the simulator changes what it *sends* (malformed body,
  client_ts value, burst pacing). No honeypot pathology code needed.
- **honeypot-executed** — signaled to the honeypot via the `X-Sim-Pathology`
  request header (comma-separated tokens); the honeypot applies them at event
  construction / produce. See services/honeypot/app.py.

`PathologyInjector.decide(...)` rolls each enabled per-request pathology once
and returns which fired, so a run's telemetry carries the defects at their
configured rates. `schema_drift` and `burst` are per-run, decided separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from random import Random

import yaml

_YML = Path(__file__).with_name("pathologies.yml")

# Tokens the honeypot executes (sent in X-Sim-Pathology).
HONEYPOT_TOKENS = frozenset(
    {
        "duplicate_delivery",
        "unkeyed",
        "undeserializable",
        "missing_source_ip",
        "missing_path",
        "schema_drift",
        "clock_skew_future_received_at",
        "clock_skew_negative_response",
    }
)
# Pathologies the simulator applies to its own outgoing request instead.
SIMULATOR_TOKENS = frozenset({"out_of_order_client_ts", "late_arrival", "malformed_body", "burst"})


@dataclass
class PathologyConfig:
    per_request: dict[str, float]  # token -> rate
    per_run: frozenset[str]  # tokens that fire once per run
    enabled: frozenset[str]  # every enabled token (for attack_runs)

    @classmethod
    def load(cls, path: Path = _YML) -> PathologyConfig:
        raw = yaml.safe_load(path.read_text())
        per_request: dict[str, float] = {}
        per_run: set[str] = set()
        enabled: set[str] = set()
        for token, spec in raw.items():
            if not spec.get("enabled", False):
                continue
            enabled.add(token)
            if spec.get("per_run", False):
                per_run.add(token)
            else:
                per_request[token] = float(spec["rate"])
        return cls(per_request=per_request, per_run=frozenset(per_run), enabled=frozenset(enabled))


@dataclass
class RequestPathologies:
    """What fired for one request."""

    honeypot_tokens: list[str] = field(default_factory=list)  # -> X-Sim-Pathology
    malformed_body: bool = False
    client_ts_offset_s: float | None = None  # simulator sets X-Client-Ts accordingly

    def header_value(self) -> str | None:
        return ",".join(self.honeypot_tokens) if self.honeypot_tokens else None


class PathologyInjector:
    def __init__(self, config: PathologyConfig, rng: Random) -> None:
        self._config = config
        self._rng = rng
        # schema_drift: once armed (mid-run), stays on for the rest of the run.
        self._schema_drifted = False

    @property
    def config(self) -> PathologyConfig:
        return self._config

    def arm_schema_drift(self) -> None:
        self._schema_drifted = True

    @property
    def schema_drift_active(self) -> bool:
        return self._schema_drifted

    def decide(self) -> RequestPathologies:
        out = RequestPathologies()
        for token, rate in self._config.per_request.items():
            if self._rng.random() >= rate:
                continue
            if token == "malformed_body":
                out.malformed_body = True
            elif token == "out_of_order_client_ts":
                # shuffled within a 30s window
                out.client_ts_offset_s = self._rng.uniform(-30.0, 30.0)
            elif token == "late_arrival":
                # 1-6 hours old
                out.client_ts_offset_s = -self._rng.uniform(3600.0, 21600.0)
            elif token in HONEYPOT_TOKENS:
                out.honeypot_tokens.append(token)
        if self._schema_drifted and "schema_drift" in self._config.per_run:
            out.honeypot_tokens.append("schema_drift")
        return out
