"""Drives the honeypot for attempts where `produces_traffic` is true,
carrying `X-Attempt-Id` so the resulting `request` event(s) correlate with
the `attempt` event via the same attempt_id (docs/07, Phase 2 checkpoint).

This is a Phase 2 minimum, not Phase 3's full villain-signature traffic
shaping: one static technique_id -> [(method, path), ...] mapping, loosely
honoring each technique's own detection_signature text (a small fixed
burst for the enumeration-flavored techniques, one request for the rest).
Phase 3 replaces this with real per-villain behavior; the plumbing here
(attempt_id header, response consumption) doesn't need to change when it
does.

`deploy_batbot` targets a path the honeypot's route table doesn't define
(`/api/v1/assistant`) — deliberately: docs/07 says it "produces no
conversation until Track B exists," and a 404 against a not-yet-built
assistant endpoint is a more honest Track A stand-in than inventing a
Track-B-flavored route early.
"""

from __future__ import annotations

import httpx

_REQUEST_SPECS: dict[str, list[tuple[str, str]]] = {
    "port_sweep": [("GET", "/"), ("GET", "/api/v1/status")],
    "active_scan": [
        ("GET", "/"),
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/users"),
        ("GET", "/admin"),
    ],
    "brute_force": [("POST", "/login")],
    "exploit_public_app": [("GET", "/cave/archives/case-0001?file=../../etc/passwd")],
    "valid_accounts": [("POST", "/login")],
    "external_remote_svc": [("GET", "/cave/vehicle-bay/status")],
    "account_discovery": [("GET", "/api/v1/users")],
    "remote_services": [("GET", "/cave/vehicle-bay/status")],
    "privesc_exploit": [("GET", "/admin")],
    "exploit_remote_svc": [("POST", "/cave/vehicle-bay/status?cmd=;cat%20/etc/shadow")],
    "alt_auth_material": [("GET", "/admin")],
    "indicator_removal": [("DELETE", "/cave/archives/audit-log-0001")],
    "data_local_system": [
        ("GET", "/cave/archives/case-0001"),
        ("GET", "/api/v1/protocol/knightfall"),
    ],
    "exfil_over_c2": [("POST", "/api/v1/status")],
    "deploy_batbot": [("POST", "/api/v1/assistant")],
}


def request_specs_for(technique_id: str) -> list[tuple[str, str]]:
    return _REQUEST_SPECS.get(technique_id, [])


def send_requests(
    client: httpx.Client,
    specs: list[tuple[str, str]],
    attempt_id: str,
    extra_headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> list[httpx.Response]:
    """Send an explicit list of (method, path) specs, all tagged with the same
    attempt_id. The session layer builds `specs` — starting from
    request_specs_for(technique) and then applying the villain's Layer 2
    signature (services/simulator/signatures.py) — so signature-shaped traffic
    (Two-Face's duplicates, Riddler's riddle params, Joker's absurd methods)
    flows through this one path. `body` attaches to write methods only."""
    headers = {"X-Attempt-Id": attempt_id, **(extra_headers or {})}
    responses = []
    for method, path in specs:
        content = body if (body and method in ("POST", "PUT", "PATCH")) else None
        responses.append(client.request(method, path, headers=headers, content=content))
    return responses


def drive_honeypot(
    client: httpx.Client,
    technique_id: str,
    attempt_id: str,
    extra_headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> list[httpx.Response]:
    """Convenience: send a technique's own request specs unmodified. Used by
    tests and any caller that doesn't apply a signature."""
    return send_requests(client, request_specs_for(technique_id), attempt_id, extra_headers, body)
