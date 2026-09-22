/*
 * db_ext.js — the write/CRUD/setlist/tag/stats half of the data layer.
 *
 * Kept separate from db.js so the already-tested read paths stay untouched.
 * Shares the same pool. This is where the richer-frontend surface lives:
 *   - song create/update(with chart re-parse)/delete
 *   - categorized tags synthesized from genres/vibes/era (+ counts)
 *   - setlists CRUD + song add/remove/reorder
 *   - library stats
 *
 * Field-name mapping to the app's vocabulary happens in mapSongOut():
 *   performance_key -> chart_written_key, capo_fret -> default_capo,
 *   bb_structure -> beatbuddy_structure.  chart_source is the editable text;
 *   chart_content is the derived structure.
 */
const { pool } = require("./db");
const { parseChartBody, chartToText } = require("./chartParser");

function conflictError() {
  const error = new Error("Song changed on the server; server version kept");
  error.status = 409;
  return error;
}

function sameVersion(current, expected) {
  if (!expected) return true;
  const currentMs = new Date(current).getTime();
  const expectedMs = new Date(expected).getTime();
  return Number.isFinite(expectedMs) && currentMs === expectedMs;
}

// ---------------------------------------------------------------- mapping

function mapSongOut(row) {
  if (!row) return row;
  return {
    ...row,
    chart_written_key: row.performance_key ?? null,
    default_capo: row.capo_fret ?? 0,
    beatbuddy_structure: row.bb_structure ?? null,
    // list rows carry vibes[]/primary_genre; surface single-value aliases the
    // views read (feel/genre) without clobbering an already-set value.
    feel: row.feel ?? (Array.isArray(row.vibes) ? row.vibes[0] : null) ?? null,
    genre: row.genre ?? row.primary_genre ?? null,
  };
}

// Lookup get-or-create (mirrors loader semantics) for tag writes.
async function getOrCreate(client, table, name) {
  if (!name || !name.trim()) return null;
  const { rows } = await client.query(
    `INSERT INTO ${table} (name) VALUES ($1)
     ON CONFLICT (LOWER(name)) DO UPDATE SET name = EXCLUDED.name
     RETURNING id`,
    [name.trim()]
  );
  return rows[0].id;
}

// ---------------------------------------------------------------- songs CRUD

const EDITABLE = {
  title: "title",
  artist: "__artist",                     // handled specially (FK)
  chart_written_key: "performance_key",
  performance_key: "performance_key",
  preferred_key: "preferred_key",
  original_key: "original_key",
  default_capo: "capo_fret",
  capo_fret: "capo_fret",
  bpm: "bpm",
  release_year: "release_year",
  beatbuddy_structure: "bb_structure",
  bb_structure: "bb_structure",
  chart_source: "chart_source",
};

async function createSong(patch) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const artistId = await getOrCreate(client, "artists", patch.artist || "Unknown");
    // Derive structured content from the edited text.
    const source = patch.chart_source || "";
    const content = parseChartBody(source);
    const { rows } = await client.query(
      `INSERT INTO songs (title, artist_id, performance_key, preferred_key,
          original_key, capo_fret, bpm, release_year, bb_structure,
          chart_source, chart_content)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id`,
      [
        patch.title || "Untitled",
        artistId,
        patch.chart_written_key ?? patch.performance_key ?? null,
        patch.preferred_key ?? null,
        patch.original_key ?? null,
        Number(patch.default_capo ?? patch.capo_fret ?? 0) || 0,
        patch.bpm ? Number(patch.bpm) : null,
        patch.release_year ? Number(patch.release_year) : null,
        patch.beatbuddy_structure ?? patch.bb_structure ?? null,
        source,
        JSON.stringify(content),
      ]
    );
    await client.query("COMMIT");
    return getSongFull(rows[0].id);
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

async function updateSongFull(id, patch) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    // Tablet sync supplies the version it originally downloaded. Lock the row
    // while comparing so a desktop write cannot race between this check and
    // the update. A mismatch is a 409 and the desktop/server copy wins.
    const locked = await client.query(
      "SELECT updated_at FROM songs WHERE id = $1 FOR UPDATE", [id]
    );
    if (!locked.rows.length) {
      await client.query("COMMIT");
      return null;
    }
    if (!sameVersion(locked.rows[0].updated_at, patch.base_updated_at)) {
      throw conflictError();
    }

    if (patch.artist !== undefined) {
      const artistId = await getOrCreate(client, "artists", patch.artist || "Unknown");
      await client.query("UPDATE songs SET artist_id = $1 WHERE id = $2", [artistId, id]);
    }

    const sets = [];
    const vals = [];
    for (const [key, col] of Object.entries(EDITABLE)) {
      if (col === "__artist" || col === "title") continue;
      if (key in patch) {
        vals.push(col === "capo_fret" ? Number(patch[key]) || 0
                : col === "bpm" || col === "release_year"
                    ? (patch[key] ? Number(patch[key]) : null)
                    : patch[key]);
        sets.push(`${col} = $${vals.length}`);
      }
    }
    if ("title" in patch) { vals.push(patch.title); sets.push(`title = $${vals.length}`); }

    // If the editable text changed, re-derive structured content.
    if ("chart_source" in patch) {
      const content = parseChartBody(patch.chart_source || "");
      vals.push(JSON.stringify(content));
      sets.push(`chart_content = $${vals.length}`);
    }

    if (sets.length) {
      vals.push(id);
      await client.query(`UPDATE songs SET ${sets.join(", ")} WHERE id = $${vals.length}`, vals);
    }
    await client.query("COMMIT");
    return getSongFull(id);
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

async function deleteSong(id, baseUpdatedAt = null) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const locked = await client.query(
      "SELECT updated_at FROM songs WHERE id = $1 FOR UPDATE", [id]
    );
    if (!locked.rows.length) {
      await client.query("COMMIT");
      return { ok: true };
    }
    if (!sameVersion(locked.rows[0].updated_at, baseUpdatedAt)) {
      throw conflictError();
    }
    await client.query("DELETE FROM songs WHERE id = $1", [id]);
    await client.query("COMMIT");
    return { ok: true };
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

// Full single song incl. editable text + genres/vibes/era, app-mapped.
async function getSongFull(id) {
  const { rows } = await pool.query(
    `SELECT s.*, a.name AS artist,
            (s.release_year / 10 * 10)::text || 's' AS era
     FROM songs s JOIN artists a ON a.id = s.artist_id WHERE s.id = $1`,
    [id]
  );
  if (!rows.length) return null;
  const song = rows[0];
  const [g, v] = await Promise.all([
    pool.query(
      `SELECT g.name, sg.is_primary FROM song_genres sg JOIN genres g ON g.id = sg.genre_id
       WHERE sg.song_id = $1 ORDER BY sg.is_primary DESC, g.name`, [id]),
    pool.query(
      `SELECT v.name FROM song_vibes sv JOIN vibes v ON v.id = sv.vibe_id
       WHERE sv.song_id = $1 ORDER BY v.name`, [id]),
  ]);
  song.genres = g.rows;
  song.genre = (g.rows.find((x) => x.is_primary) || g.rows[0] || {}).name || null;
  song.vibes = v.rows.map((r) => r.name);
  song.feel = song.vibes[0] || null;
  // If a migrated song has structured content but no editable text yet, seed it.
  if (!song.chart_source && song.chart_content) {
    song.chart_source = chartToText(song.chart_content);
  }
  return mapSongOut(song);
}

// ---------------------------------------------------------------- tags (mapped)

// Synthesize the app's categorized tag list from genres/vibes/era + counts.
async function getTags() {
  const [genres, vibes, eras] = await Promise.all([
    pool.query(
      `SELECT g.id, g.name, count(sg.song_id)::int AS song_count
       FROM genres g LEFT JOIN song_genres sg ON sg.genre_id = g.id
       GROUP BY g.id, g.name ORDER BY g.name`),
    pool.query(
      `SELECT v.id, v.name, count(sv.song_id)::int AS song_count
       FROM vibes v LEFT JOIN song_vibes sv ON sv.vibe_id = v.id
       GROUP BY v.id, v.name ORDER BY v.name`),
    pool.query(
      `SELECT (release_year/10*10)::text || 's' AS name, count(*)::int AS song_count
       FROM songs WHERE release_year IS NOT NULL
       GROUP BY 1 ORDER BY 1`),
  ]);
  const out = [];
  genres.rows.forEach((r) => out.push({ id: `g${r.id}`, name: r.name, category: "Genre", song_count: r.song_count }));
  vibes.rows.forEach((r) => out.push({ id: `v${r.id}`, name: r.name, category: "Feel", song_count: r.song_count }));
  eras.rows.forEach((r, i) => out.push({ id: `e${i}`, name: r.name, category: "Era", song_count: r.song_count }));
  return out;
}

// ---------------------------------------------------------------- song tags

// Add a tag to a song. Category routes to the right table: Genre -> genres
// (secondary), Feel/Vibe -> vibes. Era is derived and not directly taggable.
async function addSongTag(songId, name, category) {
  const cat = (category || "Feel").toLowerCase();
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    if (cat === "genre") {
      const gid = await getOrCreate(client, "genres", name);
      await client.query(
        `INSERT INTO song_genres (song_id, genre_id, is_primary) VALUES ($1,$2,false)
         ON CONFLICT (song_id, genre_id) DO NOTHING`, [songId, gid]);
    } else {
      const vid = await getOrCreate(client, "vibes", name);
      await client.query(
        `INSERT INTO song_vibes (song_id, vibe_id) VALUES ($1,$2)
         ON CONFLICT DO NOTHING`, [songId, vid]);
    }
    await client.query("COMMIT");
  } catch (e) { await client.query("ROLLBACK"); throw e; }
  finally { client.release(); }
  return getSongFull(songId);
}

async function removeSongTag(songId, name) {
  // Remove from whichever of genres/vibes it matches (name is unique-ish).
  await pool.query(
    `DELETE FROM song_genres sg USING genres g
       WHERE sg.genre_id=g.id AND sg.song_id=$1 AND LOWER(g.name)=LOWER($2)`,
    [songId, name]);
  await pool.query(
    `DELETE FROM song_vibes sv USING vibes v
       WHERE sv.vibe_id=v.id AND sv.song_id=$1 AND LOWER(v.name)=LOWER($2)`,
    [songId, name]);
  return getSongFull(songId);
}

// ---------------------------------------------------------------- setlists

async function listSetlists() {
  const { rows } = await pool.query(
    `SELECT sl.*, count(ss.song_id)::int AS song_count
     FROM setlists sl LEFT JOIN setlist_songs ss ON ss.setlist_id = sl.id
     GROUP BY sl.id ORDER BY sl.gig_date DESC NULLS LAST, sl.name`
  );
  return rows;
}

async function getSetlist(id) {
  const { rows } = await pool.query("SELECT * FROM setlists WHERE id = $1", [id]);
  if (!rows.length) return null;
  const setlist = rows[0];
  const songs = await pool.query(
    `SELECT ss.position, ss.transposed_key, ss.capo_fret, ss.notes,
            s.id AS song_id, s.title, a.name AS artist,
            s.performance_key AS chart_written_key, s.capo_fret AS default_capo,
            s.bb_structure AS beatbuddy_structure, s.bpm,
            (SELECT v.name FROM song_vibes sv JOIN vibes v ON v.id=sv.vibe_id
             WHERE sv.song_id = s.id ORDER BY v.name LIMIT 1) AS feel
     FROM setlist_songs ss
     JOIN songs s ON s.id = ss.song_id
     JOIN artists a ON a.id = s.artist_id
     WHERE ss.setlist_id = $1 ORDER BY ss.position`,
    [id]
  );
  setlist.songs = songs.rows;
  return setlist;
}

async function createSetlist(data) {
  const { rows } = await pool.query(
    `INSERT INTO setlists (name, gig_date, notes) VALUES ($1,$2,$3) RETURNING id`,
    [data.name || "New setlist", data.gig_date || null, data.notes || null]
  );
  return getSetlist(rows[0].id);
}

async function updateSetlist(id, data) {
  const sets = [], vals = [];
  for (const k of ["name", "gig_date", "notes"]) {
    if (k in data) { vals.push(data[k] || null); sets.push(`${k} = $${vals.length}`); }
  }
  if (sets.length) {
    vals.push(id);
    await pool.query(`UPDATE setlists SET ${sets.join(", ")} WHERE id = $${vals.length}`, vals);
  }
  return getSetlist(id);
}

async function deleteSetlist(id) {
  await pool.query("DELETE FROM setlists WHERE id = $1", [id]);
  return { ok: true };
}

async function addSetlistSong(id, data) {
  // append at end unless a position is given
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    let pos = data.position;
    if (pos == null) {
      const { rows } = await client.query(
        "SELECT COALESCE(MAX(position),0)+1 AS next FROM setlist_songs WHERE setlist_id=$1", [id]);
      pos = rows[0].next;
    }
    await client.query(
      `INSERT INTO setlist_songs (setlist_id, song_id, position, transposed_key, capo_fret, notes)
       VALUES ($1,$2,$3,$4,$5,$6)`,
      [id, data.song_id, pos, data.transposed_key || null,
       data.capo_fret ?? null, data.notes || null]
    );
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
  return getSetlist(id);
}

async function removeSetlistSong(id, position) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query("DELETE FROM setlist_songs WHERE setlist_id=$1 AND position=$2", [id, position]);
    // close the gap so positions stay contiguous
    await client.query(
      "UPDATE setlist_songs SET position = position - 1 WHERE setlist_id=$1 AND position > $2",
      [id, position]);
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
  return getSetlist(id);
}

async function reorderSetlist(id, songIds) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    // Two-phase to avoid unique (setlist_id, position) clashes: park to
    // negative positions, then lay down the new order.
    await client.query("UPDATE setlist_songs SET position = -position WHERE setlist_id=$1", [id]);
    let pos = 1;
    for (const songId of songIds) {
      await client.query(
        "UPDATE setlist_songs SET position=$1 WHERE setlist_id=$2 AND song_id=$3",
        [pos++, id, songId]);
    }
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
  return getSetlist(id);
}

// ---------------------------------------------------------------- stats

async function getStats() {
  const { rows } = await pool.query(`
    SELECT
      (SELECT count(*) FROM songs)::int     AS "totalSongs",
      (SELECT count(*) FROM genres)::int
        + (SELECT count(*) FROM vibes)::int AS "totalTags",
      (SELECT count(*) FROM setlists)::int  AS "totalSetlists"`);
  return rows[0];
}

module.exports = {
  mapSongOut, getSongFull, createSong, updateSongFull, deleteSong,
  getTags, addSongTag, removeSongTag, listSetlists, getSetlist, createSetlist,
  updateSetlist, deleteSetlist, addSetlistSong, removeSetlistSong,
  reorderSetlist, getStats,
};
