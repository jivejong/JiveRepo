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
const crypto = require("crypto");
const {
  parseChartBody, chartToText, isGeneratedSourceUnchanged, isGeneratedChartSource,
} = require("./chartParser");
const {
  definitionKey, findTagDefinition, lockTagDefinitionNames, normalizeDefinitionName, resolveTagDefinitionSet,
} = require("./tagDefinitionNames");

function conflictError() {
  const error = new Error("Song changed on the server; server version kept");
  error.status = 409;
  error.code = "SONG_VERSION_CONFLICT";
  return error;
}

function sameVersion(current, expected) {
  if (expected === null || expected === undefined) return true; // Legacy ordinary CRUD may omit a base.
  validateVersionToken(expected);
  // Older queued millisecond tokens are ambiguous even when their millisecond
  // matches. Let the existing 409/server-wins sync path refresh the cache.
  return EXACT_VERSION.test(expected) && current === expected;
}

const EXACT_VERSION = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;
const VERSION_PARTS = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3}|\d{6})Z$/;
function validateVersionToken(value, required = false) {
  if (value === null || value === undefined) {
    if (!required) return;
    const error = new Error("base_updated_at is required");
    error.status = 400;
    throw error;
  }
  const parts = typeof value === "string" ? VERSION_PARTS.exec(value) : null;
  const [year, month, day, hour, minute, second] = parts ? parts.slice(1, 7).map(Number) : [];
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (!parts || year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] ||
      hour > 23 || minute > 59 || second > 59) {
    const error = new Error("base_updated_at must be a UTC timestamp token with 3 or 6 fractional digits");
    error.status = 400;
    throw error;
  }
}

function normalizeTagSet(values, category) {
  if (!Array.isArray(values)) {
    const error = new Error(`${category} must be a list`);
    error.status = 400;
    error.code = "INVALID_TAG_INPUT";
    error.category = category === "Genres" ? "Genre" : "Vibe";
    throw error;
  }
  const seen = new Set();
  const result = [];
  for (const item of values) {
    const raw = typeof item === "object" && item !== null ? item.name : item;
    if (raw === null || raw === undefined || raw === "") continue;
    const name = normalizeDefinitionName(raw, category === "Genres" ? "Genre" : "Vibe");
    if (!name) continue;
    const key = definitionKey(name);
    if (!seen.has(key)) {
      seen.add(key);
      result.push({ name, is_primary: !!(item && typeof item === "object" && item.is_primary) });
    }
  }
  return result;
}

function validatedReleaseYear(value) {
  if (value === null || value === undefined || value === "") return null;
  const year = Number(value);
  if (!Number.isInteger(year) || year < 1 || year > 32767) {
    const error = new Error("Release year must be a whole number from 1 to 32767");
    error.status = 400;
    throw error;
  }
  return year;
}

async function replaceSongTagSet(client, songId, table, joinTable, fkColumn, values) {
  await client.query(`DELETE FROM ${joinTable} WHERE song_id = $1`, [songId]);
  for (const tag of values) {
    if (table === "genres") {
      await client.query(
        `INSERT INTO ${joinTable} (song_id, ${fkColumn}, is_primary) VALUES ($1,$2,$3)`,
        [songId, tag.id, tag.is_primary],
      );
    } else {
      await client.query(`INSERT INTO ${joinTable} (song_id, ${fkColumn}) VALUES ($1,$2)`, [songId, tag.id]);
    }
  }
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

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

async function createSong(patch, operationId = null) {
  const genres = patch.genres === undefined ? null : normalizeTagSet(patch.genres, "Genres");
  const vibes = patch.vibes === undefined ? null : normalizeTagSet(patch.vibes, "Vibes");
  if (genres?.length) {
    const selectedPrimary = genres.find(tag => tag.is_primary);
    genres.forEach((tag, index) => { tag.is_primary = selectedPrimary ? tag === selectedPrimary : index === 0; });
  }
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    let requestHash = null;
    if (operationId) {
      if (typeof operationId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(operationId)) {
        const error = new Error("operation_id must be a UUID"); error.status = 400; throw error;
      }
      requestHash = crypto.createHash("sha256").update(canonicalJson(patch)).digest("hex");
      await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [operationId]);
      const prior = await client.query("SELECT request_hash,response FROM public.mutation_receipts WHERE operation_id=$1", [operationId]);
      if (prior.rowCount) {
        if (prior.rows[0].request_hash !== requestHash) {
          const error = new Error("Operation ID was already used for a different request"); error.status = 409; throw error;
        }
        await client.query("COMMIT");
        return prior.rows[0].response;
      }
    }
    const resolvedGenres = genres === null ? null : await resolveTagDefinitionSet(client, "Genre", genres);
    const resolvedVibes = vibes === null ? null : await resolveTagDefinitionSet(client, "Vibe", vibes);
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
        validatedReleaseYear(patch.release_year),
        patch.beatbuddy_structure ?? patch.bb_structure ?? null,
        source,
        JSON.stringify(content),
      ]
    );
    if (resolvedGenres !== null) await replaceSongTagSet(client, rows[0].id, "genres", "song_genres", "genre_id", resolvedGenres);
    if (resolvedVibes !== null) await replaceSongTagSet(client, rows[0].id, "vibes", "song_vibes", "vibe_id", resolvedVibes);
    const saved = await getSongFull(rows[0].id, client);
    if (operationId) {
      await client.query(
        `INSERT INTO public.mutation_receipts(operation_id,request_hash,entity_type,resource_id,response)
         VALUES($1,$2,'song',$3,$4::jsonb)`,
        [operationId, requestHash, String(rows[0].id), JSON.stringify(saved)]);
    }
    await client.query("COMMIT");
    return saved;
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}

async function updateSongFull(id, patch, operationId) {
  const genres = Object.hasOwn(patch, "genres") ? normalizeTagSet(patch.genres, "Genres") : null;
  const vibes = Object.hasOwn(patch, "vibes") ? normalizeTagSet(patch.vibes, "Vibes") : null;
  if (genres?.length) {
    const selectedPrimary = genres.find(tag => tag.is_primary);
    genres.forEach((tag, index) => { tag.is_primary = selectedPrimary ? tag === selectedPrimary : index === 0; });
  }
  if (Object.hasOwn(patch, "release_year")) validatedReleaseYear(patch.release_year);
  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    if (typeof operationId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(operationId)) {
      const error = new Error("operation_id must be a UUID"); error.status = 400; throw error;
    }
    const requestHash = crypto.createHash("sha256").update(canonicalJson({
      entity: "song", song_id: Number(id), payload: patch,
    })).digest("hex");
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [operationId]);
    const prior = await client.query(
      "SELECT request_hash,response FROM public.mutation_receipts WHERE operation_id=$1", [operationId]);
    if (prior.rowCount) {
      if (prior.rows[0].request_hash !== requestHash) {
        const error = new Error("Operation ID was already used for a different request"); error.status = 409; throw error;
      }
      await client.query("COMMIT");
      const replay = prior.rows[0].response;
      Object.defineProperty(replay, "_operation_replayed", { value: true });
      return replay;
    }

    let resolvedGenres = null;
    let resolvedVibes = null;
    if (genres !== null || vibes !== null) {
      const versionRead = await client.query(
        `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
         FROM songs WHERE id=$1`, [id]);
      if (!versionRead.rowCount) { await client.query("COMMIT"); return null; }
      if (!sameVersion(versionRead.rows[0].updated_at, patch.base_updated_at)) throw conflictError();
      if (genres !== null) resolvedGenres = await resolveTagDefinitionSet(client, "Genre", genres);
      if (vibes !== null) resolvedVibes = await resolveTagDefinitionSet(client, "Vibe", vibes);
    }

    // Assignment updates acquire definition locks before the song lock, as do
    // management operations. Recheck the version under FOR UPDATE so the
    // optimistic preflight cannot authorize a stale write.
    const locked = await client.query(
      `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
              chart_source, chart_content
       FROM songs WHERE id = $1 FOR UPDATE`, [id]
    );
    if (!locked.rows.length) {
      await client.query("COMMIT");
      return null;
    }
    if (!sameVersion(locked.rows[0].updated_at, patch.base_updated_at)) {
      throw conflictError();
    }
    const currentSource = locked.rows[0].chart_source;
    const preserveChartContent = isGeneratedSourceUnchanged(
      currentSource,
      locked.rows[0].chart_content,
      patch.chart_source,
    );
    if ("chart_source" in patch && patch.chart_source &&
        (!currentSource || isGeneratedChartSource(currentSource)) &&
        !isGeneratedChartSource(patch.chart_source)) {
      const error = new Error("This chart uses generated structured source; keep its CCM line markers when editing.");
      error.status = 400;
      throw error;
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
        if (key === "chart_source" && preserveChartContent) continue;
        vals.push(col === "capo_fret" ? Number(patch[key]) || 0
                : col === "bpm"
                    ? (patch[key] ? Number(patch[key]) : null)
                : col === "release_year"
                    ? validatedReleaseYear(patch[key])
                    : patch[key]);
        sets.push(`${col} = $${vals.length}`);
      }
    }
    if ("title" in patch) { vals.push(patch.title); sets.push(`title = $${vals.length}`); }

    // If the editable text changed, re-derive structured content.
    if ("chart_source" in patch && !preserveChartContent) {
      const content = parseChartBody(patch.chart_source || "");
      vals.push(JSON.stringify(content));
      sets.push(`chart_content = $${vals.length}`);
    }

    if (sets.length) {
      vals.push(id);
      await client.query(`UPDATE songs SET ${sets.join(", ")} WHERE id = $${vals.length}`, vals);
    }
    if (resolvedGenres !== null) await replaceSongTagSet(client, id, "genres", "song_genres", "genre_id", resolvedGenres);
    if (resolvedVibes !== null) await replaceSongTagSet(client, id, "vibes", "song_vibes", "vibe_id", resolvedVibes);
    if (genres !== null || vibes !== null) {
      await client.query("UPDATE songs SET updated_at = now() WHERE id = $1", [id]);
    }
    const saved = await getSongFull(id, client);
    await client.query(
      `INSERT INTO public.mutation_receipts(operation_id,request_hash,entity_type,resource_id,response)
       VALUES($1,$2,'song',$3,$4::jsonb)`,
      [operationId, requestHash, String(id), JSON.stringify(saved)]);
    await client.query("COMMIT");
    return saved;
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
    // Match the setlist entry trigger's parent-before-song lock order. The
    // cascade then bumps each affected setlist without a lock-order inversion.
    await client.query(
      `SELECT sl.id FROM public.setlists sl
       JOIN public.setlist_songs ss ON ss.setlist_id = sl.id
       WHERE ss.song_id = $1 ORDER BY sl.id FOR UPDATE OF sl`, [id]);
    const locked = await client.query(
      `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
       FROM songs WHERE id = $1 FOR UPDATE`, [id]
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
async function getSongFull(id, queryable = pool) {
  const { rows } = await queryable.query(
    `SELECT s.*, to_char(s.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
            a.name AS artist,
            (s.release_year / 10 * 10)::text || 's' AS era
     FROM songs s JOIN artists a ON a.id = s.artist_id WHERE s.id = $1`,
    [id]
  );
  if (!rows.length) return null;
  const song = rows[0];
  const [g, v] = await Promise.all([
    queryable.query(
      `SELECT g.name, sg.is_primary FROM song_genres sg JOIN genres g ON g.id = sg.genre_id
       WHERE sg.song_id = $1 ORDER BY sg.is_primary DESC, g.name`, [id]),
    queryable.query(
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

function tagDefinitionTable(category) {
  if (category === "Genre") return { table: "public.genres", join: "public.song_genres", fk: "genre_id" };
  if (category === "Vibe") return { table: "public.vibes", join: "public.song_vibes", fk: "vibe_id" };
  const error = new Error("Only Genre and Vibe definitions can be managed.");
  error.status = 400;
  throw error;
}

function cleanDefinitionName(value) {
  const name = typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
  if (!name || name.length > 120) {
    const error = new Error("Definition name must contain 1 to 120 characters.");
    error.status = 400;
    throw error;
  }
  return name;
}

async function listManagedTagDefinitions() {
  const [genres, vibes] = await Promise.all([
    pool.query(`SELECT g.id, g.name, count(DISTINCT sg.song_id)::int AS song_count
      FROM public.genres g LEFT JOIN public.song_genres sg ON sg.genre_id=g.id
      GROUP BY g.id ORDER BY lower(g.name), g.id`),
    pool.query(`SELECT v.id, v.name, count(DISTINCT sv.song_id)::int AS song_count
      FROM public.vibes v LEFT JOIN public.song_vibes sv ON sv.vibe_id=v.id
      GROUP BY v.id ORDER BY lower(v.name), v.id`),
  ]);
  return { genres: genres.rows, vibes: vibes.rows };
}

async function createTagDefinition(category, rawName) {
  const { table } = tagDefinitionTable(category);
  const name = cleanDefinitionName(rawName);
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await lockTagDefinitionNames(client, category, [name]);
    const { rows } = await client.query(`INSERT INTO ${table}(name) VALUES($1) RETURNING id,name`, [name]);
    await client.query("COMMIT");
    return { ...rows[0], category, song_count: 0 };
  } catch (error) {
    await client.query("ROLLBACK").catch(() => {});
    if (error.code === "23505") { error.status = 409; error.message = `A ${category} named “${name}” already exists.`; }
    throw error;
  } finally { client.release(); }
}

async function mutateTagDefinition(category, id, mutation) {
  const { table, join, fk } = tagDefinitionTable(category);
  if (!Number.isSafeInteger(id) || id < 1 || !mutation || typeof mutation !== "object") {
    const error = new Error("A valid definition ID and operation are required."); error.status = 400; throw error;
  }
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const source = await client.query(`SELECT id,name FROM ${table} WHERE id=$1`, [id]);
    if (!source.rowCount) { const error = new Error("Definition not found."); error.status = 404; throw error; }
    const mode = mutation.mode;
    let replacementId = null;
    let replacementName = null;
    if (!Object.hasOwn(mutation, "name")) {
      if (!['remove', 'reassign'].includes(mode)) {
        const error = new Error("Choose whether to remove these assignments or reassign them."); error.status = 400; throw error;
      }
      if (mode === 'reassign') {
        replacementId = Number(mutation.replacement_id);
        if (!Number.isSafeInteger(replacementId) || replacementId < 1 || replacementId === id) {
          const error = new Error("Choose a different replacement definition in the same category."); error.status = 400; throw error;
        }
        const replacement = await client.query(`SELECT name FROM ${table} WHERE id=$1`, [replacementId]);
        if (!replacement.rowCount) { const error = new Error("Replacement definition not found in this category."); error.status = 400; throw error; }
        replacementName = replacement.rows[0].name;
      }
    }
    const renameName = Object.hasOwn(mutation, "name") ? cleanDefinitionName(mutation.name) : null;
    const definitionNames = [source.rows[0].name, replacementName, renameName].filter(Boolean);
    await lockTagDefinitionNames(client, category, definitionNames);

    const definitionIds = [...new Set([id, replacementId].filter(Number.isSafeInteger))].sort((a, b) => a - b);
    const lockedDefinitions = await client.query(
      `SELECT id,name FROM ${table} WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE`, [definitionIds]);
    const lockedById = new Map(lockedDefinitions.rows.map(row => [row.id, row]));
    const lockedSource = lockedById.get(id);
    if (!lockedSource) { const error = new Error("Definition no longer exists."); error.status = 404; throw error; }
    if (lockedSource.name !== source.rows[0].name) {
      const error = new Error("Definition changed while this action was being prepared. Reload and retry."); error.status = 409; throw error;
    }
    if (replacementId && !lockedById.has(replacementId)) {
      const error = new Error("Replacement definition no longer exists in this category."); error.status = 400; throw error;
    }

    const initiallyAffected = await client.query(`SELECT DISTINCT song_id FROM ${join} WHERE ${fk}=$1 ORDER BY song_id`, [id]);
    const songIds = initiallyAffected.rows.map(row => row.song_id);
    if (songIds.length) {
      await client.query("SELECT id FROM public.songs WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE", [songIds]);
    }
    // Definition-key locks prevent new assignments while this count is read;
    // song locks serialize direct removals before mutation and version bumps.
    const currentAffected = await client.query(`SELECT DISTINCT song_id FROM ${join} WHERE ${fk}=$1 ORDER BY song_id`, [id]);
    const currentSongIds = currentAffected.rows.map(row => row.song_id);
    if (currentSongIds.some(songId => !songIds.includes(songId))) {
      const error = new Error("Assignments changed while this action was being prepared. Reload the usage count and confirm again.");
      error.status = 409;
      throw error;
    }
    const affectedSongIds = currentSongIds;

    let result;
    if (Object.hasOwn(mutation, "name")) {
      await client.query(`UPDATE ${table} SET name=$1 WHERE id=$2`, [renameName, id]);
      result = { id, name: renameName, category, affected_songs: affectedSongIds.length };
    } else {
      if (!Number.isSafeInteger(mutation.expected_song_count) || mutation.expected_song_count !== affectedSongIds.length) {
        const error = new Error("Assignment count changed. Reload the definition and confirm the current song count.");
        error.status = 409;
        throw error;
      }
      if (mode === "reassign") {
        if (category === "Genre") {
          for (const songId of affectedSongIds) {
            const from = await client.query("SELECT is_primary FROM public.song_genres WHERE song_id=$1 AND genre_id=$2", [songId, id]);
            if (!from.rowCount) continue;
            const to = await client.query("SELECT is_primary FROM public.song_genres WHERE song_id=$1 AND genre_id=$2", [songId, replacementId]);
            if (to.rowCount) {
              await client.query("DELETE FROM public.song_genres WHERE song_id=$1 AND genre_id=$2", [songId, id]);
              if (from.rows[0].is_primary && !to.rows[0].is_primary) {
                await client.query("UPDATE public.song_genres SET is_primary=false WHERE song_id=$1 AND is_primary", [songId]);
                await client.query("UPDATE public.song_genres SET is_primary=true WHERE song_id=$1 AND genre_id=$2", [songId, replacementId]);
              }
            } else {
              await client.query("UPDATE public.song_genres SET genre_id=$1 WHERE song_id=$2 AND genre_id=$3", [replacementId, songId, id]);
            }
          }
        } else {
          // A song may already carry both Vibes. Remove only the redundant
          // source link in that case; moving it directly would violate the
          // (song_id, vibe_id) key. All affected song rows are locked above.
          await client.query(`DELETE FROM ${join} source USING ${join} target
            WHERE source.${fk}=$1 AND target.${fk}=$2 AND source.song_id=target.song_id`, [id, replacementId]);
          await client.query(`UPDATE ${join} SET ${fk}=$1 WHERE ${fk}=$2`, [replacementId, id]);
        }
      } else {
        await client.query(`DELETE FROM ${join} WHERE ${fk}=$1`, [id]);
        if (category === "Genre" && songIds.length) {
          await client.query(`UPDATE public.song_genres sg SET is_primary=true
            WHERE sg.song_id=ANY($1::int[]) AND NOT EXISTS
              (SELECT 1 FROM public.song_genres p WHERE p.song_id=sg.song_id AND p.is_primary)
              AND sg.genre_id=(SELECT min(next.genre_id) FROM public.song_genres next WHERE next.song_id=sg.song_id)`, [songIds]);
        }
      }
      await client.query(`DELETE FROM ${table} WHERE id=$1`, [id]);
      result = { id, name: lockedSource.name, category, mode, replacement_id: mode === 'reassign' ? replacementId : null, affected_songs: affectedSongIds.length };
    }
    // A shared definition edit changes each affected song's observable tag set.
    // The existing strict-version trigger advances updated_at and makes queued
    // stale song edits follow the established 409/server-wins path.
    if (affectedSongIds.length) await client.query("UPDATE public.songs SET updated_at=updated_at WHERE id=ANY($1::int[])", [affectedSongIds]);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    if (error.code === "23505") { error.status = 409; error.message = "That definition conflicts with an existing name or assignment."; }
    throw error;
  } finally { client.release(); }
}

// ---------------------------------------------------------------- song tags

// Add a tag to a song. Category routes to the right table: Genre -> genres
// (secondary), Feel/Vibe -> vibes. Era is derived and not directly taggable.
async function addSongTag(songId, name, category, baseUpdatedAt) {
  const cat = String(category || "").toLowerCase();
  if (!["genre", "feel", "vibe"].includes(cat)) {
    const error = new Error("Only Genre or Vibe assignments are supported.");
    error.status = 400;
    error.code = "INVALID_TAG_INPUT";
    error.category = category || undefined;
    throw error;
  }
  const definitionCategory = cat === "genre" ? "Genre" : "Vibe";
  const cleanName = normalizeDefinitionName(name, definitionCategory);
  validateVersionToken(baseUpdatedAt, true);
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const current = await client.query(
      `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
       FROM songs WHERE id = $1`, [songId]);
    if (!current.rows.length) {
      const error = new Error("Song not found"); error.status = 404; throw error;
    }
    if (!sameVersion(current.rows[0].updated_at, baseUpdatedAt)) throw conflictError();
    const definition = await findTagDefinition(client, definitionCategory, cleanName);
    const locked = await client.query(
      `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
       FROM songs WHERE id = $1 FOR UPDATE`, [songId]);
    if (!locked.rows.length) {
      const error = new Error("Song not found"); error.status = 404; throw error;
    }
    if (!sameVersion(locked.rows[0].updated_at, baseUpdatedAt)) throw conflictError();
    if (cat === "genre") {
      await client.query(
        `INSERT INTO song_genres (song_id, genre_id, is_primary)
         VALUES ($1,$2,NOT EXISTS (SELECT 1 FROM song_genres WHERE song_id=$1 AND is_primary))
         ON CONFLICT (song_id, genre_id) DO NOTHING`, [songId, definition.id]);
    } else {
      await client.query(
        `INSERT INTO song_vibes (song_id, vibe_id) VALUES ($1,$2)
         ON CONFLICT DO NOTHING`, [songId, definition.id]);
    }
    await client.query("UPDATE songs SET updated_at = now() WHERE id = $1", [songId]);
    await client.query("COMMIT");
  } catch (e) { await client.query("ROLLBACK"); throw e; }
  finally { client.release(); }
  return getSongFull(songId);
}

async function removeSongTag(songId, name, category, baseUpdatedAt) {
  const cat = String(category || "").toLowerCase();
  const cleanName = String(name || "").trim().replace(/\s+/g, " ");
  if (!cleanName || !["genre", "feel", "vibe"].includes(cat)) {
    const error = new Error("A tag name and Genre or Feel category are required");
    error.status = 400;
    throw error;
  }
  validateVersionToken(baseUpdatedAt, true);
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const locked = await client.query(
      `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
       FROM songs WHERE id = $1 FOR UPDATE`, [songId]);
    if (!locked.rows.length) {
      const error = new Error("Song not found"); error.status = 404; throw error;
    }
    if (!sameVersion(locked.rows[0].updated_at, baseUpdatedAt)) throw conflictError();
    if (cat === "genre") {
      await client.query(
        `DELETE FROM song_genres sg USING genres g
         WHERE sg.genre_id=g.id AND sg.song_id=$1 AND LOWER(g.name)=LOWER($2)`, [songId, cleanName]);
      await client.query(
        `UPDATE song_genres SET is_primary = true
         WHERE song_id=$1 AND genre_id=(
           SELECT genre_id FROM song_genres WHERE song_id=$1 ORDER BY genre_id LIMIT 1
         ) AND NOT EXISTS (SELECT 1 FROM song_genres WHERE song_id=$1 AND is_primary)`, [songId]);
    } else {
      await client.query(
        `DELETE FROM song_vibes sv USING vibes v
         WHERE sv.vibe_id=v.id AND sv.song_id=$1 AND LOWER(v.name)=LOWER($2)`, [songId, cleanName]);
    }
    await client.query("UPDATE songs SET updated_at = now() WHERE id = $1", [songId]);
    await client.query("COMMIT");
  } catch (e) { await client.query("ROLLBACK"); throw e; }
  finally { client.release(); }
  return getSongFull(songId);
}

// ---------------------------------------------------------------- setlists

const SETLIST_KEY_NAMES = [
  "C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#",
  "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb",
];
const SETLIST_KEYS = new Set([
  ...SETLIST_KEY_NAMES,
  ...SETLIST_KEY_NAMES.map(key => `${key}m`),
]);

function badSetlistRequest(message) {
  const error = new Error(message);
  error.status = 400;
  return error;
}

function validDateOnly(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const monthLengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= monthLengths[month - 1];
}

function requireSetlistName(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw badSetlistRequest("Setlist name is required and cannot be blank.");
  }
  return value.trim();
}

function requireSetlistData(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw badSetlistRequest("Setlist changes must be sent as an object.");
  }
}

async function listSetlists() {
  const { rows } = await pool.query(
    `SELECT sl.id, sl.name, to_char(sl.gig_date,'YYYY-MM-DD') AS gig_date, sl.notes, sl.created_at,
            to_char(sl.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
            count(ss.song_id)::int AS song_count
     FROM setlists sl LEFT JOIN setlist_songs ss ON ss.setlist_id = sl.id
     GROUP BY sl.id ORDER BY sl.gig_date DESC NULLS LAST, sl.name`
  );
  return rows;
}

async function getSetlist(id) {
  const { rows } = await pool.query(
    `SELECT sl.id, sl.name, to_char(sl.gig_date,'YYYY-MM-DD') AS gig_date, sl.notes, sl.created_at,
            to_char(sl.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
            COALESCE((
              SELECT json_agg(to_jsonb(entry_row) ORDER BY entry_row.position)
                FROM (
                  SELECT ss.id::text AS entry_id, ss.position, ss.transposed_key, ss.capo_fret, ss.notes,
                         s.id AS song_id, s.title, a.name AS artist,
                         s.performance_key AS chart_written_key, s.capo_fret AS default_capo,
                         s.bb_structure AS beatbuddy_structure, s.bpm,
                         (SELECT v.name FROM song_vibes sv JOIN vibes v ON v.id=sv.vibe_id
                          WHERE sv.song_id = s.id ORDER BY v.name LIMIT 1) AS feel
                    FROM public.setlist_songs ss
                    JOIN public.songs s ON s.id = ss.song_id
                    JOIN public.artists a ON a.id = s.artist_id
                   WHERE ss.setlist_id = sl.id
                ) AS entry_row
            ), '[]'::json) AS songs
       FROM public.setlists sl WHERE sl.id = $1`, [id]);
  return rows[0] || null;
}

async function createSetlist(data) {
  requireSetlistData(data);
  const name = requireSetlistName(data.name);
  let gigDate = data.gig_date ?? null;
  if (gigDate === "") gigDate = null;
  if (gigDate !== null && !validDateOnly(gigDate)) {
    throw badSetlistRequest("gig_date must be a valid YYYY-MM-DD date or null.");
  }
  const notes = data.notes ?? null;
  if (notes !== null && typeof notes !== "string") {
    throw badSetlistRequest("Setlist notes must be text or null.");
  }
  const { rows } = await pool.query(
    `INSERT INTO setlists (name, gig_date, notes) VALUES ($1,$2,$3) RETURNING id`,
    [name, gigDate, notes?.trim() || null]
  );
  return getSetlist(rows[0].id);
}

async function updateSetlist(id, data) {
  if (!Number.isInteger(id)) throw badSetlistRequest("Setlist ID must be a valid integer.");
  requireSetlistData(data);
  const sets = [], vals = [];
  for (const k of ["name", "gig_date", "notes"]) {
    if (!Object.hasOwn(data, k)) continue;
    let value = data[k];
    if (k === "name") value = requireSetlistName(value);
    if (k === "gig_date") {
      if (value === "") value = null;
      if (value !== null && !validDateOnly(value)) {
        throw badSetlistRequest("gig_date must be a valid YYYY-MM-DD date or null.");
      }
    }
    if (k === "notes") {
      if (value !== null && typeof value !== "string") {
        throw badSetlistRequest("Setlist notes must be text or null.");
      }
      value = value?.trim() || null;
    }
    vals.push(value);
    sets.push(`${k} = $${vals.length}`);
  }
  if (!sets.length) throw badSetlistRequest("At least one setlist field must be provided.");
  vals.push(id);
  const result = await pool.query(
    `UPDATE setlists SET ${sets.join(", ")} WHERE id = $${vals.length}`,
    vals,
  );
  if (!result.rowCount) {
    const error = new Error("Setlist not found.");
    error.status = 404;
    throw error;
  }
  return getSetlist(id);
}

async function deleteSetlist(id) {
  if (!Number.isInteger(id)) throw badSetlistRequest("Setlist ID must be a valid integer.");
  const result = await pool.query("DELETE FROM setlists WHERE id = $1", [id]);
  if (!result.rowCount) {
    const error = new Error("Setlist not found.");
    error.status = 404;
    throw error;
  }
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

async function updateSetlistSong(id, position, data) {
  requireSetlistData(data);
  if (!Number.isInteger(id) || !Number.isInteger(position) || position < 1) {
    const error = new Error("Setlist and entry position must be valid integers.");
    error.status = 400;
    throw error;
  }
  const sets = [], values = [];
  if (Object.hasOwn(data, "transposed_key")) {
    const key = data.transposed_key;
    if (key !== null && typeof key !== "string") {
      const error = new Error("transposed_key must be a string or null.");
      error.status = 400;
      throw error;
    }
    const normalized = key?.trim() || null;
    if (normalized !== null && !SETLIST_KEYS.has(normalized)) {
      throw badSetlistRequest("transposed_key must use a supported key name (for example, G or Bb).");
    }
    values.push(normalized);
    sets.push(`transposed_key=$${values.length}`);
  }
  if (Object.hasOwn(data, "capo_fret")) {
    const capo = data.capo_fret;
    if (capo !== null && (!Number.isInteger(capo) || capo < 0 || capo > 11)) {
      const error = new Error("capo_fret must be an integer from 0 to 11 or null.");
      error.status = 400;
      throw error;
    }
    values.push(capo);
    sets.push(`capo_fret=$${values.length}`);
  }
  if (Object.hasOwn(data, "notes")) {
    const notes = data.notes;
    if (notes !== null && typeof notes !== "string") {
      throw badSetlistRequest("notes must be text or null.");
    }
    values.push(notes?.trim() || null);
    sets.push(`notes=$${values.length}`);
  }
  if (!sets.length) {
    const error = new Error("No setlist-entry override fields were provided.");
    error.status = 400;
    throw error;
  }
  values.push(id, position);
  const result = await pool.query(
    `UPDATE setlist_songs SET ${sets.join(", ")} WHERE setlist_id=$${values.length - 1} AND position=$${values.length}`,
    values,
  );
  if (!result.rowCount) {
    const error = new Error("Setlist entry not found.");
    error.status = 404;
    throw error;
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
  getTags, listManagedTagDefinitions, createTagDefinition, mutateTagDefinition,
  addSongTag, removeSongTag, listSetlists, getSetlist, createSetlist,
  updateSetlist, deleteSetlist, addSetlistSong, updateSetlistSong, removeSetlistSong,
  reorderSetlist, getStats,
};
