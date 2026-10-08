# SuperHeroOps Engineering Log

Sep 27, 2026 · @Jong

Consolidated build history of SuperHeroOps, a Blazor Server portfolio app pairing real Chicago crime data with a fictional superhero roster and a synthetic intervention-scoring model, merged from three source logs (one design chat, two Claude Code build sessions) into a single chronological record. Phases 1–5 are complete and verified locally; the LLM provider was migrated from Groq to Gemini on 2026-09-23; deployment has not started.

## Overview

| Layer | Choice |
| --- | --- |
| Runtime / language | .NET 10, C# only |
| UI | Blazor Web App / Server, per-page `InteractiveServer` render mode |
| Data access | EF Core 10 + Npgsql, `EFCore.NamingConventions` (snake\_case) |
| Database | PostgreSQL 16 in Docker, host port 5433 |
| Hero data | SuperheroAPI, snapshotted at build time into `seed-data/heroes.json` |
| Crime data | Chicago Data Portal / Socrata SODA API, dataset `ijzp-q8t2`, trailing 90-day window |
| LLM | Google Gemini `gemini-3.1-flash-lite`, OpenAI-compatible endpoint, JSON mode (previously Groq — see Provider Migration) |
| Tests | xUnit, `SuperHeroOps.Core.Tests` (38 passing) |
| Verification tooling | Playwright driving installed Edge, run from the assistant scratchpad (not in the solution) |
| Planned deploy target | GCP e2-micro (deferred, no cloud account yet) |

**Solution layout**

```
SuperHeroOps.slnx
/src
  SuperHeroOps.Core               # domain models, scoring logic
  SuperHeroOps.Core.Tests         # unit tests for scoring
  SuperHeroOps.Data               # DbContext, migrations, seed loaders
  SuperHeroOps.Ingestion.Heroes   # console app: SuperheroAPI -> seed-data/heroes.json
  SuperHeroOps.Ingestion.Crime    # console app: bounded Socrata pull -> Postgres
  SuperHeroOps.Web                # Blazor Server app
/seed-data
  heroes.json
docker-compose.yml
HANDOFF.md
SCHEMA.md
README.md
```

**Working agreement (held across all sessions):** the user runs git and commits; the assistant never commits. Secrets stay out of the repo. No employer, client, or specific government-program references anywhere in the repo.

## Concept & Design Decisions (pre-build)

**Concept pivot.** SuperHeroOps replaced an earlier LEGO Bin Hunter .NET MAUI portfolio demo, which depended on a vision model being correct and had already been scoped down twice. The new concept puts an LLM in a report-generation role over data the app controls, which is easier to test and demonstrate.

**Real vs. synthetic split.** The app keeps a real analytics layer (verifiable Chicago crime data) strictly separate from a synthetic intervention layer (a hero as a parameterized intervention profile scored against that real data), with the fiction boundary marked visibly in the UI. Presenting "which hero reduces crime most" as a real policy result would be methodologically meaningless, so the split makes the fiction explicit.

**Mental health data dropped.** Scoring mental-health outcomes against superheroes risked reading as tone-deaf and added no analytical value over the other datasets.

**Hero data snapshotted, not called live.** SuperheroAPI is a community wrapper over fan-wiki data with unreliable uptime (\~10s response times observed), a token tied to a GitHub login, and a free tier of 60 req/min / 10,000/day. A build-time ingestion script pulls all heroes once (731 records fits the daily cap easily); the runtime never calls the API. The ingestion script stays in the repo as real, runnable code — a deliberate reliability/reproducibility choice, not a shortcut.

**Hogwarts APIs considered and rejected.** HP-API and PotterDB were evaluated as an alternative theme. Rejected because the data has no numeric attributes (every model input would be invented, making the integration decorative) and IP/rights concerns are higher. A related idea — potion ingredients as a bill-of-materials/dependency graph — was kept for a different, future project.

**Portfolio positioning.** Since other themed portfolio projects already exist (Batman, Star Wars), the UI and README were kept visually restrained so substance reads first. No employer or client references anywhere in the public repo.

**Dataset selection: Chicago crime over FEMA disaster data.** At Chicago scope, FEMA disaster data collapses to one Cook County value per neighborhood, giving nothing to model against; a nationwide version (NRI, \~3,140 county rows) would have real variance but was framed as a separate v2 vertical, not combined with crime. Chicago crime data was chosen for richer variance, personal validation ability (the author grew up in Garfield Ridge), and a better conceptual fit — different crime types call for different stat blends, forcing real trade-offs between heroes, whereas disaster response mostly reduces to one or two stats.

**Scope and UX (weekend time budget).** Confirmed UX flow: neighborhood list → neighborhood detail → hero roster (multi-select) → deploy (explicit real-to-synthetic marker) → deterministic effect computation → per-hero LLM reports side by side → *(stretch)* a comparison synthesis report. A map was estimated at +5–8 hours (GeoJSON, Leaflet via Blazor JS interop, choropleth) and cut to a sortable list for v1, with map/GIS named explicitly as v2.0 scope rather than left as a silent gap. Compare mode (multi-hero deploy) was in scope from the start, with one LLM report per hero rather than one combined ranking call, so the user — not the model — makes the comparison.

## Phase 1 — Data Ingestion (2026-09-11)

### 1a. Hero Ingestion

Scaffolded the solution (`dotnet new blazor --interactivity Server` — the `blazorserver` template no longer exists on .NET 10; the SDK also generates `SuperHeroOps.slnx` instead of `.sln`, which every later tool and doc references). Created Core entities (`CommunityArea`, `CrimeIncident`, `Hero`, `InterventionScore`, `HeroReport`) from `SCHEMA.md`, treating its SQL as illustrative and generating real EF Core migrations instead. `Ingestion.Heroes` was built with no project references (a standalone tool); it reads `SUPERHERO_API_TOKEN` from the environment, paces requests \~1.1s apart, retries with exponential backoff, and drops any hero with missing/null powerstats.

- **Issue:** first full run crashed on write because the repo-root lookup searched only for `SuperHeroOps.sln`. Fixed to check both `.sln` and `.slnx`.
- **Security issue:** a plaintext `apikey.md` containing the token was found in the repo root. Deleted outright (not just gitignored); the token lives only in the shell environment (later `.env`).
- **Result:** 731 ids requested, 0 fetch errors, 165 dropped for missing/null powerstats, **566 heroes** written to `seed-data/heroes.json`.
- **Verification:** Batman (id 70) matched the live API (Intelligence 100, Combat 100); no null powerstats in output; ids unique.

### 1b. Crime Ingestion

Inspected live Socrata payloads before coding: `arrest` is a real JSON boolean; `community_area`/`latitude`/`longitude` are quoted numeric strings; `$limit=5000` paging works without an app token; a 90-day window was \~54,771 rows across \~11 pages.

**Decisions:** bound the pull with `$where=date >= '<90 days ago>'` and `$order=date,case_number` for stable paging; delete-and-reload `crime_incidents` on each run (idempotent as the window rolls); seed the 77 fixed community areas from a hard-coded reference list independent of crime rows, so zero-incident areas still appear; drop rows with missing/invalid community area; store `OccurredAt` as `timestamp without time zone` because Socrata dates are naive Chicago wall-clock times (`timestamptz` would falsely imply UTC); map Postgres to host port 5433 because another local container already owned 5432; use `EFCore.NamingConventions` so the schema matches the snake\_case sketch.

- **Issue:** EF Core version conflict — `Microsoft.EntityFrameworkCore.Design` resolved 10.0.12 while Npgsql required EF 10.0.4. Fixed by pinning Design to 10.0.4.
- **Issue:** transient Socrata 503s, absorbed by retry/backoff.
- **Result:** 54,771 rows fetched → 73 dropped (missing/invalid community area) → **54,698 incidents loaded**; 77 areas seeded; all 6 tables created via migration.
- **Verification:** citywide volume \~608 incidents/day, consistent with historical norms. Garfield Ridge (community area 56) = 421 incidents / 52 arrests / 12.4% arrest rate, below the \~710/area average — confirmed by the author against personal knowledge. Top crime types: THEFT 11,560; BATTERY 10,235; CRIMINAL DAMAGE 6,282; ASSAULT 5,000; MOTOR VEHICLE THEFT 4,357.
- **Finding:** CRIMINAL DAMAGE was the #3 category and had been missing from the starting weight map — exactly the gap the phase-checkpoint process was meant to catch (addressed in Phase 2).
- **Known limitation logged:** `InterventionScore` and `HeroReport` are not regenerated when crime data reloads.

## Phase 2 — Intervention Scoring Model (2026-09-11)

Printed the full top-15 real crime-type distribution (of 54,698 incidents) before finalizing weights: THEFT 11,560 (21.1%), BATTERY 10,235 (18.7%), CRIMINAL DAMAGE 6,282 (11.5%), ASSAULT 5,000, MOTOR VEHICLE THEFT 4,357, OTHER OFFENSE 3,471, BURGLARY 3,380, DECEPTIVE PRACTICE 2,691, NARCOTICS 1,515, WEAPONS VIOLATION 1,381, ROBBERY 1,286, CRIMINAL TRESPASS 1,286, CRIMINAL SEXUAL ASSAULT 360, PUBLIC PEACE VIOLATION 345, SEX OFFENSE 341.

**Weight map** (`CrimeCategoryWeights.cs`, `CrimeTypeCategorizer.cs`):

| Category | IUCR types | Weighted stats |
| --- | --- | --- |
| Violent | BATTERY, ASSAULT, ROBBERY | Strength, Combat, Durability |
| Property | THEFT, BURGLARY, MOTOR VEHICLE THEFT | Speed, Intelligence |
| Criminal Damage | CRIMINAL DAMAGE | Speed, Power |
| Narcotics | NARCOTICS | Intelligence, Power |
| Weapons Violation | WEAPONS VIOLATION | Combat, Strength |
| Deceptive Practice | DECEPTIVE PRACTICE | Intelligence |
| Unscored (counted, no effect projection) | OTHER OFFENSE, CRIMINAL TRESPASS, PUBLIC PEACE VIOLATION, CRIMINAL SEXUAL ASSAULT, SEX OFFENSE, and all other types | — |

Criminal Damage was added as a new bucket (user-directed) once Phase 1b surfaced it as the #3 category. `InterventionScoring.cs` implements `RawScore` (weighted sum of stats), `NormalizeRoster` (0–100, relative to the best hero actually in the roster, not a theoretical stat max), and `ProjectedEffectPercent` (a capped linear mapping with a 40% ceiling set by one named constant, `MaxEffectCeilingPercent`). 34 unit tests were added covering categorization (incl. case-insensitivity and unmapped types), raw scoring, normalization edge cases (ties, single-hero roster, all-zero divide guard, empty roster), and clamping.

**Sanity check** over the 566-hero roster: medians \~49–63 per category, minimums \~0–9; expected leaders topped each category (Batman for Deceptive Practice, Amazo/Big Barda for Violent, Goku/Hercules for Weapons); 7–39 heroes tie at 100 per category, from cosmic-tier characters in the roster rather than a bug.

**Categorization amendment — sexual violence excluded from Violent.** CRIMINAL SEXUAL ASSAULT and SEX OFFENSE were first grouped into Violent, then moved to unscored: presenting real victimization data for named neighborhoods as something a hero's combat stats "solve," shown as a percentage, was judged the same problem that excluded mental-health data. A later proposal to move CRIMINAL SEXUAL ASSAULT back into Violent (on grounds of comic canon and consistency with homicide) was declined — homicide itself was never in the scored Violent category, only in the unscored display breakdown, and real counts stay fully visible either way; only the fictional effect layer is excluded. Tests updated; all 34 passed.

A minimal README was created with the "modeling exercise, not a forecast" statement and the known-limitation note that scores/reports are not auto-regenerated on a crime rerun (expanded later — see README Work).

## Phase 3 — Blazor Scaffold (2026-09-11)

Registered `IDbContextFactory<SuperHeroOpsDbContext>` (via `ServiceCollectionExtensions.AddSuperHeroOpsDbContext`) rather than a scoped `DbContext`, since Blazor Server circuits are long-lived and a scoped context would grow stale. Replaced the template pages with `Home.razor` (`/`) — a sortable list of all 77 community areas with incidents, arrests, and arrest rate, default sort by incidents descending — and `NeighborhoodDetail.razor` (`/neighborhoods/{id}`) — full crime-type breakdown, invalid ids handled. Sample Counter/Weather pages removed.

- **Issue:** `dotnet watch run` failed from the repo root ("couldn't find a MSBuild project file"). Fixed by running `dotnet watch run --project src/SuperHeroOps.Web` or `cd`-ing into the project first.
- **Verification:** Austin tops the list with 2,888 incidents; Garfield Ridge shows 421 incidents / 52 arrests / 12.4% arrest rate; the detail breakdown sums exactly to 421; EF SQL is parameterized and uses the indexes. No GUI browser was used by the assistant at this point — pages were checked via curl against the dev server plus EF SQL logs, and the assistant flagged live click-sorting as unverified; **the user then confirmed in a real browser** that data display and interactive sorting both worked.

## Phase 4 — Hero Roster, Deploy Screen, Images & Biographical Data (2026-09-11)

### Roster and deploy

**Gap found:** heroes had only ever been written to JSON; the `heroes` table was empty. Added an idempotent `HeroSeedLoader.LoadIfEmptyAsync` (Hero.Id reuses the SuperheroAPI id, so seeding is a direct 1:1 deserialize), run alongside migrations on Web startup. Built `HeroRoster.razor` (566 heroes, 30/page, multi-select persisting across pages via a `HashSet`) and the first single-page `Deploy.razor`: an explicit "Confirm Deployment" step, a purple-gradient "Simulated Intervention Model" banner marking the real-to-synthetic pivot, and a comparison table of projected effect % limited to categories actually present in that neighborhood's real data.

- **Verification:** 566 hero rows seeded; Abe Sapien's stats matched the seed file; A-Bomb and Batman scores for Garfield Ridge were recomputed by hand across all six categories and matched exactly (e.g., Batman 40.0% Deceptive Practice / 29.4% Narcotics; A-Bomb 32.8% Weapons / 32.5% Violent). The literal button click was not yet driven by the assistant at this point (flagged explicitly).
- **Decision:** persist `InterventionScore` rows on deploy confirmation rather than recompute live — the two-table split only makes sense if scores are stored durably, the README's staleness note assumes storage, and a populated table is evidence the pipeline ran end to end.
- **Recurring issue (began here, recurred \~6 times over the build):** stray `SuperHeroOps.Web.exe` processes left running caused MSBuild file-lock failures (MSB3026/3027); resolved each time by killing the process.

### Hero images — 403 and reversal

User reported 403s on hero portraits from the SuperheroAPI image CDN. Referer-based hotlink blocking was suspected first; **actual cause confirmed:** superherodb.com's image CDN sits behind a Cloudflare bot challenge (`Cf-Mitigated: challenge`, TLS/client fingerprinting), not a simple hotlink rule. An initial theory that the cause was a cloud-sandbox IP was wrong — the tooling runs on the user's own machine — and disabling a VPN did not clear the block either, ruling out IP reputation as the sole factor.

A first plan (download portraits during ingestion into `wwwroot/images/heroes/{id}.jpg`, rename `ImageUrl` → `ImagePath`) was implemented, then abandoned mid-run when 100% of downloads returned 403 even from a server-side `HttpClient` and via curl with browser headers — the run was killed immediately to avoid burning quota while dropping every hero. **User decision: skip images entirely.**

**Final implementation:** `Hero.ImagePath` made nullable (always null now, already wired for a future fix); ingestion has no image code; the unapplied rename migration was removed and regenerated as a single `RenameAndNullifyHeroImagePath` migration (drop `image_url`, add nullable `image_path` — never applied to a live DB before this, so no data risk); Razor renders `<img>` only when a path exists, otherwise a circular initial-letter placeholder badge (`.hero-avatar-placeholder`). Reran ingestion (566 heroes, `ImagePath: null`), truncated and reseeded `heroes` after confirming dependent tables were empty. Verified zero `<img>` tags and clean placeholders on both pages. Real portraits (would need Playwright/headless-browser automation) deferred to v2.0.

### Biographical data extension

**Request:** show hero description/abilities/stats on the deploy screen. **Finding:** SuperheroAPI has no free-text description or abilities field, but does return structured biography (full name, aliases, place of birth, first appearance, publisher, alignment), appearance (height, weight, eye/hair color), and work/connections (occupation, base, group affiliation) — kebab-case JSON keys, array-valued aliases/height/weight, and missing-data placeholders of `"-"` / `["-"]` / `["-","0 cm"]` (a different convention from the `"null"` string used for powerstats).

- **Bug caught before shipping:** an initial `IsMissingMarker` used `TrimStart('-')`, which would not flag `"- lb"`. Replaced with a "trimmed value starts with `-`" rule.
- **Decisions:** label the new section "Hero Details," never "Abilities," and never fabricate ability text for named characters; aliases stored comma-joined; height/weight stored as `"6'2 (188 cm)"`; new columns nullable text.
- A smoke test on ids 1–8 overwrote `seed-data/heroes.json` with only 8 heroes; restored by an immediate full reseed run (731 calls: 566 kept, 165 dropped, 0 errors) under migration `AddHeroBiographicalFields`.
- The Deploy screen gained a side-by-side "Hero Details" table (13 fields, `—` for missing), placed **before** the synthetic banner since it is descriptive real data, not model output.

## Phase 5 — LLM Report Generation (2026-09-11)

**Spec:** one Groq call per selected hero; input = the area's real crime summary plus that hero's **persisted** `InterventionScore` rows (this phase answers Phase 4's open question by persisting scores, upsert per area+hero+category, on the compare/deploy step); output = strict three-field JSON (`risk_assessment`, `predicted_impact_narrative`, `recommendation`), parsed and never rendered as raw text, with one corrective retry on malformed/invalid output and a null return on failure so one bad hero doesn't fail the page. The system prompt forbids inventing powers or abilities; a fixed disclaimer ("This is a modeling exercise using fictional characters — not a policy forecast.") is appended by code into the stored narrative, never left to the model. Added Core `HeroReportDisclaimer` (pure function) plus 4 unit tests (suite → 38).

- **Issue:** report generation returned 404 for every hero. Root cause via direct curl: the model `llama-3.3-70b-versatile` no longer existed on the account (the catalog had moved on since the assistant's training data; `/models` listed no Llama models). **Fix:** switched the default to `openai/gpt-oss-120b` (verified with a raw JSON-mode call first — it's a reasoning model but `message.content` comes back clean JSON), overridable via `GROQ_MODEL`.
- **Issue:** `GROQ_API_KEY` was not set for the running app. Handled in the UI with an explicit "key not set" message rather than a generic per-hero failure.
- **Verification (standard raised after the earlier missed click-through):** used Playwright + installed Edge, from a scratch project outside the solution, to click Confirm Deployment and Generate Hero Reports for A-Bomb and Batman on Garfield Ridge, and read the generated text. The score table matched the earlier hand calculations exactly; both reports cited the correct percentages; the disclaimer was present verbatim; no invented powers. Confirmed `intervention_scores` (12 rows) and `hero_reports` (2 rows) persisted, disclaimer included inside `predicted_impact_narrative`.

Status: completed and confirmed working by the author (2026-09-26 log date).

## Secrets Handling & README

**`.env` adoption.** The app reported the API key as unset even after the user ran it, because the assistant had only been exporting keys inline in its own shells and reading a notes file by hand — nothing in the app itself read that file. **Decision:** a standard `.env` at the repo root (gitignored via both `.env` and `*.env` rules), loaded at startup without overriding variables already set in the shell. Added `EnvFileLoader` in `Data` (wired into `Web` and `Ingestion.Crime`, which find `.env` by walking up to `SuperHeroOps.slnx`) plus a small duplicate inside `Ingestion.Heroes`, which has no solution references. Added a committed `.env.example`; migrated keys into `.env`; deleted the redundant notes file. Verified by unsetting shell vars, starting Web, and rerunning the Playwright click-through — reports still generated.

**README.** Expanded `SuperHeroOps/README.md` to satisfy the spec's non-negotiables: the modeling-exercise statement up front, why the hero ingestion script is checked in, and a v2.0 section (map/GIS; disaster data as a separate vertical). Added concept, stack, layout, data sources, scoring table, local setup, and known limitations (score/report staleness, placeholder portraits, no synthesis report, deployment not done). Grepped for employer/client leakage — only the technical phrase "HTTP clients" matched. Deployment status recorded as deferred until the user has a GCP account. A SuperHeroOps entry was added to the root repository README only, placed after the other flagship full-stack project.

## Deploy Flow Restructured Into Three Pages (2026-09-13)

**Request:** progression = hero information cards → impact comparison → AI reports, with a "next" button at the bottom. Replaced the single `Deploy.razor` with `DeployDetails` (`/deploy/{id}/details`, per-hero cards), `DeployCompare` (`/compare`), and `DeployReports` (`/reports`); `HeroRoster` now navigates to `/details`; hero ids travel as repeated `heroIds` query params.

**Decisions:** landing on `/compare` now *is* the deploy confirmation — scores compute and persist automatically on load (idempotent upsert), replacing the earlier separate "Confirm Deployment" click. The reports page keeps an explicit **Generate Hero Reports** button, since a paid API call should stay deliberate, and has no "Next" (it's the terminal step). "← Back" links were added for consistency. Persisted-report recall on reload was explicitly left out of scope.

- **Issue:** report generation failed again with `403 "Access denied. Please check your network settings."` from Groq, even though the same request had worked days earlier. `ipinfo` showed the outbound IP on PacketHub S.A. (VPN/proxy hosting) — the user's VPN was back on. Disconnecting it changed the IP; a direct curl then succeeded, and the full three-page Playwright walk-through passed (correct URL transitions, zero "Next" buttons on the final page, disclaimer present).

## Provider Migration — Groq to Gemini (2026-09-23)

**Request:** swap the LLM behind hero-report generation from Groq to Google Gemini `gemini-3.1-flash-lite`, keep both keys out of the repo, update the README, and re-test against the running local stack — without committing (the user commits).

| Item | Before | After |
| --- | --- | --- |
| Provider | Groq (`api.groq.com/openai/v1/`) | Gemini, OpenAI-compatible endpoint (`generativelanguage.googleapis.com/v1beta/openai/`) |
| Default model | `openai/gpt-oss-120b` | `gemini-3.1-flash-lite` |
| API key env var | `GROQ_API_KEY` | `GEMINI_API_KEY` |
| Model override env var | `GROQ_MODEL` | `GEMINI_MODEL` |
| Service class | `GroqHeroReportService` | `GeminiHeroReportService` |

Wire format, prompts, JSON validation, and the one-retry rule were **not** changed.

**Decision — use Gemini's OpenAI-compatible endpoint** rather than the native Gemini REST API: the existing request/response classes, Bearer-header auth, `json_object` response format, and JSON validation all carry over unchanged, so the change is a rename plus config rather than a rewrite. It's also a secret-safety win — auth stays in a header, whereas the native API commonly takes the key as a `?key=` query parameter that's more likely to leak into logs.

**Implementation:** renamed files (`GroqChatModels.cs` → `GeminiChatModels.cs`, `GroqHeroReportService.cs` → `GeminiHeroReportService.cs`) via plain `mv`, so nothing was staged. Updated `Program.cs`'s DI registration, `DeployReports.razor` (injected type, env-var check, `geminiKeyMissing`, user-facing message), comments in `EnvFileLoader.cs`/`HeroReport.cs`, `.env.example`, and the README's tech-stack and setup sections. A repo-wide grep for `groq|grok` (excluding `bin/obj`) returned nothing after the change; `dotnet build` succeeded with 0 warnings/errors.

- **Secret-hygiene verification:** searched the project for each key's actual value (read into a shell variable, never echoed). A first pass reported one "leak" outside `.env` for each key — a false alarm caused by `--exclude` flags being placed after `--`, so grep treated them as filenames instead of excludes; the single hit was `.env` itself. Re-run with flags ordered correctly: **0 occurrences outside `.env`** for both keys. Confirmed `.env` is git-ignored via `git check-ignore -v`.
- **Residual housekeeping:** `.env` still holds the old, now-unused `GROQ_API_KEY`. Suggested deleting that line and rotating the key if still active.
- **Testing:** `dotnet test` (38/38 passing, no LLM involved). `dotnet run` served the home page at 200 with no key material in the startup log (566 heroes, 54,698 crime incidents, 78 intervention scores, 8 existing hero reports, community areas 1–77). The reports page is an interactive Blazor Server button click with no browser tooling available in this session, so a throwaway console harness was built to call `GeminiHeroReportService.GenerateAsync` directly against real DB rows (area 25 Austin, hero 17 Alfred Pennyworth).
  - **Issue:** first harness run printed `key set: False` / `RESULT: null` because `EnvFileLoader` walks up from the executable to find `.env`, and the harness lived outside the repo. This also confirmed that a missing key fails silently (`null`), not with an error. **Fix:** exported `GEMINI_API_KEY` into the harness process environment directly (not printed).
  - **Result:** success — valid three-field JSON returned, disclaimer correctly appended by code. Harness deleted afterward; web process stopped; temp log checked for key patterns (0 matches).

**Known pre-existing design weakness (not changed):** `GenerateAsync` returns `null` for any failure — missing key, HTTP error, or bad JSON — with no logging, so the UI only ever shows "Report generation failed for this hero." Flagged as a candidate improvement (log status code and a sanitized error).

**Other notes:** the report generated for Alfred Pennyworth stated a background in "field medicine and logistics," contradicting the system prompt's ban on inventing ability/background details (occupation was the only input) — flagged, not fixed. The 8 pre-existing `hero_reports` rows (likely from Groq runs) are silently overwritten by the UI's upsert-on-regenerate behavior. The Gemini model id `gemini-3.1-flash-lite` was used exactly as specified and confirmed only by the live call succeeding, not independently checked against Google's docs.

**End state:** code complete, built, and live-tested; **nothing committed** (git commands run were read-only: `git check-ignore`, `git status --short`).

## Consolidated Issue Register

| # | Phase | Issue | Root cause | Resolution |
| --- | --- | --- | --- | --- |
| 1 | 1a | `blazorserver` template not found | Unified into `blazor` on .NET 10 | `dotnet new blazor --interactivity Server` |
| 2 | 1a | Hero ingest crashed at write | Repo-root search only checked `.sln`; SDK generated `.slnx` | Check both extensions |
| 3 | 1a | Superhero token "not set" | Never exported/persisted | Read from a local key file, later `.env` |
| 4 | 1b | Port 5432 taken | Another local Postgres container | Mapped this project to 5433 |
| 5 | 1b | EF Core version conflict (CS1705) | Design package 10.0.12 vs. Npgsql-required 10.0.4 | Pinned Design to 10.0.4 |
| 6 | 1b | `timestamptz` vs. naive Socrata dates | Npgsql default demands UTC kind | Column type `timestamp without time zone`; regenerated unapplied migration |
| 7 | 1b | Socrata 503s | Transient | Retry/backoff |
| 8 | 4 | Repeated MSBuild file locks | Stray `SuperHeroOps.Web.exe` from earlier runs | Kill process; recurred \~6 times |
| 9 | 4 | Heroes never in Postgres | Only a JSON file existed | Idempotent `HeroSeedLoader` on Web startup |
| 10 | 4 | Portrait 403s | Cloudflare bot challenge on image CDN (TLS fingerprinting) | Dropped images; nullable `ImagePath` + placeholder badges |
| 11 | 4 | Wrong diagnosis (sandbox IP) | Assistant assumed a remote sandbox | Corrected: tools run locally; VPN test then ruled out IP-only cause |
| 12 | 4 | Missing-data placeholder bug (`"- lb"`) | Over-clever hyphen trim | "Starts with `-`" rule; caught before shipping |
| 13 | 4 | Smoke test clobbered `heroes.json` | Ingest writes to the real seed path | Restored by an immediate full run |
| 14 | 5 | Groq 404 on every report | Model `llama-3.3-70b-versatile` deprecated | Switched to `openai/gpt-oss-120b`; `GROQ_MODEL` override |
| 15 | 5 / Secrets | "API key not set" for the user | Key only lived in the assistant's shell | `.env` loader |
| 16 | Restructure | Groq 403 "network settings" | VPN exit IP blocked (PacketHub) | Disconnected VPN |
| 17 | 3–5 | Unverified interactivity in earlier phases | No browser driven by the assistant | User confirmed manually; later Playwright click-throughs |
| 18 | Gemini migration | Grep falsely reported a leaked key | `--exclude` flags placed after `--`, so grep treated them as filenames | Reordered flags; re-ran, 0 leaks |
| 19 | Gemini migration | Harness printed `key set: False` | `EnvFileLoader` walks up to find `.env`; harness lived outside the repo | Exported the key into the harness process env directly |

## Consolidated Decision Register

1. Migrations generated from EF Core entities, not hand-applied SQL.
2. `IDbContextFactory` over a scoped `DbContext`, for Blazor Server's long-lived circuits.
3. Bounded 90-day crime pull with delete-and-reload semantics; community areas seeded independently of crime rows.
4. Naive Chicago timestamps stored without time zone.
5. Six scoring buckets, with Criminal Damage split out; sexual-violence and other ambiguous types left unscored (user-final, after one reversal attempt).
6. Roster-relative score normalization; a 40% illustrative effect ceiling as one named constant.
7. Hero ingestion stays a real, checked-in script; the runtime never calls SuperheroAPI live.
8. Portraits dropped after the Cloudflare block; `ImagePath` made nullable with a UI placeholder badge.
9. "Hero Details" section holds only real biographical data, never invented abilities, and sits before the synthetic banner.
10. `InterventionScore` persisted at the compare/deploy step; reports read the persisted rows, not a live recompute.
11. One LLM call per hero; the disclaimer is appended by code into the stored narrative; one corrective retry; null-on-failure so one bad hero doesn't fail the page.
12. `.env` (gitignored) + committed `.env.example`; shell environment variables always win over the file.
13. Three-page deploy flow (details → compare → reports); landing on compare doubles as the deploy confirmation; the paid-call reports step keeps an explicit button.
14. Verification standard raised to real-browser (Playwright) click-throughs after an early phase shipped with unverified interactivity.
15. LLM provider switched from Groq to Gemini via its OpenAI-compatible endpoint, keeping the existing wire types, Bearer-header auth, and JSON-mode validation unchanged — a rename plus config, not a rewrite.
16. All Groq-named types, files, and env vars renamed rather than left misleading after the Gemini switch.
17. No request/response logging added anywhere in the LLM path, to avoid any way a key or payload could leak into logs.
18. No git operations that change repository state are ever performed by the assistant; the user commits everything, across every session.

## Status & Open Items

**Done:** hero ingestion, crime ingestion, scoring model with tests, neighborhood list/detail, hero roster with multi-select, three-page deploy flow with persisted scores, hero biographical details, per-hero LLM reports (now on Gemini), `.env`-based secrets, and a README covering the spec's non-negotiables.

**Open items:**

- Manually click through "Generate Hero Reports" end-to-end in a real browser on the current Gemini code path (only a scratch-harness call and earlier Groq-era Playwright runs have exercised it so far).
- Decide whether to tighten the system prompt against invented hero backstory (the Pennyworth "field medicine and logistics" case).
- Decide whether to add sanitized failure logging to `GenerateAsync` (currently silent `null` on any failure).
- Remove the stale `GROQ_API_KEY` line from `.env` and rotate that key if it's still active.
- Commit the outstanding changes — the rename pair and README update are suggested as one logical commit (the user commits; the assistant has made none).
- Deploy to GCP e2-micro — not started, blocked on a cloud account.
- Comparison synthesis report (stretch goal) — not built.
- Report recall — the reports page does not reload persisted `HeroReport` rows on visit; regeneration is manual.
- Scores/reports are not automatically regenerated when crime data refreshes (documented limitation, not fixed).
- Ingestion safety: the hero ingester writes straight to `seed-data/heroes.json`; a partial-range smoke test overwrote it once. Consider a configurable output path/range.
- Real hero portraits: out of scope without browser automation to get past the Cloudflare challenge (deferred to v2.0, alongside map/GIS and nationwide disaster data).
- Minor schema note: `HeroReport.RiskAssessment` was implemented as text rather than the jsonb the original schema sketch suggested — worth reconciling if schema-vs-sketch drift matters going forward.

**Key numbers to keep on hand:** 731 hero ids requested / 566 kept / 165 dropped; 54,771 crime rows fetched / 54,698 loaded / 73 dropped; 77 community areas; Garfield Ridge (area 56) = 421 incidents / 52 arrests / 12.4% arrest rate; 38 unit tests passing; Postgres on port 5433; dev server on port 5246.
