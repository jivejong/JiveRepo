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

### Chart auto-scroll

Charts open with auto-scroll paused. Use **Start** and **Pause** to move the
vertical chart view at a steady speed; **Slower** and **Faster** adjust the
visible rate from 5 to 40 CSS pixels per second in 5-pixel steps. Each chart
opens at 10 CSS pixels per second. Auto-scroll works offline and keeps running
when you manually scroll vertically; it continues from the new position.
Pause it with **Pause**, or let it stop at the bottom. A hidden page pauses it
until you press **Start** again. Horizontal chord-row panning and
left/right song navigation remain available. A frame delay over 1,000 ms while
scrolling is treated as suspended time: the current position is held and the
normal selected speed resumes on the next frame without a catch-up jump.

### Appearance themes

Choose **Light**, **Dark**, or **System** under **Settings → Appearance**. The
default is System, which follows the device color-scheme setting while the app
is open. A Light or Dark choice overrides the device. The preference is stored
in this browser on this device and works offline; it is not synced with the
server. If browser storage is unavailable or unreadable, the app falls back to
System for the current session. Theme changes affect colors only, so chart
anchors, pipe groups, spacers, and auto-scroll positioning are preserved.

### Chart text size

Choose **-2 Smallest**, **-1 Smaller**, **Default**, **+1 Larger**, or
**+2 Largest** in Settings. Each step changes chart text by 7.5% (0.85× to
1.15× across the full range) and is saved on this device for offline use.
Smaller text can show more chart content at once, but does not guarantee that
an entire song fits without scrolling. At tablet widths, chart content uses
the available width with 12px side padding; phone and desktop shell widths
remain unchanged. Chords and lyrics scale together, with their measured
positions recalculated by the existing chart layout observers.

### Chart metadata while scrolling

The Key/BPM/BeatBuddy row stays pinned directly below the chart title while the
chart scrolls. It remains in normal layout flow, so the first chart rows begin
below it; BeatBuddy uses the Key text size and long values stay within the row.

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
line runs as one spacer; leading/trailing empty lines are dropped, while
trailing spaces on nonblank lyric/raw lines are preserved. Chord/lyric pairing
does not cross a blank line. A blank paragraph before a new section uses the automatic section
gap; a spacer after a section heading remains explicit and suppresses that
automatic gap so the two spaces do not stack.

Pipe-separated chord rows that mix chord tokens with separators, annotations,
or repeat text are kept as literal raw-line text so their order and spacing
remain visible. The renderer transposes only clearly recognized chord tokens
outside parenthesized/bracketed annotations; ambiguous tokens, prose, tablature,
and ordinary progression lines remain unchanged. When an older human-source
record still has the old progression structure, it continues to render that
stored structure until the user intentionally changes its chart text in Edit
and saves. A chart-source field unchanged from its loaded baseline is omitted
from the save, and the server preserves `chart_content` when submitted source
matches its stored value. To refresh a previously affected chart, make an
intentional text edit and save; the corrected parser then derives its new
representation. Opening a chart does not rewrite its record.

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
Do not put transaction commands (`BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT` and
the like) in a migration; keep transaction boundaries with the runner.
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

With the repository's Compose database, host tools connect through loopback at
`localhost:15432` by default (`DB_PORT` in `.env`); containers continue to use
the internal `db:5432` address.

Terminal 1:

```bash
cd server
npm install
DATABASE_URL=postgresql://chords:chords@localhost:15432/chords PORT=3001 npm start
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

From `app/`, install locked dependencies, then run unit tests:

```powershell
npm.cmd ci
npm.cmd --prefix server ci
npm.cmd test
```

The full T11 command builds the client, runs unit and scratch-database API tests,
then runs the serial Playwright suite against a task-owned `ccm_test` database.
It requires the local PostgreSQL settings from `app/.env`, refuses to run if
`ccm_test` already exists, initializes it with the migration runner, and drops
only the database it created after verifying cleanup and the protected main
database fingerprints. Playwright uses installed system Chrome (`channel: chrome`)
for this command; install Chrome before running it.

```powershell
npm.cmd run test:t11
```

Run `npm.cmd run test:t11` twice consecutively to check repeatability. The
standalone `npm.cmd test` remains available for unit tests. Raw Playwright and
T11 API integration commands refuse to run outside the isolated harness, so
browser fixtures cannot write to the live `chords` database. These commands
use Windows `.cmd` shims; on macOS or Linux use `npm`.

Dependency checks:

```bash
npm audit
cd server && npm audit --omit=dev
```

## User-facing behavior

### Songs

- Search titles and artists and filter by genre, feel, or era tags.
- Sort the opening Songs list by title (default), date added, artist, primary
  genre, era, or alphabetically first vibe. The adjacent direction control
  reverses the selected value while missing values stay last. Sort choice is
  stored locally and works offline; chart navigation follows the visible order.
- A song first created offline receives its actual local creation timestamp for
  Date added sorting. Existing records without a timestamp remain undated; after
  sync, a new song uses the server's creation timestamp.
- Create, edit, and delete songs online or offline.
- Edit assigned Genres and Vibes online or offline using separate
  category-specific autocomplete fields backed by managed definitions. Only
  selecting an existing suggestion changes the draft; query text alone never
  adds an assignment. With no definition list available, assigned chips remain
  removable while additions are disabled. Era is read-only and derived from
  `release_year` by decade. Release years must be whole numbers from 1 to 32767.
- Chart bodies use the existing textarea and ordinary chart-text parser for
  editing and rendering. Existing CCM v1/v2 chart sources remain supported and
  are not converted as part of ingestion or UI changes. Newly ingested DOCX
  charts store a readable plain-text body; title and other metadata continue to
  use the existing metadata fields and parser.
- Chart edits use the existing versioned online save and offline sync flow. An
  unchanged chart or metadata-only save preserves its existing source and
  structured chart data.
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
  that entry without changing the song's defaults or chart source. Setlist
  changes are queued offline and synchronize with the next successful sync.
- **Edit setlist** changes its required name, gig date, and notes. Blank date
  and notes clear those optional values; date-only values remain date-only.
- **Delete setlist** asks for confirmation, removes the setlist and its entries,
  and keeps the songs in the library.
- The edit control beside an entry updates its key override, capo override,
  and performance note in place. Blank key/capo fields clear those overrides
  so chart opening falls back to the song; capo `0` remains an explicit value.
- Cached setlists retain their overrides for offline chart reading and editing.
  Setlist and song changes use the existing mutation queue; list/detail/chart
  views update immediately, and a stale setlist is resolved server-wins during
  synchronization.

### Offline sync

The service worker caches the application shell and IndexedDB stores songs,
setlists, and sync metadata.

The Songs screen shows matching cached rows as soon as an online list request
starts, then replaces the clean server projection when it arrives. Pending
local create/edit/delete projections remain applied during that reconciliation.
When the browser is offline, the list is read directly from IndexedDB. The
service worker provides the app shell and a bonus NetworkFirst API cache with
a four-second network timeout. The Songs screen does not wait for that
response before showing nonempty IndexedDB results.

When the app first opens online, when a connection returns, or when **Sync
now** is selected in Settings, it:

1. Pushes queued tablet song creations, updates, and deletions.
2. Pulls a fresh library and setlist snapshot.
3. Replaces the local cache with that server snapshot.

Existing-song updates and deletions include the server `updated_at` value on
which the tablet change was based. Online editor and chart updates use the
same queue and acknowledgement path as offline changes. Each update dispatch
freezes its payload and local revision; if a newer edit arrives in flight, the
response advances its base token and leaves that newer edit queued. The API
checks the version while holding a row lock. Each queued update also carries a
stable operation ID and receipt: if the server commits but the response is
lost, retry returns that operation's original result without applying the
update twice. A stale first attempt receives HTTP 409, the server copy is kept, and
Settings reports that the tablet change was replaced. Offline-created songs
use temporary `local-*` IDs until the server assigns permanent IDs. A dispatched
create keeps its exact payload under its operation ID so a lost response can be
replayed safely. Edits made while that request is in flight remain queued as a
follow-up update, and acknowledging the create rewrites dependent cached
setlist entries to the server song ID. A pull keeps local projections that
still have queued work. Deleting a local song with an uncertain dispatched
setlist-entry request is blocked until sync resolves it.
When two offline editors save the same existing song, a later save merges only
fields changed from that editor's loaded baseline onto the latest cached row.
Non-overlapping edits are retained in one queued update. If both editors changed
the same field, the stale save is rejected with a reload-and-retry message and
does not alter the cached song or pending mutation.
Genre and Vibe arrays are separate conflict fields. A metadata-only save omits
untouched categories from a new update payload, while an earlier queued change
to either category remains in that payload. A definitive unknown/ambiguous
definition response (422) keeps the queued operation for correction; a true
stale song version (409) keeps the server version. A rejected offline-created
song can be corrected in EditView and retried under a new operation ID while
retaining its local ID, chart, and unrelated metadata. An uncertain network
attempt keeps its frozen payload and is never unfrozen by this correction path.
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
`{category,name}` values. Within Genre, Vibe (the API's legacy `Feel`
category), or Era, selected values are alternatives (OR); selected categories
combine with AND. Genre matches primary and secondary assignments. Vibe
matches any assigned vibe. Era is derived from `release_year` by decade;
missing years do not match. The opening-screen All control clears only its
category, while Clear all removes every active category filter. These rules
are the same for server search and the offline IndexedDB cache.

**Settings → Manage Genres / Manage Vibes** opens one category at a time and
shows shared definitions with current song-usage counts. The last successful
definition list is cached locally for offline viewing. Creation, rename,
reassignment, and deletion still require a working connection because these
operations affect shared data and do not have an offline mutation-queue
contract. While offline, or when a definition request fails, those controls
remain disabled with a connection message; Back to Settings remains available.
Individual song assignments can still be edited offline through the existing
song queue. Deleting an in-use definition requires choosing either
to remove those assignments or to reassign them to another definition of the
same category, then confirming the affected-song count. Era is derived from
release year and is not managed as a definition. A shared change advances the
versions of affected songs, so stale queued edits follow the normal 409/server-
wins behavior. Settings reports when the server change succeeded but the cache
refresh still needs Sync now.

Song assignment writes only resolve an existing definition in the selected
category. Direct song create/update and direct tag-add return `400
INVALID_TAG_INPUT` for malformed input, `422 UNKNOWN_TAG_DEFINITION` for a
missing definition, or `422 AMBIGUOUS_TAG_DEFINITION` if normalized lookup
matches multiple rows. Updates with an actually stale version return `409
SONG_VERSION_CONFLICT`; a 422 is validation recovery and does not invoke the
server-wins conflict policy. Definition rows are shared-locked in the song
write transaction until assignment joins commit, so a concurrent management
deletion cannot slip between validation and assignment.

Genre assignments are read in primary-first order, followed by definition-name
order. Removing the primary in EditView promotes the first remaining draft
assignment in that order; removing all Genres clears the primary. Adding a
secondary preserves the selected primary, and re-adding a former primary after
promotion leaves it secondary. Full-song reads and offline export use the same
`is_primary DESC, name` order, so sync and reopen retain the same promotion.

Stage A's prior full guarded run on 2026-10-14 passed the production build,
157 unit tests, 3 API integration tests, and 51 serial system-Chrome E2E tests.
A later focused guarded run for the combobox and offline-export regressions
also passed the build, 157 unit tests, 3 API integration tests, and the 2
selected serial E2E tests. The harness removed its owned scratch database and
verified its absence after both runs. The focused run matched the exact rows,
versions, sources, and documented associations for the eight protected
Sample.docx songs; this evidence does not fingerprint every main-database
record. Main health stayed healthy and aggregate counts remained 11 songs, 5
tags, and 0 setlists. The vocabulary rehearsal ran only in scratch; the utility
restricts application to owner-marked scratch storage. Those guarded test runs
left live definitions and songs unchanged. The Sol Medium review is clear;
the user accepted the new tag system ("The new tag system works") and
confirmed the populated choices. T24 is Done under explicit closeout authorization.
Physical tablet acceptance is tracked separately.

The separately authorized initial vocabulary population added 43 Genres and
40 Vibes on 2026-10-14, reusing Classic Rock and Pop unchanged. Current totals
are 45 Genres and 40 Vibes; the repeated live read-only preview has zero missing,
ambiguous, or spelling-collision entries. A fresh full backup was verified by
an owned scratch restore. The focused population integration rehearsal passed,
including lossless large-number preservation and rollback checks, followed by
clear Sol Medium re-review. Exact existing-definition and protected-row checks
passed before commit and afterward. Browser storage was not opened or synced.
The user accepted the population result: "They are in there. We're good."
Stage A/T24 is complete.
The Artist field in song create/edit offers a short case-insensitive prefix
list drawn from songs in the local cache, including pending and locally created
songs. Suggestions also work offline. Typing remains ordinary free text; the
stored value changes to an existing spelling only when a suggestion is chosen.
Use the arrow keys and Enter to select from the list, Escape to close it, or tap
a suggestion on a touch screen. New artist names remain valid.
Setlist conflict notices are stored in IndexedDB and remain visible after a
later sync failure or reload. When Web Locks are unavailable, another tab
waits to the active IndexedDB lease expiry and rechecks ownership and its
fencing token. If the owner renewed the lease, it schedules another bounded
wait; leaving the page cancels the current wait.

Service workers require HTTPS, except on `localhost`. A separate tablet cannot
use the secure-context exception by visiting `http://<computer-ip>:3000`.
For the tailnet-only setup, follow
[`../docs/ANDROID_SETUP.md`](../docs/ANDROID_SETUP.md); Compose publishes both
the app and PostgreSQL on loopback, and Tailscale Serve proxies only the app.

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

The later normal user-run T11 passed the current build, 362 unit, 3 API,
21 enrichment and 51 browser tests. Its owned scratch database was removed
and absence verified; eight Sample fingerprints plus main health/counts
were preserved. This harness result is separate from the accepted live
applications' fresh backup, actual restore proof and postchecks.

## Deployment

Cloud, Kubernetes, Terraform, and the split nginx/API topology are out of
scope. See `../docs/AGENTS.md` → Recorded decisions. No infrastructure
implementation is part of this project phase.
