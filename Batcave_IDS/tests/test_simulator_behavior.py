"""Layer 1 BehaviorProfile (services/simulator/behavior.py). Checks the
derivation's ordering properties — that the stats drive the parameters in the
documented direction — not exact tuned coefficients, which move during the
separability loop."""

from services.simulator.behavior import BehaviorProfile
from services.simulator.catalog import load_villains


def _profiles():
    return {slug: BehaviorProfile.from_villain(v) for slug, v in load_villains().items()}


def test_evasion_tracks_intelligence():
    p = _profiles()
    # Croc (INT 19) loud, Riddler (INT 100) quiet.
    assert p["386-killer-croc"].evasion < p["558-riddler"].evasion


def test_faster_villain_makes_more_requests_per_min():
    p = _profiles()
    # Killer Croc SPD 35 vs Mister Freeze SPD 12.
    assert p["386-killer-croc"].requests_per_min > p["457-mister-freeze"].requests_per_min


def test_durability_drives_session_duration_and_tolerance():
    p = _profiles()
    # Croc DUR 90 vs Riddler DUR 14.
    assert p["386-killer-croc"].session_duration_s > p["558-riddler"].session_duration_s
    assert p["386-killer-croc"].failure_tolerance > p["558-riddler"].failure_tolerance


def test_power_drives_body_growth():
    p = _profiles()
    # Poison Ivy PWR 100 (monotonic growth) vs Two-Face PWR 9.
    assert p["522-poison-ivy"].body_growth > p["678-two-face"].body_growth


def test_combat_drives_tier_aggression():
    p = _profiles()
    # Ra's al Ghul CMB 100 / Bane CMB 95 vs Riddler CMB 14.
    assert p["538-ras-al-ghul"].tier_aggression > p["558-riddler"].tier_aggression


def test_high_intelligence_rotates_user_agent():
    p = _profiles()
    assert p["558-riddler"].rotates_user_agent is True  # INT 100
    assert p["386-killer-croc"].rotates_user_agent is False  # INT 19


def test_profile_is_derived_for_every_villain():
    # No villain is special-cased out; the mapping works for all twelve.
    assert len(_profiles()) == 12
