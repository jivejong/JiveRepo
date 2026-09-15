"""Per-run / per-request network identity (services/simulator/identity.py)."""

import random

from services.simulator.identity import RunIdentity


def test_fixed_identity_is_stable_across_requests():
    ident = RunIdentity(random.Random(0))
    h1, h2, h3 = ident.headers(), ident.headers(), ident.headers()
    assert h1["X-Forwarded-For"] == h2["X-Forwarded-For"] == h3["X-Forwarded-For"]
    assert h1["User-Agent"] == h2["User-Agent"] == h3["User-Agent"]


def test_source_ip_is_test_net():
    ident = RunIdentity(random.Random(3))
    ip = ident.headers()["X-Forwarded-For"]
    assert ip.startswith("192.0.2.")
    octet = int(ip.rsplit(".", 1)[1])
    assert 1 <= octet <= 254


def test_ip_rotation_varies_within_a_run():
    ident = RunIdentity(random.Random(1), rotate_ip=True)
    ips = {ident.headers()["X-Forwarded-For"] for _ in range(30)}
    assert len(ips) > 1, "rotate_ip should produce more than one source IP"


def test_user_agent_rotation_varies_within_a_run():
    ident = RunIdentity(random.Random(1), rotate_user_agent=True)
    uas = {ident.headers()["User-Agent"] for _ in range(30)}
    assert len(uas) > 1


def test_different_runs_get_different_base_ips():
    ips = {RunIdentity(random.Random(seed)).headers()["X-Forwarded-For"] for seed in range(20)}
    assert len(ips) > 1, "distinct runs should not all share one source IP"
