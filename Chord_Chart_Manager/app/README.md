# Chord Chart Manager application

The application is a React/Vite progressive web app backed by an Express API
and PostgreSQL. The production image builds the client into `dist/`; Express
serves that build and the `/api` routes from the same origin.

Charts use dual storage:

- `chart_source` is the editable text.
- `chart_content` is the structured representation used for rendering and
  transposition.

The browser and server use the same JavaScript parser source so an offline
edit can re-render immediately and produce the same structure the server will
save after synchronization.

Paired chord and lyric rows use the same monospace character grid. They stay
unwrapped and scroll together horizontally on narrow screens. Overlapping chord
labels appear in original order on one row separated by pipes. Each group starts
at its first chord's measured lyric position; noncolliding chords keep their own
measured anchors. Symbols within a group share that first display anchor while
their stored positions remain unchanged.

When the server synthesizes editable text from `chart_content`, it writes a
versioned `[[CCM-CHART:2]]` representation. Section, lyric, progression, repeat,
raw, and explicit spacer lines have markers. Each lyric stores escaped text followed
by a chord list with a zero-based UTF-16 code-unit column and percent-encoded symbol,
such as `[[CCM-CHORDS]][[C:8:G]][[C:26:C%23]]`. Changing lyric length therefore
does not move its chord anchors. Anchors beyond the lyric end remain valid and
reserve horizontal row width. Stored columns remain UTF-16 offsets, while the
renderer measures the corresponding rendered lyric grapheme boundary with a
DOM text range; it does not assume one UTF-16 unit equals one visible `ch`.
Range coordinates are converted into the chord row's local CSS coordinates using
its measured rendered-to-layout scale and containing-block origin, so CSS zoom,
row borders, and horizontal scrolling do not change stored columns.
Range coordinates are converted into the chord row's local CSS coordinates using
its measured rendered-to-layout scale and containing-block origin, so CSS zoom,
row borders, and horizontal scrolling do not change stored columns.
When an old or malformed offset falls inside a surrogate pair or combining
grapheme, the chord is shown at that grapheme's leading edge and marked with a
tooltip. The stored offset is not changed. Literal backslashes and marker-opening brackets
are escaped. The shared JavaScript parser and pipeline's Python parser support
version 2 and continue to read legacy version 1 generated text. Existing
human-authored chart text without the header keeps its syntax. Keep generated
line markers when editing; incomplete markup is rejected instead of being
reinterpreted as ordinary chart text. An unchanged chart source preserves the
current structured content on online and offline saves.

An explicit empty chart paragraph is stored as a spacer line and rendered in
normal layout flow, so it contributes to scrolling and element-based height
measurements. Ordinary human-authored chart text also preserves internal blank
line runs as one spacer; leading/trailing padding is trimmed, and chord/lyric
pairing does not cross a blank line. A blank paragraph before a new section uses the automatic section
gap; a spacer after a section heading remains explicit and suppresses that
automatic gap so the two spaces do not stack.

## Run with Docker Compose

Docker is the recommended local workflow. Run Compose from `app/`:

```powershell
docker compose up --build -d
docker compose ps
```

Compose supplies development defaults if no `.env` exists. If local overrides
are needed, preserve an existing `app/.env`; create it from `app/.env.example`
only when it is absent. From `app/`, PowerShell can use
`if (-not (Test-Path .env)) { Copy-Item .env.example .env }`; macOS/Linux can use
`test -f .env || cp .env.example .env`.

Open <http://localhost:3000>. The app connects to PostgreSQL over Compose at
`db:5432`; the host-published port may differ.

```bash
curl http://localhost:3000/healthz
curl http://localhost:3000/api/stats
```

The app runs pending SQL migrations before listening. Existing databases adopt
`0001_baseline.sql` only after the runner verifies the expected tables, columns,
types, constraints, indexes, and triggers. A partial or incompatible schema
fails without being silently adopted. Editing the baseline does not update an
existing database; add the next numbered file under `app/db/migrations/` for
future changes. Do not delete or recreate the volume to apply a migration.
`0002_monotonic_song_version.sql` gives song updates a strictly increasing
microsecond version, including when a writer waits for a row lock. The
setlist timestamp trigger remains on its baseline function. Baseline-adoption
validation runs only when no migration is recorded; databases already recording
later migrations are checked against their migration history instead.

Migration files are plain SQL applied inside a transaction owned by the runner.
The runner pins its connection to `public` (with PostgreSQL built-ins resolved
from `pg_catalog`) before inspecting or applying schema changes. Keep application
objects in `public`.
Do not put `BEGIN`, `START TRANSACTION`, `COMMIT`/`END`, `ROLLBACK`/`ABORT`,
`SAVEPOINT`, `RELEASE`, or `PREPARE TRANSACTION` in a migration. The validator
lexes SQL comments, strings, quoted identifiers, and dollar-quoted routine
bodies, so block delimiters and quoted keyword text are not mistaken for
transaction commands. It rejects direct transaction commands inside routine
bodies too. Migration files do not support dynamic SQL (`EXECUTE`) or procedure
calls (`CALL`); keep transaction boundaries with the runner.
Use regular files named
`NNNN_lowercase_name.sql`; every `.sql` file in the migration directory is
treated as a migration candidate.

Run migrations explicitly inside the running app container with
`docker compose exec app npm --prefix /app/server run migrate`.

### Database credential mismatches

Changing a password in `app/.env` does not change the password stored for a
role in an initialized PostgreSQL volume. Container environment changes require
recreation of the affected container; restarting alone does not apply them.
Never delete or recreate the database volume to resolve a mismatch.

## Run without the app container

PostgreSQL must be reachable. Start the server once so it can apply pending
migrations before listening.

Terminal 1:

```bash
cd server
npm install
DATABASE_URL=postgresql://chords:chords@localhost:5432/chords PORT=3001 npm start
```

Terminal 2:

```bash
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to the server on port 3001.

## Automated tests

Client tests are in `test/unit/`; Playwright tests are in `test/e2e/`. The client
and production server use separate lockfiles, and Docker installs them with
`npm ci`.

From `app/`, install locked dependencies and Chromium once, then run unit tests:

```powershell
npm.cmd ci
npx.cmd playwright install chromium
npm.cmd test
```

The Playwright suite targets the production app at <http://localhost:3000> by
default, so start Compose before running it:

```powershell
docker compose up --build -d
npm.cmd run test:e2e
```

`app/package.json` also provides `npm.cmd run test:all` to run both suites when
the target app is available. These commands use Windows `.cmd` shims; on macOS
or Linux use `npm` and `npx`.

Dependency checks:

```bash
npm audit
cd server && npm audit --omit=dev
```

## User-facing behavior

### Songs

- Search titles and artists and filter by genre, feel, or era tags.
- Create, edit, and delete songs online or offline.
- Edit Genres and Vibes online or offline; new values stay within those existing
  categories and sync with the song update. Era is read-only and derived from
  `release_year` by decade. Release years must be whole numbers from 1 to 32767.
- Edit `chart_source`; the parsed chart is available immediately after an
  offline save.
- Transpose charts and adjust capo. Saving stores `preferred_key` and
  `default_capo`; it does not rewrite the chart into a different key.
- Swipe left for the next song and right for the previous song in the current
  song or setlist order.

### Setlists

- Create a setlist with a name, gig date, and notes.
- Add songs with optional key, capo, and performance-note overrides.
- Move songs up or down, remove them, and open charts in setlist order.
- Opening a chart from a setlist uses that entry's key and capo overrides;
  missing fields fall back to the song's preferred key and default capo.
- In a setlist chart, **Save to setlist** stores the displayed key and capo on
  that entry without changing the song's defaults or chart source. Saving
  setlist overrides requires a connection.
- **Edit setlist** changes its required name, gig date, and notes. Blank date
  and notes clear those optional values; date-only values remain date-only.
- **Delete setlist** asks for confirmation, removes the setlist and its entries,
  and keeps the songs in the library.
- The edit control beside an entry updates its key override, capo override,
  and performance note in place. Blank key/capo fields clear those overrides
  so chart opening falls back to the song; capo `0` remains an explicit value.
- Cached setlists retain their overrides for offline chart reading. Setlist
  writes remain online-only until T10; song mutations use the existing offline
  queue. Setlist management controls are disabled offline with a connection-to-
  app-server message.

### Offline sync

The service worker caches the application shell and IndexedDB stores songs,
setlists, and sync metadata.

When the app first opens online, when a connection returns, or when **Sync
now** is selected in Settings, it:

1. Pushes queued tablet song creations, updates, and deletions.
2. Pulls a fresh library and setlist snapshot.
3. Replaces the local cache with that server snapshot.

Existing-song updates and deletions include the server `updated_at` value on
which the tablet change was based. The API checks that version while holding a
row lock. A stale mutation receives HTTP 409, the server copy is kept, and
Settings reports that the tablet change was replaced. Offline-created songs
use temporary `local-*` IDs until the server assigns permanent IDs.
Song version tokens use UTC with six fractional digits and are compared as
exact strings. Older queued three-digit tokens are ambiguous and receive 409;
the queued request is sent first, then server-wins recovery refreshes the
authoritative song and the subsequent pull refreshes the library. No pending
edit is cleared before that response. Direct assignment routes also require
`base_updated_at`: `POST /api/songs/:id/tags` accepts `{name, category,
base_updated_at}`; `DELETE /api/songs/:id/tags/:name` accepts `{category,
base_updated_at}` in its JSON body. `category` is `Genre` or `Feel` (`Vibe`
is an alias). Missing or malformed tokens return 400; stale or ambiguous
tokens return 409. The song filter sends `tag_filters` as a JSON list of
`{category,name}` values; every selected Genre, Feel, or Era must match.

Service workers require HTTPS, except on `localhost`. A separate tablet cannot
use the secure-context exception by visiting `http://<computer-ip>:3000`; the
eventual deployment must provide trusted HTTPS.

## Source layout

```text
app/
├── src/
│   ├── App.jsx                    navigation and view routing
│   ├── hooks.js                   data loading, gestures, and sync state
│   ├── views/
│   │   ├── HomeView.jsx           song search/filter/create
│   │   ├── ChartView.jsx          chart, transpose, capo, swipe
│   │   ├── EditView.jsx           song create/edit/delete
│   │   ├── SetlistListView.jsx    setlist list/create
│   │   ├── SetlistView.jsx        setlist songs and ordering
│   │   └── SettingsView.jsx       health and sync status
│   └── lib/
│       ├── api.js                 HTTP client
│       ├── cache.js               IndexedDB and mutation queue
│       ├── chartParser.js         browser adapter for shared parser
│       └── chartRenderer.jsx      structured chart renderer
├── server/
│   ├── server.js                  Express routes and static host
│   ├── db.js                      read/export queries
│   ├── db_ext.js                  writes, conflicts, tags, setlists
│   └── chartParser.js             shared text-to-structure parser
├── db/migrations/                 canonical baseline and numbered migrations
├── Dockerfile
└── docker-compose.yml
```

## Current validation status

T02 on 2026-10-09 passed the production image/Vite build, unit tests (2 files,
7 tests), and Playwright E2E (1 test). The browser assertions checked Songs
screen controls. After the computer crash, the existing volume remained
attached; TCP authentication passed, both containers were healthy, and
`/healthz` and `/api/stats` returned HTTP 200. Unit/E2E were completed before
the crash; TCP, health, and endpoints were repeated after restart.

T02 did not validate full offline workflows, real corpus import, or live
enrichment. Earlier feature checks in project history are historical and were
not part of T02.

## Deployment

Cloud, Kubernetes, Terraform, and the split nginx/API topology are out of
scope. See `../docs/AGENTS.md` → Recorded decisions. No infrastructure
implementation is part of this project phase.
