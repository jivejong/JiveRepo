"""services.triage.prompt — renders against a small in-memory warehouse (no
live corpus needed), and checks the contract docs/04 specifies: all twelve
slugs present, observability withheld, every attack_id from the seeds, valid
JSON output shape embedded, no Windows-console-mojibake characters.
"""

import duckdb
import pytest

from services.triage.prompt import ARCHETYPES, SIGNATURE_NOTES, build_system_prompt


@pytest.fixture
def con():
    connection = duckdb.connect()
    connection.execute("""
        create table dim_villains (
            villain_slug varchar, villain_name varchar, archetype varchar,
            intelligence int, strength int, speed int, durability int, power int, combat int
        )
    """)
    connection.execute("""
        insert into dim_villains values
        ('370-joker', 'Joker', 'chaotic', 100, 10, 12, 60, 43, 70),
        ('558-riddler', 'Riddler', 'cerebral', 100, 10, 12, 14, 10, 14)
    """)
    connection.execute("""
        create table dim_techniques (
            technique_id varchar, attack_id varchar, display_name varchar,
            stage int, detection_signature varchar, observability varchar
        )
    """)
    connection.execute("""
        insert into dim_techniques values
        ('brute_force', 'T1110', 'Credential Brute Force', 2,
         'repeated POST /login with consecutive 401s', 'high')
    """)
    return connection


def test_renders_without_error(con):
    prompt = build_system_prompt(con)
    assert len(prompt) > 1000


def test_observability_is_never_leaked(con):
    """docs/04: inferring which techniques are detectable is part of the
    task - the tier itself must never appear."""
    prompt = build_system_prompt(con)
    assert "observability" not in prompt.lower()
    assert "'high'" not in prompt and '"high"' not in prompt


def test_every_villain_in_the_fixture_appears(con):
    prompt = build_system_prompt(con)
    assert "370-joker" in prompt
    assert "558-riddler" in prompt
    assert "Joker" in prompt
    assert "Riddler" in prompt


def test_every_catalog_technique_appears(con):
    prompt = build_system_prompt(con)
    assert "T1110" in prompt
    assert "repeated POST /login with consecutive 401s" in prompt


def test_output_contract_fields_present(con):
    """The JSON shape from docs/04 must survive the {{ }} escaping intact."""
    prompt = build_system_prompt(con)
    for field in (
        "threat_level",
        "suspected_villain",
        "alternate_suspects",
        "suspected_archetype",
        "confidence",
        "identified_techniques",
        "identified_tactics",
        "reconstructed_stage_reached",
        "reasoning",
        "in_person_intervention_required",
        "recommended_countermeasures",
        "attack_pattern_summary",
    ):
        assert f'"{field}"' in prompt, f"missing output field: {field}"


def test_no_em_dash_mojibake(con):
    """This project's established fix for Windows console mojibake (Phase 3):
    ASCII dashes only."""
    prompt = build_system_prompt(con)
    assert "—" not in prompt


def test_missing_signature_note_raises_rather_than_silently_omitting(con):
    """A roster grown past SIGNATURE_NOTES must fail loudly, not prompt the
    model with an incomplete signature set."""
    con.execute(
        "insert into dim_villains values "
        "('999-new-villain', 'New Villain', 'chaotic', 50, 50, 50, 50, 50, 50)"
    )
    with pytest.raises(ValueError, match="999-new-villain"):
        build_system_prompt(con)


def test_archetypes_cover_all_twelve_real_slugs():
    """Sanity check on the static ARCHETYPES table against the real roster
    shape - five archetypes, twelve members, no duplicates."""
    all_slugs = [s for members in ARCHETYPES.values() for s in members]
    assert len(all_slugs) == 12
    assert len(set(all_slugs)) == 12
    assert len(ARCHETYPES) == 5


def test_signature_notes_cover_all_twelve_real_villains():
    from services.simulator.catalog import load_villains

    real_slugs = set(load_villains())
    assert set(SIGNATURE_NOTES) == real_slugs
