"""Gap-based session derivation (services/honeypot/session.py).

Uses explicit epoch timestamps rather than real sleeps, so the boundary
case is exact and fast rather than flaky.
"""

from services.honeypot.session import SessionTracker


def test_same_key_within_gap_shares_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0)
    sid2 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1050.0)
    assert sid1 == sid2


def test_different_ip_starts_new_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0)
    sid2 = tracker.session_id_for("5.6.7.8", "curl/8.0", now_epoch=1000.0)
    assert sid1 != sid2


def test_different_user_agent_starts_new_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0)
    sid2 = tracker.session_id_for("1.2.3.4", "python-requests/2.0", now_epoch=1000.0)
    assert sid1 != sid2


def test_gap_boundary_just_under_continues_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0)
    sid2 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0 + 119.9)
    assert sid1 == sid2


def test_gap_boundary_just_over_starts_new_session():
    tracker = SessionTracker(gap_seconds=120)
    sid1 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0)
    sid2 = tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=1000.0 + 120.1)
    assert sid1 != sid2


def test_stale_keys_are_evicted():
    tracker = SessionTracker(gap_seconds=10, eviction_multiple=2.0)
    tracker.session_id_for("1.2.3.4", "curl/8.0", now_epoch=0.0)
    assert len(tracker) == 1
    # Far past eviction_after (10 * 2 = 20s): a request from a different key
    # should trigger a sweep that drops the first key.
    tracker.session_id_for("9.9.9.9", "curl/8.0", now_epoch=100.0)
    assert len(tracker) == 1
