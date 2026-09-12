# SuperHeroOps

**This is a modeling exercise, not a real forecast.** SuperHeroOps pairs real Chicago
crime data with a seeded, fictional superhero dataset and a synthetic, deterministic
"intervention" model to explore a "what if we deployed a comic-book hero to this
neighborhood" scenario. The crime data is real. Everything about a hero's projected
effect is fiction, and the app draws a hard, visible line between the two.

## Concept

1. Browse real Chicago Police Department crime data by neighborhood (community area) —
   a sortable list, then a crime-type breakdown for one area.
2. Browse a seeded roster of ~566 superheroes and select one or more to compare.
3. Confirm a deployment to a neighborhood. **This is the real → synthetic pivot point,**
   marked explicitly in the UI with a banner, not just in code comments.
4. See a deterministic, instant "projected effect" per hero per real crime category
   present in that neighborhood — a capped, illustrative percentage derived from the
   hero's powerstats relative to the rest of the roster. No LLM involved.
5. Generate a short, LLM-written report per selected hero interpreting those numbers,
   rendered side by side.

## Tech stack

- .NET 10, C# only (no second language, no JS framework)
- Blazor Server (interactive server-side rendering)
- PostgreSQL via Docker for local dev
- EF Core for data access and migrations
- [Groq](https://groq.com) for LLM report generation — structured JSON output, one
  retry on a malformed response, a fixed disclaimer injected by code into every report

## Project structure

```
SuperHeroOps.slnx
/src
  SuperHeroOps.Core              domain models, intervention scoring logic
  SuperHeroOps.Core.Tests        unit tests for the scoring math
  SuperHeroOps.Data               EF Core DbContext, migrations, seed loaders
  SuperHeroOps.Ingestion.Heroes   console app: one-time SuperheroAPI pull -> seed file
  SuperHeroOps.Ingestion.Crime    console app: bounded Socrata pull -> Postgres
  SuperHeroOps.Web                 the Blazor Server app
/seed-data
  heroes.json                     build-time snapshot of the hero roster
docker-compose.yml                local Postgres
.env.example                      template for required/optional local secrets
```

## Data sources

### Heroes — a build-time snapshot, not a runtime dependency

Hero data comes from [SuperheroAPI](https://superheroapi.com) (powerstats, biography,
appearance, work/connections for ~731 characters). `SuperHeroOps.Ingestion.Heroes` is a
real, runnable console app that pulls the full roster once and writes it to
`seed-data/heroes.json`; the web app loads that file into Postgres at startup and never
calls SuperheroAPI itself.

**The ingestion script is checked into this repo on purpose, not left as a missing
feature.** It documents the real integration and makes the seed data reproducible —
anyone with a free SuperheroAPI token can regenerate `heroes.json` from scratch and get
the same shape of data back. Records with missing/null powerstats are dropped at
ingestion time.

Hero portrait images are **not** downloaded or hotlinked: SuperheroAPI's image CDN sits
behind a Cloudflare bot challenge that blocks non-browser HTTP clients regardless of
headers or source IP (confirmed by direct testing). Rather than route around that with
browser automation, the UI shows a simple initial-letter placeholder instead of a
broken image link.

### Crime — a bounded, real pull into Postgres

Crime data comes from the Chicago Data Portal's Socrata API, dataset `ijzp-q8t2`
("Crimes - 2001 to Present"). `SuperHeroOps.Ingestion.Crime` pulls **only the trailing
90 days** via a `$where` date filter — never the full multi-million-row table — and
loads it straight into Postgres via EF Core. The 77 fixed Chicago community areas are
seeded from the Chicago Data Portal's own reference list, independent of the crime
pull, so areas with zero incidents in the current window still show up.

## The intervention scoring model

A hero's "effect" on a crime category is a deterministic function of their powerstats,
computed once real crime data made the actual category mix visible:

| Category | Weighted powerstats |
|---|---|
| Violent (battery, assault, robbery) | strength, combat, durability |
| Property (theft, burglary, motor vehicle theft) | speed, intelligence |
| Criminal Damage | speed, power |
| Narcotics | intelligence, power |
| Weapons Violation | combat, strength |
| Deceptive Practice | intelligence |

Sexual-violence categories (criminal sexual assault, sex offense) and ambiguous
catch-all IUCR types (other offense, criminal trespass, public peace violation) are
deliberately left unscored — modeling those as something a hero's combat stats "solve"
isn't something this app represents, the same reasoning that kept mental health data
out of the concept entirely. Real incident counts for those categories still appear in
the neighborhood breakdown; they just don't get a hero-effect projection.

A hero's raw score for a category is normalized against the **best score anyone in the
seeded roster** achieves for that category (not a theoretical stat maximum), then
mapped linearly onto a capped, illustrative "projected effect" percentage. See
`SuperHeroOps.Core/Scoring/` and its unit tests for the exact math.

## Local setup

1. **Copy `.env.example` to `.env`** and fill in:
   - `SUPERHERO_API_TOKEN` — required only to run hero ingestion yourself (sign in at
     superheroapi.com with GitHub). `heroes.json` is already checked in, so most
     workflows don't need this.
   - `GROQ_API_KEY` — required to generate hero reports.
   - `SOCRATA_APP_TOKEN` — optional, avoids throttling on the crime pull.
   - `SUPERHEROOPS_DB_CONNECTION` — optional, overrides the local Postgres connection
     string (defaults to match `docker-compose.yml`).

   Every entry point loads `.env` at startup, so a normal `dotnet run` picks it up
   automatically — no manual `export` needed.

2. **Start Postgres:**
   ```
   docker compose up -d
   ```

3. **Pull crime data** (bounded to the trailing 90 days):
   ```
   dotnet run --project src/SuperHeroOps.Ingestion.Crime
   ```
   This also applies EF Core migrations and seeds the 77 community areas.

4. **Run the web app:**
   ```
   dotnet run --project src/SuperHeroOps.Web
   ```
   On first run this applies any remaining migrations and seeds the hero roster from
   `seed-data/heroes.json`. Visit the URL printed in the console.

Regenerating `heroes.json` yourself (optional, requires `SUPERHERO_API_TOKEN`):
```
dotnet run --project src/SuperHeroOps.Ingestion.Heroes
```

Running the test suite:
```
dotnet test src/SuperHeroOps.Core.Tests
```

## Known limitations

- `InterventionScore` and `HeroReport` rows are not automatically regenerated when
  crime data is refreshed by a crime ingestion rerun. Recomputing scores/reports
  against a newer crime pull currently requires triggering that separately from the
  Deploy screen. This is a known limitation, not a bug.
- Hero portraits are placeholders (see "Heroes" above) — not a bug, a deliberate
  response to CDN bot protection rather than a workaround that hotlinks or scrapes it.
- The comparison synthesis report (a single trade-off narrative across all selected
  heroes, rather than one report per hero) is a stretch goal from the original build
  plan and hasn't been built yet.
- Deployment to a live GCP e2-micro instance is documented as the intended target but
  hasn't happened yet — it's waiting on a cloud account, not a technical blocker.

## v2.0 (deliberate future scope, not gaps)

- **Map/GIS view.** v1 is a sortable list by design — a map is a real upgrade, not an
  oversight, and it's scoped as its own follow-up rather than rushed into v1.
- **A disaster/nationwide dataset as a separate vertical.** v1 is deliberately
  single-vertical (Chicago crime only); pairing the same hero-deployment model against
  a different real-world dataset is future scope, not scope creep into v1.
