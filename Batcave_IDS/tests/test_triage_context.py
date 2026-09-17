"""services.triage.context — built against a tiny in-memory warehouse so the
boundary and shape guarantees are checked without a real corpus.
"""

import duckdb
import pytest

from services.triage.context import FEATURE_COLUMNS, build_session_context


@pytest.fixture
def con():
    connection = duckdb.connect()
    cols = ", ".join(f"{c} double" for c in FEATURE_COLUMNS)
    connection.execute(f"create table int_session_features_observed (session_id varchar, {cols})")
    values = ", ".join("1" for _ in FEATURE_COLUMNS)
    connection.execute(f"insert into int_session_features_observed values ('s1', {values})")
    connection.execute("""
        create table mart_threat_scores (
            session_id varchar, threat_score double, component_max_path_tier double,
            component_enumeration double, component_exploit_evidence double,
            component_auth_failure double, component_error_ratio double,
            component_regularity double, component_evasion double, component_volume double
        )
    """)
    connection.execute("insert into mart_threat_scores values ('s1', 55.0, 1,1,1,1,1,1,1,1)")
    connection.execute("""
        create table fct_attack_events (
            session_id varchar, path varchar, status_returned int, user_agent varchar,
            request_body varchar, source_ip varchar, received_at timestamp
        )
    """)
    connection.execute("""
        insert into fct_attack_events values
        ('s1', '/login', 401, 'curl/8', repeat('x', 300), '198.51.100.7', '2026-01-01 00:00:00'),
        ('s1', '/admin', 200, 'curl/8', null, '198.51.100.7', '2026-01-01 00:00:01')
    """)
    connection.execute("""
        create table fct_botchat_turns (
            session_id varchar, turn_number int, speaker varchar, objective varchar,
            refused boolean, extracted_intent_flags varchar
        )
    """)
    return connection


def test_assembles_a_complete_context(con):
    ctx = build_session_context(con, "s1")
    assert ctx["session_id"] == "s1"
    assert ctx["threat_score"]["total"] == 55.0
    assert ctx["path_sequence"] == ["/login", "/admin"]
    assert ctx["status_code_distribution"] == {"401": 1, "200": 1}


def test_source_ip_is_truncated_to_three_octets(con):
    ctx = build_session_context(con, "s1")
    assert ctx["source_ip_prefix"] == "198.51.100.x"


def test_request_body_truncated_to_200_chars(con):
    ctx = build_session_context(con, "s1")
    assert len(ctx["request_bodies_sample"][0]) == 200


def test_missing_session_raises_rather_than_returning_empty(con):
    with pytest.raises(ValueError, match="no-such-session"):
        build_session_context(con, "no-such-session")


def test_path_sequence_truncates_at_forty_keeping_first_and_last_twenty(con):
    con.execute("delete from fct_attack_events")
    rows = [
        ("s1", f"/p{i}", 200, "curl/8", None, "198.51.100.7", f"2026-01-01 00:{i:02d}:00")
        for i in range(50)
    ]
    con.executemany("insert into fct_attack_events values (?,?,?,?,?,?,?)", rows)
    ctx = build_session_context(con, "s1")
    assert len(ctx["path_sequence"]) == 41  # 20 + marker + 20
    assert ctx["path_sequence"][:3] == ["/p0", "/p1", "/p2"]
    assert ctx["path_sequence"][-1] == "/p49"
    assert "truncated" in ctx["path_sequence"][20]
