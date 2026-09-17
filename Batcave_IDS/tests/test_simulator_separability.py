"""services.simulator.separability's topic-derived villain mapping.

Found live in Phase 6: an earlier version of `session_to_villain_from_attack_runs`
matched every `attack_run` event on the topic with no filter. The topic is
shared and retained (24h), so it pulled in an unrelated 144-session
`make pathology-check` corpus alongside this harness's own 120-session run
(120+144=264, exactly what got returned) - and the pathology corpus's
clock-skew/out-of-order pathologies produced six-figure-millisecond
`inter_request_stddev_ms` values that would have been reported as real
separability findings. Caught by the session count being wildly larger than
the corpus actually generated, not by these tests - they exist so the same
contamination can't happen silently again.
"""

from services.simulator.separability import session_to_villain_from_attack_runs


def _attack_run(session_id, villain_slug, *, time_scale=0.2, pathologies=None):
    return {
        "event_kind": "attack_run",
        "session_id": session_id,
        "villain_slug": villain_slug,
        "timing_compression_factor": time_scale,
        "pathologies_enabled": pathologies or [],
    }


def test_excludes_any_run_with_pathologies_enabled():
    """This harness never injects pathologies (run_corpus passes no injector),
    so a pathology corpus sharing the topic must never contribute rows -
    regardless of its time_scale matching."""
    events = [
        _attack_run("clean-1", "370-joker", pathologies=[]),
        _attack_run("dirty-1", "370-joker", pathologies=["duplicate_delivery"]),
    ]
    mapping = session_to_villain_from_attack_runs(events, time_scale=0.2)
    assert mapping == {"clean-1": "370-joker"}


def test_excludes_a_different_time_scale():
    """Two clean separability runs at different scales can coexist on the
    topic; only the one matching the requested scale must be picked up."""
    events = [
        _attack_run("fast-1", "370-joker", time_scale=0.02),
        _attack_run("slow-1", "370-joker", time_scale=0.2),
    ]
    mapping = session_to_villain_from_attack_runs(events, time_scale=0.2)
    assert mapping == {"slow-1": "370-joker"}


def test_the_264_session_regression_scenario():
    """Reproduces the exact contamination found live: a 2-session clean corpus
    at 0.2 sharing the topic with a 3-session pathology corpus. Unfiltered,
    this returned all 5; filtered, it must return only the 2 clean ones."""
    events = [
        _attack_run("clean-a", "370-joker", time_scale=0.2, pathologies=[]),
        _attack_run("clean-b", "558-riddler", time_scale=0.2, pathologies=[]),
        _attack_run("dirty-a", "370-joker", time_scale=0.2, pathologies=["clock_skew"]),
        _attack_run("dirty-b", "558-riddler", time_scale=0.2, pathologies=["malformed_body"]),
        _attack_run("dirty-c", "60-bane", time_scale=0.2, pathologies=["duplicate_delivery"]),
    ]
    mapping = session_to_villain_from_attack_runs(events, time_scale=0.2)
    assert mapping == {"clean-a": "370-joker", "clean-b": "558-riddler"}


def test_ignores_non_attack_run_events():
    events = [
        {"event_kind": "request", "session_id": "s1"},
        _attack_run("s2", "165-catwoman"),
    ]
    mapping = session_to_villain_from_attack_runs(events, time_scale=0.2)
    assert mapping == {"s2": "165-catwoman"}


def test_no_time_scale_filter_when_none_passed():
    """time_scale=None (the unscoped call) matches on pathology-free alone -
    used only when the caller has no scale to compare against."""
    events = [
        _attack_run("a", "370-joker", time_scale=0.02),
        _attack_run("b", "370-joker", time_scale=1.0),
    ]
    mapping = session_to_villain_from_attack_runs(events, time_scale=None)
    assert mapping == {"a": "370-joker", "b": "370-joker"}


def test_missing_timing_compression_factor_is_excluded_when_scoped():
    """A malformed/older event missing the field must not slip through a
    scoped match by accident."""
    events = [{"event_kind": "attack_run", "session_id": "s1", "villain_slug": "370-joker"}]
    assert session_to_villain_from_attack_runs(events, time_scale=0.2) == {}
