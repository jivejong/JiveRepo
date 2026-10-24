# Chord Chart Manager data pipeline

The pipeline converts Word chord-chart documents into JSON. The loader is
insert-only and enrichment stays a separate, later operation. The current
legacy enrichment code uses MusicBrainz and GetSongBPM; it is not the approved
Stage B workflow and must remain off for C1 chart import. The recommended
workflow uses the Python 3.10 Compose service.

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
Chord rows are retained as literal raw lines when the structured progression
form would move punctuation, repeat markers, annotations, or meaningful
spacing away from its source position. Python ingestion and the shared
JavaScript editor parser use the same chord spans and eligibility rule. The
literal line keeps its actual separator characters (including hyphens versus
en dashes). Parenthesized/bracketed annotations remain opaque. Recognized
chord spans in a mixed literal row transpose in place; an unsupported
chord-like token stays byte-for-byte unchanged, the row is marked partial,
and a located `ordered_chord_token_unresolved` diagnostic blocks loading.
Rows containing prose lookalikes are left unchanged; they are not treated as
unsupported chord tokens. Literal
chord spans followed by a lyric retain their association in chart content
while the complete source row remains visible with its original punctuation
and spacing.
Ordinary progressions that preserve their token order remain structured.

Before chart content begins, the DOCX parser recognizes complete bounded
opening `Capo N`, `key-BPM`, and `orig key` fields. These fill capo, original
key, and BPM independently of the heading's written/performance and sounding
keys. Conflicts remain visible with a located blocking diagnostic; arbitrary
body text is not treated as metadata, and uncaptured text is preserved. The
bounded opening original-key/BPM token accepts ASCII hyphen or en dash, with
optional surrounding whitespace (for example `A-120`, `A - 120`, or `A – 120`).

For an optional host-Python workflow:

When connecting host Python to this repository's Compose database, use the
loopback-published host port `localhost:15432` by default (`DB_PORT` in
`app/.env`). The Docker pipeline service continues to use the internal
`db:5432` address; do not use that service name from host Python.

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

DOCX song boundaries follow the user's authoritative document contract: only a
nonempty effective Word outline heading starts a song. Outline levels 0–8 are
headings; level 9 is body. Direct paragraph properties override inherited
style properties, and the default paragraph style applies when `w:pStyle` is
absent. Heading text supplies title, artist, and key fields; missing artist or
key values remain blank rather than being inferred. Nonempty headings with
unsupported fields remain song boundaries and carry a located blocking
diagnostic so they cannot silently become part of the preceding chart.
In an en-dash heading, a parenthesized key such as `D (E)` or the separate
tab field `D<TAB>(E)` means the chart is written in D and sounds in E with a
capo. The parser stores D as the written performance key, E as the alternate
key, and derives capo fret 2 from that interval. It keeps the artist in its
own field. A key pair whose capo relation cannot be derived produces a located
blocking diagnostic.

This heading-only rule supersedes earlier Normal-style title handling. Bold,
tabs, title-like wording, dash or slash notation, `pageBreakBefore`, and
explicit or rendered page breaks do not start songs. Explicit breaks inside a
run still preserve the text on both sides as ordered chart spans. Rendered
`lastRenderedPageBreak` pagination remains layout metadata only. Meaningful
chart-like content before the first heading stops parsing with a paragraph/span
location for review; the parser does not assign uncertain ownership. A plain
cover line retains the existing cover-genre behavior where that input format
is used. Reconcile parsed rows against headings as evidence, not as proof that
the source contains every intended song.

The report distinguishes low-confidence space-aligned lines from
`word_tab_placement_unresolved`. The latter records document, song, one-based
section/line, and a reason code without chart text. It means an inserted DOCX
tab uses layout outside the bounded normalization rule; the parser's
provisional columns are retained for diagnosis but the loader rejects the
batch before connecting. Resolve the flagged source lines before parsing and
loading again. Tab-stop definitions alone are not text and do not trigger this
diagnostic.

The loader requires every song object to include
`parser_diagnostics_version: 1`, which batch parsing adds after checking each
chord/lyric pair. Every version-1 song object must also include `warnings` as
a list; an empty list is valid. The DOCX parser reads inserted tabs from inline
run content and ignores paragraph tab-stop definitions. For supported tabbed
chart paragraphs it advances from the current position to the next effective
explicit left or default stop, then maps that physical distance to a character
column. The rule uses the Courier New cell width 1229/2048 em scaled to the
effective run size in half-points: `size * 10 * 1229/2048` twips per cell (8 pt
is 96.015625 twips). It uses nearest-column rounding with exact half values
rounded up. For paired rows it maps the chord row's physical position into the
lyric row's UTF-16 columns using that row's effective width. It accounts for
left paragraph indentation, explicit left stops, and the 720-twip default
grid. Other fonts, mixed sizes within one tabbed paragraph, non-ASCII runs in a tabbed paragraph,
non-left/cleared stops, unsupported indentation, character spacing/scaling, or
missing effective stops are reported as unresolved. Font and size can inherit
through paragraph and character-style chains. When a paragraph omits `w:pStyle`,
the document's default paragraph style is used. Missing, wrong-type, or cyclic
style references on a tabbed row, and on a lyric row paired with a tabbed chord
row, are reported as unresolved. Inherited non-default character spacing or
width scaling is reported as unresolved. Unrelated tab-free rows are not blocked
by unresolved physical placement metadata. The chord token's physical
start is retained through row pairing and rounded once into lyric columns. This
is a bounded rule, not a general Word layout engine; review any new layout
family before using it.
Tab-free paragraphs retain the existing extraction and pairing behavior.

New DOCX `chart_source` values contain readable ordinary chart text without
CCM markers. Python derives that source from the parsed structure and checks
whether ordinary chart syntax can express each line; unsupported structures
produce `plain_text_body_unresolved` and are rejected before loading. The
serializer retains a section label's same-paragraph raw/progression tail on
that ordinary line when it came from a bracketed or bare-colon source and the
complete parsed chart round-trips exactly. Other sections keep their separate
label and body lines. Pipe notation is retained as raw text where progression
normalization would otherwise alter it. When inline serialization fails the
exact comparison, the parser tries the separate label/body spelling and keeps
it only if the complete chart, including confidence fields, reparses exactly;
otherwise the existing blocking diagnostic remains.
loader reparses each ordinary `chart_source` at batch preflight and requires
strictly typed exact Python chart-object equality with supplied `chart_content` before any
database connection or write. Equality includes section/line types, text,
chord symbols and order, UTF-16 positions, repeat/progression fields, spacers,
raw content, and confidence fields; no fields are dropped or normalized to
make a mismatch pass. Null sources and explicit CCM v1/v2 generated sources
remain supported legacy paths and are not subjected to the ordinary-text
comparison. The existing shared JavaScript parser derives `chart_content`
from ordinary source on app load/save. Title, artist, key, BPM, genre, and
other metadata extraction remain unchanged. Existing stored CCM v1/v2 sources
are not converted by this change.
The loader rejects a batch containing an unresolved Word-tab or plain-text
body warning before opening the database or creating artists, genres, or
vibes. This also applies to `--dry-run`. Parsed artifacts made before this guard (including
older enriched JSON) lack the marker and are intentionally rejected; rerun
`batch_parse.py` from the source DOCX, then redo any separately authorized
enrichment while preserving the added fields. Ordinary low-confidence lines
remain review warnings and do not by themselves block loading.

## 3. Keep enrichment separate

`enrich_songs.py` remains legacy MusicBrainz/GetSongBPM code. Do not run it as
part of C1 or treat its output as a chart-import requirement. Any enrichment
must happen after the inserted charts are reviewed and accepted, under its own
explicit task and current approved workflow. This loader does not call
providers, infer missing metadata, or consume provider caches.

## 4. Preview and insert PostgreSQL rows

From `app/`, produce a read-only plan against the selected database:

```bash
docker compose run --rm pipeline \
  python load_songs.py parsed_out/songs.json --dry-run
```

The preview reports proposed inserts, exact skips, blocking identity
collisions, unresolved categories, and parser/validation failures. It prints
identities and category labels only, never chart bodies. Planning runs in a
repeatable-read, read-only transaction and does not advance sequences.
The locked planner reports an exact title/artist/source match as a skip before
artist resolution or Genre/Vibe assignment resolution. Apply may take
protective normalized-name and matching definition-row locks before that
planner check to preserve the app's lock order; these locks do not change
catalog definitions or song rows. Repeated normalized title/artist inputs
with different source documents block the entire batch before SQL writes,
even when no matching database song exists.
Title and artist comparisons are case-insensitive using the existing lower()
semantics; `source_document` must match exactly, with NULL matching NULL. A
same-title/artist record from another source or multiple matches blocks the
whole batch for a bounded decision. There is no fuzzy matching or aliasing.

Only after the exact document and target scope have a fresh backup/actual
restore proof and explicit load authorization, run apply:

```bash
docker compose run --rm pipeline \
  python load_songs.py parsed_out/songs.json
```

The normal load inserts new songs only. Existing matches are never updated;
rerunning a committed batch inserts zero rows for them. It does not create or
rename Genre/Vibe definitions. Explicit categories resolve by the app's
category-specific normalization (trim and collapse whitespace, then
case-insensitive exact comparison). Missing or ambiguous labels are reported
and left unassigned; resolved Genre assignments are non-primary. A Genre from
the source filename is retained as provenance in parsed JSON but is not
assigned. No primary Genre is inferred.
Parsed JSON with a nonempty primary Genre and missing/invalid
`primary_genre_source` is rejected before database access with an instruction
to reparse using the current parser; the loader does not infer provenance or
discard that category.

Apply first takes the app's sorted normalized-name advisory locks for Genre
labels, then Vibe labels. For each normalized category/name key, it locks all
matching definition rows in ascending ID order with `FOR SHARE`, then takes
locks on the artist, song, and assignment tables in that order. It does not
table-lock the definition catalogs. The planner rechecks catalog resolution
after acquiring these locks, so concurrent app definition
create/rename/delete and assignment operations using the same name locks
cannot introduce a normalized-name ambiguity between lookup and insert.
Apply reruns the same identity and category planner under those locks.
The complete batch commits together or all inserted rows roll back. A
preflight/collision or SQL failure stops the batch; there are no per-song
savepoints or partial commits. There is intentionally no `--truncate` or
update mode. `--database-url URL` can select a database instead of
`DATABASE_URL`; confirm the target before any separately authorized apply.

All inputs and outputs that must follow the project folder are bind-mounted:

- `pipeline/docs/` → `/pipeline/docs`
- `pipeline/parsed_out/` → `/pipeline/parsed_out`

The image contains the pipeline code and Python dependencies. Rebuild the
pipeline image after changing a pipeline script or `requirements.txt`.

## Pipeline tests

The pipeline image installs the small test-only dependency set from
`requirements-test.txt` (currently pytest); these tools are not app
dependencies. For each run, choose a unique database name matching
`ccm_loader_c1_<suffix>`, verify that exact name is absent, create it under
the active Compose database role, and record a task-specific database comment
before proceeding. Initialize only that database with the T06 app migration
runner:

```bash
docker compose exec app npm --prefix /app/server run migrate -- --database "$PIPELINE_TEST_DATABASE"
docker compose run --rm --no-deps -e PIPELINE_TEST_DATABASE="$PIPELINE_TEST_DATABASE" pipeline pytest -q
```

The parser test builds a synthetic `.docx` in a temporary directory using
invented text. The loader integration verifies all application tables and
sequences across read-only preview, insert/skip, collision, atomic rollback,
and concurrent loads. The guard refuses names outside the task prefix. Drop
only the database created for the run, verify its owner marker first and its
absence afterward, and never point the test at `chords`.

## Current validation status

T02 did not run the pipeline or validate enrichment. Historical checks reported
parser, simulated batch, mocked enrichment, and loader checks. A real-document
corpus run and live enrichment remain pending. T14 remains enrichment-off.
