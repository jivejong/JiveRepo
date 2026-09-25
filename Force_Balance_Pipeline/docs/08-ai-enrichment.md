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

1. Every enrichment call uses `gemini-3.1-flash-lite` at its default temperature of 1.0. Google's
   Gemini 3 guidance warns that temperatures below 1.0 risk looping and degraded output, so the
   layer does not lower it. Reproducibility comes from the reviewed, frozen CSVs, not from the
   model: **reruns do not reproduce committed values.** Thinking level: OPEN until the first
   review pass.
2. Output CSVs are committed to git and reviewed by a human before use.
3. The model ID, temperature, thinking level, prompt version or hash, generation date, and number
   of regeneration cycles are recorded in a header comment in each CSV and in
   `seeds/ENRICHMENT_PROVENANCE.md`.
4. Regeneration is a **migration event**. It requires regenerating the backfill and truncating
   derived tables. Do not do it casually.
5. Review anchors are never named in enrichment instruction text (system prompts, preambles,
   hints). Every planet named in the doc 07 Phase 1 checkpoint or in the review checklist below,
   and its expected channel values, is an anchor. Naming one in instruction text would turn the
   review into a check of the prompt, not of the model. The per-planet data entries the model must
   describe are inherent to the request and are not covered.

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

`scripts/enrich_planets.py` — reads `data/swapi_snapshot/planets.json`, sends the 59 planets other
than planets/28 to the model in one call (`--batch-size N` splits it if a response is truncated),
writes `warehouse/dbt/seeds/dim_sector.csv`.

### Output schema

| Column | Type | Notes |
|---|---|---|
| `sector_id` | string | slug of SWAPI `name` — PK, never model-generated. One exception: planets/28 maps to `uncharted` (see "The `unknown` planet") |
| `sector_name` | string | from SWAPI (`unknown` for planets/28) |
| `climate`, `terrain`, `population`, `diameter_km` | | from SWAPI; `population` and `diameter_km` are blank when SWAPI's value is not a number, and `diameter_km` is also blank when it is 0 (see below) |
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
| `is_unknown` | boolean | true for planets/28 only (see "The `unknown` planet") |

**Departure from passthrough.** `population` and `diameter_km` are SWAPI values passed through, with
one exception: a value that is not a number (SWAPI's `"unknown"`) is written as empty (null), so
the seed columns can be numeric. A `diameter_km` of `0` is treated the same way: SWAPI uses `0` for
"unknown" diameters (six planets, including planets/28), and a 0 km planet is meaningless. Other
numeric values are kept as given. Text fields (`climate`, `terrain`) keep `"unknown"`.

`sigma` and `dark_spike_probability` are the columns that matter most and the ones easiest to
forget. Without per-planet spread, every planet behaves identically around a different center.
With it, Mustafar is dark-volatile, Coruscant is midi-high and steady, Dagobah is quietly
anomalous — and the galaxy map has character.

**Sigma is enforced per row.** Each sigma must be a fraction of that row's own baseline: midi
2-5%, kyber 8-15%, dark 5-10%. The response schema can only bound values absolutely, so the script
clamps an out-of-band sigma to the band and records every adjustment in the provenance sidecar
(`--strict-sigma` fails instead). The `is_unknown` row is exempt: its values are medians.

`canon_confidence` exists because the model will be certain about Tatooine and inventing things
about Ojom. Sort by it during review and spot-check the bottom.

### The `unknown` planet

SWAPI includes one planet named `unknown` (planets/28) with no real data: `climate`, `terrain` and
`population` are all `"unknown"` and it has no films. Five people list it as their homeworld,
including Yoda and Qui-Gon Jinn. It stays in `dim_sector`, so the table keeps 60 rows, the probe's
60-planet sweep stays complete, and those Jedi keep a valid `homeworld_sector_id`.

1. **Explicit id mapping.** planets/28 maps to `sector_id` `uncharted`. The id is not derived from
   the name: every other `sector_id` is a slug of the SWAPI `name`, and this row is the one
   exception. `sector_name` stays SWAPI's `unknown`. Yoda's and Qui-Gon Jinn's
   `homeworld_sector_id` are `uncharted`, so the mapping is applied wherever a planets/28 URL is
   resolved to an id. A SWAPI refresh must not change it.
2. `dim_sector` carries an `is_unknown` boolean, true for this row only.
3. The row is not sent to the model. Nothing but the word "unknown" is available to it, so any
   values would be invention.
4. **Baseline treatment.** Each numeric column of the row (baselines, sigmas, spike probability)
   is the median of the other 59 sectors, computed once after review and committed to the CSV.
   Text columns are fixed: `system_name` "Unknown", `region` "Unknown Regions", `canon_confidence`
   0.0, and a description stating that the values are medians of the other sectors.
5. Review checks for spread and anchors exclude `is_unknown` rows, by the flag and not by id.

### System prompt

```
You are a Star Wars loremaster with encyclopedic knowledge of canon and Legends material,
assisting with a data engineering simulation. For each planet you will produce Force-related
sensor parameters and descriptive context.

Three measured channels:

MIDICHLORIAN DENSITY (ppm, range 1000-25000)
Ambient midichlorian concentration. Driven by population density, sentient life, and
connection to the Force. Densely populated worlds run high. Barren or lifeless
worlds run low. This channel is STABLE — sigma should be 2-5% of baseline.

KYBER RESONANCE (0-100)
Ambient crystalline Force resonance. Driven by geology, crystal deposits, and Force-attuned
locations. Worlds with major crystal deposits run very high. Gas giants and artificial worlds run low.
MODERATELY VARIABLE — sigma should be 8-15% of baseline.

DARK SIDE ACTIVITY (0-100)
Ambient dark side presence. Driven by Sith history, atrocity, suffering, and dark side nexuses.
Sith strongholds, sites of atrocity, and war-scarred worlds run high. Peaceful worlds run low.
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
- `kyber_baseline` is high for Utapau
- Sort by `canon_confidence` ascending and read the bottom ten rows
- No more than 10% of rows have a sigma clamped on any channel (`sigma_adjustments` in the
  provenance sidecar). More than that is a prompt problem: regenerate, do not accept
- Baselines are spread across the range, not clustered at the midpoint

That last one is the most common failure. Models regress to the mean when generating numeric
tables. If everything lands between 40 and 60, regenerate in smaller batches with explicit
instruction to use the full range.

After `dbt seed`, "spread across the range" and "high" are checked by the queries in
`warehouse/dbt/analyses/` (`phase1_check_1_spread.sql`, `phase1_check_2_anchors.sql`,
`phase1_check_3_jedi.sql`). The thresholds live in those files.

---

## Jedi enrichment

### Script

`scripts/enrich_jedi.py` — reads `data/swapi_snapshot/people.json`, filters to the Jedi roster
below, enriches, writes `warehouse/dbt/seeds/dim_jedi.csv`.

### Roster: Jedi Order members, Episodes I-III, SWAPI only

**Membership rule.** A person is on the roster if they were a member of the Jedi Order during
Episodes I-III and appear in SWAPI. Anakin Skywalker is included. Sith and non-Jedi
Force-sensitives are excluded. Force-sensitivity is never inferred, and nothing is invented.

**No invented Jedi.** SWAPI is the spine; enrichment adds attributes to existing rows only. The
roster is these 17 SWAPI people, as `name (people id)`: Obi-Wan Kenobi (10), Anakin Skywalker (11),
Yoda (20), Qui-Gon Jinn (32), Ayla Secura (46), Mace Windu (51), Ki-Adi-Mundi (52), Kit Fisto (53),
Eeth Koth (54), Adi Gallia (55), Saesee Tiin (56), Yarael Poof (57), Plo Koon (58), Luminara
Unduli (64), Barriss Offee (65), Jocasta Nu (74), Shaak Ti (78). SWAPI spells "Ayla Secura"; the
canonical spelling is "Aayla", and the data keeps SWAPI's spelling. Seventeen is comfortable
against the three-concurrent-deployment cap.

The filter list is hand-maintained in `scripts/jedi_roster.py` as an explicit array of SWAPI
person URLs. Do not try to infer Force-sensitivity from SWAPI fields — there is no such field.
An explicit, readable, version-controlled list is the correct answer.

### Output schema

| Column | Type | Notes |
|---|---|---|
| `jedi_id` | string | slug of SWAPI `name` — PK |
| `jedi_name` | string | from SWAPI |
| `species_id`, `homeworld_sector_id` | string | SWAPI FKs. `species_id` is the slug of the SWAPI species name; SWAPI leaves species empty for Humans, so an empty species means Human (species/1, `human`). `homeworld_sector_id` uses the `dim_sector` id mapping, so planets/28 is `uncharted` |
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

| Seed | Model ID | Temperature | Thinking level | Prompt version / hash | Generated | Regeneration cycles | Reviewed by | Rows |
|---|---|---|---|---|---|---|---|---|
| dim_sector.csv | gemini-3.1-flash-lite | 1.0 (default) | <open> | planets-v1 / <hash> | 2026-09-xx | <n> | <you> | 60 |
| dim_jedi.csv | gemini-3.1-flash-lite | 1.0 (default) | <open> | jedi-v1 / <hash> | 2026-09-xx | <n> | <you> | 17 |

Source: LLM-generated from model knowledge, human-reviewed. Not scraped from any wiki.
Reruns do not reproduce the committed values; the reviewed CSVs are the source of truth.
Regeneration invalidates the 90-day backfill and all derived baselines — see
docs/08-ai-enrichment.md.
```

Keep this current. It's the artifact that turns "I had an AI make up some numbers" into
"I built a governed enrichment layer," and the difference is entirely in the documentation.
