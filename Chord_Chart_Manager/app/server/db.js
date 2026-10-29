/*
 * db.js — Postgres access for the chord API.
 *
 * A thin query layer, not an ORM: the schema is small and the queries are
 * specific (era derived from release_year, genres/vibes aggregated).
 * Parameterized everywhere — no string interpolation into SQL.
 */
const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

/* Every song as a lightweight row for the list view (no chart_content). */
async function listSongs() {
  const sql = `
    SELECT s.id, s.title, a.name AS artist, s.created_at, s.performance_key, s.original_key,
           s.alt_key, s.capo_fret, s.bpm, s.release_year,
           (s.release_year / 10 * 10)::text || 's' AS era,
           COALESCE(pg.name, NULL) AS primary_genre,
           (SELECT COALESCE(jsonb_agg(jsonb_build_object('name', g_all.name, 'is_primary', sg_all.is_primary)
             ORDER BY sg_all.is_primary DESC, lower(g_all.name)), '[]'::jsonb)
            FROM song_genres sg_all JOIN genres g_all ON g_all.id = sg_all.genre_id
            WHERE sg_all.song_id = s.id) AS genres,
           COALESCE(array_agg(DISTINCT v.name) FILTER (WHERE v.name IS NOT NULL), '{}') AS vibes
    FROM songs s
    JOIN artists a ON a.id = s.artist_id
    LEFT JOIN song_genres spg ON spg.song_id = s.id AND spg.is_primary
    LEFT JOIN genres pg ON pg.id = spg.genre_id
    LEFT JOIN song_vibes sv ON sv.song_id = s.id
    LEFT JOIN vibes v ON v.id = sv.vibe_id
    GROUP BY s.id, a.name, pg.name
    ORDER BY a.name, s.title
  `;
  const { rows } = await pool.query(sql);
  return rows;
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

/* Everything the client needs to run fully offline, in 3 queries total
 * (not N+1): all songs with chart_content, plus genres and vibes stitched
 * in. */
async function exportAll() {
  const songsQ = `
    SELECT s.id, s.title, a.name AS artist, s.performance_key, s.preferred_key,
           s.original_key,
           s.alt_key, s.capo_fret, s.bpm, s.release_year,
           (s.release_year / 10 * 10)::text || 's' AS era,
           s.bb_structure, s.chart_source, s.chart_content,
           s.created_at,
           to_char(s.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
    FROM songs s JOIN artists a ON a.id = s.artist_id
    ORDER BY a.name, s.title`;
  const genresQ = `
    SELECT sg.song_id, g.name, sg.is_primary
    FROM song_genres sg JOIN genres g ON g.id = sg.genre_id
    ORDER BY sg.song_id, sg.is_primary DESC, g.name`;
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

module.exports = { pool, listSongs, exportAll };
