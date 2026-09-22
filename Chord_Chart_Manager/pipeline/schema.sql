-- ============================================================
-- Chord Chart App — Postgres Schema (v1)
-- ============================================================
-- Design principle: normalize the fields you filter/search on
-- (artist, genre, vibe), keep the chart body itself as
-- structured JSON so the app layer can transpose / reflow it
-- without a chord-by-chord relational model.

-- ---------- Lookup tables (drive faceted search) ----------

CREATE TABLE artists (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL
);
CREATE UNIQUE INDEX artists_name_lower_idx ON artists (LOWER(name));

CREATE TABLE genres (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL
);
CREATE UNIQUE INDEX genres_name_lower_idx ON genres (LOWER(name));

CREATE TABLE vibes (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL
);
CREATE UNIQUE INDEX vibes_name_lower_idx ON vibes (LOWER(name));

-- ---------- Core songs table ----------

CREATE TABLE songs (
    id                  SERIAL PRIMARY KEY,
    title               TEXT NOT NULL,
    artist_id           INTEGER NOT NULL REFERENCES artists(id),
    -- genre is many-to-many; see song_genres junction below.
    -- The genre from a doc's cover page becomes the PRIMARY genre at
    -- migration time (is_primary = true), and additional genres can be
    -- layered on afterward without losing which binder the song came from.

    original_key        TEXT,               -- e.g. 'G', 'Bb', 'F#m'
    performance_key      TEXT,               -- key the chart is written in (a.k.a. chart_written_key in the app)
    preferred_key       TEXT,               -- key the player prefers to perform in (may differ from written)
    alt_key             TEXT,               -- parenthetical alternate key on the title line, e.g. '(E)' -> 'E'
    capo_fret           SMALLINT DEFAULT 0 CHECK (capo_fret BETWEEN 0 AND 11),

    bpm                 SMALLINT CHECK (bpm IS NULL OR bpm > 0),
    release_year        SMALLINT,           -- backs "era" filtering, see note below

    bb_structure        TEXT,               -- e.g. 'Intro-V-C-V-C-B-C-Outro' (BeatBuddy)

    -- Structured chart body: sections -> lines -> lyric + chord positions.
    -- Keeping this as JSONB (vs. a fully normalized chord table) is what
    -- makes transpose and page-fit reasonable to implement — transpose only
    -- rewrites the "chord" values, page-fit only touches layout/rendering,
    -- neither has to touch relational rows.
    --
    -- Example shape:
    -- {
    --   "sections": [
    --     { "label": "Verse 1",
    --       "lines": [
    --         { "lyric": "Amazing grace how sweet the sound",
    --           "chords": [ {"chord": "G", "pos": 0}, {"chord": "C", "pos": 8} ] }
    --       ]
    --     }
    --   ]
    -- }
    chart_content        JSONB NOT NULL,

    -- Editable source of truth: the raw chart text the user types/edits.
    -- On save, the app re-parses this into chart_content (the structured JSONB
    -- above) so rendering + transpose stay clean while editing stays natural.
    -- Nullable because migrated songs may be backfilled from the docs.
    chart_source         TEXT,

    source_document      TEXT,               -- original .docx filename (= the genre binder it came from), for migration provenance
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX songs_artist_idx  ON songs (artist_id);
CREATE INDEX songs_bpm_idx     ON songs (bpm);
CREATE INDEX songs_year_idx    ON songs (release_year);
CREATE INDEX songs_title_idx   ON songs (LOWER(title));

-- ---------- Vibe/feel: many-to-many (a song can be "chill" AND "romantic") ----------

CREATE TABLE song_vibes (
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    vibe_id     INTEGER NOT NULL REFERENCES vibes(id) ON DELETE CASCADE,
    PRIMARY KEY (song_id, vibe_id)
);

-- ---------- Genre: many-to-many (a song can be rock AND classic-rock) ----------
-- is_primary marks the genre from the song's origin doc (its cover-page
-- binder). Exactly one primary per song is enforced by the partial unique
-- index below; additional non-primary genres are unconstrained in count.

CREATE TABLE song_genres (
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    genre_id    INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    is_primary  BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (song_id, genre_id)
);

CREATE INDEX song_genres_genre_idx ON song_genres (genre_id);
-- at most one primary genre per song:
CREATE UNIQUE INDEX song_genres_one_primary_idx
    ON song_genres (song_id) WHERE is_primary;

-- ---------- Setlists: ordered, gig-oriented collections of songs ----------
-- A setlist is a performance running order. Each entry can override the key
-- and capo for that gig (e.g. same song, different key for a given singer)
-- without touching the song's own defaults.

CREATE TABLE setlists (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    gig_date    DATE,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE setlist_songs (
    setlist_id      INTEGER NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    song_id         INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,          -- 1-based running order
    transposed_key  TEXT,                      -- per-gig key override (null = song default)
    capo_fret       SMALLINT CHECK (capo_fret IS NULL OR capo_fret BETWEEN 0 AND 11),
    notes           TEXT,                      -- per-song performance note
    PRIMARY KEY (setlist_id, position)
);
CREATE INDEX setlist_songs_song_idx ON setlist_songs (song_id);

-- ---------- Keep updated_at honest ----------

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER songs_set_updated_at
    BEFORE UPDATE ON songs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER setlists_set_updated_at
    BEFORE UPDATE ON setlists
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- Notes ----------
-- 1. "Era" isn't a field you listed — it's derived from release_year
--    (e.g. (release_year / 10) * 10 || 's') rather than stored directly.
--    If you'd rather tag era manually (some songs' "vibe era" != release
--    year, e.g. a 2020s song written in a 60s style), add an era_id FK to
--    a small `eras` lookup table instead — same pattern as genres.
-- 2. For fuzzy/typo-tolerant artist & title search later, add the
--    pg_trgm extension and a GIN trigram index — not needed at ~2000 rows.
-- 3. Genre is many-to-many via song_genres. At migration time, each doc's
--    cover-page genre is written as the song's is_primary genre; you can add
--    more genres per song later. To query "primary genre" (e.g. to reproduce
--    the original binder view), filter song_genres WHERE is_primary.
