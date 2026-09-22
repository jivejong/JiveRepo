# Chart Book data pipeline

The pipeline converts Word chord-chart documents into JSON, optionally enriches
their metadata, and loads them into PostgreSQL. The recommended workflow uses
the dedicated Python 3.10 Compose service, so no host Python installation is
required.

## Requirements

For the Docker workflow:

- Docker Desktop or Docker Engine with Compose.
- Source `.docx` files in `pipeline/docs/`.
- Enrichment credentials in `app/.env` when enrichment is run.

The container provides Python 3.10 and installs `requirements.txt`. It connects
to the Compose database through the service name `db`, so no host-specific
database address is needed.

For an optional host-Python workflow:

- Python 3.10 or newer.
- PostgreSQL only for `load_songs.py`.
- `psycopg` from `requirements.txt` for the loader.
- `MUSICBRAINZ_CONTACT` for
  [MusicBrainz](https://musicbrainz.org/) enrichment.
- `GETSONGBPM_API_KEY` for
  [GetSongBPM](https://getsongbpm.com/) enrichment.

The parsers and enrichment HTTP clients otherwise use the Python standard
library.

```bash
python -m venv .venv
# Activate .venv for your shell, then:
pip install -r requirements.txt
```

Keep real `.docx` files, generated song JSON, enrichment caches, database
dumps, and secrets out of source control. The song files can contain
copyrighted lyrics.

## Current local readiness

The Python 3.10 image builds successfully, imports every pipeline module, sees
both bind-mounted folders, and connects to the Compose PostgreSQL database.
There are no implementation blockers.

Before processing real data:

1. Put the source `.docx` files in `pipeline/docs/`.
2. Run the parser and review `pipeline/parsed_out/report.json`.
3. Before enrichment, copy `app/.env.example` to `app/.env` and provide
   `MUSICBRAINZ_CONTACT` and `GETSONGBPM_API_KEY`.
4. Dry-run the loader before performing the real database load.

Steps 1 and 2 do not require MusicBrainz or GetSongBPM credentials. If
enrichment is postponed, `parsed_out/songs.json` can be dry-run or loaded
directly instead of `parsed_out/songs.enriched.json`.

## Docker workflow

Run these commands from `app/`. Build the one-shot pipeline image:

```bash
docker compose --profile pipeline build pipeline
docker compose run --rm pipeline python --version
```

The second command should report Python 3.10.x. Compose automatically starts
the database dependency when a pipeline command needs it.

## 1. Inspect one document

`chart_parser.py` is useful for debugging one source document:

```bash
docker compose run --rm pipeline \
  python chart_parser.py docs/Sample.docx
```

It prints a JSON array to standard output.

## 2. Parse the corpus

```bash
docker compose run --rm pipeline \
  python batch_parse.py docs -o parsed_out --per-doc
```

Important outputs:

- `parsed_out/songs.json`: combined song records.
- `parsed_out/report.json`: failures, flagged songs, and corpus statistics.
- Per-document output when `--per-doc` is supplied.

Review every failure and flagged song against the source documents before
continuing. Use `--strict` when the command should fail if any document fails.

## 3. Enrich metadata (optional)

Start with a small trial:

```bash
docker compose run --rm pipeline \
  python enrich_songs.py parsed_out/songs.json \
  -o parsed_out/songs.enriched.json \
  --cache parsed_out/enrich_cache.json \
  --limit 20
```

Then inspect matches, non-matches, release years, BPM values, and keys before
removing `--limit`. Enrichment is cached in
`parsed_out/enrich_cache.json`, so a long run can be resumed. Set
`MUSICBRAINZ_CONTACT` and `GETSONGBPM_API_KEY` in `app/.env` first; Compose
passes them into the one-shot container.

Parsed source values take precedence over enrichment results. GetSongBPM's
terms require a visible backlink in any application using its data. The
project README links to the service, but the React UI does not currently
include that attribution.

## 4. Load PostgreSQL

From `app/`, dry-run the load into the Compose database:

```bash
docker compose run --rm pipeline \
  python load_songs.py parsed_out/songs.enriched.json --dry-run
```

`--dry-run` performs the same parse, connection, and database work inside one
transaction, then rolls everything back. This also applies when combined with
`--truncate`.

Run without `--dry-run` after reviewing the summary:

```bash
docker compose run --rm pipeline \
  python load_songs.py parsed_out/songs.enriched.json
```

If enrichment was skipped, substitute `parsed_out/songs.json` in both loader
commands.

Other loader options:

- `--database-url URL`: use a URL instead of `DATABASE_URL`.
- `--truncate`: clear song-related tables before loading. This is destructive
  unless paired with `--dry-run`.

The loader is idempotent for its natural song key and preserves existing song
IDs on updates. Each song uses a savepoint so one malformed record does not
roll back successful records in a normal load.

All inputs and outputs that must follow the project folder are bind-mounted:

- `pipeline/docs/` → `/pipeline/docs`
- `pipeline/parsed_out/` → `/pipeline/parsed_out`

The image contains the pipeline code and Python dependencies. Rebuild the
pipeline image after changing a pipeline script or `requirements.txt`.

## Current validation status

- The parser was validated against the available sample document.
- The batch runner was tested with simulated multi-document inputs.
- Enrichment logic was tested with mocked API responses; the real enrichment
  trial remains pending.
- The loader was tested against PostgreSQL for insert, repeat load, truncate,
  and both regular and truncate dry-run rollback.
- A full real-document corpus run remains pending.
