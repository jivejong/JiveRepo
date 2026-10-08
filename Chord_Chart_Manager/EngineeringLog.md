# Chord Chart Manager Engineering Log

Last updated: 2026-10-08

## Purpose

This log records the architecture, implementation decisions, significant problems, fixes, verification work, and known limitations of Chord Chart Manager. Use `README.md` for setup and normal operation, and `HANDOFF.md` when beginning a new development session.

## Product Summary

Chord Chart Manager is a tablet-oriented progressive web application for storing, editing, transposing, and performing chord charts. It also manages ordered setlists and includes a Python ingestion pipeline that converts `.docx` charts into database records.

The locally developed deployment model is:

```text
Browser / installed PWA
        |
        | same-origin HTTP locally
        v
Express server (port 3000)
  - REST API under /api
  - compiled React/Vite client
        |
        v
PostgreSQL 16

Optional one-shot Python pipeline
  .docx -> parse -> optional enrichment -> JSON -> PostgreSQL
```

Local operation was deliberately prioritized before cloud infrastructure work.

## Architecture

### Components

| Component | Technology | Responsibility |
| --- | --- | --- |
| Web client | React 18, Vite 7 | Song library, charts, editing, setlists, settings, offline UI |
| PWA layer | `vite-plugin-pwa`, service worker | Installation and static asset caching |
| Offline storage | Browser IndexedDB | Cached data, pending song mutations, sync metadata, conflicts |
| API server | Express on Node 22 | CRUD, search, export, setlists, sync conflict checks, static hosting |
| Database | PostgreSQL 16 | Authoritative songs, metadata, and setlists |
| Ingestion pipeline | Python 3.10 | Parse `.docx`, enrich metadata, and load songs idempotently |
| Orchestration | Docker Compose | Local database/app and optional pipeline profile |
| Tests | Vitest and Playwright | Unit tests and live-browser smoke testing |

### Repository Layout

```text
Chord_Chart_Manager/
├── README.md
├── HANDOFF.md
├── EngineeringLog.md
├── .env.example
├── app/
│   ├── src/                 React client
│   ├── server/              Express API and shared chart parser source
│   ├── db/init/             PostgreSQL initialization schema
│   ├── tests/               Vitest tests
│   ├── e2e/                 Playwright smoke test
│   └── Dockerfile           Combined production app image
├── pipeline/
│   ├── docs/                Source `.docx` files, intentionally untracked
│   ├── parsed_out/          Generated JSON/cache, intentionally untracked
│   ├── chart_parser.py
│   ├── batch_parse.py
│   ├── enrich_songs.py
│   ├── load_songs.py
│   ├── schema.sql
│   └── Dockerfile           Python 3.10 pipeline image
└── infra/                   Future infrastructure notes; not deployed
```

### Local Runtime

The app image uses a multi-stage Node 22 Alpine build. It installs locked dependencies with `npm ci`, builds the Vite client, installs production server dependencies, and runs as the non-root Node user. Express serves both `/api` and the compiled client so browser requests remain same-origin.

Docker Compose defines:

- `db`: PostgreSQL 16, host port `5432`, persistent named volume `pgdata`.
- `app`: Express and compiled client on host port `3000`.
- `pipeline`: opt-in profile for one-shot Python commands, with `pipeline/docs` and `pipeline/parsed_out` bind-mounted from the project folder.

The infrastructure notes propose a possible future split between a static nginx web image and an API image. That architecture has not been selected or implemented. The locally verified design is the combined Express API/static server.

## Data Model

The database contains eight primary tables:

- `artists`, `genres`, and `vibes` normalize reusable metadata.
- `songs` stores song metadata, source chart text, structured chart JSON, key/capo defaults, and timestamps.
- `song_genres` and `song_vibes` implement many-to-many relationships.
- `setlists` stores the setlist name, optional gig date, and notes.
- `setlist_songs` stores ordering and optional key, capo, and note overrides.

An `updated_at` trigger maintains modification timestamps. These timestamps are also the version tokens used by offline conflict detection.

Two schema copies exist:

- `app/db/init/01-schema.sql`, used when PostgreSQL initializes an empty volume.
- `pipeline/schema.sql`, available to pipeline workflows.

These copies were identical when checked before the Git revert. They can drift, so future schema work must verify both or replace them with one canonical migration source.

PostgreSQL init scripts run only when `pgdata` is empty. Editing init SQL does not update an existing database. There is no migration framework yet.

## Chart Representation and Rendering

Songs deliberately use two representations:

- `chart_source`: editable, human-readable chart text.
- `chart_content`: structured JSON for reliable rendering and transposition.

The JavaScript chart parser lives in `app/server/chartParser.js`. The server imports it directly. The browser receives the same source through a Vite virtual-module transformation that removes the CommonJS export branch. This allows offline edits to render immediately without maintaining a second browser parser.

The Python `.docx` parser is a separate whole-document implementation. The JavaScript and Python parsers can drift and need fixtures if strict behavioral equivalence becomes important.

Older records that have structured content but no source text can be reconstructed into editable text. Reconstruction may not preserve the original spacing exactly.

## Application Behavior

### Navigation

The client uses view state in `App.jsx` rather than a URL router. Primary tabs select Songs, Setlists, or Settings; internal state opens chart, edit, and setlist-detail views.

At the time of the previous implementation review, chart swipe navigation was horizontal:

- Swipe left moved to the next song.
- Swipe right moved to the previous song.
- The threshold was 60 pixels.
- Horizontal movement had to exceed vertical movement by a factor of 1.5.

The earlier user requirement and old handoff specified up/down navigation. This is an unresolved product discrepancy. Re-check the post-revert code and confirm the desired gesture with the user before changing it.

### Songs and Charts

Implemented behavior before the revert included:

- Search by title/artist and filter by tag.
- Create, edit, and delete songs.
- Structured chart rendering.
- Client-side transposition and capo adjustment.
- Persist preferred key and default capo.
- Offline song creation, editing, deletion, and immediate re-rendering.

The API supports adding/removing tags, but the UI only used tags for filtering.

### Setlists

Implemented setlist behavior before the revert included:

- Create a setlist with name, optional gig date, and notes.
- Add songs with optional key, capo, and note overrides.
- Reorder entries with explicit up/down controls.
- Remove entries.
- Read cached setlists while offline.

Known gaps were:

- Opening a chart from a setlist did not apply the entry's key/capo override.
- Existing entry overrides could not be edited in the UI.
- Rename/delete controls were not exposed in the UI.
- Setlist mutations were not queued offline.

### Offline Song Synchronization

The original design treated the desktop/database copy as the only editable source. Tablet editing later became a requirement. IndexedDB was expanded from passive caching into a song mutation queue supporting offline create, update, delete, and preferred-key/capo changes.

Offline-created songs receive a temporary `local-<uuid>` ID. Successful upload maps that ID to the server-generated record.

Synchronization order is intentional:

1. Push pending song mutations.
2. Fetch the authoritative song export and setlists.
3. Replace the local cache with the fetched state.

The selected conflict policy is server/desktop wins. Offline updates and deletes carry `base_updated_at`. The server locks the target row, compares timestamps, and returns HTTP 409 for a stale mutation. The client discards the stale tablet mutation, retains the server copy, and records a conflict notice in Settings.

An offline-created song deleted before its first synchronization is removed locally and never sent to the server.

Synchronization is attempted on reconnect and can also be started manually from Settings.

Service workers require a secure context. Browsers treat `localhost` as secure, but a tablet using plain `http://<LAN-IP>:3000` should not be expected to install or run the PWA offline reliably. Trusted HTTPS is required for dependable tablet use.

## Ingestion Pipeline

The Python pipeline consists of:

1. `chart_parser.py`: parses one Word document into structured song data.
2. `batch_parse.py`: processes `pipeline/docs/*.docx` and writes `songs.json`, `report.json`, and optional per-document output.
3. `enrich_songs.py`: optionally adds MusicBrainz and GetSongBPM data. Parsed values take precedence; responses are cached in `parsed_out/enrich_cache.json`.
4. `load_songs.py`: loads JSON into PostgreSQL.

MusicBrainz is intended to supply release year and genre data, with vibe as a best-effort derivation. GetSongBPM is intended to supply BPM and key. MusicBrainz requires contact information in its User-Agent, and GetSongBPM requires an API key. The README links to both services. Visible GetSongBPM attribution is still needed before enriched data is distributed through the UI.

The loader is idempotent on `(lower(title), artist_id, source_document)`. Per-song savepoints isolate malformed records, and secondary genres are retained.

Dry-run originally interacted incorrectly with savepoints and could permit partial commits. It was changed to run the entire operation, including optional truncation, inside one outer transaction that is rolled back at the end.

The pipeline image uses Python 3.10 and bind-mounts input/output directories from the project folder. This avoids a host-Python dependency and keeps pipeline files portable with the repository.

Before the revert, the working copy had no source `.docx` files, generated parse output, MusicBrainz contact value, or GetSongBPM key. A real corpus enrichment/load had not yet been completed.

## Decision Record

| Decision | Reason | Consequence |
| --- | --- | --- |
| Finish local operation before cloud work | Establish a dependable baseline | Infrastructure remains intentionally deferred |
| Serve local client and API from Express | Simple same-origin local deployment | Split nginx/API topology remains an open cloud decision |
| Store source text and structured chart JSON | Editing convenience plus deterministic rendering | Both representations must stay synchronized |
| Share one JavaScript parser source | Identical online/offline rendering | Requires a Vite bridge for the CommonJS source |
| Transpose in the client | Instant performance controls | Client chord handling needs strong tests |
| Use IndexedDB | Tablet can browse/edit without connectivity | Sync and conflict handling become application responsibilities |
| Push before pulling | Protect unsent local work | Failed mutations must be handled individually |
| Server/desktop wins conflicts | Simple policy explicitly selected by the user | A tablet edit can be discarded and must be reported |
| Queue song mutations only | Meets the immediate editing requirement | Setlist changes are not available offline |
| Use PostgreSQL and JSONB | Relational integrity plus flexible chart structures | Database lifecycle and migrations must be managed |
| Use a Python 3.10 pipeline container | Reproducible, portable ingestion | Image must be rebuilt after pipeline changes |
| Commit lockfiles and use `npm ci` | Deterministic dependencies | Lockfile updates must be intentional |
| Keep Playwright development-only | Browser testing without production bloat | Browser binaries require a developer install |
| Defer authentication for the local prototype | Keep the initial scope focused | The app must remain on a trusted network |

## Significant Development Issues and Fixes

### Loader transaction safety

`load_songs.py --dry-run` did not initially guarantee rollback of the whole operation around nested savepoints. The loader was restructured around one outer transaction while retaining per-song savepoints.

### Setlist experience

Setlists were expanded into dedicated list/detail views with gig metadata, notes, song selection, performance overrides, ordering buttons, and entry removal.

### Offline editing

The design changed from desktop-only editing to bidirectional tablet push/pull. Temporary IDs, queued mutations, version checks, reconnect sync, and visible conflict reporting were added.

### Parser blank-page regression

The first direct reuse of the server's UMD/CommonJS parser built successfully but caused a blank browser page because the CommonJS path did not establish the expected browser global. A Vite virtual module now loads the exact source and removes the CommonJS branch for browser use.

### Export completeness

Offline sync required more fields than the original export returned. Export data was expanded to include chart source, preferred key, capo, and timestamps, with source reconstruction for older records.

### Dependency and test hygiene

An earlier project copy had no lockfiles or test files and installed nondeterministic dependency versions. `jsdom` and Playwright were incorrectly production server dependencies.

The remediation added:

- Client and server lockfiles.
- `npm ci` in Docker builds.
- Vitest parsing/transposition tests.
- Playwright as a development-only dependency and an app-shell smoke test.
- Removal of browser-test libraries from production dependencies.
- Vite/PWA/test tool upgrades that previously produced zero known npm audit advisories.

### Portability and documentation

The project moved from `projects/Chord_Chart_Manager` to the repository root on 2026-09-22. Relative Compose contexts, mounts, and documentation were updated so the folder could move as a unit. The main README was expanded on 2026-09-29.

## Last Known Verification Snapshot

Before the Git revert reported on 2026-10-08, the previous inspection recorded:

| Check | Last known result | Notes |
| --- | --- | --- |
| `npm test` in `app/` | Passed | 2 files, 7 tests |
| `npm run test:e2e` | Not exercised in that snapshot | `ERR_CONNECTION_REFUSED` because Compose was stopped; the smoke test had passed previously with the app running |
| `docker compose ps` | No running services | Stack was stopped, not diagnosed as broken |
| `app/.env` | Absent | Needed to be copied from `.env.example` |
| Pipeline inputs/output | Empty | No `.docx` files or generated data |
| Enrichment credentials | Not configured | Required only for live enrichment |
| Schema copies | Matched | Verified before the revert |

Historically verified during implementation:

- Docker app build and PostgreSQL health.
- `/healthz` and API responses.
- Production Vite/PWA build.
- Clean-browser rendering after the parser fix.
- Offline create/update/delete sync and server-wins conflict handling through a browser smoke script.
- Pipeline Python 3.10 runtime, module imports, bind mounts, and PostgreSQL connectivity.
- Playwright shell smoke test while the stack was running.

Because Git was reverted, rerun these checks before treating them as current facts.

## Open Issues and Risks

### Product

1. Confirm horizontal left/right versus the historically requested up/down chart swipe.
2. Apply setlist key/capo overrides when opening a chart.
3. Add editing of existing setlist entry overrides.
4. Add setlist rename/delete UI if desired.
5. Decide whether setlist mutations need offline queueing.
6. Add tag editing if users need more than filtering.
7. Add visible GetSongBPM attribution before distributing enriched content.

### Security and infrastructure

1. There is no authentication or authorization.
2. Tablet PWA use needs trusted HTTPS rather than a plain LAN-IP HTTP URL.
3. The future cloud topology—combined app image or split web/API images—is undecided.
4. Secrets must remain in ignored environment files or a future secret manager.

### Data and portability

1. Docker stores PostgreSQL data in named volume `pgdata`; moving only the folder does not move existing records.
2. A backup/restore or folder-local export workflow is needed for full portability.
3. There is no database migration framework.
4. Duplicate schema files can drift.
5. Real corpus parsing, enrichment, review, and load remain outstanding.
6. Raw charts and generated data may be copyrighted and should remain untracked without redistribution rights.

### Test coverage

Missing automated coverage includes:

- IndexedDB mutation queue and reconnect behavior.
- Conflict-resolution edge cases.
- API CRUD and setlist routes.
- Python parser, enricher, and loader behavior.
- Full offline browser workflows.
- Database migration compatibility.

## Operational Notes

- Run Compose commands from the project root so relative contexts and mounts resolve.
- Use `docker compose down` to stop services without deleting data.
- Do not use `docker compose down -v` unless deliberately deleting the database volume.
- Rebuild the app image after production source or dependency changes.
- Rebuild the pipeline image after Python source or dependency changes.
- Playwright expects the app at `http://localhost:3000` unless configured otherwise.
- Keep `.env`, source `.docx`, parsed output, and enrichment caches untracked.

## Maintenance Principles

- Re-verify behavior from the post-revert code before relying on this historical log.
- Treat implementation/documentation discrepancies as open decisions, not permission to guess.
- Ask the user before meaningful product or architecture decisions.
- Keep local operation working before beginning cloud infrastructure.
- Do not commit on the user's behalf unless explicitly requested.
