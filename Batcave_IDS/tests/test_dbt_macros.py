"""Unit tests for the DuckDB-specific macros in transform/macros/.

These render the macro body the way dbt would and run it against literal values,
so each branch is proven without needing a corpus that happens to contain the
right rows. `is_valid_json` in particular has a branch that fires on roughly 3%
of requests (the malformed-body pathology), which a small corpus can miss
entirely — leaving the logic unverified exactly where it matters.
"""

import re
from pathlib import Path

import duckdb
import pytest

MACROS = Path(__file__).resolve().parent.parent / "transform" / "macros"


def _render(macro_file: str, macro_name: str, **args: str) -> str:
    """Extract a macro body and substitute its {{ params }} — enough Jinja for
    these single-expression macros, without standing up a dbt runtime."""
    source = (MACROS / macro_file).read_text(encoding="utf-8")
    match = re.search(
        rf"{{%-?\s*macro\s+{macro_name}\(.*?-?%}}(.*?){{%-?\s*endmacro\s*-?%}}",
        source,
        re.DOTALL,
    )
    assert match, f"macro {macro_name} not found in {macro_file}"
    body = match.group(1)
    body = re.sub(r"{#.*?#}", "", body, flags=re.DOTALL)
    for key, value in args.items():
        body = body.replace("{{ " + key + " }}", value)
    return body.strip()


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def _is_valid_json(con, body: str | None):
    expr = _render("is_valid_json.sql", "is_valid_json", column_name="b")
    literal = "null" if body is None else "'" + body.replace("'", "''") + "'"
    return con.sql(f"select {expr} from (select {literal}::varchar as b)").fetchone()[0]


@pytest.mark.parametrize(
    ("body", "expected", "why"),
    [
        (None, None, "no body at all (a GET)"),
        ("x" * 200, None, "filler bytes sizing body_bytes - never claimed to be JSON"),
        ("hello world", None, "plain text, not JSON-shaped"),
        ('{"ok": true}', True, "well-formed object"),
        ("[1, 2, 3]", True, "well-formed array"),
        ('{"truncated": ', False, "the malformed-body pathology's exact payload"),
        ('{"unbalanced": [1, 2}', False, "unbalanced brackets"),
        ('  {"leading": "space"}', True, "leading whitespace still JSON-shaped"),
    ],
)
def test_is_valid_json_branches(con, body, expected, why):
    assert _is_valid_json(con, body) is expected, why


def test_filler_bodies_do_not_count_as_invalid(con):
    """The regression this macro exists to prevent: judging non-JSON filler
    bodies with a bare json_valid() marked 43 of 59 requests invalid in a corpus
    with zero malformed-JSON rows, which would peg invalid_body_ratio near 1.0
    for every villain and drown the ~3% pathology it exists to surface."""
    assert _is_valid_json(con, "x" * 512) is None
    # A bare json_valid() would have said False here.
    assert con.sql("select json_valid('" + "x" * 8 + "')").fetchone()[0] is False


def test_only_json_shaped_bodies_are_ever_judged(con):
    """Anything that is judged must be JSON-shaped, so invalid_body_ratio
    averages over a population where the question is meaningful."""
    for body in (None, "xxx", "plain", "42", "true"):
        assert _is_valid_json(con, body) is None
    for body in ('{"a":1}', "[]", '{"bad": '):
        assert _is_valid_json(con, body) is not None
