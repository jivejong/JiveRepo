"""The honeypot's route table: path -> (tier, status, body, extra headers).

Tiers and statuses are exactly docs/01-architecture.md's table. Response bodies
are this phase's own reasonable filling of "canned responses" — the doc
specifies tier/status/theme, not literal content.

Matched by regex in declaration order; the first match wins. Nothing here
evaluates, forwards, or executes anything from the request — every handler is
a static, canned response (CLAUDE.md hard constraint).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteMatch:
    tier: int
    status_code: int
    body: dict
    extra_headers: dict[str, str]


def _landing(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(0, 200, {"page": "welcome"}, {})


def _login(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(1, 401, {"error": "invalid_credentials", "hint": "username recognized"}, {})


def _status(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(1, 200, {"status": "operational", "version": "1.0"}, {})


def _users(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(2, 200, {"users": ["bwayne", "adfries", "hquinzel"], "total": 47}, {})


def _payroll(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(2, 403, {"error": "forbidden"}, {})


def _vehicle_bay(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(2, 200, {"bay_1": "occupied", "bay_2": "unknown", "bay_3": "occupied"}, {})


def _admin(_: re.Match[str]) -> RouteMatch:
    return RouteMatch(3, 401, {"error": "unauthorized"}, {})


def _archives(match: re.Match[str]) -> RouteMatch:
    archive_id = match.group("id")
    return RouteMatch(
        3, 200, {"id": archive_id, "classification": "restricted", "summary": "redacted"}, {}
    )


def _knightfall(_: re.Match[str]) -> RouteMatch:
    # Leaks a header suggesting the protocol exists despite the 403 body.
    return RouteMatch(4, 403, {"error": "forbidden"}, {"X-Protocol-Status": "exists"})


def _not_found() -> RouteMatch:
    return RouteMatch(0, 404, {"error": "not_found"}, {})


_ROUTES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], RouteMatch]]] = [
    (re.compile(r"^/$"), _landing),
    (re.compile(r"^/login$"), _login),
    (re.compile(r"^/api/v1/status$"), _status),
    (re.compile(r"^/api/v1/users$"), _users),
    (re.compile(r"^/wayne-enterprises/payroll$"), _payroll),
    (re.compile(r"^/cave/vehicle-bay/status$"), _vehicle_bay),
    (re.compile(r"^/admin$"), _admin),
    (re.compile(r"^/cave/archives/(?P<id>[^/]+)$"), _archives),
    (re.compile(r"^/api/v1/protocol/knightfall$"), _knightfall),
]


def match_route(path: str) -> RouteMatch:
    for pattern, handler in _ROUTES:
        m = pattern.match(path)
        if m is not None:
            return handler(m)
    return _not_found()
