"""services.triage.baseline — the nearest-centroid classifier and hard
discriminators, checked against a small synthetic warehouse (no live corpus
needed) and against docs/02's own disambiguating-feature list.
"""

import duckdb
import pytest

from services.triage.baseline import (
    CENTROID_FEATURES,
    HARD_DISCRIMINATORS,
    TECHNIQUE_EVIDENCE_RULES,
    build_reference_profiles,
    classify_techniques,
    classify_villain,
)


def _feature_row(**overrides):
    row = dict.fromkeys(CENTROID_FEATURES, 0.0)
    row.update(overrides)
    return row


@pytest.fixture
def con():
    connection = duckdb.connect()
    cols = ", ".join(f"{c} double" for c in CENTROID_FEATURES)
    connection.execute(f"create table int_session_features_observed (run_id varchar, {cols})")
    connection.execute("create table fct_attack_runs (run_id varchar, villain_slug varchar)")

    # Two well-separated synthetic villains: "loud" has high everything,
    # "quiet" has low everything - trivially separable, to check the
    # mechanics without needing the real corpus's noise.
    for i in range(5):
        connection.execute(
            "insert into int_session_features_observed values (?, "
            + ",".join(["?"] * len(CENTROID_FEATURES))
            + ")",
            [f"loud-{i}"] + [100.0 + i] * len(CENTROID_FEATURES),
        )
        connection.execute("insert into fct_attack_runs values (?, 'loud-villain')", [f"loud-{i}"])
        connection.execute(
            "insert into int_session_features_observed values (?, "
            + ",".join(["?"] * len(CENTROID_FEATURES))
            + ")",
            [f"quiet-{i}"] + [1.0 + i * 0.1] * len(CENTROID_FEATURES),
        )
        connection.execute(
            "insert into fct_attack_runs values (?, 'quiet-villain')", [f"quiet-{i}"]
        )
    return connection


def test_build_reference_profiles_has_one_centroid_per_villain(con):
    profiles = build_reference_profiles(con)
    assert set(profiles["centroids"]) == {"loud-villain", "quiet-villain"}


def test_nearest_centroid_classifies_a_clear_case_correctly(con):
    profiles = build_reference_profiles(con)
    loud_session = _feature_row(**{f: 102.0 for f in CENTROID_FEATURES})
    predicted, alternates, confidence = classify_villain(loud_session, profiles)
    assert predicted == "loud-villain"
    assert confidence > 0


def test_build_reference_profiles_raises_on_empty_warehouse():
    empty = duckdb.connect()
    cols = ", ".join(f"{c} double" for c in CENTROID_FEATURES)
    empty.execute(f"create table int_session_features_observed (run_id varchar, {cols})")
    empty.execute("create table fct_attack_runs (run_id varchar, villain_slug varchar)")
    with pytest.raises(ValueError, match="no labeled sessions"):
        build_reference_profiles(empty)


# --- hard discriminators: docs/02's own list, made executable ------------


def test_riddler_discriminator_fires_on_any_riddle_param():
    features = _feature_row(riddle_param_count=1)
    assert HARD_DISCRIMINATORS["558-riddler"](features) is True


def test_riddler_discriminator_does_not_fire_without_riddle_params():
    features = _feature_row(riddle_param_count=0)
    assert HARD_DISCRIMINATORS["558-riddler"](features) is False


def test_twoface_discriminator_needs_at_least_two_duplicate_paths():
    assert HARD_DISCRIMINATORS["678-two-face"](_feature_row(exact_duplicate_path_pairs=2)) is True
    assert HARD_DISCRIMINATORS["678-two-face"](_feature_row(exact_duplicate_path_pairs=1)) is False


def test_penguin_discriminator_needs_ip_rotation():
    assert HARD_DISCRIMINATORS["514-penguin"](_feature_row(distinct_source_ips=2)) is True
    assert HARD_DISCRIMINATORS["514-penguin"](_feature_row(distinct_source_ips=1)) is False


def test_croc_discriminator_is_volume_capped_at_low_tier():
    """The ground-truth discriminator (attempts_per_stage_reached) isn't
    observable - this proxy requires BOTH high volume and a capped tier,
    not just one."""
    assert HARD_DISCRIMINATORS["386-killer-croc"](_feature_row(request_count=40, max_path_tier=1))
    # High volume alone, at a high tier, is not Croc's shape.
    assert not HARD_DISCRIMINATORS["386-killer-croc"](
        _feature_row(request_count=40, max_path_tier=4)
    )


def test_hard_discriminator_overrides_nearest_centroid(con):
    """A session that LOOKS like loud-villain by centroid distance but
    trips Riddler's discriminator must still be called Riddler - the
    override is deliberate (docs/02: signature features as hard
    discriminators, not just distance inputs)."""
    profiles = build_reference_profiles(con)
    profiles["centroids"]["558-riddler"] = {f: 0.0 for f in CENTROID_FEATURES}
    loud_but_riddler = _feature_row(**{f: 102.0 for f in CENTROID_FEATURES})
    loud_but_riddler["riddle_param_count"] = 5
    predicted, _, confidence = classify_villain(loud_but_riddler, profiles)
    assert predicted == "558-riddler"
    assert confidence == 0.9  # the fixed hard-discriminator confidence, not a distance-based one


# --- technique reconstruction: the six enforced evidence mappings --------


def test_technique_evidence_rules_match_the_enforced_mapping():
    """Must stay in lockstep with assert_high_observability_techniques_leave_evidence
    (docs/02) - the same six technique -> evidence-feature pairs."""
    assert TECHNIQUE_EVIDENCE_RULES == {
        "active_scan": "path_enumeration_runs",
        "account_discovery": "path_enumeration_runs",
        "brute_force": "repeated_auth_failure_runs",
        "exploit_public_app": "traversal_pattern_count",
        "exploit_remote_svc": "injection_pattern_count",
        "data_local_system": "sensitive_data_access_runs",
    }


def test_classify_techniques_predicts_only_with_positive_evidence():
    features = _feature_row(repeated_auth_failure_runs=1, traversal_pattern_count=0)
    predictions = classify_techniques(features)
    ids = {p["technique_id"] for p in predictions}
    assert "brute_force" in ids
    assert "exploit_public_app" not in ids


def test_classify_techniques_predicts_nothing_with_no_evidence():
    """Conservative by design: no dedicated evidence feature means no guess,
    not a fallback prediction."""
    assert classify_techniques(_feature_row()) == []


def test_classify_techniques_can_predict_multiple_at_once():
    features = _feature_row(
        repeated_auth_failure_runs=2, traversal_pattern_count=1, path_enumeration_runs=1
    )
    ids = {p["technique_id"] for p in classify_techniques(features)}
    assert ids == {"brute_force", "exploit_public_app", "active_scan", "account_discovery"}
