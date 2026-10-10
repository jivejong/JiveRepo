# Chord Chart Manager

Chord Chart Manager is a tablet-friendly chord-chart library for rehearsal and
live performance. It keeps songs searchable, renders chords above lyrics,
transposes charts without changing the lyrics, and organizes songs into an
ordered setlist. After an online sync, the progressive web app (PWA) can open
cached charts without a network connection.

The project includes the complete local stack: a React/Vite PWA, an Express
API, PostgreSQL, and a Python pipeline for importing `.docx` chart collections.

## What the application does

- Search the library by song title or artist.
- Filter songs by categorized tags such as genre, era, feel, tempo, and vibe.
- Create, edit, and delete songs from the browser.
- Render sectioned chord charts with chords aligned above lyrics.
- Transpose a chart by half-step or whole-step and change its capo setting.
- Save a preferred performance key and default capo without rewriting the
  chart's source text.
- Build dated setlists, add performance notes, and arrange the running order.
- Move directly to the next or previous song while performing.
- Cache songs and setlists for offline use, including queued offline song
  changes that sync when the connection returns.

## Quick start

### Requirements

- Docker Desktop, or Docker Engine with the Compose plugin
- A current web browser

### Start the application

From `Chord_Chart_Manager/app`:

```bash
docker compose up --build -d
docker compose ps
```

Open <http://localhost:3000>. The first build can take a few minutes. Both the
app and database should report as healthy before you begin using it.
The app applies pending database migrations before it starts listening. An
existing database is adopted only when it matches the complete baseline; a
partial or incompatible schema prevents startup with a diagnostic.

An `.env` file is optional for local use because Compose supplies development
defaults. Run the following from `app/` only when you need local overrides and
`app/.env` does not already exist. Preserve an existing `.env`; do not overwrite
it.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

On macOS or Linux, run `test -f .env || cp .env.example .env` from `app/`.

### Add some songs

A new database is empty. Populate it in either of these ways:

1. Open **Songs**, select **New**, and enter a song manually.
2. Import a collection of Word documents with the [data pipeline](#import-a-docx-library).

PostgreSQL stores its data in the Docker volume named `pgdata`, so songs remain
available across container restarts and ordinary `docker compose down` calls.

## Using the application

The bottom navigation has three areas:

- **Songs** searches and browses the chart library.
- **Setlists** prepares and opens performance running orders.
- **Settings** shows connectivity, sync status, queued changes, conflicts, and
  library totals.

The bottom navigation is hidden while reading or editing a chart to maximize
the available screen space. Use the back arrow to return.

### Find a song

On the **Songs** screen:

1. Type part of a title or artist into the search box.
2. Select **Filter** to show the available tag categories.
3. Select one or more tags to narrow the results; all selected tags must match.
   Genre and Feel values with the same name remain separate filters, and a
   secondary genre matches its Genre filter online and offline.
4. Select a song row to open its chart.

The badge at the right of each row shows the chart's written key and, when set,
its default capo fret. Edit a song to add or remove Genre and Vibe tags, including
while offline. Era is read-only and derived from the release year by decade;
enter a year to add an Era when one is not already available.

### Read and transpose a chart

The chart screen shows the song title, artist, performance key, capo, BPM, and
BeatBuddy structure when those values are available.

- Use **−½** or **+½** to move one semitone.
- Use **−1** or **+1** to move one whole step.
- Use the capo **−** and **+** controls to select frets 0 through 11.
- When a capo is active, the key badge shows the written key and sounding key.
- Use the reset arrow to discard unsaved key/capo adjustments.
- Select **Save key** to make the displayed key and capo the song's defaults.

Saving a key stores `preferred_key` and `default_capo`. It does not rewrite the
chart text; transposition is applied while the chart is rendered.

When a chart is open, swipe left for the next song and right for the previous song, including while following a setlist. The neighboring titles at the bottom of
the chart show where each swipe will go.

### Create or edit a song

Select **New** on the Songs screen to create a chart. To change an existing
song, open it and select the pencil icon.

The editor supports:

- title (required) and artist;
- written key and original key;
- default capo and BPM;
- a short BeatBuddy arrangement description; and
- the editable chart body.

The simplest chart format uses bracketed section names followed by a chord line
and lyric line. Spacing in the chord line controls where chords appear:

```text
[Verse 1]
G             C
This is the first lyric line

[Chorus]
G        D       Em      C
Sing the chorus together
(Chorus) x2
```

Inline chord notation is also supported and can be easier to edit on a phone or
tablet:

```text
[Verse]
{G}This is a {C}lyric with {D}inline chords
```

Chord-only progressions such as `G C D 4x`, bare section names such as `Bridge`,
and guitar-tab lines are also recognized. Save the song and review the rendered
chart to confirm that ambiguous source spacing was interpreted as intended.

Deleting a song is permanent once it reaches the server. The editor asks for
confirmation first.

### Build and use a setlist

Setlist changes require an online connection.

1. Open **Setlists** and select **+**.
2. Enter a name and, optionally, a gig date and notes.
3. Open the new setlist and select **Add song**.
4. Choose a song and optionally record a key override, capo override, and
   performance note.
5. Use the up/down controls to change the order, or the trash control to remove
   a song from the setlist.
6. Select a song row to open its chart. Swipe left and right to follow the
   setlist order.
7. Choose **Edit setlist** to change its name, gig date, or notes. Clear the
   date or notes by leaving those fields blank; the name is required.
8. Choose **Delete setlist** and confirm to remove the setlist and its entries.
   Songs remain in the library.
9. Use the edit control beside an entry to change its key, capo, and
   performance note in place. Blank key/capo fields clear those overrides and
   restore the song defaults; capo `0` is an explicit override.

The setlist row displays its saved key/capo override and performance note.
Opening an entry applies its key/capo overrides, with each missing field
falling back to the song's preferred key or default capo. Adjust the chart and
choose **Save to setlist** to update that entry only; the song's defaults and
chart source are unchanged. This write requires a connection. Cached setlists
retain entry overrides for offline chart reading, but setlist writes are not
queued offline yet (T10). All setlist changes require a connection to the app
server.

## Offline use and synchronization

For dependable offline use, open the app while online before leaving for a gig
and confirm a recent **Last synced** time under **Settings**. Use **Sync now** if
you want to refresh immediately. The sync downloads the complete song library
and setlist collection into IndexedDB in that browser profile.

Once cached, you can work offline as follows:

| Action | Offline behavior |
| --- | --- |
| Search, filter, and read cached songs | Available |
| Read cached setlists | Available |
| Create, edit, delete, or save a song key/capo | Saved locally and queued |
| Create or change a setlist | Requires a connection |

When connectivity returns, the app automatically pushes queued song changes
and then downloads a fresh server snapshot. You can also trigger that process
from **Settings**.

If the same existing song was changed on both the tablet and server, the server
version wins. Settings reports that the tablet change was replaced. An offline
song creation receives a temporary local ID until its first successful sync.

The app shell can be installed from the browser's PWA/install menu. Service
workers require either `localhost` or a trusted HTTPS origin. Opening
`http://<computer-ip>:3000` from a tablet may reach the page, but it will not
provide dependable PWA installation or offline service-worker behavior.

## Operational notes

### Database credential mismatches

The app connects to the Compose database at `db:5432`; this internal service
address is independent of the host-published PostgreSQL port. Changing a
password in `app/.env` does not change the password stored for a role in an
initialized PostgreSQL volume. Container environment changes take effect only
after recreating the affected container; restarting it alone does not apply new
environment values. Never delete or recreate the database volume to resolve a
credential mismatch.

### Start, stop, and inspect the local stack

Run these commands from `app/`:

```bash
# Start an existing installation
docker compose up -d

# Stop containers without deleting the database
docker compose down

# Follow application logs
docker compose logs -f app

# Check the app and database-backed statistics
curl http://localhost:3000/healthz
curl http://localhost:3000/api/stats
```

After changing React source or client dependencies, rebuild the app image:

```bash
docker compose up --build -d
```

Server source is bind-mounted in the local Compose configuration, but a server
code change still requires an app-container restart.

Do not run `docker compose down -v` unless you intend to delete the local
PostgreSQL database. The app applies migrations before listening. Run them
explicitly inside the running app container with
`docker compose exec app npm --prefix /app/server run migrate`.

### Security and deployment boundary

The application currently has no login or per-user permissions. Anyone who can
reach it can view and modify the library. Keep the local instance on a trusted
machine/network unless authentication and production hardening are added.

The verified deployment is local Docker Compose. Cloud, Kubernetes, Terraform,
and the split nginx/API topology are out of scope; see `docs/AGENTS.md` →
Recorded decisions. A tablet deployment must keep the client and relative
`/api` routes on one trusted HTTPS origin.

## Developer guide

### Repository layout

```text
Chord_Chart_Manager/
|-- app/                     React PWA, Express API, database, and Compose
|   |-- src/                 screens, components, hooks, cache, and API client
|   |-- server/              Express routes, database access, chart parser
|   |-- db/migrations/       canonical baseline and numbered SQL migrations
|   |-- test/unit/           Vitest parser and transposition tests
|   `-- test/e2e/            Playwright browser smoke tests
|-- pipeline/                .docx parser, enrichment, and database loader
|-- infra/                   proposed Kubernetes/Terraform design
`-- README.md                this guide
```

The production Docker image builds the Vite client and serves it from the same
Express process that owns `/api`. Same-origin routing is important to the PWA's
offline behavior.

Charts use dual storage:

- `chart_source` is the text a user edits.
- `chart_content` is the parsed structure used to render and transpose chords.

The browser and server share `app/server/chartParser.js`, so an offline save can
render immediately and produce the same structure that the server will store.
The Python `.docx` importer has a separate whole-document parser.

### Run the client and API outside the app container

Start PostgreSQL and run the app migration command before starting the API
outside its container. Then use two terminals. The server also runs pending
migrations automatically before listening.

Terminal 1, from `app/server`:

```bash
npm install
DATABASE_URL=postgresql://chords:chords@localhost:5432/chords PORT=3001 npm start
```

Terminal 2, from `app`:

```bash
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies relative `/api` requests to port
3001. In PowerShell, set environment variables with `$env:DATABASE_URL = '...'`
and `$env:PORT = '3001'` before running `npm start`.

### Tests and dependency checks

From `app/`:

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

Set `E2E_BASE_URL` to test another deployment. `npm.cmd run test:all` runs both
suites when the target application is already available. On Windows systems
where PowerShell blocks `npm.ps1`, use `npm.cmd` and `npx.cmd`.

Dependency checks:

```bash
npm audit
cd server
npm audit --omit=dev
```

## Import a `.docx` library

The Python 3.10 pipeline converts Word chord charts to JSON, can enrich metadata,
and loads PostgreSQL. Current code uses MusicBrainz and GetSongBPM. The approved
replacement design is MusicBrainz → AcousticBrainz → Gemini fallback; it is not
implemented or live-verified. T14 keeps enrichment off. Do not use proposed
future configuration or commands until code supports them.

1. Put source `.docx` files in `pipeline/docs/`.
2. From `app/`, build and run the parser:

   ```bash
   docker compose --profile pipeline build pipeline
   docker compose run --rm pipeline \
     python batch_parse.py docs -o parsed_out --per-doc
   ```

3. Review `pipeline/parsed_out/report.json`, especially failures and flagged
   songs.
4. Dry-run the database load:

   ```bash
   docker compose run --rm pipeline \
     python load_songs.py parsed_out/songs.json --dry-run
   ```

5. Load the reviewed data:

   ```bash
   docker compose run --rm pipeline \
     python load_songs.py parsed_out/songs.json
   ```

The current optional implementation uses MusicBrainz and GetSongBPM; parsing and direct loading do not require enrichment. The approved MusicBrainz → AcousticBrainz → Gemini replacement is future design only. See the [pipeline guide](pipeline/README.md) for enrichment, caches, strict parsing,
`--truncate`, and host-Python workflows.

Source charts and generated JSON may contain copyrighted lyrics and are ignored
by Git. Do not commit them or enrichment credentials.

Current code uses [MusicBrainz](https://musicbrainz.org/) and [GetSongBPM](https://getsongbpm.com/). The approved future replacement is MusicBrainz → AcousticBrainz → Gemini fallback, and is not implemented or live-verified. Do not remove GetSongBPM attribution if previously sourced data may remain in use; verify data provenance first. T14 remains enrichment-off.

## Troubleshooting

### The app opens but there are no songs

This is normal for a new database. Create a song in the UI or run the pipeline
loader. Check the current count with `curl http://localhost:3000/api/stats`.

### Adding a database schema change

Migrations run before the API starts listening. Add the next numbered plain SQL
file under `app/db/migrations/` (for example, `0003_add_song_field.sql`), then
restart or rebuild the app. Existing databases that match `0001_baseline.sql`
are recorded as having adopted that baseline without rerunning its DDL. A
partial or incompatible schema fails startup and is not silently adopted.
Editing `0001_baseline.sql` does not change an existing database; all future
schema changes need a new numbered migration. Run migrations explicitly with
`docker compose exec app npm --prefix /app/server run migrate`. Never recreate
the PostgreSQL volume to apply schema changes.

### Offline charts are missing

Reconnect, open **Settings**, run **Sync now**, and confirm that **Last synced**
updates before going offline. Cache data belongs to the current browser profile;
private browsing, site-data clearing, and a different browser do not share it.

### A tablet can open the LAN address but cannot install the app

Plain HTTP is a secure-context exception only on `localhost`. Serve the app and
API together over trusted HTTPS for a real tablet deployment.

### A queued tablet edit disappeared after sync

Check the conflict notice in **Settings**. If another client updated that song
after the tablet cached it, synchronization deliberately keeps the newer server
copy.

## Backup and restore

Run the backup script from any working directory; it locates `app/docker-compose.yml` relative to itself and writes validated custom-format dumps to the project-root `backups/` directory. The default target is the Compose `POSTGRES_DB` (normally `chords`); each database has its own timestamped `DATABASE-YYYYMMDD-HHMMSS.dump` sequence. A successful backup requires `pg_dump` success, a nonempty file, and a successful `pg_restore --list`. The default retention is 14 backups per database; only files matching that database's exact timestamped pattern are pruned.

```powershell
.\scripts\backup.ps1
.\scripts\backup.ps1 -Database ccm_backuptest -Keep 7
```

```bash
./scripts/backup.sh
./scripts/backup.sh --database ccm_backuptest --keep 7
```

Restore requires an explicit confirmation because it replaces objects in the selected database. It first validates the archive and creates a separate safety backup. If that backup fails, restore stops. Safety backups are retained without pruning.

```powershell
.\scripts\restore.ps1 -Path .\backups\chords-20261009-120000.dump -Force
.\scripts\restore.ps1 -Path .\backups\ccm_backuptest-20261009-120000.dump -Database ccm_backuptest -Force
```

```bash
./scripts/restore.sh --file ./backups/chords-20261009-120000.dump --yes
./scripts/restore.sh --file ./backups/ccm_backuptest-20261009-120000.dump --database ccm_backuptest --yes
```

To schedule a backup, create a Windows Task Scheduler action using `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\Chord_Chart_Manager\scripts\backup.ps1"`, or add a Linux cron entry such as `0 2 * * * /path/to/Chord_Chart_Manager/scripts/backup.sh >> /path/to/backup.log 2>&1`. These are examples only; no scheduled job is created.

## Current validation status

T02 baseline verification on 2026-10-09 passed the production image/Vite build,
unit tests (2 files, 7 tests), and Playwright E2E (1 test). The browser
assertions checked Songs-screen controls. After the computer crash, the existing
`app_pgdata` volume remained attached; TCP authentication passed, both
containers were healthy, and `/healthz` plus `/api/stats` returned HTTP 200.
The stack remains running. Unit/E2E checks were completed before the crash; after
restart, TCP, health, and endpoints were checked again.

T02 validated the baseline and Songs screen only. It did not verify complete
offline workflows, a real corpus import, or live enrichment. Earlier feature
checks recorded in the engineering history are historical and were not part of
T02.

More implementation detail is available in [app/README.md](app/README.md), and the current development checkpoint is recorded in [HANDOFF.md](docs/HANDOFF.md).
