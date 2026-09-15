"""Cookie-based, gap-enforced session derivation (services/honeypot/session.py).

Uses explicit epoch timestamps rather than real sleeps, so the boundary
case is exact and fast rather than flaky.
"""

from services.honeypot.session import SessionTracker


def test_no_cookie_mints_a_fresh_session():
    tracker = SessionTracker(gap_seconds=120)
    sid = tracker.session_id_for(None, now_epoch=1000.0)
    assert sid  # a real token, not None/empty


def test_returned_cookie_within_gap_continues_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for(None, now_epoch=1000.0)
    sid2 = tracker.session_id_for(sid1, now_epoch=1050.0)
    assert sid1 == sid2


def test_unknown_cookie_mints_a_fresh_session():
    tracker = SessionTracker(gap_seconds=120)
    sid = tracker.session_id_for("never-issued-this-token", now_epoch=1000.0)
    assert sid != "never-issued-this-token"


def test_rotation_stays_one_session_when_cookie_is_carried():
    """The whole point of the cookie: identity survives IP/UA rotation. The
    tracker only sees the cookie, so a rotating attacker who carries it stays
    one session — IP/UA aren't part of the key anymore."""
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for(None, now_epoch=1000.0)
    sid2 = tracker.session_id_for(sid1, now_epoch=1001.0)
    sid3 = tracker.session_id_for(sid1, now_epoch=1002.0)
    assert sid1 == sid2 == sid3


def test_gap_boundary_just_under_continues_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for(None, now_epoch=1000.0)
    sid2 = tracker.session_id_for(sid1, now_epoch=1000.0 + 119.9)
    assert sid1 == sid2


def test_gap_boundary_just_over_starts_new_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for(None, now_epoch=1000.0)
    sid2 = tracker.session_id_for(sid1, now_epoch=1000.0 + 120.1)
    assert sid1 != sid2


def test_stale_sessions_are_evicted():
    tracker = SessionTracker(gap_seconds=10, eviction_multiple=2.0)
    sid = tracker.session_id_for(None, now_epoch=0.0)
    assert len(tracker) == 1
    # Far past eviction_after (10 * 2 = 20s): a request under a different
    # (new) cookie triggers a sweep that drops the first session.
    tracker.session_id_for(None, now_epoch=100.0)
    assert len(tracker) == 1
    _ = sid  # first token is now evicted
