# Chord Chart Manager data pipeline

The pipeline converts Word chord-chart documents into JSON and loads them into
PostgreSQL. Current enrichment code uses MusicBrainz and GetSongBPM. The
approved replacement design is MusicBrainz → AcousticBrainz → Gemini fallback;
it has not been implemented or live-verified. T14 keeps enrichment off. Do not
use future configuration or commands operationally until the code supports them.
The recommended workflow uses the Python 3.10 Compose service.

## Requirements

For the Docker workflow:

- Docker Desktop or Docker Engine with Compose.
- Source `.docx` files in `pipeline/docs/`.
- Any MusicBrainz/GetSongBPM settings below apply only to the existing implementation; the approved replacement is not implemented.

The container provides Python 3.10 and installs `requirements.txt`. It connects
to the Compose database through the service name `db`, so no host-specific
database address is needed.
The app owns schema initialization and applies migrations before listening;
start the app once before running the pipeline against a fresh volume. The
pipeline does not define or apply a separate schema.

The pipeline parser also recognizes the app's versioned generated chart source
(`[[CCM-CHART:2]]`) when such text is present in a document, and the DOCX path
also accepts legacy v1. Explicit section and line markers preserve progression,
repeat, raw, spacer, and lyric/chord structure. Version 2 stores lyric text
separately from numeric chord columns, so lyric edits do not shift later anchors.
Columns are zero-based UTF-16 code units, matching JavaScript string indices;
anchors past lyric end are retained. Generated raw-line trailing spaces are
significant and preserved. Ordinary Word chart parsing ignores paragraph-spacing
attributes but preserves explicit empty paragraphs inside songs as spacer lines;
blank padding outside song boundaries is ignored. Manual Word line breaks split
chart text into separate lines. Text-wrapping `w:br` and `w:cr` controls split
chart lines; a terminal text break becomes a spacer before following content.
Consecutive breaks and adjacent empty paragraphs collapse to one spacer. Page
and column breaks remain layout controls and do not create chart rows. A spacer before a section heading is normalized
to the normal section gap; a spacer after a heading remains explicit and turns
off that section gap to prevent doubled spacing. Synthetic tests cover these
cases and the shared serialization contract. Ordinary chart text edited in the
app follows the same internal blank-line normalization; a chord-only line is
flushed as a progression instead of being paired across a blank separator.

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

## Historical local readiness

Earlier checks reported a successful Python 3.10 image build, module imports,
bind mounts, and database connectivity. These are historical pipeline checks,
not part of T02. A real corpus and live enrichment remain unverified.

Before processing real data:

1. Put source `.docx` files in `pipeline/docs/`.
2. Run the parser and review `pipeline/parsed_out/report.json`.
3. Parsing and direct loading need no enrichment credentials. If using the
   current legacy enrichment code, preserve an existing `app/.env`; create it
   from `app/.env.example` only if it is absent.
4. Dry-run the loader before a real database load.

T14 keeps enrichment off.

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

## 3. Enrich metadata (optional; current implementation only)

The current `enrich_songs.py` code uses MusicBrainz and GetSongBPM. Its existing
command and settings describe that implementation only. The approved replacement
is MusicBrainz → AcousticBrainz → Gemini fallback; it is not implemented or
live-verified, and no settings or command for that future design are available.
T14 keeps enrichment off. Do not use this legacy path for T14.

If the current implementation is being tested in a separate authorized task,
start with a small trial:

```bash
docker compose run --rm pipeline \
  python enrich_songs.py parsed_out/songs.json \
  -o parsed_out/songs.enriched.json \
  --cache parsed_out/enrich_cache.json \
  --limit 20
```

The current code expects its existing MusicBrainz/GetSongBPM configuration in
`app/.env`; preserve any existing file and do not infer future settings from
this guide. Check cache provenance before reuse. If data may have come from
GetSongBPM, retain the visible backlink requirement until its source is checked;
do not remove attribution blindly.

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

## Pipeline tests

The pipeline image installs the small test-only dependency set from
`requirements-test.txt` (currently pytest); these tools are not app
dependencies. Create an empty `ccm_pipelinetest` database only after checking
that it does not exist, then initialize it with the T06 app migration runner:

```bash
docker compose exec app npm --prefix /app/server run migrate -- --database ccm_pipelinetest
docker compose run --rm -e PIPELINE_TEST_DATABASE=ccm_pipelinetest pipeline pytest -q
```

The parser test builds a synthetic `.docx` in a temporary directory using
invented text. The loader dry-run test checks all eight application tables'
contents before and after rollback and refuses any database name other than
`ccm_pipelinetest`. Drop only this task-owned scratch database after testing;
never point the test at `chords`.

## Current validation status

T02 did not run the pipeline or validate enrichment. Historical checks reported
parser, simulated batch, mocked enrichment, and loader checks. A real-document
corpus run and live enrichment remain pending. T14 remains enrichment-off.
