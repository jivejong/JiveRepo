# Chord Chart Manager

Chord Chart Manager is a searchable, transposable chord-chart manager for live
performance. It combines a Python import pipeline, a React/Vite progressive
web app, an Express API, and PostgreSQL.

## Current status

The local application milestone is complete:

- Docker Compose builds and runs the app and PostgreSQL at
  <http://localhost:3000>.
- Songs can be searched, filtered, edited, transposed, and organized into
  setlists.
- Setlists support gig dates, notes, per-song key/capo overrides, performance
  notes, reordering, and removal.
- The PWA caches the library and setlists in IndexedDB.
- Song creation, editing, transposition preferences, and deletion work while
  offline and sync when the connection returns.
- Sync pushes tablet changes before pulling a fresh server snapshot. If both
  the tablet and server changed the same existing song, the server copy wins.

Cloud and Kubernetes work has not started. The current stopping point and
remaining work are recorded in [HANDOFF.md](HANDOFF.md). The proposed
infrastructure is documented separately in
[infra/INFRA_HANDOFF.md](infra/INFRA_HANDOFF.md).

### Local readiness

There are no remaining code, dependency, build, or Docker blockers for local
use. The web app, PostgreSQL, and Python 3.10 pipeline image have all been
validated locally.

The remaining prerequisites are user-provided data and credentials:

- Add source `.docx` files to `pipeline/docs/` before parsing.
- Copy `app/.env.example` to `app/.env` before enrichment, then set
  `MUSICBRAINZ_CONTACT` and `GETSONGBPM_API_KEY` when available.
- Parsing does **not** require either enrichment credential.
- The database remains empty until parsed or enriched JSON is loaded.

## Repository layout

- [`pipeline/`](pipeline/) parses `.docx` chord chart docs, optionally enriches
  metadata through MusicBrainz and GetSongBPM, and loads PostgreSQL. See
  [pipeline/README.md](pipeline/README.md).
- [`app/`](app/) contains the React/Vite PWA, Express API, PostgreSQL schema,
  and Docker Compose setup. See [app/README.md](app/README.md).
- [`infra/`](infra/) contains the proposed Kubernetes and Terraform design.
  It is planning material, not an implemented deployment.

## Run locally

From `app/`:

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

Open <http://localhost:3000>. The database is published on
`localhost:5432` for the Python loader. On first startup, PostgreSQL applies
`app/db/init/01-schema.sql` to the empty `pgdata` volume.

Useful checks:

```bash
curl http://localhost:3000/healthz
curl http://localhost:3000/api/stats
docker compose logs -f app
```

See [app/README.md](app/README.md) for development and offline-sync details.

## Run the pipeline in Python 3.10

The pipeline has a dedicated one-shot Docker image, so the host does not need
Python installed. Put the source documents in `pipeline/docs/`, then run from
`app/`. MusicBrainz and GetSongBPM credentials are not needed for this parsing
step:

```bash
docker compose --profile pipeline build pipeline
docker compose run --rm pipeline \
  python batch_parse.py docs -o parsed_out --per-doc
```

Generated files stay in `pipeline/parsed_out/`. Create the ignored `app/.env`
file and add enrichment credentials only before running enrichment. See
[pipeline/README.md](pipeline/README.md) for the parse, enrichment, dry-run,
and load commands.

## Data sources

The optional enrichment step uses
[MusicBrainz](https://musicbrainz.org/) for release and genre metadata and
[GetSongBPM](https://getsongbpm.com/) for tempo and key data. GetSongBPM's
terms require a visible backlink when its data is used. This README provides a
project attribution link; a visible link in the React UI is still required
before distributing an enriched production library.

## Moving the project folder

The source tree contains no machine-specific absolute paths. Application,
pipeline, Docker build, and test paths are resolved relative to files or the
current project directory, so the entire `Chord_Chart_Manager` folder can be
moved without editing source code.

Local tooling state may need to be recreated after a move:

- Recreate `.venv` rather than relying on a moved Python virtual environment.
- Run `npm ci` in `app/`; `node_modules` is generated and is not portable
  between operating systems.
- Run `npx playwright install chromium` on a new machine. Playwright stores its
  browser outside the project folder.
- Keep or recreate the ignored `.env` file and set enrichment environment
  variables on the destination machine.

PostgreSQL data is the exception: Compose currently stores it in a Docker named
volume, outside the project folder. Moving the folder does not move the live
database. Export/import the database when moving machines, or explicitly
choose a folder-relative bind mount before treating the database files as part
of the portable folder.
