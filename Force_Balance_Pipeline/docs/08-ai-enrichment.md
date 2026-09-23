# 08 — AI enrichment layer

SWAPI gives you 60 planets with sparse, inconsistent fields. `residents` is empty for most of
them, `films` is empty for many, and `population` is frequently the string `"unknown"`. Deriving
sensor baselines from those fields produces a galaxy where forty planets are statistically dead.

The enrichment layer solves this by having an LLM generate per-planet Force parameters, system
and region assignments, and narrative context from its own knowledge of Star Wars canon.

---

## The load-bearing constraint: build-time and frozen

**Enrichment runs once, offline. Output is committed as seed CSVs. It is never called at
runtime.**

This is not a style preference. Your 90-day synthetic backfill is generated from these
parameters, and every rolling baseline and z-score in the warehouse derives from that backfill.
Regenerating enrichment with different output would silently invalidate all historical
statistics — no error, no warning, just detection thresholds that no longer mean what they meant
yesterday.

Rules:

1. `temperature: 0` on every enrichment call.
2. Output CSVs are committed to git and reviewed by a human before use.
3. The model name, prompt version, and generation date are recorded in a header comment in each
   CSV and in `seeds/ENRICHMENT_PROVENANCE.md`.
4. Regeneration is a **migration event**. It requires regenerating the backfill and truncating
   derived tables. Do not do it casually.

This constraint is also what makes the layer defensible. AI-generated data entering a pipeline
through a reviewed, version-controlled gate is a meaningfully different thing from a live API
call nobody audits — and it's worth a section in the README.

## Sourcing

Prompt the model from its own knowledge. **Do not scrape Wookieepedia.** Fandom content is
CC BY-SA, which carries share-alike implications for derived data, and their terms are unfriendly
to scraping. For a public portfolio repo that's an avoidable problem, and the model knows this
material well.

Provenance claim in the seed header: *"LLM-generated from model knowledge, human-reviewed."*
Accurate, and the more honest claim than implying a sourced dataset.

---

## Planet enrichment

### Script

`scripts/enrich_planets.py` — reads `data/swapi_snapshot/planets.json`, calls the model once per
planet (or in batches of 10 for consistency), writes `warehouse/dbt/seeds/dim_sector.csv`.

### Output schema

| Column | Type | Notes |
|---|---|---|
| `sector_id` | string | slug of SWAPI `name` — PK, never model-generated |
| `sector_name` | string | from SWAPI |
| `climate`, `terrain`, `population`, `diameter_km` | | from SWAPI, unchanged |
| `system_name` | string | e.g. "Tatoo system" |
| `region` | string | `Core Worlds` \| `Colonies` \| `Inner Rim` \| `Expansion Region` \| `Mid Rim` \| `Outer Rim` \| `Wild Space` \| `Unknown Regions` |
| `midi_baseline` | float | 1000–25000 ppm |
| `midi_sigma` | float | stddev — keep small, this channel is stable |
| `kyber_baseline` | float | 0–100 resonance units |
| `kyber_sigma` | float | larger than midi_sigma |
| `dark_baseline` | float | 0–100 activity units |
| `dark_sigma` | float | |
| `dark_spike_probability` | float | 0.0–0.05 — per-scan chance of a spike event |
| `description` | string | 2–3 sentences |
| `force_history` | string | 2–3 sentences of canonical Force-related events |
| `canon_confidence` | float | 0.0–1.0, model's self-assessment |

`sigma` and `dark_spike_probability` are the columns that matter most and the ones easiest to
forget. Without per-planet spread, every planet behaves identically around a different center.
With it, Mustafar is dark-volatile, Coruscant is midi-high and steady, Dagobah is quietly
anomalous — and the galaxy map has character.

`canon_confidence` exists because the model will be certain about Tatooine and inventing things
about Ojom. Sort by it during review and spot-check the bottom.

### System prompt

```
You are a Star Wars loremaster with encyclopedic knowledge of canon and Legends material,
assisting with a data engineering simulation. For each planet you will produce Force-related
sensor parameters and descriptive context.

Three measured channels:

MIDICHLORIAN DENSITY (ppm, range 1000-25000)
Ambient midichlorian concentration. Driven by population density, sentient life, and
connection to the Force. Coruscant and other dense worlds run high. Barren or lifeless
worlds run low. This channel is STABLE — sigma should be 2-5% of baseline.

KYBER RESONANCE (0-100)
Ambient crystalline Force resonance. Driven by geology, crystal deposits, and Force-attuned
locations. Ilum and similar worlds run very high. Gas giants and artificial worlds run low.
MODERATELY VARIABLE — sigma should be 8-15% of baseline.

DARK SIDE ACTIVITY (0-100)
Ambient dark side presence. Driven by Sith history, atrocity, suffering, and dark side nexuses.
Mustafar, Korriban-adjacent, and war-scarred worlds run high. Peaceful worlds run low.
Mostly quiet with RARE SPIKES — sigma 5-10% of baseline, and dark_spike_probability between
0.0 and 0.05 representing the per-scan chance of a dark side surge.

Also assign:
- system_name: the star system, using canonical naming where known
- region: exactly one of Core Worlds, Colonies, Inner Rim, Expansion Region, Mid Rim,
  Outer Rim, Wild Space, Unknown Regions
- description: 2-3 sentences on the planet's character
- force_history: 2-3 sentences on canonical Force-related events there
- canon_confidence: 0.0-1.0, how confident you are this reflects actual canon rather than
  your own reasonable extrapolation. Be honest. Obscure planets should score low.

Every planet must receive non-zero, plausible values on all three channels. There are no
Force-dead worlds in this simulation.

Return ONLY a JSON array. No preamble, no markdown fences.
```

### Review checklist

Before committing the CSV:

- No nulls or zeros in any baseline or sigma column
- Every `region` value is in the allowed set
- `dark_baseline` is high for Mustafar, Dathomir, Geonosis; low for Naboo, Alderaan
- `midi_baseline` is high for Coruscant; low for gas giants and barren worlds
- `kyber_baseline` is high for Ilum
- Sort by `canon_confidence` ascending and read the bottom ten rows
- Baselines are spread across the range, not clustered at the midpoint

That last one is the most common failure. Models regress to the mean when generating numeric
tables. If everything lands between 40 and 60, regenerate in smaller batches with explicit
instruction to use the full range.

---

## Jedi enrichment

### Script

`scripts/enrich_jedi.py` — reads `data/swapi_snapshot/people.json`, filters to the prequel-era
Force-user roster, enriches, writes `warehouse/dbt/seeds/dim_jedi.csv`.

### Roster: prequel era, SWAPI only

**No invented Jedi.** SWAPI is the spine; enrichment adds attributes to existing rows only.
Roughly 18 Force users appear in SWAPI's prequel-era people — Yoda, Mace Windu, Obi-Wan Kenobi,
Qui-Gon Jinn, Plo Koon, Ki-Adi-Mundi, Shaak Ti, Luminara Unduli, Barriss Offee, Aayla Secura,
Adi Gallia, Saesee Tiin, Eeth Koth, and others. That is comfortable against a three-concurrent-
deployment cap.

The filter list is hand-maintained in `scripts/jedi_roster.py` as an explicit array of SWAPI
person URLs. Do not try to infer Force-sensitivity from SWAPI fields — there is no such field.
An explicit, readable, version-controlled list is the correct answer.

### Output schema

| Column | Type | Notes |
|---|---|---|
| `jedi_id` | string | slug of SWAPI `name` — PK |
| `jedi_name` | string | from SWAPI |
| `species_id`, `homeworld_sector_id` | string | SWAPI FKs |
| `rank` | string | `padawan` \| `knight` \| `master` \| `council_member` \| `grand_master` |
| `primary_specialty` | string | `combat` \| `diplomacy` \| `investigation` \| `stealth` |
| `secondary_specialty` | string | same set, nullable |
| `power_rating` | int | 1–10 |
| `lightsaber_form` | string | flavor, shown in the dashboard |
| `notable_for` | string | 1–2 sentences |
| `canon_confidence` | float | |

`primary_specialty` is load-bearing — the constraint layer uses it to enforce signature matching
(doc 06). Make sure the roster covers all four specialties with at least three Jedi each, or
certain signature types will have no valid responder. **Check this during review** and adjust the
prompt if the distribution is lopsided; models over-assign `combat`.

### System prompt

```
You are a Star Wars loremaster. For each Jedi provided, assign gameplay attributes for a
simulation in which Jedi are deployed to respond to disturbances in the Force.

rank: padawan, knight, master, council_member, or grand_master. Use canonical standing during
the Clone Wars era.

primary_specialty and secondary_specialty, each one of:
  combat        - lightsaber mastery, direct confrontation, Sith engagement
  diplomacy     - negotiation, de-escalation, civil and political situations
  investigation - tracking, mystery, sensing disturbances, uncovering causes
  stealth       - infiltration, reconnaissance, covert operations

Assign these based on how the character actually behaves in canon, not on rank. Distribute
across all four specialties — do not default everyone to combat. secondary_specialty may be
null if the character is strongly one-dimensional.

power_rating: 1-10 relative to this roster. Reserve 10 for Yoda.

lightsaber_form: canonical form where known (Form I Shii-Cho through Form VII Juyo/Vaapad),
or the most plausible given their fighting style.

notable_for: 1-2 sentences on what this Jedi is known for.

canon_confidence: 0.0-1.0. Be honest about obscure characters.

Return ONLY a JSON array. No preamble, no markdown fences.
```

---

## Provenance file

`warehouse/dbt/seeds/ENRICHMENT_PROVENANCE.md`:

```markdown
# Enrichment provenance

| Seed | Model | Prompt version | Generated | Reviewed by | Rows |
|---|---|---|---|---|---|
| dim_sector.csv | <model> | planets-v1 | 2026-09-xx | <you> | 60 |
| dim_jedi.csv | <model> | jedi-v1 | 2026-09-xx | <you> | 18 |

Source: LLM-generated from model knowledge, human-reviewed. Not scraped from any wiki.
Temperature 0. Regeneration invalidates the 90-day backfill and all derived baselines —
see docs/08-ai-enrichment.md.
```

Keep this current. It's the artifact that turns "I had an AI make up some numbers" into
"I built a governed enrichment layer," and the difference is entirely in the documentation.
