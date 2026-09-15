"""Gating is derived from the seeds, never hardcoded per villain
(docs/07). These assertions are computed values, taken from real
`gated_techniques` output printed and inspected by hand before writing
this file — not asserted from the catalog by eye.
"""

from services.simulator.catalog import gated_techniques, load_stages, load_techniques, load_villains


def test_all_seeds_load():
    assert len(load_villains()) == 12
    assert len(load_techniques()) == 23
    assert len(load_stages()) == 5


def test_killer_croc_stalls_at_stage_three():
    croc = load_villains()["386-killer-croc"]
    assert [t.technique_id for t in gated_techniques(croc, 1)] == ["port_sweep"]
    assert [t.technique_id for t in gated_techniques(croc, 2)] == ["brute_force"]
    assert gated_techniques(croc, 3) == []
    assert gated_techniques(croc, 4) == []


def test_ras_al_ghul_reaches_everything_except_privesc_exploit():
    ras = load_villains()["538-ras-al-ghul"]
    all_technique_ids = {t.technique_id for t in load_techniques()}
    reachable = {t.technique_id for stage in range(1, 5) for t in gated_techniques(ras, stage)}
    assert all_technique_ids - reachable == {"privesc_exploit"}


def test_gating_is_self_consistent_for_all_twelve_villains():
    """Every technique a villain is gated into must actually satisfy that
    technique's own thresholds against that villain's own stats — checked
    directly, not trusted from the function under test."""
    for villain in load_villains().values():
        for stage in range(1, 5):
            for t in gated_techniques(villain, stage):
                assert t.stage == stage
                assert villain.intelligence >= t.min_intelligence
                assert villain.power >= t.min_power
                assert villain.strength >= t.min_strength


def test_only_killer_croc_has_an_empty_stage_three():
    """Confirmed by running gated_techniques for all twelve: Croc is the
    only villain structurally unable to ever reach stage 4. Recorded here
    so a future seed change that silently strands a second villain fails
    a test instead of going unnoticed."""
    villains = load_villains()
    empty_stage_three = [slug for slug, v in villains.items() if gated_techniques(v, 3) == []]
    assert empty_stage_three == ["386-killer-croc"]
