# T01 — Post-revert audit

Audit date: 2026-10-09  
Evidence base: current tracked code at `4381e2e`; file references are relative to `Chord_Chart_Manager/`.

## 1. Git state

- Starting `git status --short -- Chord_Chart_Manager` was empty for reported changes. The `docs/` directory and its existing `TASKS.md`/`EngineeringLog.md` files were already ignored by `Chord_Chart_Manager/.gitignore:40`; this audit edits only the authorized T01 entries there. Consequently ordinary `git status` and `git diff` cannot show those document edits. The audit does not change `.gitignore` or stage files.
- The last ten repository commits at audit time were: `4381e2e` Phase 4 close prep; `7acd280` Refuse an indefinite mode override unless asked for; `dfe6c52` Updated gitignore; `d719616` Bat bot becomes the payload for any cleared run; `1aa212d` Added engineering log; `6603ca4` Updated gitignore to include new documentation; `824c4cc` Updated gitignore; `aa87d28` Restore all missing documentation; `af67c42` Merge branch main; `4c279d7` Revert "Added engineering log".
- The revert commit `4c279d7` only removes documentation (including `Chord_Chart_Manager/EngineeringLog.md`); its stat contains no app or pipeline code. The current application/pipeline snapshot is reachable at `eaa985b26c9e6b7842c81c74289d0668524a6e04`, which moved those files into `Chord_Chart_Manager/`. No separate reachable commit containing code removed by the reported revert was identified. Reflog entries include `350997f` (revert of the engineering-log addition) and `4c279d7`; neither establishes a missing application-code snapshot. T03 should not replay a commit based on the current evidence.
- Read-only commands used: `git status --short`, `git log -10 --oneline`, `git log --all --oneline -- Chord_Chart_Manager`, `git show --stat 4c279d7`, and `git reflog -20 --oneline`.

## 2. Feature inventory

| Feature | Expected | Evidence (file:line) | Present / Partial / Missing | Notes |
| --- | --- | --- | --- | --- |
| IndexedDB cached data and sync state | Songs, setlists, pending song changes, sync metadata and conflicts | `app/src/lib/cache.js:21-44,141-170,188-205` | Present | Stores include songs, setlists, metadata and dirty song IDs; server-wins conflicts are recorded in metadata. |
| Search by title/artist; filter by tags | Search and categorized multi-tag filtering | `app/src/views/HomeView.jsx:8-14,49-79`; `app/server/server.js:78-99` | Present | Cache search is also implemented in `app/src/lib/cache.js:289-308`. |
| Create, edit, delete songs | CRUD in the client, including offline song changes | `app/src/views/EditView.jsx:31-78,80-105`; `app/server/server.js:108-128` | Present | Offline mutations are retained in IndexedDB and marked dirty (`app/src/lib/cache.js:96-110,152-167`). |
| Render structured charts | Render parsed chart structure | `app/src/views/ChartView.jsx:71-75,117`; `app/src/lib/chartRenderer.jsx:75-` | Present | `chart_content` is JSONB and `chart_source` is editable text (`app/db/init/01-schema.sql:68-75`). |
| Transpose and adjust capo | Client-side performance controls | `app/src/views/ChartView.jsx:65-82,139-` | Present | Transposition operates on structured chord data. |
| Save preferred key/default capo | Persist separately from written key/source | `app/src/views/ChartView.jsx:86-108`; `app/db/init/01-schema.sql:40-45` | Present | Offline save uses the existing song mutation path. |
| Create setlists with date and notes | Setlist metadata is editable at creation | `app/src/views/SetlistListView.jsx:20-` | Present | API insert uses name, gig date, and notes (`app/server/db_ext.js:324-330`). |
| Add songs with key/capo/note overrides | Per-entry values persist and display | `app/src/views/SetlistView.jsx:62-76,150-181`; `app/db/init/01-schema.sql:125-132` | Partial | Values are stored, but opening an entry discards override fields: `app/src/App.jsx:95-98` passes only song ID/title and `ChartView` initializes from the song (`app/src/views/ChartView.jsx:36-46`). T07 addresses this. |
| Reorder and remove setlist entries | Up/down ordering and removal | `app/src/views/SetlistView.jsx:48-59,88-105`; `app/server/server.js:156-166` | Present | Online API actions exist. |
| View cached setlists offline | Read cached list/detail while offline | `app/src/hooks.js:151-169`; `app/src/views/SetlistView.jsx:26-39`; `app/src/lib/cache.js:114-136` | Present | Setlist mutations themselves are not queued. |
| Offline song create/update/delete and immediate rendering | IndexedDB cache plus pending changes | `app/src/views/EditView.jsx:31-78,80-105`; `app/src/lib/cache.js:96-110,209-254` | Present | Client parses edited chart text before caching (`EditView.jsx:60-70`). |
| Delete an offline-created song before its first sync | Remove local record without sending a server delete | `app/src/views/EditView.jsx:88-90` | Present | The local create mutation is removed from cache/dirty state. |
| Temporary IDs and push-before-pull sync | Local IDs map after create; push pending before refreshing snapshot | `app/src/lib/cache.js:106-110,199-205,209-275` | Present | Sync state and reconnect trigger are in `app/src/hooks.js:174-224`. |
| Reconnect/manual sync and server-wins conflicts | Retry sync on reconnect; HTTP 409 retains server version and reports conflict | `app/src/hooks.js:198-224`; `app/server/db_ext.js:19-30,115-172,174-197`; `app/src/lib/cache.js:188-198,224-252`; `app/src/views/SettingsView.jsx:53-92` | Present | This is code inspection only; not exercised against a live API/browser in T01. |
| Offline setlist changes | All setlist writes should queue and sync | `app/src/lib/cache.js:257-275` syncs songs plus fetches setlists; `app/src/views/SetlistView.jsx:48-105` calls API directly | Missing | Explicitly absent from current implementation and already scoped by T10 under the recorded offline decision. |
| Editable source and structured chart data | `chart_source` is editable; `chart_content` drives rendering/transposition | `app/db/init/01-schema.sql:68-75`; `app/src/views/EditView.jsx:21-22,58-62`; `app/src/views/ChartView.jsx:71-75` | Present | Both representations are stored; save reparses source to structure. |
| Shared JavaScript parser in server/browser | One parser source through Vite bridge | `app/server/chartParser.js:1-21`; `app/vite.config.js:8-34`; `app/src/lib/chartParser.js:1-10` | Present | Python `.docx` parser is a separate implementation and may drift (`pipeline/chart_parser.py`). |

## 3. Historical fixes

| Historical item | Evidence (file:line) | Present / Partial / Missing | Notes |
| --- | --- | --- | --- |
| Loader dry-run rolls back the full transaction | `pipeline/load_songs.py:192-233` | Present | Outer transaction wraps per-song savepoints and calls rollback for dry-run. Code inspection only; no database run. |
| Vite parser bridge | `app/vite.config.js:8-34`; `app/src/lib/chartParser.js:1-10` | Present | The browser bridge transforms the shared CommonJS parser source. |
| Expanded export for offline sync | `app/server/db.js:131-180`; `app/server/server.js:53-61` | Present | Export selects the chart, performance fields, timestamps, facets and tags. |
| Lockfiles and deterministic `npm ci` | `app/package-lock.json`; `app/server/package-lock.json`; `app/Dockerfile:6-7,17-18` | Present | Docker installs both client and server from lockfiles. |
| Playwright development-only dependency and smoke test | `app/package.json:6-13,19-24`; `app/test/e2e/app-shell.spec.js:1-` | Present | `@playwright/test` is declared in `devDependencies`; the script's working install/runtime was not exercised. |
| Python 3.10 pipeline image | `pipeline/Dockerfile:1-12` | Present | Image is `python:3.10-slim`; no image was built. |
| Relative paths / portable Compose contexts | `app/docker-compose.yml:30-32,52-62`; `app/Dockerfile:6-28` | Present | Paths are relative to `app/` and its sibling `pipeline/`. Compose is `docker-compose.yml`, not `compose.yaml`. |

## 4. Ground truth for conflicting docs

- Compose file: `app/docker-compose.yml` (`app/docker-compose.yml:1-8`). Run Compose from `Chord_Chart_Manager/app`, where the build context `.` and `../pipeline` bind paths resolve (`app/docker-compose.yml:30-32,52-62`). The repository does not contain `compose.yaml`.
- Environment template: `app/.env.example`. Put the runtime `.env` beside the Compose file at `app/.env`; Compose substitutes values such as `APP_PORT`, DB credentials, and enrichment config (`app/docker-compose.yml:7,13-18,34-37,57-59`). `.env` existence was not inspected to avoid exposing its contents; no values were read.
- Unit tests: `app/test/unit/` (`app/test/unit/chartParser.test.js`, `app/test/unit/transpose.test.js`). E2E: `app/test/e2e/` (`app/test/e2e/app-shell.spec.js`). The old `app/tests/` and `app/e2e/` map is stale.
- Actual client npm scripts from `app/package.json:6-14`: `dev`, `build`, `preview`, `test` (`vitest run`), `test:watch`, `test:e2e` (`playwright test`), and `test:all` (unit then e2e). Playwright is declared as a dev dependency (`app/package.json:19-24`). A clean install/runtime was not exercised.

## 5. Swipe

`app/src/hooks.js:23-44` records touch coordinates and triggers only a horizontal gesture over 60 pixels whose horizontal distance is more than 1.5 times the vertical distance. Negative `dx` calls `onSwipeLeft` and positive `dx` calls `onSwipeRight` (`hooks.js:32-40`). `app/src/views/ChartView.jsx:48-60` binds left to next and right to previous. This matches the recorded decision in `docs/AGENTS.md`; no behavior change is proposed.

## 6. Data model notes

`app/db/init/01-schema.sql` defines these eight tables and columns:

| Table | Columns |
| --- | --- |
| `artists` | `id`, `name` |
| `genres` | `id`, `name` |
| `vibes` | `id`, `name` |
| `songs` | `id`, `title`, `artist_id`, `original_key`, `performance_key`, `preferred_key`, `alt_key`, `capo_fret`, `bpm`, `release_year`, `bb_structure`, `chart_content`, `chart_source`, `source_document`, `created_at`, `updated_at` |
| `song_vibes` | `song_id`, `vibe_id` |
| `song_genres` | `song_id`, `genre_id`, `is_primary` |
| `setlists` | `id`, `name`, `gig_date`, `notes`, `created_at`, `updated_at` |
| `setlist_songs` | `setlist_id`, `song_id`, `position`, `transposed_key`, `capo_fret`, `notes` |

Evidence for table/column definitions: `app/db/init/01-schema.sql:11-151`.

Tags are represented by `genres` and `vibes`, with `song_genres` and `song_vibes` many-to-many tables. Era is derived from `songs.release_year`; `getTags()` synthesizes genre, vibe, and era categories (`app/server/db_ext.js:228-253`). API routes list/update tags and add/remove song tags (`app/server/server.js:45-76`).

`setlists.updated_at` exists and has a before-update trigger; `setlist_songs` has neither `updated_at` nor a trigger (`app/db/init/01-schema.sql:116-151`). Existing setlist-entry add/remove/reorder SQL updates only `setlist_songs` (`app/server/db_ext.js:349-415`), so it does not bump the parent timestamp. Song tag add/remove writes only the junction tables and does not update `songs.updated_at` (`app/server/db_ext.js:257-290`); T09 must explicitly ensure tag edits bump it for conflict detection.

The two schema files have identical SHA-256 hashes: `0FFF64AAB019A3C63278765969C07164A9223C97A1F9CE394D905C13E968A143` for `app/db/init/01-schema.sql` and `pipeline/schema.sql` (diff summary: no differences). The pipeline image copies `schema.sql` (`pipeline/Dockerfile:9-12`), but `load_songs.py` does not read or execute it; the loader assumes a previously initialized database. No recording MBID or per-field enrichment-provenance columns exist in the current schema; source-specific fields are limited to general song columns such as `bpm`, keys, `release_year`, genres and vibes (`app/db/init/01-schema.sql:31-78`).

## 7. Test inventory

| Test file | Coverage |
| --- | --- |
| `app/test/unit/chartParser.test.js` | Structured sections, chord-over-lyric parsing, inline chord parsing and text round-trip. |
| `app/test/unit/transpose.test.js` | Chord transposition, flat spelling, key deltas/sounding keys, and chart transposition without lyric changes. |
| `app/test/e2e/app-shell.spec.js` | Production app shell loads in a browser. |

The repository currently has two unit test files and one E2E spec. T01 did not run tests, build the app, start Compose, or open a browser, per task instructions.

## 8. Restore candidates

| Candidate | Size | Recovery source / status |
| --- | --- | --- |
| Apply setlist-entry key/capo overrides when opening and navigating charts | M | Not a revert loss: the current UI passes only song ID/title to `ChartView` (`app/src/App.jsx:95-98`), and this gap is directly specified in T07. Implement from T07; no restore SHA required. |
| Setlist rename/delete and in-place entry override/note editing | M | API update/delete exists, but UI does not expose it; T08 is the scoped rebuild specification. No removed-code SHA identified. |
| Edit tags online/offline with song timestamp updates | M | Server tag endpoints exist, but `EditView` does not expose tag controls; writes do not bump `songs.updated_at` (`app/server/db_ext.js:257-290`). T09 is the scoped rebuild specification. No removed-code SHA identified. |
| Queue setlist create/edit/add/remove/reorder offline with server-wins sync | L | Missing by design in current cache/API flow; T10 is the scoped rebuild spec. No removed-code SHA identified. |

These are planned product gaps, not code proven to have been removed by the revert. The reachable application snapshot is `eaa985b26c9e6b7842c81c74289d0668524a6e04`; `4c279d7` only reverted documentation.

## 9. Proposed task changes

1. **T03:** no application-code restore is supported by reachable commit evidence. Keep it waiting for user-approved candidates, and avoid treating the setlist override, tag editor, or offline setlist queue as revert losses; T07/T09/T10 already own them.
2. **T09:** retain the existing requirement that every tag change bumps `songs.updated_at`; current add/remove routes do not do this. This can likely be an explicit song-row update in the transaction and need not imply a migration by itself.
3. **T10:** add a requirement that every setlist entry mutation bumps `setlists.updated_at`; the parent timestamp exists, but entry operations currently do not update it.
4. **T13 enrichment transition:** replace step 9's GetSongBPM backlink requirement with the approved MusicBrainz → AcousticBrainz → Gemini fallback. Scope the smallest spec change to T13 and supporting configuration/docs: retain parsed/manual values and fill only gaps; select and retain a suitable MusicBrainz recording MBID (do not silently match covers/live/remixes); treat AcousticBrainz values as audio-analysis results and verify live endpoint/corpus coverage separately; Gemini estimates only remaining gaps using validated structured output, explicit unknowns, metadata only, bounded retries, and an explicit call cap. Missing Gemini credentials must not block MusicBrainz/AcousticBrainz, and parsing stays credential-free. Keep field-level provenance, preserve existing caches without relabeling old GetSongBPM entries, and review attribution if any such values are already loaded. T14 remains enrichment-off.
5. **Schema/config/documentation follow-up:** no recording MBID or field-level provenance columns are present, so inspect a minimal schema extension through T06's migration mechanism before implementing storage. Update `pipeline/enrich_songs.py`, `pipeline/README.md`, root README, `app/.env.example`, and Compose pipeline environment (remove GetSongBPM config, add only needed MusicBrainz/AcousticBrainz/Gemini config). Update attribution UI only if retained GetSongBPM-sourced values are actually in use. Preserve `pipeline/parsed_out/enrich_cache.json`; do not reinterpret its existing shape as AcousticBrainz. Actual AcousticBrainz access and coverage remain unverified without a separately authorized live check.
6. **T04:** fix Compose and test path instructions to `app/docker-compose.yml`, commands from `app/`, `.env` in `app/`, and tests under `app/test/`. Also reconcile historical GetSongBPM text when the new T13 spec/config is agreed.

No task other than T01 was edited during this audit.
