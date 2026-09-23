"""services.triage.console_triage — the single-session triage path the
Phase 10 finale uses to bypass the production threshold for exactly one
console session, checked against a small synthetic warehouse.

Two things this module must get right, both covered here: it refuses
anything that isn't a real console session (the scope-limiting guarantee -
this bypass must never become reachable for a headless session), and on a
real console session it reuses the same classify_villain/classify_
techniques/write_*_order machinery `run_triage` uses for every other
session, writing a row that `make eval`'s eval-boundary filter (session_
source != 'headless') then excludes.
"""

import duckdb
import pytest

from services.triage.baseline import CENTROID_FEATURES
from services.triage.console_triage import NotAConsoleSessionError, triage_console_session
from services.triage.context import FEATURE_COLUMNS


@pytest.fixture
def con():
    connection = duckdb.connect()
    cols = ", ".join(f"{c} double" for c in FEATURE_COLUMNS)
    connection.execute(
        f"create table int_session_features_observed (session_id varchar, run_id varchar, {cols})"
    )
    connection.execute(
        "create table fct_attack_runs "
        "(session_id varchar, run_id varchar, villain_slug varchar, session_source varchar)"
    )
    connection.execute("create table dim_villains (villain_slug varchar, archetype varchar)")
    connection.execute("create table dim_techniques (technique_id varchar, attack_id varchar)")

    # Two labeled reference sessions (headless), well-separated in feature
    # space, so classify_villain has something to nearest-centroid against -
    # mirrors test_triage_baseline.py's "loud"/"quiet" synthetic pair.
    for i in range(3):
        loud_values = {c: 100.0 + i for c in FEATURE_COLUMNS}
        quiet_values = {c: 1.0 + i * 0.1 for c in FEATURE_COLUMNS}
        connection.execute(
            "insert into int_session_features_observed values (?, ?, "
            + ",".join(["?"] * len(FEATURE_COLUMNS))
            + ")",
            [f"loud-session-{i}", f"loud-run-{i}"] + [loud_values[c] for c in FEATURE_COLUMNS],
        )
        connection.execute(
            "insert into fct_attack_runs values (?, ?, 'loud-villain', 'headless')",
            [f"loud-session-{i}", f"loud-run-{i}"],
        )
        connection.execute(
            "insert into int_session_features_observed values (?, ?, "
            + ",".join(["?"] * len(FEATURE_COLUMNS))
            + ")",
            [f"quiet-session-{i}", f"quiet-run-{i}"] + [quiet_values[c] for c in FEATURE_COLUMNS],
        )
        connection.execute(
            "insert into fct_attack_runs values (?, ?, 'quiet-villain', 'headless')",
            [f"quiet-session-{i}", f"quiet-run-{i}"],
        )

    connection.execute("insert into dim_villains values ('loud-villain', 'brute')")
    connection.execute("insert into dim_villains values ('quiet-villain', 'schemer')")

    # One console session, loud-shaped, so it should nearest-centroid to
    # loud-villain the same as any headless loud session would.
    console_values = {c: 100.5 for c in FEATURE_COLUMNS}
    connection.execute(
        "insert into int_session_features_observed values ('console-session', 'console-run', "
        + ",".join(["?"] * len(FEATURE_COLUMNS))
        + ")",
        [console_values[c] for c in FEATURE_COLUMNS],
    )
    connection.execute(
        "insert into fct_attack_runs values "
        "('console-session', 'console-run', 'loud-villain', 'console')"
    )
    return connection


def test_refuses_a_session_with_no_fct_attack_runs_row(con):
    with pytest.raises(NotAConsoleSessionError, match="no fct_attack_runs row"):
        triage_console_session(con, "session-that-does-not-exist", llm_client=None)


def test_refuses_a_headless_session(con):
    """The scope-limiting guarantee: this bypass must never be reachable
    for anything other than session_source='console', or the threshold it
    exists to bypass stops meaning anything for headless sessions too."""
    with pytest.raises(NotAConsoleSessionError, match="not 'console'"):
        triage_console_session(con, "loud-session-0", llm_client=None)


def test_triages_a_console_session_with_the_baseline_when_no_llm_client(con):
    result = triage_console_session(con, "console-session", llm_client=None)
    assert result.source == "baseline"
    assert result.suspected_villain == "loud-villain"
    assert result.confidence is not None


def test_writes_a_baseline_order_row_for_the_console_session(con):
    triage_console_session(con, "console-session", llm_client=None)
    row = con.execute(
        "select order_id, session_id, run_id, source, suspected_villain "
        "from raw_triage_predictions where session_id = 'console-session'"
    ).fetchone()
    assert row == (
        "console-session:baseline",
        "console-session",
        "console-run",
        "baseline",
        "loud-villain",
    )


def test_never_writes_an_llm_order_when_llm_client_is_none(con):
    triage_console_session(con, "console-session", llm_client=None)
    n = con.execute(
        "select count(*) from raw_triage_predictions "
        "where session_id = 'console-session' and source = 'llm'"
    ).fetchone()[0]
    assert n == 0


def test_re_triaging_the_same_session_overwrites_rather_than_duplicates(con):
    triage_console_session(con, "console-session", llm_client=None)
    triage_console_session(con, "console-session", llm_client=None)
    n = con.execute(
        "select count(*) from raw_triage_predictions where session_id = 'console-session'"
    ).fetchone()[0]
    assert n == 1


def test_does_not_disturb_centroid_features_constant_assumption(con):
    """Sanity check on the fixture itself: CENTROID_FEATURES must be a
    subset of FEATURE_COLUMNS, or int_session_features_observed as built
    above wouldn't actually carry what build_reference_profiles reads."""
    assert set(CENTROID_FEATURES) <= set(FEATURE_COLUMNS)
