"""Pathology config loading and per-request injection decisions
(services/simulator/pathologies.py)."""

from random import Random

from services.simulator.pathologies import (
    HONEYPOT_TOKENS,
    PathologyConfig,
    PathologyInjector,
)


def test_config_loads_all_ten_enabled():
    cfg = PathologyConfig.load()
    # Ten pathologies, with clock-skew split into two -> 12 tokens total.
    assert len(cfg.enabled) == 12
    assert "schema_drift" in cfg.per_run
    assert "burst" in cfg.per_run
    assert "clock_skew_future_received_at" in cfg.per_request
    assert "clock_skew_negative_response" in cfg.per_request


def test_rates_fire_at_roughly_their_configured_frequency():
    cfg = PathologyConfig.load()
    inj = PathologyInjector(cfg, Random(0))
    n = 20000
    malformed = sum(inj.decide().malformed_body for _ in range(n))
    # malformed_body rate is 0.03; allow generous tolerance.
    assert 0.02 < malformed / n < 0.04


def test_undeserializable_is_rare_but_present():
    cfg = PathologyConfig.load()
    inj = PathologyInjector(cfg, Random(1))
    hits = sum("undeserializable" in inj.decide().honeypot_tokens for _ in range(20000))
    assert hits > 0  # 0.2% over 20k -> ~40


def test_honeypot_tokens_go_in_the_header_value():
    cfg = PathologyConfig.load()
    inj = PathologyInjector(cfg, Random(2))
    # Find a request that fired at least one honeypot token.
    for _ in range(5000):
        p = inj.decide()
        if p.honeypot_tokens:
            assert p.header_value() == ",".join(p.honeypot_tokens)
            assert all(t in HONEYPOT_TOKENS for t in p.honeypot_tokens)
            return
    raise AssertionError("no honeypot pathology fired in 5000 requests")


def test_schema_drift_only_after_armed():
    cfg = PathologyConfig.load()
    inj = PathologyInjector(cfg, Random(3))
    assert not inj.schema_drift_active
    assert all("schema_drift" not in inj.decide().honeypot_tokens for _ in range(200))
    inj.arm_schema_drift()
    assert inj.schema_drift_active
    # After arming, every request carries the drift token.
    assert all("schema_drift" in inj.decide().honeypot_tokens for _ in range(50))


def test_late_arrival_and_out_of_order_set_client_ts_offset():
    cfg = PathologyConfig.load()
    inj = PathologyInjector(cfg, Random(4))
    late = 0
    shuffled = 0
    for _ in range(20000):
        p = inj.decide()
        if p.client_ts_offset_s is not None:
            if p.client_ts_offset_s <= -3600:
                late += 1
            else:
                shuffled += 1
    assert late > 0 and shuffled > 0
