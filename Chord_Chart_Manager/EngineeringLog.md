# Chord Chart Manager Engineering Log

Last updated: 2026-10-07

## Purpose

This document records the architecture, implementation decisions, problems encountered, fixes applied, verification performed, and known limitations of Chord Chart Manager. It is intended to explain how the application reached its current state and why its major design choices exist.

For installation and day-to-day usage, see `README.md`. For a concise continuation brief for a new development session, see `HANDOFF.md`.

## Product Summary

Chord Chart Manager is a tablet-friendly progressive web application for storing, editing, transposing, and performing chord charts. It also manages ordered setlists and includes a Python ingestion pipeline for converting `.docx` chord charts into database records.

The locally verified deployment model is a small Docker Compose stack:

```text
Browser / installed PWA
        |
        | HTTP, same origin
        v
Express server (port 3000)
  - REST API under /api
  - production React/Vite assets
        |
        v
PostgreSQL 16

Optional one-shot pipeline container
  .docx -> parse -> enrich -> songs.json -> PostgreSQL
```

The application is local-first in the sense that its code, configuration templates, and pipeline inputs/outputs travel with the project folder. PostgreSQL data is the main exception: Docker stores it in the named `pgdata` volume, outside the project directory.

## Architecture

### Components

| Component | Technology | Responsibility |
| --- | --- | --- |
| Web client | React 18, Vite 7 | Song library, chart display/editing, setlists, settings, offline UI |
| PWA layer | `vite-plugin-pwa`, service worker | App installation and static asset caching |
| Offline data | Browser IndexedDB | Cached songs/setlists, pending song mutations, sync metadata and conflicts |
| API server | Express on Node 22 | CRUD, search, export, setlists, optimistic conflict detection, static client hosting |
| Database | PostgreSQL 16 | Authoritative song, metadata, and setlist data |
| Ingestion pipeline | Python 3.10 | Parse `.docx`, optionally enrich metadata, and load songs idempotently |
| Local orchestration | Docker Compose | Starts the database/app and runs the optional pipeline profile |
| Automated tests | Vitest, Playwright | Unit tests and browser smoke testing |

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
│   └── Dockerfile           Production app image
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

### Runtime Deployment

The production-style local app image is a multi-stage Node 22 Alpine build. It installs locked dependencies with `npm ci`, builds the Vite client, installs production server dependencies, and runs as the non-root Node user. Express serves both `/api` and the compiled client, which keeps browser requests same-origin.

Docker Compose defines:

- `db`: PostgreSQL 16, host port `5432`, persistent named volume `pgdata`.
- `app`: Express and the compiled client on host port `3000`.
- `pipeline`: an opt-in Compose profile for one-shot Python commands. It bind-mounts `pipeline/docs` and `pipeline/parsed_out` so source documents and generated output stay with the project folder.

The infrastructure documents describe a possible future split between a static nginx web image and an API image. That topology has not been selected or implemented. The verified local image remains the combined Express API/static server.

## Data Model

The schema contains eight primary tables:

- `artists`, `genres`, and `vibes` normalize reusable metadata.
- `songs` stores song metadata, source chart text, structured chart JSON, key/capo defaults, and timestamps.
- `song_genres` and `song_vibes` implement many-to-many relationships.
- `setlists` stores setlist-level metadata such as name, gig date, and notes.
- `setlist_songs` stores ordering plus optional key, capo, and note overrides for each performance entry.

An `updated_at` trigger maintains modification timestamps. Those timestamps are also used for offline conflict detection.

Two schema copies currently exist:

- `app/db/init/01-schema.sql`, used when the PostgreSQL volume is first initialized.
- `pipeline/schema.sql`, available to pipeline workflows.

They were byte-for-byte equivalent when checked on 2026-10-07. Keeping duplicate schema files is nevertheless a maintenance hazard, so future schema changes must update and verify both copies or replace them with a single canonical migration source.

PostgreSQL initialization scripts run only when the data volume is empty. Editing an init script does not upgrade an existing database. A migration mechanism is still needed before schema evolution becomes routine.

## Chart Representation and Rendering

Songs use a deliberate dual representation:

- `chart_source` is editable, human-readable chord-chart text.
- `chart_content` is structured JSON used for reliable rendering and transposition.

This supports a convenient text editor without forcing the performance view to reinterpret arbitrary text on every render.

The JavaScript chart parser lives in `app/server/chartParser.js`. The server imports it directly. The browser receives the same source through a small Vite virtual-module transformation that removes the CommonJS export branch. Sharing the exact parser source prevents online and offline edits from producing different chart structures.

The Python pipeline has a separate whole-document parser for `.docx` input. Because the Python and JavaScript parsers serve different entry formats and are separately implemented, their behavior can drift. Parser fixtures should be expanded if strict equivalence becomes important.

When an older record has structured chart content but no source text, the API can reconstruct source-like text for editing. That reconstruction is usable but may not preserve the original spacing exactly.

## Application Behavior

### Navigation

The client uses a small state machine in `App.jsx` rather than a URL router. Primary tabs select Songs, Setlists, or Settings, while internal view state opens song charts, song editing, or setlist detail.

The current chart swipe implementation is horizontal:

- Swipe left moves to the next song.
- Swipe right moves to the previous song.
- The movement threshold is 60 pixels.
- Horizontal movement must exceed vertical movement by a factor of 1.5.

An earlier requirement and the previous handoff described up/down navigation. This is an unresolved product discrepancy, not a documentation-only issue. The desired gesture must be confirmed with the user before changing the implementation.

### Songs and Charts

The client supports:

- Search by title and artist.
- Tag filtering.
- Creating, editing, and deleting songs.
- Structured chart display.
- Client-side transposition.
- Capo adjustment.
- Persisting the preferred key and default capo.

The API supports adding and removing tags, but the current client only uses tags for filtering. A tag-editing UI has not been implemented.

### Setlists

The setlist UI supports:

- Creating a setlist with name, optional gig date, and notes.
- Viewing an ordered song list.
- Adding songs.
- Setting key, capo, and note overrides when adding a song.
- Reordering entries with explicit up/down controls.
- Removing entries.
- Reading cached setlists while offline.

Known gaps:

- Opening a chart from a setlist does not currently apply that entry's key/capo override.
- Existing entry overrides cannot yet be edited in the UI.
- The UI does not currently rename or delete setlists, although the API has broader update/delete support.
- Setlist mutations are not queued offline.

### Offline Editing and Synchronization

The original design treated the desktop/database copy as the only editable source. Tablet editing was later made a product requirement. The implemented model now supports offline song creation, editing, deletion, and preferred-key/capo changes.

IndexedDB stores cached songs, cached setlists, sync metadata, pending song mutations, and conflict records. Offline-created songs receive a temporary `local-<uuid>` identifier. On successful upload, that identifier is mapped to the server-generated ID.

Synchronization intentionally runs in this order:

1. Push pending song mutations.
2. Fetch the authoritative song export and setlists from the server.
3. Replace the local cache with the newly fetched state.

Pushing first prevents a normal pull from erasing unsent local work. The server remains authoritative after conflict resolution.

The conflict policy is explicit: if the desktop/server and tablet both changed the same song from the same base version, the desktop/server copy wins. Offline updates and deletes carry `base_updated_at`. The API locks the target row, compares timestamps, and returns HTTP 409 for a stale mutation. The client discards that stale mutation, keeps the server record, and exposes a conflict notice in Settings.

Deleting a song that was created offline and has never synchronized simply removes the local record and pending create operation; no server request is necessary.

Synchronization is attempted when connectivity returns and can also be triggered manually from Settings.

Service-worker features require a secure context. `localhost` is treated as secure by browsers, but a tablet using a plain `http://<LAN-IP>:3000` URL should not be expected to install or run the PWA offline reliably. Trusted HTTPS is required for dependable tablet deployment.

## Ingestion Pipeline

The Python pipeline has four stages:

1. `chart_parser.py` parses one Word document into structured song data.
2. `batch_parse.py` processes `pipeline/docs/*.docx` and writes `songs.json`, `report.json`, and optional per-document output under `pipeline/parsed_out`.
3. `enrich_songs.py` optionally augments the parsed records with MusicBrainz and GetSongBPM data. Parsed values take precedence over enrichment results. Responses are cached in `parsed_out/enrich_cache.json`.
4. `load_songs.py` loads the final JSON into PostgreSQL.

MusicBrainz is intended to supply release year and genre data, with vibe information as a best-effort derivation. GetSongBPM is intended to supply BPM and key information. MusicBrainz requires a contact identifier in the User-Agent, and GetSongBPM requires an API key. The project README links back to both services. GetSongBPM attribution must also be visible in the product before enriched data is distributed through the app.

Loading is idempotent on the natural key `(lower(title), artist_id, source_document)`. Per-song savepoints isolate malformed records while preserving the rest of a real load. Secondary genres are retained rather than collapsing to a single genre.

Dry-run mode originally interacted incorrectly with savepoints and could allow partial commits. It was changed so the entire operation, including optional truncation, runs inside one outer transaction and is rolled back at the end.

The container uses Python 3.10 and mounts input/output directories from the project folder. This removes a host-Python requirement and keeps the pipeline portable with the source tree.

As of 2026-10-07, this working copy contains no source `.docx` files, generated parse output, MusicBrainz contact setting, or GetSongBPM API key. The pipeline code and container have been exercised, but a real corpus enrichment/load has not yet been completed.

## Key Engineering Decisions

| Decision | Reason | Tradeoff / consequence |
| --- | --- | --- |
| Serve the local client and API from one Express process | Simple same-origin local deployment | Future split nginx/API topology remains a separate infrastructure decision |
| Store both source text and structured chart JSON | Editing convenience plus deterministic rendering/transposition | Both representations must remain synchronized |
| Share the JavaScript parser source between server and browser | Immediate offline rendering with identical behavior | Requires a Vite virtual-module transformation for the CommonJS source |
| Perform transposition in the client | Instant performance controls without an API round trip | Client needs correct key/chord handling and tests |
| Cache application data in IndexedDB | Tablet can browse and edit without connectivity | Synchronization and conflict handling become application responsibilities |
| Push pending work before pulling | Prevent normal synchronization from erasing unsent changes | Failed mutations must be handled individually |
| Let the server/desktop win concurrent edits | Simple, predictable conflict policy requested by the user | Tablet edits can be discarded and must be surfaced as conflicts |
| Queue only song mutations offline | Meets immediate editing requirement with limited complexity | Offline setlist changes are unsupported |
| Use PostgreSQL with normalized metadata and JSONB charts | Relational integrity with flexible chart content | Database lifecycle and migrations must be managed |
| Run ingestion as an optional Python 3.10 container | Reproducible, folder-portable pipeline | Image rebuild is required after pipeline dependency/code changes |
| Commit Node lockfiles and use `npm ci` in images | Deterministic dependency installation | Lockfiles must be updated intentionally |
| Keep Playwright development-only | Browser automation without production bloat | Browser binaries are a separate developer install |
| Defer authentication for the local prototype | Reduces initial local-development scope | The app must remain on a trusted network until authentication exists |

## Development History and Resolved Issues

### Initial Local Application

The project began with the React client, Express API, PostgreSQL schema, Word-document parser, and Docker setup. Early work focused on making the complete local stack build and run before doing cloud infrastructure work.

### `load_songs.py` Transaction Safety

The loader's dry-run path used nested transaction/savepoint behavior that did not guarantee a final rollback of the whole operation. It was restructured around one outer transaction. Each song still uses a savepoint so a bad record can be reported without corrupting the batch, while dry-run always rolls back the complete batch.

### Setlist Experience

The setlist UI was expanded from a partial implementation to dedicated list and detail views. It gained gig dates, notes, song selection, performance overrides, deterministic ordering controls, and removal. Explicit up/down buttons were selected for ordering so touch operation is predictable.

### Offline Song Editing

The offline design changed materially when tablet editing became a requirement. IndexedDB moved from passive caching to a mutation queue with temporary IDs, push/pull synchronization, version checks, and visible conflict reporting. The user selected immediate tablet re-rendering and a desktop/server-wins conflict policy.

### Shared Parser and Blank-Page Regression

The first attempt to reuse the server's UMD/CommonJS parser directly in the browser built successfully but produced a blank page: the CommonJS branch executed without establishing the expected browser global. A Vite virtual module now loads the exact server source and removes that branch for the browser build. This retained one implementation while fixing browser initialization.

### Export Completeness

Offline sync needed more than the original export returned. The export response was extended to include source text, preferred key, capo, and timestamps. Records lacking source text receive a reconstructed editable representation.

### Dependency and Test Hygiene

An earlier copy had no lockfiles or test files and installed nondeterministic versions. `jsdom` and Playwright were also incorrectly listed as production server dependencies despite not being used at runtime.

The project now has:

- Separate client and server lockfiles.
- Docker builds using `npm ci`.
- Vitest unit tests for parsing and transposition.
- Playwright as a development-only dependency and an application-shell smoke test.
- No production dependency on `jsdom` or Playwright.
- Updated Vite/PWA/test tooling that previously audited with zero known npm advisories.

### Portability and Documentation

The project was moved from `projects/Chord_Chart_Manager` to the repository root on 2026-09-22. Relative paths, Compose build contexts, bind mounts, and documentation were adjusted so the folder can be moved as a unit. The main README was substantially rewritten on 2026-09-29 for setup and usage clarity.

## Verification Record

### Current snapshot: 2026-10-07

| Check | Result | Notes |
| --- | --- | --- |
| `npm test` in `app/` | Passed | 2 test files, 7 tests |
| `npm run test:e2e` in `app/` | Not exercised against a live app | Playwright reached `http://localhost:3000` and received `ERR_CONNECTION_REFUSED` because the Compose stack was stopped |
| `docker compose ps` | No running services | This is an operational state, not a diagnosed build failure |
| App `.env` | Not present | Copy `.env.example` before starting |
| Pipeline source documents | None | `pipeline/docs` contains no `.docx` files |
| Pipeline generated output | None | `pipeline/parsed_out` is empty |
| Enrichment credentials | Not configured | Required only for live enrichment |
| Schema copies | Matched | `app/db/init/01-schema.sql` and `pipeline/schema.sql` were identical |

### Previously verified during implementation

- Docker app image build.
- PostgreSQL startup and health checks.
- App `/healthz` and API access.
- Production Vite/PWA build.
- Clean-browser application render after the parser bundling fix.
- Song offline create/update/delete synchronization and server-wins conflict handling through a browser smoke script.
- Pipeline image running Python 3.10, importing its modules, seeing bind mounts, and connecting to PostgreSQL.
- npm audits with zero known advisories after dependency upgrades.
- Playwright application-shell smoke test while the stack was running.

These historical checks should not be treated as a substitute for rerunning the validation commands after meaningful changes.

## Open Issues and Risks

### Product and UI

1. Confirm whether chart navigation should use the currently implemented left/right gesture or the historically requested up/down gesture.
2. Apply setlist key and capo overrides when opening a chart from a setlist.
3. Add editing for existing setlist entry overrides.
4. Add rename/delete controls for setlists if desired.
5. Decide whether setlist mutations also need offline queueing.
6. Add song tag management to the UI if users need more than filtering.
7. Add visible GetSongBPM attribution before distributing enriched content in the app.

### Security and Deployment

1. There is no authentication or authorization. Anyone who can reach the app can modify its data.
2. Plain HTTP on a LAN address is insufficient for dependable PWA installation/offline use. A tablet deployment needs trusted HTTPS.
3. The future cloud topology is undecided: retain the combined local image or split static web and API images as proposed in the infrastructure notes.
4. Secrets must remain in ignored environment files or a future secret manager, never committed.

### Data and Portability

1. The database is in the Docker named volume `pgdata`, so moving only the project folder does not move existing records.
2. A documented backup/restore or folder-local database export workflow is still needed for complete portability.
3. There is no migration framework; init SQL only applies to a new volume.
4. Duplicate schema files can drift.
5. Real `.docx` corpus parsing, enrichment, review, and load remain to be performed.
6. Source chord charts may be copyrighted; keep raw documents and generated data out of version control unless redistribution is authorized.

### Test Coverage

Existing tests are useful but thin. Missing automated coverage includes:

- IndexedDB mutation queue and reconnect behavior.
- Conflict-resolution edge cases.
- API CRUD and setlist routes.
- Python parser/enricher/loader behavior.
- Full offline browser workflows under Playwright.
- Database migrations and schema compatibility.

## Operational Notes

- Run Compose commands from the project root so relative build contexts and bind mounts resolve correctly.
- Use `docker compose down` to stop the stack without deleting the database.
- Do not use `docker compose down -v` unless intentionally discarding the PostgreSQL volume.
- Rebuild the app image after client/server dependency or production-source changes.
- Rebuild the pipeline image after pipeline source or Python dependency changes.
- The Playwright smoke test expects the app at `http://localhost:3000` unless its configuration is changed.
- Keep `.env`, source `.docx` files, parsed output, and enrichment caches untracked.

## Maintenance Principles

- Verify current behavior from code and tests before copying claims into documentation.
- Treat implementation/documentation discrepancies as open decisions rather than silently choosing one.
- Ask the user before making meaningful architecture or product decisions.
- Keep the local stack working before beginning cloud infrastructure work.
- Do not commit on the user's behalf unless the user explicitly asks; the user normally manages commits.

