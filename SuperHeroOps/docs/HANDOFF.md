# SuperHeroOps — Weekend Build Handoff

## Concept

A Blazor Server app that pairs real Chicago crime data (Chicago Data Portal / Socrata)
with a seeded superhero dataset (SuperheroAPI). Users browse real crime statistics by
community area, select one or more heroes to "deploy," and the app runs a deterministic
intervention model plus LLM-generated per-hero reports. The real-data layer and the
synthetic-modeling layer are kept explicitly separate — in the schema, in the UI, and in
the README — because the crime data is real and the "effect" of deploying a hero is fiction.

## Explicit exclusions (do not add without a new decision)

- **No employer/client references anywhere in the repo** — public repo, active IP
  disclosure process, this project must read as unambiguously personal prior work.
- **No mental health dataset.**
- **No map in v1** — sortable list only. GIS/map is a named v2.0 item, not an oversight.
- **No disaster/nationwide dataset in v1** — Chicago crime only, single vertical.
- **The hero ingestion script stays in the repo** and is documented as a deliberate
  build-time snapshot, not a missing feature — runtime never calls the live SuperheroAPI.

## Tech stack

- .NET 10, C# only (no second language, no JS framework)
- Blazor Server
- PostgreSQL via Docker for local dev (same pattern as Chord Chart Manager / Batcave)
- EF Core for data access
- Groq for LLM report generation — structured JSON output, one-retry validation on
  malformed responses (reuse the Song Enrichment project's pattern)
- Deploy target: GCP e2-micro (same box pattern as other portfolio projects)

## Solution structure

```
SuperHeroOps.sln
/src
  SuperHeroOps.Core                # domain models, intervention scoring logic
  SuperHeroOps.Data                 # EF Core DbContext, migrations, seed loader
  SuperHeroOps.Ingestion.Heroes     # console app: one-time SuperheroAPI pull -> seed file
  SuperHeroOps.Ingestion.Crime      # console app: bounded Socrata pull -> Postgres load
  SuperHeroOps.Web                  # Blazor Server app
/seed-data
  heroes.json
README.md
```

## Data ingestion

### Heroes (one-time, documented process — not a runtime dependency)
- Source: `https://superheroapi.com/api.php/{access-token}/{id}`, IDs 1–731
- Token via GitHub login at superheroapi.com; read from a local env var
  (`SUPERHERO_API_TOKEN`) only — never committed
- Free tier: 60 req/min, 10,000 req/day — 731 sequential calls fit in minutes with
  plenty of headroom; basic retry/backoff is enough, no need to over-engineer this
- Output: `heroes.json` — id, name, powerstats (intelligence, strength, speed,
  durability, power, combat), image URL, publisher, alignment
- Drop any record with missing/null powerstats at ingestion time

### Crime (bounded pull, loaded straight to Postgres)
- Source: Socrata SODA API, `data.cityofchicago.org`, dataset **`ijzp-q8t2`**
  ("Crimes - 2001 to Present")
- **Bound the query** — a `$where` date filter for the trailing 90 days, never the
  full multi-million-row table
- No auth required at this volume, but register a free Socrata app token anyway to
  avoid throttling
- Fields to keep: case number, date, primary crime type (IUCR category), community
  area, arrest flag, latitude/longitude
- This console app should write directly to Postgres via EF Core — this is the real,
  testable ingest-to-DB pipeline, not just a file dump

## Data model (sketch — flesh out in Claude Code)

- `CommunityArea` (id, name)
- `CrimeIncident` (id, community_area_id, date, primary_type, arrest, lat, lon)
- `Hero` (id, name, powerstats..., image_url, publisher, alignment)
- `InterventionScore` (community_area_id, hero_id, per-crime-type projected effect)
- `HeroReport` (community_area_id, hero_id, generated JSON payload, generated_at)

## Intervention scoring model

1. **After** crime ingestion, inspect the actual top IUCR categories in your 90-day
   window before finalizing weights — don't pre-guess the full taxonomy.
2. Starting weight map (tune once real distribution is visible):

   | Category (illustrative) | Weighted powerstats |
   |---|---|
   | Violent (assault, battery, robbery) | strength, combat, durability |
   | Property (burglary, theft, MVT) | speed, intelligence |
   | Narcotics | intelligence, power |
   | Weapons violation | combat, strength |
   | Deceptive practice / fraud | intelligence |

3. Score = weighted sum of the hero's relevant stats, normalized against the max
   possible score in the roster so scores are comparable 0–100 across heroes.
4. Convert score to a projected-effect percentage per crime type using a simple
   capped linear mapping (e.g., `score/100 * max_effect_ceiling`). Label this in the
   UI as illustrative, not a forecast — this is the step that's fiction.

## Report generation

- One Groq call per hero (confirmed: per-hero reports, not a single multi-hero call —
  easier to test, lets the user weigh the reports rather than the model deciding)
- Input: community area name, real crime summary stats, that hero's computed effect
  numbers
- Output: structured JSON (`risk_assessment`, `predicted_impact_narrative`,
  `recommendation`) — parsed and rendered, never raw free text
- **Every report gets a disclaimer line injected by code**, not left to the model:
  *"This is a modeling exercise using fictional characters — not a policy forecast."*
- Comparison synthesis report (stretch goal, build only after everything else works):
  input is just the array of computed effect numbers — small payload regardless of
  hero count — output is a short trade-off narrative across the selected heroes

## UX flow (confirmed)

1. Neighborhood list — sortable, real CPD stats, no map in v1
2. Neighborhood detail — crime-type breakdown
3. Hero roster — browse, multi-select for compare
4. Deploy decision — **UI marks the real→synthetic pivot explicitly here** (a visual
   divider or banner: "Simulated Intervention Model" from this point on)
5. Effect computation — deterministic, instant, no LLM
6. Side-by-side hero reports — one LLM-generated report per selected hero
7. *(stretch)* Comparison synthesis report

## Weekend phase plan (checkpoint-based)

**Phase 1 — verify before layering**
- Hero ingestion working, `heroes.json` validated
- Crime ingestion working, loaded into local Postgres, spot-checked against Garfield
  Ridge numbers you know personally
- ✋ Checkpoint: don't start the scoring model until both ingests are verified

**Phase 2**
- Intervention scoring model implemented against the real ingested crime-type mix
- Unit tests on the scoring math (cheap, since it's deterministic)
- ✋ Checkpoint: scores look directionally sane before touching the UI

**Phase 3**
- Blazor scaffold: neighborhood list + detail, bound to real DB data
- ✋ Checkpoint: real data renders correctly before adding the hero flow

**Phase 4**
- Hero roster, multi-select, deploy screen wired to the scoring model

**Phase 5**
- Groq integration, per-hero report generation, disclaimer injection

**Phase 6 — if time remains**
- Comparison synthesis report
- Deploy to e2-micro

## README requirements (non-negotiable)

- No employer or client references anywhere
- Explicit "modeling exercise, not a real forecast" statement, up front
- A short note on why the hero ingestion script is checked in despite the seeded
  runtime data (documents the real integration; chosen for reliability/reproducibility)
- A "v2.0" section naming the map/GIS upgrade and disaster-data-as-a-separate-vertical
  as deliberate future scope, not gaps
