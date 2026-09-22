/*
 * db.js — Postgres access for the chord API.
 *
 * A thin query layer, not an ORM: the schema is small and the queries are
 * specific (faceted search, era derived from release_year, genres/vibes
 * aggregated). Parameterized everywhere — no string interpolation into SQL.
 */
const { Pool } = require("pg");
const { chartToText } = require("./chartParser");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

/* Faceted song search. All filters optional and AND-combined.
 * filters: { q, artist, genre, vibe, era }  (era like "1970s")
 * Returns lightweight rows for the list view (no chart_content). */
async function searchSongs(filters = {}) {
  const where = [];
  const params = [];
  const p = (v) => (params.push(v), `$${params.length}`);

  if (filters.q) {
    where.push(`(s.title ILIKE ${p("%" + filters.q + "%")}
                 OR a.name ILIKE ${p("%" + filters.q + "%")})`);
  }
  if (filters.artist) where.push(`LOWER(a.name) = LOWER(${p(filters.artist)})`);

  if (filters.genre) {
    // song must be linked to this genre (primary or secondary)
    where.push(`EXISTS (SELECT 1 FROM song_genres sg2 JOIN genres g2 ON g2.id=sg2.genre_id
                        WHERE sg2.song_id=s.id AND LOWER(g2.name)=LOWER(${p(filters.genre)}))`);
  }
  if (filters.vibe) {
    where.push(`EXISTS (SELECT 1 FROM song_vibes sv2 JOIN vibes v2 ON v2.id=sv2.vibe_id
                        WHERE sv2.song_id=s.id AND LOWER(v2.name)=LOWER(${p(filters.vibe)}))`);
  }
  if (filters.era) {
    // "1970s" -> decade start 1970, range [1970, 1980)
    const decade = parseInt(filters.era, 10);
    if (!Number.isNaN(decade)) {
      where.push(`s.release_year >= ${p(decade)} AND s.release_year < ${p(decade + 10)}`);
    }
  }

  const sql = `
    SELECT s.id, s.title, a.name AS artist, s.performance_key, s.original_key,
           s.alt_key, s.capo_fret, s.bpm, s.release_year,
           (s.release_year / 10 * 10)::text || 's' AS era,
           COALESCE(pg.name, NULL) AS primary_genre,
           COALESCE(array_agg(DISTINCT v.name) FILTER (WHERE v.name IS NOT NULL), '{}') AS vibes
    FROM songs s
    JOIN artists a ON a.id = s.artist_id
    LEFT JOIN song_genres spg ON spg.song_id = s.id AND spg.is_primary
    LEFT JOIN genres pg ON pg.id = spg.genre_id
    LEFT JOIN song_vibes sv ON sv.song_id = s.id
    LEFT JOIN vibes v ON v.id = sv.vibe_id
    ${where.length ? "WHERE " + where.join(" AND ") : ""}
    GROUP BY s.id, a.name, pg.name
    ORDER BY a.name, s.title
  `;
  const { rows } = await pool.query(sql, params);
  return rows;
}

/* One song with full chart_content and all genres/vibes. */
async function getSong(id) {
  const songQ = `
    SELECT s.*, a.name AS artist,
           (s.release_year / 10 * 10)::text || 's' AS era
    FROM songs s JOIN artists a ON a.id = s.artist_id
    WHERE s.id = $1`;
  const { rows } = await pool.query(songQ, [id]);
  if (!rows.length) return null;
  const song = rows[0];

  const genresQ = `
    SELECT g.name, sg.is_primary FROM song_genres sg
    JOIN genres g ON g.id = sg.genre_id WHERE sg.song_id = $1
    ORDER BY sg.is_primary DESC, g.name`;
  const vibesQ = `
    SELECT v.name FROM song_vibes sv JOIN vibes v ON v.id = sv.vibe_id
    WHERE sv.song_id = $1 ORDER BY v.name`;
  const [g, v] = await Promise.all([
    pool.query(genresQ, [id]),
    pool.query(vibesQ, [id]),
  ]);
  song.genres = g.rows;
  song.vibes = v.rows.map((r) => r.name);
  return song;
}

/* Distinct facet values for the filter UI. */
async function getFacets() {
  const [genres, vibes, artists, eras] = await Promise.all([
    pool.query(`SELECT name FROM genres ORDER BY name`),
    pool.query(`SELECT name FROM vibes ORDER BY name`),
    pool.query(`SELECT DISTINCT a.name FROM artists a
                JOIN songs s ON s.artist_id = a.id ORDER BY a.name`),
    pool.query(`SELECT DISTINCT (release_year/10*10)::text || 's' AS era
                FROM songs WHERE release_year IS NOT NULL ORDER BY era`),
  ]);
  return {
    genres: genres.rows.map((r) => r.name),
    vibes: vibes.rows.map((r) => r.name),
    artists: artists.rows.map((r) => r.name),
    eras: eras.rows.map((r) => r.era),
  };
}

/* Write path: update the player-adjustable fields. Whitelisted columns only. */
async function updateSong(id, patch) {
  const allowed = ["performance_key", "capo_fret", "bpm", "chart_content"];
  const sets = [];
  const params = [];
  for (const key of allowed) {
    if (key in patch) {
      params.push(key === "chart_content" ? JSON.stringify(patch[key]) : patch[key]);
      sets.push(`${key} = $${params.length}`);
    }
  }
  if (!sets.length) return getSong(id);
  params.push(id);
  await pool.query(
    `UPDATE songs SET ${sets.join(", ")} WHERE id = $${params.length}`,
    params
  );
  return getSong(id);
}

/* Everything the client needs to run fully offline, in 3 queries total
 * (not N+1): all songs with chart_content, plus genres and vibes stitched
 * in. Shape per song matches getSong() so the viewer needs no special-casing. */
async function exportAll() {
  const songsQ = `
    SELECT s.id, s.title, a.name AS artist, s.performance_key, s.preferred_key,
           s.original_key,
           s.alt_key, s.capo_fret, s.bpm, s.release_year,
           (s.release_year / 10 * 10)::text || 's' AS era,
           s.bb_structure, s.chart_source, s.chart_content,
           s.created_at, s.updated_at
    FROM songs s JOIN artists a ON a.id = s.artist_id
    ORDER BY a.name, s.title`;
  const genresQ = `
    SELECT sg.song_id, g.name, sg.is_primary
    FROM song_genres sg JOIN genres g ON g.id = sg.genre_id`;
  const vibesQ = `
    SELECT sv.song_id, v.name
    FROM song_vibes sv JOIN vibes v ON v.id = sv.vibe_id`;

  const [songs, genres, vibes, facets] = await Promise.all([
    pool.query(songsQ),
    pool.query(genresQ),
    pool.query(vibesQ),
    getFacets(),
  ]);

  const byId = new Map();
  for (const s of songs.rows) {
    if (!s.chart_source && s.chart_content) {
      s.chart_source = chartToText(s.chart_content);
    }
    s.genres = [];
    s.vibes = [];
    byId.set(s.id, s);
  }
  for (const g of genres.rows) {
    const s = byId.get(g.song_id);
    if (s) s.genres.push({ name: g.name, is_primary: g.is_primary });
  }
  for (const v of vibes.rows) {
    const s = byId.get(v.song_id);
    if (s) s.vibes.push(v.name);
  }
  // stable ordering: primary genre first
  for (const s of byId.values()) {
    s.genres.sort((a, b) => (b.is_primary === true) - (a.is_primary === true));
    s.primary_genre = (s.genres.find((x) => x.is_primary) || {}).name || null;
  }
  return { exported_at: new Date().toISOString(), facets, songs: songs.rows };
}

module.exports = { pool, searchSongs, getSong, getFacets, updateSong, exportAll };
