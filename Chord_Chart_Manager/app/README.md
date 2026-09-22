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

## Run with Docker Compose

Docker is the recommended local workflow.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD in .env if desired.
docker compose up --build -d
docker compose ps
```

The local app can use Compose's defaults without an `.env` file. Create the
file to retain custom database settings or before supplying the pipeline's
MusicBrainz and GetSongBPM enrichment values.

Open <http://localhost:3000>. Expected services:

- `app`: React production build plus Express API on port 3000.
- `db`: PostgreSQL 16, published on port 5432 for the local pipeline.

Health checks:

```bash
curl http://localhost:3000/healthz
curl http://localhost:3000/api/stats
```

The schema initialization scripts run only when the `pgdata` volume is empty.
Changing a schema file does not migrate an existing volume. Do not run
`docker compose down -v` unless deleting the local database is intentional.

## Run without the app container

PostgreSQL must already be running with the schema applied.

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

The client/build toolchain and production server have separate committed
lockfiles. Docker requires them and uses `npm ci`; it no longer falls back to
an unconstrained install.

Install the locked test dependencies and Playwright's Chromium once:

```bash
npm ci
npx playwright install chromium
```

Run the Vitest unit suite:

```bash
npm test
```

The Playwright smoke test targets the production app at
`http://localhost:3000` by default, so start Compose first:

```bash
docker compose up --build -d
npm run test:e2e
```

Set `E2E_BASE_URL` to test another deployment. `npm run test:all` runs the unit
and browser suites together when the target app is already available. On
Windows systems where PowerShell blocks `npm.ps1`, use `npm.cmd` for the same
commands.

Dependency checks:

```bash
npm audit
cd server && npm audit --omit=dev
```

## User-facing behavior

### Songs

- Search titles and artists and filter by genre, feel, or era tags.
- Create, edit, and delete songs online or offline.
- Edit `chart_source`; the parsed chart is available immediately after an
  offline save.
- Transpose charts and adjust capo. Saving stores `preferred_key` and
  `default_capo`; it does not rewrite the chart into a different key.
- Swipe up for the next song and down for the previous song in the current
  song or setlist order.

### Setlists

- Create a setlist with a name, gig date, and notes.
- Add songs with optional key, capo, and performance-note overrides.
- Move songs up or down, remove them, and open charts in setlist order.
- Setlists are cached for offline viewing. Setlist mutations currently require
  a connection; only song mutations are queued offline.

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
├── db/init/01-schema.sql          first-run PostgreSQL schema
├── Dockerfile
└── docker-compose.yml
```

## Verified locally

- Production Vite/PWA build.
- Vitest unit suite: 7 tests covering transposition and shared chart parsing.
- Playwright Chromium smoke test against the production Compose app.
- Client/test and production-server npm audits with zero known advisories.
- Docker image build and healthy Compose startup.
- Clean-browser production render.
- Song CRUD and conflict-aware API behavior.
- Offline edit, create, and delete followed by synchronization.
- Immediate offline chart re-render.
- Desktop/server-wins conflict handling and Settings notification.
- Setlist create, add, reorder, remove, and per-song overrides.
- Up/down swipe navigation.

The test records used for validation were removed afterward.

## Deployment

The historical single-VM deployment notes are no longer the active plan. See
[../infra/INFRA_HANDOFF.md](../infra/INFRA_HANDOFF.md) for the proposed cloud
work. No infrastructure implementation has started.

If GetSongBPM-enriched metadata is used, add its required visible backlink to
the React UI before deployment.
