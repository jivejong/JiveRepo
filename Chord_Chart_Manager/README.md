# Chord Chart Manager

Chord Chart Manager is a tablet-friendly chord-chart library for rehearsal and
live performance. It keeps songs searchable, renders chords above lyrics,
transposes charts without changing the lyrics, and organizes songs into an
ordered setlist. After an online sync, the progressive web app (PWA) can open
cached charts without a network connection.

The project includes the complete local stack: a React/Vite PWA, an Express
API, PostgreSQL, and a Python pipeline for importing `.docx` chart collections.

## Contents

- [What the application does](#what-the-application-does)
- [Quick start](#quick-start)
- [Using the application](#using-the-application)
- [Offline use and synchronization](#offline-use-and-synchronization)
- [Import a `.docx` library](#import-a-docx-library)
- [Backup and restore](#backup-and-restore)
- [Operational notes](#operational-notes)
- [Developer guide](#developer-guide)
- [Troubleshooting](#troubleshooting)
- [Current validation status](#current-validation-status)
- [Project documents](#project-documents)

## What the application does

- Search the library by song title or artist.
- Filter songs by Genre, Vibe, and Era, and sort the list several ways.
- Create, edit, and delete songs from the browser.
- Render sectioned chord charts with chords aligned above lyrics.
- Transpose a chart by half-step or whole-step and change its capo setting.
- Save a preferred performance key and default capo without rewriting the
  chart's source text.
- Scroll a chart automatically at an adjustable speed while playing.
- Build dated setlists, add performance notes, and arrange the running order.
- Move directly to the next or previous song while performing.
- Choose a Light, Dark, or System theme and one of five chart text sizes.
- Manage the shared Genre and Vibe definitions.
- Cache songs and setlists for offline use, including queued offline song and
  setlist changes that sync when the connection returns.

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
The app applies pending database migrations before it starts listening. A
database that already has application tables but no recorded migrations
prevents startup with a diagnostic; it is never adopted.

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
  library totals, and holds the appearance, text-size, and Genre/Vibe
  management controls.

The bottom navigation is hidden while reading or editing a chart to maximize
the available screen space. Use the back arrow to return.

### Find a song

On the **Songs** screen:

1. Type part of a title or artist into the search box.
2. Select **Filter** to show the available tag categories.
3. Select one or more tags to narrow the results. Tags selected within one
   category (Genre, Vibe, or Era) are alternatives, so a song matches if it has
   any of them; the categories combine, so a song must match every category
   that has a selection. A Genre filter matches a song's primary or any
   secondary Genre; a Vibe filter matches any assigned Vibe. A Genre and a Vibe
   with the same name remain separate filters. A song with no release year
   matches no Era.
4. The **All** control beside a category clears only that category; **Clear
   all** removes every active filter.
5. Select a song row to open its chart.

Filtering runs on the device against the cached library, so it works the same
online and offline.

Sort the list by title (default), date added, artist, primary Genre, Era, or the
alphabetically first Vibe. The adjacent direction control reverses the selected
order, and songs missing that value always stay last. The sort choice is stored
on the device and works offline; chart navigation follows the visible order. A
song created offline is sorted by its real local creation time until it syncs;
after that it uses the server's creation time.

The badge at the right of each row shows the chart's written key and, when set,
its default capo fret. Era is read-only and derived from the release year by
decade; enter a year to add an Era when one is not already available.

### Read and transpose a chart

The chart screen shows the song title, artist, performance key, capo, BPM, and
BeatBuddy structure when those values are available. The Key/BPM/BeatBuddy row
stays pinned directly below the title while the chart scrolls; long BeatBuddy
values stay within the row.

- Use **−½** or **+½** to move one semitone.
- Use **−1** or **+1** to move one whole step.
- Use the capo **−** and **+** controls to select frets 0 through 11.
- When a capo is active, the key badge shows the written key and sounding key.
- Use the reset arrow to discard unsaved key/capo adjustments.
- Select **Save key** to make the displayed key and capo the song's defaults.

Saving a key stores `preferred_key` and `default_capo`. It does not rewrite the
chart text; transposition is applied while the chart is rendered. The song's
original key is displayed separately and is never changed by transposition,
capo, or setlist overrides.

Only clearly recognized chord tokens transpose. Prose, section labels,
tablature, and annotations in parentheses or brackets are left unchanged.

When a chart is open, swipe left for the next song and right for the previous
song, including while following a setlist. The neighboring titles at the bottom
of the chart show where each swipe will go.

Paired chord and lyric rows never wrap; on narrow screens they scroll together
horizontally, and a horizontal swipe that starts on such a row scrolls it
instead of changing songs. When chord labels would overlap, they appear in
their original order on one row, separated by pipes.

**Auto-scroll.** Charts open with auto-scroll paused. Use **Start** and
**Pause** to move the chart at a steady speed; **Slower** and **Faster** adjust
the rate from 5 to 40 CSS pixels per second in 5-pixel steps, and each chart
opens at 10. Auto-scroll works offline and keeps running when you scroll by
hand, continuing from the new position. It stops at the bottom, and a hidden
page pauses it until you press **Start** again. A frame delay over 1,000 ms is
treated as suspended time, so the selected speed resumes on the next frame
without a catch-up jump. Horizontal panning and left/right song navigation stay
available.

### Create or edit a song

Select **New** on the Songs screen to create a chart. To change an existing
song, open it and select the pencil icon.

The editor supports:

- title (required) and artist;
- written key and original key;
- default capo and BPM;
- a release year, which must be a whole number from 1 to 32767;
- Genres and Vibes;
- a short BeatBuddy arrangement description; and
- the editable chart body.

**Genres and Vibes.** Each category has its own autocomplete field, backed by
the shared managed definitions. Only choosing an existing suggestion adds an
assignment; typing alone never does. If the definition list is unavailable,
assigned chips can still be removed but additions are disabled. One Genre is the
song's primary Genre. Removing it promotes the first remaining Genre (primary
first, then by name), removing every Genre clears the primary, and adding a
secondary Genre keeps the selected primary. Genres and Vibes can be edited
offline too.

**Artist.** The Artist field suggests case-insensitive prefix matches from the
songs cached on the device, including pending and locally created songs, so it
works offline. Typing remains ordinary free text, and a new artist name is
always valid. Use the arrow keys and Enter to choose a suggestion, Escape to
close the list, or tap one on a touch screen.

**Chart text.** The simplest chart format uses bracketed section names followed
by a chord line and lyric line. Spacing in the chord line controls where chords
appear:

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
tablet. Inline chords move with the text they sit in; chords on a separate
chord line stay where they are until you change the spacing:

```text
[Verse]
{G}This is a {C}lyric with {D}inline chords
```

Chord-only progressions such as `G C D 4x`, bare section names such as `Bridge`,
and guitar-tab lines are also recognized. Blank lines inside a chart show as
spacing. Save the song and review the rendered chart to confirm that ambiguous
source spacing was interpreted as intended.

Saving without changing the chart text keeps the stored chart exactly, and
opening a chart never changes its record. Saving with an empty title is
refused.

Deleting a song is permanent once it reaches the server. The editor asks for
confirmation first, and a song created offline and deleted before its first
sync is never sent to the server.

### Build and use a setlist

Setlist changes work offline too: they are saved on the device and sent at
the next sync.

1. Open **Setlists** and select **+**.
2. Enter a name and, optionally, a gig date and notes.
3. Open the new setlist and select **Add song**.
4. Choose a song and optionally record a key override, capo override, and
   performance note. A song can appear in a setlist more than once.
5. Use the up/down controls to change the order, or the trash control to remove
   a song from the setlist.
6. Select a song row to open its chart. Swipe left and right to follow the
   setlist order.
7. Choose **Edit setlist** to change its name, gig date, or notes. Clear the
   date or notes by leaving those fields blank; the name is required, and the
   date is a date only.
8. Choose **Delete setlist** and confirm to remove the setlist and its entries.
   Songs remain in the library.
9. Use the edit control beside an entry to change its key, capo, and
   performance note in place. A key override must be a supported key name (C
   through B, with `#` or `b`, and an optional `m`); blank key/capo fields clear
   those overrides and restore the song defaults, and capo `0` is an explicit
   override.

The setlist row displays its saved key/capo override and performance note.
Opening an entry applies its key/capo overrides, with each missing field
falling back to the song's preferred key or default capo. Adjust the chart and
choose **Save to setlist** to update that entry only; the song's defaults and
chart source are unchanged. Offline, this and every other setlist change is
saved on the device and sent at the next sync.

### Settings

- **Appearance.** Choose **Light**, **Dark**, or **System**. The default is
  System, which follows the device's color-scheme setting while the app is
  open. The choice is stored in this browser on this device, works offline, and
  is not synced. If browser storage is unavailable, the app falls back to System
  for the session. Themes change colors only, so chart alignment and
  auto-scroll positioning are preserved.
- **Chart text size.** Choose **-2 Smallest**, **-1 Smaller**, **Default**,
  **+1 Larger**, or **+2 Largest**. Each step changes chart text by 7.5% (0.85×
  to 1.15× across the range) and is saved on this device. Smaller text can show
  more at once but does not guarantee that a whole song fits without scrolling.
  Chords and lyrics scale together.
- **Manage Genres / Manage Vibes.** Opens one category at a time and lists the
  shared definitions with their current song counts. Names are unique within a
  category, ignoring case and runs of whitespace; a Genre and a Vibe may share a
  name. Creating, renaming, reassigning, and deleting definitions need a
  connection, because they change shared data; offline, the list is shown from
  the last successful load with those controls disabled. Deleting an in-use
  definition requires choosing either to remove those assignments or to
  reassign them to another definition in the same category, then confirming the
  affected-song count. Era is derived from the release year and is not a
  definition. A change advances the version of every affected song, so stale
  queued edits follow the normal server-wins rule. If the server change worked
  but the cache refresh did not, Settings says to run **Sync now**.
- **Sync status.** Connectivity, last-synced time, pending changes, conflicts,
  and library totals, plus **Sync now**.

## Offline use and synchronization

For dependable offline use, open the app while online before leaving for a gig
and confirm a recent **Last synced** time under **Settings**. Use **Sync now** if
you want to refresh immediately. The sync downloads the complete song library
and setlist collection into IndexedDB in that browser profile.

Once cached, you can work offline as follows:

| Action | Offline behavior |
| --- | --- |
| Search, filter, sort, and read cached songs | Available |
| Read cached setlists | Available |
| Create, edit, delete, or save a song key/capo | Saved locally and queued |
| Create or change a setlist or its entries | Saved locally and queued |
| Create, rename, or delete a Genre/Vibe definition | Requires a connection |

When connectivity returns, the app automatically pushes queued song and setlist
changes and then downloads a fresh server snapshot. You can also trigger that
process from **Settings**.

If the same existing song or setlist was changed on both the tablet and the
server, the server version wins. Settings reports that the tablet change was
replaced, and the notice stays visible after a later sync failure or reload.
An offline creation receives a temporary local ID until its first successful
sync. If two editors are open on the same song, the later save replaces the
earlier one; they are not merged.

The app shell can be installed from the browser's PWA/install menu. For
tailnet-only tablet access over trusted HTTPS, follow
[`docs/ANDROID_SETUP.md`](docs/ANDROID_SETUP.md). Direct LAN HTTP is not the
tablet setup: service workers require a trusted HTTPS origin away from
`localhost`.

## Import a `.docx` library

The Python 3.10 pipeline converts Word chord charts to JSON, and its loader
inserts them into the database. The loader is insert-only: an exact existing
title/artist/source match is reported and skipped, while duplicate identities
and same-title/artist entries from another source stop the batch for review. A
rerun does not refresh charts or metadata.

The pipeline has no enrichment step. The earlier enrichment tooling was removed
and is to be rewritten (`docs/REQUIREMENTS.md`, FR-101 onward); until then the
loader does not call outside providers, infer missing metadata, or consume
provider caches.

### Set up

The recommended workflow uses the Python 3.10 Compose service. You need Docker
with Compose and source `.docx` files in `pipeline/docs/`. The container
installs `requirements.txt` and reaches the database through the service name
`db`, so no database address is needed. The app owns the schema: start the app
once before running the pipeline against a fresh volume, because the pipeline
does not define or apply a schema.

Run these commands from `app/`. Build the one-shot pipeline image and check it:

```bash
docker compose --profile pipeline build pipeline
docker compose run --rm pipeline python --version
```

The second command should report Python 3.10.x. Compose starts the database
dependency when a pipeline command needs it. The image contains the pipeline
code and Python dependencies, so rebuild it after changing a pipeline script or
`requirements.txt`. `pipeline/docs/` and `pipeline/parsed_out/` are bind-mounted
to `/pipeline/docs` and `/pipeline/parsed_out`.

Optionally, run the pipeline from host Python 3.10 or newer instead. Create a
virtual environment and install `requirements.txt` (`psycopg` is needed only by
`load_songs.py`; the parsers use the Python standard library). Host Python
reaches this repository's Compose database through the loopback-published port
`localhost:15432` (`DB_PORT` in `app/.env`); the internal `db:5432` address works
only inside Compose. Parsing and loading need no API credentials.

```bash
python -m venv .venv
# Activate .venv for your shell, then:
pip install -r requirements.txt
```

### Import steps

1. Put source `.docx` files in `pipeline/docs/`.
2. Optionally inspect one document. `chart_parser.py` prints a JSON array to
   standard output:

   ```bash
   docker compose run --rm pipeline python chart_parser.py docs/Sample.docx
   ```

3. Parse the corpus:

   ```bash
   docker compose run --rm pipeline \
     python batch_parse.py docs -o parsed_out --per-doc
   ```

   The outputs are `parsed_out/songs.json` (combined song records),
   `parsed_out/report.json` (failures, flagged songs, and corpus statistics),
   and per-document output with `--per-doc`. Use `--strict` to make the command
   fail if any document fails.
4. Review `pipeline/parsed_out/report.json`, especially failures and flagged
   songs, against the source documents before continuing.
5. Dry-run the database load:

   ```bash
   docker compose run --rm pipeline \
     python load_songs.py parsed_out/songs.json --dry-run
   ```

6. Review the complete preview. Only after a fresh backup of the target
   database (see [Backup and restore](#backup-and-restore)) and explicit
   authorization for that exact load, insert the reviewed data:

   ```bash
   docker compose run --rm pipeline \
     python load_songs.py parsed_out/songs.json
   ```

`--database-url URL` selects a database instead of `DATABASE_URL`; confirm the
target before any apply.

Keep real `.docx` files, generated song JSON, database dumps, and secrets out
of source control. The song files can contain copyrighted lyrics, and they are
ignored by Git. Stage B's accepted results, remaining exceptions and the
one-document C1 boundary are recorded in
[the Stage B closeout handoff](docs/STAGE_B_CLOSEOUT_STAGE_C1_HANDOFF_2026-10-20.md).

### How the loader works

**Preview** is read-only. It reports proposed inserts, exact skips, blocking
identity collisions, unresolved categories, and parser/validation failures. It
prints identities and category labels only, never chart bodies. Planning runs in
a repeatable-read, read-only transaction and does not advance sequences.

**Identity.** Title and artist comparisons are case-insensitive (using the
existing `lower()` semantics); `source_document` must match exactly, with NULL
matching NULL. An exact title/artist/source match is reported as a skip before
artist or Genre/Vibe resolution. A same-title/artist record from another source
or multiple matches blocks the whole batch for a decision, and so do repeated
normalized title/artist inputs with different source documents, even when no
matching database song exists. There is no fuzzy matching or aliasing.

**Genres and Vibes.** The loader never creates or renames definitions.
Explicit categories resolve by the app's category-specific normalization (trim
and collapse whitespace, then compare case-insensitively). Missing or ambiguous
labels are reported and left unassigned. The parsed `primary_genre` (from the
cover page, or from the source filename without one) becomes the song's primary
Genre when it resolves; other Genre assignments are non-primary. An unresolved
primary is reported and no primary is set. Parsed JSON with a nonempty primary
Genre but a missing or invalid `primary_genre_source` is rejected before
database access, with an instruction to reparse using the current parser.

**Apply** reruns the same identity and category planner under locks. For each
normalized category/name key it locks all matching definition rows in ascending
ID order with `FOR SHARE`, then takes locks on the artist, song, and assignment
tables in that order. It does not table-lock the definition catalogs, so an
existing definition cannot be renamed or deleted between lookup and insert. The
complete batch commits together or every inserted row rolls back; there are no
per-song savepoints or partial commits, and no `--truncate` or update mode. A
rerun of a committed batch inserts zero rows for existing matches.

**Preflight.** Before opening the database, the loader rejects a batch that
contains any of the following. This also applies to `--dry-run`.

- A song without `parser_diagnostics_version: 1` or without a `warnings` list
  (an empty list is valid). Batch parsing adds both; artifacts made before this
  guard lack them and must be reparsed with `batch_parse.py` from the source
  documents.
- A blocking parser warning: an unresolved Word tab
  (`word_tab_placement_unresolved`), plain-text body
  (`plain_text_body_unresolved`), or ordered chord token
  (`ordered_chord_token_unresolved`), or a conflict between opening metadata
  fields (`opening_metadata_conflict`).
- A song without a `chart_source` (`chart_source_missing`), or whose
  `chart_source`, reparsed with the ordinary chart parser, does not equal its
  `chart_content` exactly. Equality is strict: section and line types, text,
  chord symbols and order, UTF-16 positions, repeat/progression fields,
  spacers, raw content, and confidence fields. Nothing is dropped or normalized
  to make a mismatch pass. The generated `[[CCM-CHART]]` format is no longer
  recognized, so it fails this comparison.
- A song with no artist ("artist must be nonempty text"), unlike the app, which
  stores such a song under "Unknown".

Ordinary low-confidence lines remain review warnings and do not by themselves
block loading.

### How the parser reads Word documents

**Song boundaries.** Only a nonempty effective Word outline heading starts a
song. Outline levels 0–8 are headings; level 9 is body. Direct paragraph
properties override inherited style properties, and the document's default
paragraph style applies when `w:pStyle` is absent. Heading text supplies the
title, artist, and key fields; a missing artist or key stays blank rather than
being inferred. A nonempty heading with unsupported fields is still a song
boundary and carries a located blocking diagnostic, so it cannot silently
become part of the preceding chart. Bold, tabs, title-like wording, dash or
slash notation, `pageBreakBefore`, and explicit or rendered page breaks do not
start songs. An explicit break inside a run keeps the text on both sides as
ordered chart spans; `lastRenderedPageBreak` is layout metadata only.
Meaningful chart-like content before the first heading stops parsing with a
paragraph/span location for review, because ownership is uncertain. A plain
cover line keeps the existing cover-Genre behavior where that input format is
used. Treat parsed rows reconciled against headings as evidence, not proof that
the source contains every intended song.

**Heading keys.** In an en-dash heading, a parenthesized key such as `D (E)` or
the separate tab field `D<TAB>(E)` means the chart is written in D and sounds in
E with a capo: D is the written (performance) key, E is the alternate key, and
the parser derives capo fret 2 from that interval. The artist stays in its own
field. A key pair whose capo relation cannot be derived produces a located
blocking diagnostic.

**Opening metadata.** Before chart content begins, complete bounded `Capo N`,
`key-BPM`, and `orig key` fields fill capo, original key, and BPM independently
of the heading's written and sounding keys. The original-key/BPM token accepts
an ASCII hyphen or an en dash with optional surrounding whitespace (`A-120`,
`A - 120`, `A – 120`). Conflicts stay visible with a located blocking
diagnostic. Arbitrary body text is not treated as metadata, and uncaptured text
is preserved.

**Lines, blanks, and breaks.** Chord columns are zero-based UTF-16 code units,
matching JavaScript string indices, and anchors past the end of a lyric are
kept. Parsing ignores paragraph-spacing attributes. Explicit empty paragraphs
inside a song become spacer lines, and blank padding outside song boundaries is
ignored. Manual line breaks and text-wrapping `w:br`/`w:cr` controls split chart
lines; a terminal break becomes a spacer before the following content, and
consecutive breaks or adjacent empty paragraphs collapse to one spacer. Page
and column breaks are layout controls and create no rows. A spacer before a
section heading becomes the normal section gap; a spacer after a heading stays
explicit and turns the gap off, so spacing is never doubled. Ordinary chart
text edited in the app follows the same blank-line rules, and a chord-only line
is flushed as a progression instead of being paired across a blank line.

**Chord rows.** A chord row stays a literal raw line when the structured
progression form would move punctuation, repeat markers, annotations, or
meaningful spacing away from its source position; ordinary progressions that
preserve token order stay structured. The literal line keeps its separators
(hyphens versus en dashes), and parenthesized or bracketed annotations stay
opaque. Python ingestion and the shared JavaScript editor parser use the same
chord spans and eligibility rule. Recognized spans in a mixed literal row
transpose in place; an unsupported chord-like token stays byte-for-byte
unchanged, the row is marked partial, and a located
`ordered_chord_token_unresolved` diagnostic blocks loading. Rows that contain
prose lookalikes are left unchanged and are not treated as unsupported chord
tokens. Literal chord spans followed by a lyric keep their association in the
chart content, while the whole source row stays visible with its punctuation
and spacing.

**Tabs.** The parser reads inserted tabs from inline run content and ignores
paragraph tab-stop definitions. For a supported tabbed chart paragraph it
advances from the current position to the next effective explicit left or
default stop, then maps that physical distance to a character column using the
Courier New cell width, 1229/2048 em scaled to the effective run size in
half-points (`size * 10 * 1229/2048` twips per cell; 8 pt is 96.015625 twips),
with nearest-column rounding and exact halves rounded up. For paired rows it
maps the chord row's physical position into the lyric row's UTF-16 columns using
that row's effective width, accounting for left paragraph indentation, explicit
left stops, and the 720-twip default grid. The chord token's physical start is
kept through row pairing and rounded once into lyric columns. Font and size can
inherit through paragraph and character-style chains. The following are reported
as unresolved: other fonts, mixed sizes within one tabbed paragraph, non-ASCII
runs in a tabbed paragraph, non-left or cleared stops, unsupported indentation,
character spacing or scaling, missing effective stops, and a missing,
wrong-type, or cyclic style reference on a tabbed row or on a lyric row paired
with one. Unrelated tab-free rows are not blocked by unresolved placement
metadata, and tab-free paragraphs keep the existing extraction and pairing.
This is a bounded rule, not a general Word layout engine; review any new layout
family before relying on it.

The report separates low-confidence space-aligned lines from
`word_tab_placement_unresolved`, which records document, song, one-based
section/line, and a reason code without chart text. It means an inserted tab
uses layout outside the bounded rule; the provisional columns are kept for
diagnosis, but the loader rejects the batch before connecting. Resolve the
flagged source lines, then parse and load again.

**Stored chart text.** New DOCX `chart_source` values are readable ordinary
chart text. Python derives the source from the parsed structure and checks that
ordinary chart syntax can express every line; unsupported structures produce
`plain_text_body_unresolved` and are rejected before loading. A section label's
same-paragraph raw or progression tail stays on that ordinary line when it came
from a bracketed or bare-colon source and the whole chart round-trips exactly;
otherwise the section keeps separate label and body lines. When inline
serialization fails the exact comparison, the parser tries the separate
label/body spelling and keeps it only if the complete chart, confidence fields
included, reparses exactly; otherwise the blocking diagnostic remains. Pipe
notation is kept as raw text where progression normalization would alter it.
The shared JavaScript parser derives `chart_content` from ordinary source on app
load and save.

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

Restore requires an explicit confirmation because it replaces objects in the selected database. It first validates the archive and creates a separate safety backup. If that backup fails, restore stops. Safety backups use the ordinary timestamped name, so they fall under the
normal retention (the newest 14 per database) and are pruned once newer
backups exist. Copy one elsewhere if it must be kept longer.

```powershell
.\scripts\restore.ps1 -Path .\backups\chords-20261009-120000.dump -Force
.\scripts\restore.ps1 -Path .\backups\ccm_backuptest-20261009-120000.dump -Database ccm_backuptest -Force
```

```bash
./scripts/restore.sh --file ./backups/chords-20261009-120000.dump --yes
./scripts/restore.sh --file ./backups/ccm_backuptest-20261009-120000.dump --database ccm_backuptest --yes
```

To schedule a backup, create a Windows Task Scheduler action using `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\Chord_Chart_Manager\scripts\backup.ps1"`, or add a Linux cron entry such as `0 2 * * * /path/to/Chord_Chart_Manager/scripts/backup.sh >> /path/to/backup.log 2>&1`. These are examples only; no scheduled job is created.

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

`/healthz` returns 200 `{"status":"ok"}` when the database answers, and 503
otherwise; `/api/stats` returns the song, tag-definition, and setlist counts.

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

The verified deployment is local Docker Compose; the host ports for the app and
database bind to loopback only. Cloud, Kubernetes, Terraform, and the split
nginx/API topology are out of scope; see `docs/AGENTS.md` → Recorded decisions.
No infrastructure implementation is part of this project phase. A tablet
deployment must keep the client and relative `/api` routes on one trusted HTTPS
origin, because service workers need HTTPS except on `localhost`. A separate
tablet cannot use the secure-context exception by visiting
`http://<computer-ip>:3000`. For the tailnet-only setup, follow
[`docs/ANDROID_SETUP.md`](docs/ANDROID_SETUP.md): Compose publishes both the app
and PostgreSQL on loopback, and Tailscale Serve proxies only the app.

## Developer guide

### Repository layout

```text
Chord_Chart_Manager/
|-- app/                     React PWA, Express API, database, and Compose
|   |-- src/                 screens, components, hooks, cache, and API client
|   |-- server/              Express routes, database access, chart parser
|   |-- db/migrations/       canonical baseline and numbered SQL migrations
|   |-- test/unit/           Vitest unit tests (parser, sync, setlists, sorting)
|   |-- test/integration/    API tests against the scratch ccm_test database
|   `-- test/e2e/            Playwright browser tests
|-- pipeline/                .docx parser and database loader
|   `-- tests/               pytest suite
|-- scripts/                 backup and restore scripts
|-- docs/                    requirements, decisions, handoffs, engineering log
|-- infra/                   proposed Kubernetes/Terraform design (untracked)
`-- README.md                this guide
```

### Source layout

```text
app/
├── src/
│   ├── App.jsx                    navigation and view routing
│   ├── hooks.js                   data loading, gestures, and sync state
│   ├── tagMutations.js            tag normalization and filter matching
│   ├── setlistMutations.js        setlist edit validation
│   ├── setlistOverrides.js        key/capo override resolution
│   ├── setlistQueue.js            queued setlist mutations
│   ├── views/
│   │   ├── HomeView.jsx           song search/filter/create
│   │   ├── ChartView.jsx          chart, transpose, capo, swipe
│   │   ├── EditView.jsx           song create/edit/delete
│   │   ├── SetlistListView.jsx    setlist list/create
│   │   ├── SetlistView.jsx        setlist songs and ordering
│   │   └── SettingsView.jsx       sync status, appearance, definitions
│   ├── components/                tag panel, definition manager, artist field
│   └── lib/
│       ├── api.js                 HTTP client
│       ├── cache.js               IndexedDB and mutation queue
│       ├── songLoading.js         cache-first song loading
│       ├── chartParser.js         browser adapter for shared parser
│       ├── chartRenderer.jsx      structured chart renderer
│       └── autoScroll.js, chartDisplay.js, theme.js, songSort.js, ...
├── server/
│   ├── server.js                  Express routes and static host
│   ├── db.js                      list and export queries
│   ├── db_ext.js                  song writes, definitions, setlist reads
│   ├── setlistMutations.js        setlist writes and receipts
│   ├── tagDefinitionNames.js      definition-name normalization and lookup
│   ├── migrate.js, migrationRunner.js   migrations
│   └── chartParser.js             shared text-to-structure parser
├── db/migrations/                 canonical baseline and numbered migrations
├── Dockerfile
└── docker-compose.yml

pipeline/
├── batch_parse.py                 parse a folder of .docx files
├── chart_parser.py                the .docx parser
├── load_songs.py                  insert-only database loader
└── tests/test_pipeline.py         pytest suite
```

### Architecture

The production Docker image builds the Vite client into `dist/` and serves it
from the same Express process that owns `/api`. Same-origin routing is important
to the PWA's offline behavior. The service worker caches the application shell
and a NetworkFirst API cache with a four-second network timeout; IndexedDB holds
songs, setlists, and sync metadata.

Charts use dual storage:

- `chart_source` is the text a user edits.
- `chart_content` is the parsed structure used to render and transpose chords.

The browser and server share `app/server/chartParser.js` (the build serves the
same file to the browser), so an offline save can render immediately and
produce the same structure that the server will store. The Python `.docx`
importer has a separate whole-document parser. Charts are stored and edited
only as ordinary chart text. The server returns a song with no `chart_source`
as `null`, and a save whose `chart_source` equals the stored value keeps both
`chart_source` and `chart_content` exactly.

**Chart rendering.** Paired chord and lyric rows use the same monospace
character grid, stay unwrapped, and scroll together horizontally. Overlapping
chord labels appear in original order on one row, separated by pipes; each
group starts at its first chord's measured lyric position, noncolliding chords
keep their own measured anchors, and symbols within a group share that first
display anchor while their stored positions are unchanged. Stored columns are
UTF-16 offsets, while the renderer measures the matching rendered grapheme
boundary with a DOM text range rather than assuming one UTF-16 unit equals one
visible `ch`. Range coordinates are converted into the chord row's local CSS
coordinates using its measured rendered-to-layout scale and containing-block
origin, so CSS zoom, row borders, and horizontal scrolling do not change stored
columns. When an old or malformed offset falls inside a surrogate pair or
combining grapheme, the chord is shown at that grapheme's leading edge with a
tooltip and the stored offset is left alone.

An explicit empty chart paragraph is stored as a spacer line and rendered in
normal layout flow, so it counts toward scrolling and element-based height
measurements. Ordinary human-authored chart text also keeps internal blank-line
runs as one spacer; leading and trailing empty lines are dropped, and trailing
spaces on nonblank lyric and raw lines are kept. Chord/lyric pairing does not
cross a blank line. A blank paragraph before a new section uses the automatic
section gap, while a spacer after a section heading stays explicit and
suppresses that gap so the two do not stack.

Pipe-separated chord rows that mix chord tokens with separators, annotations, or
repeat text are kept as literal raw-line text so their order and spacing stay
visible. The renderer transposes only clearly recognized chord tokens outside
parenthesized or bracketed annotations. When an older record still has the old
progression structure, it continues to render that stored structure until the
user changes its chart text in Edit and saves; the corrected parser then derives
the new representation. At tablet widths chart content uses the available width
with 12px side padding, and the chart title and metadata rows are pinned
together as one block.

**Song versions and validation.** Every song carries an `updated_at` version
token: a UTC timestamp with exactly six fractional digits, compared as exact
strings. A song update or delete must send the token it was based on as
`base_updated_at`; a missing or malformed token, including a three-digit one, is
rejected with 400, and a stale one returns 409 `SONG_VERSION_CONFLICT`. The
check runs while the API holds a row lock. Direct song create and update return
`400 INVALID_TAG_INPUT` for malformed Genre/Vibe input, `422
UNKNOWN_TAG_DEFINITION` for a missing definition, or `422
AMBIGUOUS_TAG_DEFINITION` if normalized lookup matches several rows. A 422 is
validation recovery and does not invoke the server-wins conflict policy.
Definition rows are shared-locked in the song-write transaction until the
assignment joins commit, so a concurrent management deletion cannot slip between
validation and assignment. Genre assignments are read primary first, then by
definition name; full-song reads and the offline export use the same
`is_primary DESC, name` order, so sync and reopen keep the same promotion.

**Migrations.** The app runs pending SQL migrations before listening. An empty
database is initialized from `0001_baseline.sql`; a database with application
tables but no recorded migrations stops startup with a diagnostic and is never
adopted. Editing the baseline does not update an existing database, so every
schema change needs the next numbered file. Migration files are plain SQL
named `NNNN_lowercase_name.sql` under `app/db/migrations/`; every `.sql` file in
that directory is a migration candidate. The runner applies each file inside a
transaction it owns, so do not put transaction commands (`BEGIN`, `COMMIT`,
`ROLLBACK`, `SAVEPOINT` and the like) in a migration. It pins its connection to
`public` (with PostgreSQL built-ins resolved from `pg_catalog`), so keep
application objects in `public`. `0002_monotonic_song_version.sql` gives song
updates a strictly increasing microsecond version, including when a writer
waits for a row lock.

**Sync internals.** The Songs screen shows matching cached rows as soon as an
online list request starts, then replaces the clean server projection when it
arrives; pending local create, edit, and delete projections stay applied during
that reconciliation. Offline, the list is read directly from IndexedDB. Sync
runs when the app first opens online, when a connection returns, and on **Sync
now**: it pushes queued song creations, updates, and deletions, then queued
setlist changes, pulls a fresh library and setlist snapshot, and replaces the
local cache with it. Web Locks keep two tabs from syncing at once; where a
browser lacks them, sync runs without a cross-tab lock.

Online editor and chart updates use the same queue and acknowledgement path as
offline changes. Each dispatch freezes its payload and local revision; if a
newer edit arrives in flight, the response advances its base token and leaves
the newer edit queued. Each queued write carries a stable operation ID and a
server receipt, so if the server commits but the response is lost, the retry
returns the original result without applying it twice; reusing an ID with a
different body gets 409. Offline-created songs use temporary `local-*` IDs. A
dispatched create keeps its exact payload under its operation ID so a lost
response can be replayed safely, edits made while it is in flight stay queued
as a follow-up update, and acknowledging the create rewrites dependent cached
setlist entries to the server song ID. Deleting a local song with an uncertain
dispatched setlist-entry request is blocked until sync resolves it. A pull keeps
local projections that still have queued work.

An update that changes only some fields omits the untouched categories from its
payload: a metadata-only save sends no Genres or Vibes, while an earlier queued
change to either category stays in the payload. A definitive 422 keeps the
queued operation for correction, and a rejected offline-created song can be
corrected in the editor and retried under a new operation ID, keeping its local
ID, chart, and unrelated metadata. An uncertain network attempt keeps its frozen
payload and is never unfrozen by that correction. A stale song or setlist
version (409) keeps the server version. The editor saves what it shows, with no
merge between editors.

### Run the client and API outside the app container

Start PostgreSQL and run the app migration command before starting the API
outside its container. Then use two terminals. The server also runs pending
migrations automatically before listening.

With the repository's Compose database, host tools connect through loopback at
`localhost:15432` by default (`DB_PORT` in `app/.env`); containers continue to
use the internal `db:5432` address.

Terminal 1, from `app/server`:

```bash
npm install
DATABASE_URL=postgresql://chords:chords@localhost:15432/chords PORT=3001 npm start
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

The client and the production server use separate lockfiles, and Docker installs
them with `npm ci`. Unit tests are in `app/test/unit/`, API tests in
`app/test/integration/`, and Playwright tests in `app/test/e2e/`. From `app/`,
install the locked dependencies and run the unit tests:

```powershell
npm.cmd ci
npm.cmd --prefix server ci
npm.cmd test
```

The guarded full command builds the client, runs the unit tests and the
scratch-database API tests, then runs the serial Playwright suite against a
harness server backed by a task-owned `ccm_test` database. It requires the local
PostgreSQL settings from `app/.env`, refuses to run if `ccm_test` already
exists, creates and migrates `ccm_test`, and drops only the database it created.
It then checks that the main app is healthy and its `/api/stats` counts are
unchanged. Playwright uses the installed system Chrome (`channel: chrome`), so
install Chrome first. Run the command twice consecutively to check
repeatability:

```powershell
npm.cmd run test:t11
```

`npm.cmd run test:all` is an alias. Raw Playwright and API integration runs
refuse to start outside the isolated harness, so browser fixtures cannot write
to the live `chords` database. Recorded browser coverage is serial installed
system Chrome; parallel execution, bundled Chromium, and a physical Android
tablet remain unverified. On Windows systems where PowerShell blocks `npm.ps1`,
use `npm.cmd` and `npx.cmd`; on macOS or Linux use `npm`.

Dependency checks:

```bash
npm audit
cd server
npm audit --omit=dev
```

**Pipeline tests.** The pipeline image installs the small test-only dependency
set from `requirements-test.txt` (currently pytest). For each run, choose a
unique database name matching `ccm_loader_c1_<suffix>`, verify that exact name is
absent, create it under the active Compose database role, and record a
task-specific comment on it. Initialize only that database with the app's
migration runner, then run pytest from `app/`:

```bash
docker compose exec -T db psql -U chords -d postgres -c "CREATE DATABASE $PIPELINE_TEST_DATABASE"
docker compose exec app npm --prefix /app/server run migrate -- --database "$PIPELINE_TEST_DATABASE"
docker compose run --rm --no-deps -e PIPELINE_TEST_DATABASE="$PIPELINE_TEST_DATABASE" pipeline pytest -q
```

Rebuild the pipeline image first if you changed a pipeline file. The parser
tests build a synthetic `.docx` in a temporary directory from invented text. The
loader integration test checks all application tables and sequences across
read-only preview, insert/skip, collision, atomic rollback, and concurrent
loads. The guard refuses names outside the `ccm_loader_c1_` prefix. Afterward,
drop only the database you created, after checking its comment and confirming it
is gone, and never point the test at `chords`.

Every test name (or, for pytest, the comment above it) starts with the
requirement IDs it covers, from `docs/REQUIREMENTS.md`.

## Troubleshooting

### The app opens but there are no songs

This is normal for a new database. Create a song in the UI or run the pipeline
loader. Check the current count with `curl http://localhost:3000/api/stats`.

### Adding a database schema change

Migrations run before the API starts listening. Add the next numbered plain SQL
file under `app/db/migrations/` (for example, `0005_add_song_field.sql`), then
restart or rebuild the app. See [Migrations](#architecture) for the file rules.
Run migrations explicitly with
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

## Current validation status

Verified on 2026-10-29 at the end of the simplification refactor:

- `npm.cmd run test:t11` passed the production build, 153 unit tests, 9 API
  integration tests, and 52 serial Playwright E2E tests against a scratch
  `ccm_test` database, which was dropped afterwards. The main app stayed
  healthy and its `/api/stats` counts were unchanged.
- The pipeline image passed 112 pytest tests against a scratch
  `ccm_loader_c1_*` database.

Not yet verified: a full real-corpus import (FR-099), the physical-tablet
checklist (FR-100), and enrichment, which was removed and is to be rewritten
(FR-092, FR-093, FR-101 to FR-115).

## Project documents

These live in `docs/` (ignored by Git):

- [`docs/HANDOFF.md`](docs/HANDOFF.md): the current development checkpoint.
- `docs/REQUIREMENTS.md`: the functional and non-functional requirements with
  their test coverage, and `docs/OPEN_QUESTIONS.md`: the owner's decisions.
- `docs/AGENTS.md`: recorded decisions and working rules.
- `docs/ANDROID_SETUP.md`: tablet setup over the tailnet.
- `docs/EngineeringLog.md` and `docs/TASKS.md`: the change history.
- `docs/REFACTOR_REPORT.md`: the 2026 simplification refactor.
