"""The traversal / injection matchers, checked against the payloads the
simulator actually emits.

This is the guard against the specific failure mode docs/02 flags for these two
features: a matcher that quietly matches nothing. The feature then computes zero
forever, and `assert_high_observability_techniques_leave_evidence` fails much
later for a reason nobody connects back to a regex.

The payloads are IMPORTED from services/simulator/traffic.py rather than copied,
so changing a simulator payload without updating the matcher fails here — at the
seam, immediately — instead of surfacing as an unexplained zero in a mart.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
import pytest

from services.simulator.traffic import _REQUEST_SPECS

MACROS = Path(__file__).resolve().parent.parent / "transform" / "macros" / "attack_patterns.sql"

# Which catalog techniques are supposed to leave which kind of evidence.
# Mirrors the technique -> evidence mapping the dbt test uses (docs/02).
TRAVERSAL_TECHNIQUES = {"exploit_public_app"}
INJECTION_TECHNIQUES = {"exploit_remote_svc"}


def _render(macro_name: str) -> str:
    source = MACROS.read_text(encoding="utf-8")
    match = re.search(
        rf"{{%-?\s*macro\s+{macro_name}\(.*?-?%}}(.*?){{%-?\s*endmacro\s*-?%}}",
        source,
        re.DOTALL,
    )
    assert match, f"macro {macro_name} not found"
    body = re.sub(r"{#.*?#}", "", match.group(1), flags=re.DOTALL)
    # Inline the _haystack() helper the two matchers share.
    if macro_name != "_haystack":
        body = body.replace("{{ _haystack() }}", f"({_render('_haystack')})")
    return body.strip()


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def _matches(con, macro: str, path: str, query: str | None, body: str | None) -> bool:
    expr = _render(macro)

    def lit(v):
        return "null" if v is None else "'" + v.replace("'", "''") + "'"

    sql = (
        f"select {expr} from (select {lit(path)}::varchar as path, "
        f"{lit(query)}::varchar as query_string, {lit(body)}::varchar as request_body)"
    )
    return con.sql(sql).fetchone()[0]


def _split(spec_path: str) -> tuple[str, str | None]:
    """Split a traffic.py spec the way the honeypot stores it: `path` and the
    RAW percent-encoded `query_string` (request.url.query)."""
    parts = urlsplit(spec_path)
    return parts.path, parts.query or None


# --- the payloads the simulator really sends ----------------------------


@pytest.mark.parametrize("technique_id", sorted(TRAVERSAL_TECHNIQUES))
def test_traversal_matcher_catches_the_simulators_payload(con, technique_id):
    specs = _REQUEST_SPECS[technique_id]
    assert specs, f"{technique_id} emits no requests — the mapping is stale"
    path, query = _split(specs[0][1])
    assert _matches(con, "is_traversal_pattern", path, query, None), (
        f"traversal matcher does not match {technique_id}'s payload "
        f"(path={path!r} query={query!r}) — the feature would compute zero forever"
    )


@pytest.mark.parametrize("technique_id", sorted(INJECTION_TECHNIQUES))
def test_injection_matcher_catches_the_simulators_payload(con, technique_id):
    specs = _REQUEST_SPECS[technique_id]
    assert specs, f"{technique_id} emits no requests — the mapping is stale"
    path, query = _split(specs[0][1])
    assert _matches(con, "is_injection_pattern", path, query, None), (
        f"injection matcher does not match {technique_id}'s payload "
        f"(path={path!r} query={query!r}) — the feature would compute zero forever"
    )


def test_the_mapped_techniques_still_exist_in_the_catalog():
    """If a technique is renamed or dropped, this mapping is stale and the
    tests above would vacuously pass on an empty parametrize."""
    for technique_id in TRAVERSAL_TECHNIQUES | INJECTION_TECHNIQUES:
        assert technique_id in _REQUEST_SPECS, f"{technique_id} gone from traffic.py"


def test_percent_encoding_is_handled(con):
    """The injection payload arrives as `cmd=;cat%20/etc/shadow` — query_string
    is stored raw from request.url.query, so %20 not a space. A matcher written
    against the decoded form would miss every real one."""
    assert _matches(
        con, "is_injection_pattern", "/cave/vehicle-bay/status", "cmd=;cat%20/etc/shadow", None
    )


def test_traversal_and_injection_do_not_overlap(con):
    """The two features map to two different techniques, so a payload that is
    one must not register as the other — otherwise Phase 6 sees injection
    evidence for a session that only performed traversal."""
    traversal_only = ("/cave/archives/case-0001", "file=../../etc/passwd", None)
    injection_only = ("/cave/vehicle-bay/status", "cmd=;cat%20/etc/shadow", None)

    assert _matches(con, "is_traversal_pattern", *traversal_only)
    assert not _matches(con, "is_injection_pattern", *traversal_only)

    assert _matches(con, "is_injection_pattern", *injection_only)
    assert not _matches(con, "is_traversal_pattern", *injection_only)


# --- it must not match everything ---------------------------------------


@pytest.mark.parametrize(
    ("path", "query", "body"),
    [
        ("/", None, None),
        ("/api/v1/status", None, None),
        ("/login", None, "x" * 200),
        ("/admin", None, None),
        ("/api/v1/users", None, None),
        ("/cave/archives/case-0001", None, None),
        ("/cave/vehicle-bay/status", None, None),
        # Riddler's signature adds a riddle= param to ordinary paths.
        ("/api/v1/status", "riddle=what-walks-on-four-legs", None),
    ],
)
def test_benign_traffic_matches_neither(con, path, query, body):
    """A matcher that fires on everything is as useless as one that fires on
    nothing — it would make every villain look like an exploit attempt."""
    assert not _matches(con, "is_traversal_pattern", path, query, body)
    assert not _matches(con, "is_injection_pattern", path, query, body)


def test_every_benign_catalog_path_is_clean(con):
    """Sweep the whole catalog: only the two exploit techniques should match."""
    for technique_id, specs in _REQUEST_SPECS.items():
        expected_traversal = technique_id in TRAVERSAL_TECHNIQUES
        expected_injection = technique_id in INJECTION_TECHNIQUES
        for _method, spec_path in specs:
            path, query = _split(spec_path)
            assert _matches(con, "is_traversal_pattern", path, query, None) == expected_traversal, (
                f"traversal matcher disagrees on {technique_id} ({spec_path})"
            )
            assert _matches(con, "is_injection_pattern", path, query, None) == expected_injection, (
                f"injection matcher disagrees on {technique_id} ({spec_path})"
            )


def test_matcher_finds_payloads_in_the_body_too(con):
    """Evidence can arrive in any of path, query, or body."""
    assert _matches(con, "is_traversal_pattern", "/upload", None, '{"f": "../../etc/passwd"}')
    assert _matches(con, "is_injection_pattern", "/upload", None, '{"c": "; cat /etc/shadow"}')
