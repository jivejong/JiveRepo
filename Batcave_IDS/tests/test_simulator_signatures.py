"""Layer 2 signatures (services/simulator/signatures.py). Each signature's
deterministic quirk, checked on the request-spec transform in isolation."""

import random

from services.simulator.signatures import signature_for


def test_riddler_adds_riddle_param_to_every_request():
    sig = signature_for("558-riddler")
    specs = [("GET", "/api/v1/status"), ("GET", "/api/v1/users?x=1")]
    out = sig.transform(specs, random.Random(0))
    assert len(out) == len(specs)
    assert all("riddle=" in path for _, path in out)


def test_two_face_issues_every_request_twice():
    sig = signature_for("678-two-face")
    specs = [("GET", "/admin"), ("POST", "/login")]
    out = sig.transform(specs, random.Random(0))
    assert len(out) == 2 * len(specs)
    assert out.count(("GET", "/admin")) == 2
    assert out.count(("POST", "/login")) == 2


def test_joker_sometimes_uses_absurd_methods():
    sig = signature_for("370-joker")
    specs = [("GET", "/admin")] * 100
    out = sig.transform(specs, random.Random(1))
    methods = {m for m, _ in out}
    assert methods - {"GET"}, "Joker should emit a non-GET absurd method over 100 requests"


def test_scarecrow_adds_error_seeking_requests():
    sig = signature_for("576-scarecrow")
    specs = [("GET", "/api/v1/status")]
    out = sig.transform(specs, random.Random(0))
    assert len(out) > len(specs)
    assert any("/admin/" in path for _, path in out)


def test_penguin_rotates_ip_flag():
    assert signature_for("514-penguin").rotate_ip is True
    assert signature_for("60-bane").rotate_ip is False


def test_harley_is_bursty():
    assert signature_for("309-harley-quinn").burstiness > 0
    assert signature_for("370-joker").burstiness == 0


def test_mister_freeze_holds_connections():
    # Long response delay -> high response_time_ms, long duration (docs/03).
    assert signature_for("457-mister-freeze").response_delay_ms > 0
    assert signature_for("386-killer-croc").response_delay_ms == 0


def test_clean_operators_exit_early():
    # Ra's al Ghul and Catwoman stop when the stage machine resolves rather
    # than grinding to budget (docs/03: "then exits", "clean exit").
    assert signature_for("538-ras-al-ghul").clean_operator is True
    assert signature_for("165-catwoman").clean_operator is True
    # A grinder does not.
    assert signature_for("386-killer-croc").clean_operator is False


def test_unknown_slug_gets_noop_signature():
    sig = signature_for("999-nobody")
    specs = [("GET", "/")]
    assert sig.transform(specs, random.Random(0)) == specs
    assert sig.rotate_ip is False
