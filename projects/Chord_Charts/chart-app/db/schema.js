/**
 * db/schema.js
 *
 * Initializes the SQLite database and creates all tables.
 * Uses sql.js (pure JS — no native build needed).
 *
 * Usage:
 *   const { getDb, saveDb } = require('./db/schema');
 *   const db = await getDb('./charts.db');
 *   // ... queries ...
 *   saveDb(db, './charts.db');
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const initSqlJs = require('sql.js');

// ---------------------------------------------------------------------------
// DDL
// ---------------------------------------------------------------------------
const SCHEMA_SQL = `
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ── songs ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS songs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,

  -- Identity
  title               TEXT NOT NULL,
  artist              TEXT,

  -- Keys
  chart_written_key   TEXT,          -- chords as written on page  (e.g. G)
  sounding_key        TEXT,          -- chart_written_key + capo   (e.g. A)
  original_key        TEXT,          -- recording key, if noted    (e.g. Ab)
  preferred_key       TEXT,          -- user's go-to key           (defaults to chart_written_key)

  -- Capo & tempo
  default_capo        INTEGER DEFAULT 0,   -- 0 = no capo
  bpm                 INTEGER,             -- nullable

  -- Metadata (AI-tagged, user-confirmable)
  genre               TEXT,
  feel                TEXT,
  era                 TEXT,
  tempo_feel          TEXT,

  -- BeatBuddy
  beatbuddy_structure TEXT,

  -- Chart content (raw text from DOCX, kept for re-parsing)
  chart_content       TEXT NOT NULL,

  -- Source tracking
  source_file         TEXT,          -- original DOCX filename
  cloud_provider      TEXT,          -- 'gdrive' | 'dropbox' | 'local'
  cloud_file_id       TEXT,          -- provider's file ID for sync

  -- AI confidence (0–1, per field average)
  ai_confidence       REAL,

  -- Timestamps
  imported_at         DATETIME DEFAULT (datetime('now')),
  synced_at           DATETIME,
  user_edited_at      DATETIME,

  -- Offline edit flag (tablet-side use)
  edited_offline      INTEGER DEFAULT 0   -- boolean
);

CREATE INDEX IF NOT EXISTS idx_songs_title  ON songs(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_songs_genre  ON songs(genre);
CREATE INDEX IF NOT EXISTS idx_songs_era    ON songs(era);
CREATE INDEX IF NOT EXISTS idx_songs_feel   ON songs(feel);

-- ── tags ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  category    TEXT,                  -- user-defined grouping (nullable)
  created_at  DATETIME DEFAULT (datetime('now')),
  UNIQUE(name COLLATE NOCASE)        -- case-insensitive uniqueness
);

-- ── song_tags ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS song_tags (
  song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  tag_id      INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
  PRIMARY KEY (song_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_song_tags_tag  ON song_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_song_tags_song ON song_tags(song_id);

-- ── setlists ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS setlists (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  gig_date    DATE,
  notes       TEXT,
  created_at  DATETIME DEFAULT (datetime('now')),
  updated_at  DATETIME DEFAULT (datetime('now'))
);

-- ── setlist_songs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS setlist_songs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  setlist_id      INTEGER NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
  song_id         INTEGER NOT NULL REFERENCES songs(id)    ON DELETE CASCADE,
  position        INTEGER NOT NULL,          -- 1-based order in setlist
  transposed_key  TEXT,                      -- if different from song's preferred_key
  capo_fret       INTEGER,                   -- overrides song default_capo for this gig
  sounding_key    TEXT,                      -- computed: transposed_key + capo_fret
  notes           TEXT,                      -- per-gig notes on this song
  UNIQUE(setlist_id, position)
);

CREATE INDEX IF NOT EXISTS idx_setlist_songs_setlist ON setlist_songs(setlist_id);
CREATE INDEX IF NOT EXISTS idx_setlist_songs_song    ON setlist_songs(song_id);

-- ── import_log ───────────────────────────────────────────────────────────────
-- Tracks every import run so re-imports are idempotent
CREATE TABLE IF NOT EXISTS import_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file   TEXT NOT NULL,
  song_id       INTEGER REFERENCES songs(id) ON DELETE SET NULL,
  status        TEXT NOT NULL,    -- 'ok' | 'skipped' | 'error'
  message       TEXT,
  imported_at   DATETIME DEFAULT (datetime('now'))
);
`;

// ---------------------------------------------------------------------------
// DB lifecycle
// ---------------------------------------------------------------------------

let _SQL = null;  // cached sql.js instance

async function getSqlJs() {
  if (!_SQL) _SQL = await initSqlJs();
  return _SQL;
}

/**
 * Load or create the database at dbPath.
 * Returns a sql.js Database object.
 */
async function getDb(dbPath) {
  const SQL = await getSqlJs();
  let db;

  if (fs.existsSync(dbPath)) {
    const fileBuffer = fs.readFileSync(dbPath);
    db = new SQL.Database(fileBuffer);
  } else {
    db = new SQL.Database();
  }

  // Apply schema (CREATE IF NOT EXISTS — safe to run repeatedly)
  db.run(SCHEMA_SQL);

  return db;
}

/**
 * Persist the in-memory sql.js database back to disk.
 * Call after any write operations.
 */
function saveDb(db, dbPath) {
  const data = db.export();
  const buffer = Buffer.from(data);
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  fs.writeFileSync(dbPath, buffer);
}

module.exports = { getDb, saveDb, SCHEMA_SQL };
