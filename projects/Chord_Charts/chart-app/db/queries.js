/**
 * db/queries.js
 *
 * All database read/write operations.
 * The API layer calls these; nothing else touches the DB directly.
 *
 * Every function takes (db) as first argument — the sql.js Database instance.
 */

'use strict';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Run a SELECT and return all rows as plain objects */
function all(db, sql, params = []) {
  const stmt = db.prepare(sql);
  stmt.bind(params);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();
  return rows;
}

/** Run a SELECT and return the first row, or null */
function get(db, sql, params = []) {
  const rows = all(db, sql, params);
  return rows[0] || null;
}

/** Run an INSERT/UPDATE/DELETE, return { changes, lastInsertRowid } */
function run(db, sql, params = []) {
  db.run(sql, params);
  const info = db.exec('SELECT changes() as c, last_insert_rowid() as id');
  if (info.length === 0) return { changes: 0, lastInsertRowid: null };
  const row = info[0].values[0];
  return { changes: row[0], lastInsertRowid: row[1] };
}

// ---------------------------------------------------------------------------
// Songs
// ---------------------------------------------------------------------------

/**
 * Insert a parsed song record.
 * Returns the new song id.
 */
function insertSong(db, song) {
  const { lastInsertRowid } = run(db, `
    INSERT INTO songs (
      title, artist,
      chart_written_key, sounding_key, original_key, preferred_key,
      default_capo, bpm,
      genre, feel, era, tempo_feel,
      beatbuddy_structure,
      chart_content,
      source_file, cloud_provider, cloud_file_id,
      ai_confidence
    ) VALUES (
      ?, ?,
      ?, ?, ?, ?,
      ?, ?,
      ?, ?, ?, ?,
      ?,
      ?,
      ?, ?, ?,
      ?
    )`,
    [
      song.title,           song.artist,
      song.chartWrittenKey, song.soundingKey,   song.originalKey, song.preferredKey,
      song.defaultCapo,     song.bpm ?? null,
      song.genre ?? null,   song.feel ?? null,  song.era ?? null, song.tempoFeel ?? null,
      song.beatbuddyStructure ?? null,
      song.chartContent,
      song.sourceFile ?? null, song.cloudProvider ?? null, song.cloudFileId ?? null,
      song.aiConfidence ?? null,
    ]
  );
  return lastInsertRowid;
}

/**
 * Update an existing song's metadata fields.
 * chartContent and source fields are not touched here — use updateChartContent for that.
 */
function updateSong(db, id, fields) {
  const allowed = [
    'title','artist','chart_written_key','sounding_key','original_key','preferred_key',
    'default_capo','bpm','genre','feel','era','tempo_feel','beatbuddy_structure',
    'ai_confidence','user_edited_at','edited_offline',
  ];
  const updates = Object.entries(fields)
    .filter(([k]) => allowed.includes(k))
    .map(([k]) => `${k} = ?`);
  const values = Object.entries(fields)
    .filter(([k]) => allowed.includes(k))
    .map(([, v]) => v);
  if (updates.length === 0) return;
  run(db, `UPDATE songs SET ${updates.join(', ')} WHERE id = ?`, [...values, id]);
}

function updateChartContent(db, id, chartContent) {
  run(db, `UPDATE songs SET chart_content = ?, synced_at = datetime('now') WHERE id = ?`,
    [chartContent, id]);
}

function deleteSong(db, id) {
  run(db, `DELETE FROM songs WHERE id = ?`, [id]);
}

function getSongById(db, id) {
  return get(db, `SELECT * FROM songs WHERE id = ?`, [id]);
}

function getSongByTitleArtist(db, title, artist) {
  return get(db,
    `SELECT * FROM songs WHERE title = ? COLLATE NOCASE AND artist = ? COLLATE NOCASE`,
    [title, artist]);
}

/**
 * Full song search + filter.
 * filters: { query, genre, feel, era, tags: [tagName], artist }
 * sort: 'title' | 'artist' | 'era'
 */
function searchSongs(db, { query, genre, feel, era, artist, tags } = {}, sort = 'title') {
  const conditions = [];
  const params = [];

  if (query) {
    conditions.push(`(s.title LIKE ? OR s.artist LIKE ?)`);
    params.push(`%${query}%`, `%${query}%`);
  }
  if (genre)  { conditions.push(`s.genre = ?`);  params.push(genre); }
  if (feel)   { conditions.push(`s.feel = ?`);   params.push(feel); }
  if (era)    { conditions.push(`s.era = ?`);    params.push(era); }
  if (artist) { conditions.push(`s.artist LIKE ?`); params.push(`%${artist}%`); }

  let sql = `
    SELECT DISTINCT s.id, s.title, s.artist,
           s.chart_written_key, s.sounding_key, s.original_key,
           s.default_capo, s.bpm,
           s.genre, s.feel, s.era, s.tempo_feel,
           s.beatbuddy_structure, s.preferred_key
    FROM songs s
  `;

  // Tag filter — requires all specified tags (AND semantics)
  if (tags && tags.length > 0) {
    sql += `
      JOIN song_tags st ON st.song_id = s.id
      JOIN tags t       ON t.id = st.tag_id
                       AND t.name IN (${tags.map(() => '?').join(',')})
    `;
    params.push(...tags);
    conditions.push(`1=1`); // placeholder so GROUP BY works
    sql += conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
    sql += ` GROUP BY s.id HAVING COUNT(DISTINCT t.name) = ${tags.length}`;
  } else {
    sql += conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
  }

  const sortMap = { title: 's.title COLLATE NOCASE', artist: 's.artist COLLATE NOCASE', era: 's.era' };
  sql += ` ORDER BY ${sortMap[sort] || sortMap.title}`;

  return all(db, sql, params);
}

// ---------------------------------------------------------------------------
// Tags
// ---------------------------------------------------------------------------

/**
 * Get or create a tag by name. Returns tag id.
 */
function upsertTag(db, name, category = null) {
  const existing = get(db, `SELECT id FROM tags WHERE name = ? COLLATE NOCASE`, [name]);
  if (existing) return existing.id;
  const { lastInsertRowid } = run(db,
    `INSERT INTO tags (name, category) VALUES (?, ?)`, [name, category]);
  return lastInsertRowid;
}

function addTagToSong(db, songId, tagName, category = null) {
  const tagId = upsertTag(db, tagName, category);
  run(db, `INSERT OR IGNORE INTO song_tags (song_id, tag_id) VALUES (?, ?)`, [songId, tagId]);
}

function removeTagFromSong(db, songId, tagName) {
  const tag = get(db, `SELECT id FROM tags WHERE name = ? COLLATE NOCASE`, [tagName]);
  if (!tag) return;
  run(db, `DELETE FROM song_tags WHERE song_id = ? AND tag_id = ?`, [songId, tag.id]);
}

function getTagsForSong(db, songId) {
  return all(db, `
    SELECT t.id, t.name, t.category
    FROM tags t
    JOIN song_tags st ON st.tag_id = t.id
    WHERE st.song_id = ?
    ORDER BY t.category NULLS LAST, t.name COLLATE NOCASE
  `, [songId]);
}

function getAllTags(db) {
  return all(db, `
    SELECT t.id, t.name, t.category, COUNT(st.song_id) as song_count
    FROM tags t
    LEFT JOIN song_tags st ON st.tag_id = t.id
    GROUP BY t.id
    ORDER BY t.category NULLS LAST, song_count DESC, t.name COLLATE NOCASE
  `);
}

function renameTag(db, oldName, newName) {
  run(db, `UPDATE tags SET name = ? WHERE name = ? COLLATE NOCASE`, [newName, oldName]);
}

function updateTagCategory(db, tagName, category) {
  run(db, `UPDATE tags SET category = ? WHERE name = ? COLLATE NOCASE`, [category, tagName]);
}

// ---------------------------------------------------------------------------
// Setlists
// ---------------------------------------------------------------------------

function createSetlist(db, { name, gigDate, notes }) {
  const { lastInsertRowid } = run(db,
    `INSERT INTO setlists (name, gig_date, notes) VALUES (?, ?, ?)`,
    [name, gigDate ?? null, notes ?? null]);
  return lastInsertRowid;
}

function updateSetlist(db, id, fields) {
  const allowed = ['name','gig_date','notes'];
  const updates = Object.entries(fields).filter(([k]) => allowed.includes(k));
  if (updates.length === 0) return;
  run(db,
    `UPDATE setlists SET ${updates.map(([k]) => `${k} = ?`).join(', ')}, updated_at = datetime('now') WHERE id = ?`,
    [...updates.map(([,v]) => v), id]);
}

function deleteSetlist(db, id) {
  run(db, `DELETE FROM setlists WHERE id = ?`, [id]);
}

function getAllSetlists(db) {
  return all(db, `
    SELECT s.*, COUNT(ss.id) as song_count
    FROM setlists s
    LEFT JOIN setlist_songs ss ON ss.setlist_id = s.id
    GROUP BY s.id
    ORDER BY s.gig_date DESC NULLS LAST, s.name COLLATE NOCASE
  `);
}

function getSetlistWithSongs(db, setlistId) {
  const setlist = get(db, `SELECT * FROM setlists WHERE id = ?`, [setlistId]);
  if (!setlist) return null;
  setlist.songs = all(db, `
    SELECT ss.position, ss.transposed_key, ss.capo_fret, ss.sounding_key, ss.notes,
           s.id as song_id, s.title, s.artist,
           s.chart_written_key, s.preferred_key, s.default_capo, s.bpm,
           s.beatbuddy_structure, s.genre, s.feel
    FROM setlist_songs ss
    JOIN songs s ON s.id = ss.song_id
    WHERE ss.setlist_id = ?
    ORDER BY ss.position
  `, [setlistId]);
  return setlist;
}

function addSongToSetlist(db, setlistId, songId, { position, transposedKey, capoFret, notes } = {}) {
  // Auto-position at end if not specified
  if (position == null) {
    const last = get(db,
      `SELECT MAX(position) as max_pos FROM setlist_songs WHERE setlist_id = ?`, [setlistId]);
    position = (last?.max_pos ?? 0) + 1;
  }
  run(db, `
    INSERT OR REPLACE INTO setlist_songs
      (setlist_id, song_id, position, transposed_key, capo_fret, notes)
    VALUES (?, ?, ?, ?, ?, ?)`,
    [setlistId, songId, position, transposedKey ?? null, capoFret ?? null, notes ?? null]);
}

function removeSongFromSetlist(db, setlistId, position) {
  run(db, `DELETE FROM setlist_songs WHERE setlist_id = ? AND position = ?`, [setlistId, position]);
}

function reorderSetlist(db, setlistId, orderedSongIds) {
  orderedSongIds.forEach((songId, idx) => {
    run(db,
      `UPDATE setlist_songs SET position = ? WHERE setlist_id = ? AND song_id = ?`,
      [idx + 1, setlistId, songId]);
  });
}

// ---------------------------------------------------------------------------
// Import log
// ---------------------------------------------------------------------------

function logImport(db, { sourceFile, songId, status, message }) {
  run(db,
    `INSERT INTO import_log (source_file, song_id, status, message) VALUES (?, ?, ?, ?)`,
    [sourceFile, songId ?? null, status, message ?? null]);
}

function getImportLog(db, limit = 100) {
  return all(db,
    `SELECT * FROM import_log ORDER BY imported_at DESC LIMIT ?`, [limit]);
}

// ---------------------------------------------------------------------------
// Stats (for dashboard)
// ---------------------------------------------------------------------------
function getStats(db) {
  return {
    totalSongs:    get(db, `SELECT COUNT(*) as n FROM songs`).n,
    totalTags:     get(db, `SELECT COUNT(*) as n FROM tags`).n,
    totalSetlists: get(db, `SELECT COUNT(*) as n FROM setlists`).n,
    byGenre:       all(db, `SELECT genre, COUNT(*) as n FROM songs WHERE genre IS NOT NULL GROUP BY genre ORDER BY n DESC`),
    byEra:         all(db, `SELECT era,   COUNT(*) as n FROM songs WHERE era   IS NOT NULL GROUP BY era   ORDER BY era`),
    byFeel:        all(db, `SELECT feel,  COUNT(*) as n FROM songs WHERE feel  IS NOT NULL GROUP BY feel  ORDER BY n DESC`),
  };
}

module.exports = {
  // songs
  insertSong, updateSong, updateChartContent, deleteSong,
  getSongById, getSongByTitleArtist, searchSongs,
  // tags
  upsertTag, addTagToSong, removeTagFromSong,
  getTagsForSong, getAllTags, renameTag, updateTagCategory,
  // setlists
  createSetlist, updateSetlist, deleteSetlist,
  getAllSetlists, getSetlistWithSongs,
  addSongToSetlist, removeSongFromSetlist, reorderSetlist,
  // import log
  logImport, getImportLog,
  // stats
  getStats,
};
