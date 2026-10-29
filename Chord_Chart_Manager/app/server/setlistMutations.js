const crypto = require("crypto");
const { pool } = require("./db");
const { getSetlist: readSetlist } = require("./db_ext");

const KEY_NAMES = ["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"];
const VALID_KEYS = new Set([...KEY_NAMES, ...KEY_NAMES.map(key => `${key}m`)]);
const EXACT_VERSION = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;

function apiError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function exactToken(value, required) {
  if (value == null && !required) return;
  if (typeof value !== "string" || !EXACT_VERSION.test(value)) {
    throw apiError(400, "base_updated_at must be an exact six-digit UTC timestamp token");
  }
}

function checkSetlistVersion(current, expected) {
  exactToken(expected, true);
  if (current !== expected) throw apiError(409, "Setlist changed on the server; server version kept");
}

function validName(value) {
  if (typeof value !== "string" || !value.trim()) throw apiError(400, "Setlist name is required");
  return value.trim();
}

function validateMetadata(payload) {
  const values = {};
  for (const field of ["name", "gig_date", "notes"]) {
    if (!Object.hasOwn(payload, field)) continue;
    let value = payload[field];
    if (field === "name") value = validName(value);
    if (field === "gig_date" && value === "") value = null;
    if (field === "gig_date" && value != null && !validDateOnly(value)) {
      throw apiError(400, "gig_date must be a valid YYYY-MM-DD date or null");
    }
    if (field === "notes" && value != null && typeof value !== "string") throw apiError(400, "notes must be text or null");
    values[field] = field === "notes" ? (value?.trim() || null) : value;
  }
  if (!Object.keys(values).length) throw apiError(400, "No setlist fields were provided");
  return values;
}

function validDateOnly(value) {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const [, y, m, d] = match.map(Number);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return y > 0 && m >= 1 && m <= 12 && d >= 1 && d <= days[m - 1];
}

function validateEntryPatch(payload, allowed, allowEmpty = false) {
  const values = {};
  for (const field of allowed) {
    if (!Object.hasOwn(payload, field)) continue;
    const value = payload[field];
    if (field === "transposed_key") {
      if (value !== null && (typeof value !== "string" || !VALID_KEYS.has(value.trim()))) throw apiError(400, "transposed_key must be a supported key or null");
      values[field] = value?.trim() || null;
    } else if (field === "capo_fret") {
      if (value !== null && (!Number.isInteger(value) || value < 0 || value > 11)) throw apiError(400, "capo_fret must be an integer from 0 to 11 or null");
      values[field] = value;
    } else {
      if (value !== null && typeof value !== "string") throw apiError(400, "notes must be text or null");
      values[field] = value?.trim() || null;
    }
  }
  if (!Object.keys(values).length && !allowEmpty) throw apiError(400, "No entry fields were provided");
  return values;
}

// Mutation responses (and their stored receipts) also carry song_count.
async function getSetlist(client, id) {
  const setlist = await readSetlist(id, client);
  if (setlist) setlist.song_count = setlist.songs.length;
  return setlist;
}

async function readReceipt(client, operationId, requestHash) {
  const result = await client.query(
    "SELECT request_hash, response FROM public.mutation_receipts WHERE operation_id = $1", [operationId]);
  if (!result.rows.length) return null;
  if (result.rows[0].request_hash !== requestHash) throw apiError(409, "Operation ID was already used for a different request");
  return result.rows[0].response;
}

async function applySetlistMutation(operation) {
  if (!operation || typeof operation !== "object" || Array.isArray(operation)) throw apiError(400, "Mutation must be an object");
  const { operation_id: operationId, type, payload = {} } = operation;
  if (typeof operationId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(operationId)) {
    throw apiError(400, "operation_id must be a UUID");
  }
  const canonical = { entity: "setlist", setlist_id: operation.setlist_id ?? null, type, base_updated_at: operation.base_updated_at ?? null, payload };
  const requestHash = crypto.createHash("sha256").update(stableJson(canonical)).digest("hex");
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    // Serialize retries for this id before reading/inserting the receipt.
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [operationId]);
    const replay = await readReceipt(client, operationId, requestHash);
    if (replay) {
      await client.query("COMMIT");
      return replay;
    }

    let id = operation.setlist_id;
    let result;
    if (type === "create") {
      const values = validateMetadata(payload);
      if (!Object.hasOwn(values, "name")) throw apiError(400, "Setlist name is required");
      const inserted = await client.query(
        `INSERT INTO public.setlists(name,gig_date,notes) VALUES($1,$2,$3) RETURNING id`,
        [values.name, values.gig_date ?? null, values.notes ?? null]);
      id = inserted.rows[0].id;
      result = await getSetlist(client, id);
    } else {
      if (!Number.isInteger(Number(id)) || Number(id) < 1) throw apiError(400, "setlist_id must be a positive integer");
      id = Number(id);
      const locked = await client.query(
        `SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
           FROM public.setlists WHERE id = $1 FOR UPDATE`, [id]);
      if (!locked.rowCount) throw apiError(409, "Setlist no longer exists; server state kept");
      checkSetlistVersion(locked.rows[0].updated_at, operation.base_updated_at);

      if (type === "metadata") {
        const values = validateMetadata(payload);
        const keys = Object.keys(values);
        await client.query(`UPDATE public.setlists SET ${keys.map((key, i) => `${key}=$${i + 1}`).join(",")} WHERE id=$${keys.length + 1}`,
          [...keys.map(key => values[key]), id]);
      } else if (type === "delete") {
        await client.query("DELETE FROM public.setlists WHERE id=$1", [id]);
        result = { ok: true, deleted_setlist_id: id };
      } else if (type === "entry_add") {
        const songId = Number(payload.song_id);
        if (!Number.isInteger(songId) || songId < 1) throw apiError(400, "song_id must be a positive integer");
        const referencedSong = await client.query("SELECT 1 FROM public.songs WHERE id=$1", [songId]);
        if (!referencedSong.rowCount) throw apiError(409, "Referenced song no longer exists; server setlist kept");
        const entry = validateEntryPatch(payload, ["transposed_key", "capo_fret", "notes"], true);
        const position = (await client.query("SELECT COALESCE(MAX(position),0)+1 AS n FROM public.setlist_songs WHERE setlist_id=$1", [id])).rows[0].n;
        const insertedEntry = await client.query(
          `INSERT INTO public.setlist_songs(setlist_id,song_id,position,transposed_key,capo_fret,notes)
           VALUES($1,$2,$3,$4,$5,$6) RETURNING id`,
          [id, songId, position, entry.transposed_key ?? null, entry.capo_fret ?? null, entry.notes ?? null]);
        result = await getSetlist(client, id);
        result.created_entry_id = insertedEntry.rows[0].id.toString();
      } else if (type === "entry_edit") {
        const entryId = String(payload.entry_id ?? "");
        if (!/^\d+$/.test(entryId)) throw apiError(400, "entry_id must be a server entry ID");
        const values = validateEntryPatch(payload, ["transposed_key", "capo_fret", "notes"]);
        const keys = Object.keys(values);
        const updated = await client.query(
          `UPDATE public.setlist_songs SET ${keys.map((key, i) => `${key}=$${i + 1}`).join(",")} WHERE setlist_id=$${keys.length + 1} AND id=$${keys.length + 2}`,
          [...keys.map(key => values[key]), id, entryId]);
        if (!updated.rowCount) throw apiError(409, "Setlist entry no longer exists; server state kept");
      } else if (type === "entry_remove") {
        const entryId = String(payload.entry_id ?? "");
        if (!/^\d+$/.test(entryId)) throw apiError(400, "entry_id must be a server entry ID");
        const removed = await client.query("DELETE FROM public.setlist_songs WHERE setlist_id=$1 AND id=$2 RETURNING position", [id, entryId]);
        if (!removed.rowCount) throw apiError(409, "Setlist entry no longer exists; server state kept");
        await client.query("UPDATE public.setlist_songs SET position=-position WHERE setlist_id=$1", [id]);
        await client.query(`UPDATE public.setlist_songs
          SET position = -position - CASE WHEN -position > $2 THEN 1 ELSE 0 END
          WHERE setlist_id=$1 AND position < 0`, [id, removed.rows[0].position]);
      } else if (type === "reorder") {
        const entryIds = payload.entry_ids;
        if (!Array.isArray(entryIds) || entryIds.some(entryId => !/^\d+$/.test(String(entryId))) || new Set(entryIds.map(String)).size !== entryIds.length) {
          throw apiError(400, "entry_ids must be a unique ordered list of entry IDs");
        }
        const current = await client.query("SELECT id::text FROM public.setlist_songs WHERE setlist_id=$1 ORDER BY position", [id]);
        if (stableJson(current.rows.map(row => row.id).sort()) !== stableJson(entryIds.map(String).sort())) {
          throw apiError(400, "entry_ids must contain every current setlist entry exactly once");
        }
        await client.query("UPDATE public.setlist_songs SET position=-position WHERE setlist_id=$1", [id]);
        for (let index = 0; index < entryIds.length; index += 1) {
          await client.query("UPDATE public.setlist_songs SET position=$1 WHERE setlist_id=$2 AND id=$3", [index + 1, id, String(entryIds[index])]);
        }
        // An empty order has no entry rows to fire the parent-version trigger.
        if (!entryIds.length) await client.query("UPDATE public.setlists SET updated_at=updated_at WHERE id=$1", [id]);
      } else {
        throw apiError(400, `Unsupported setlist mutation type: ${type}`);
      }
      if (!result) result = await getSetlist(client, id);
    }
    await client.query(
      `INSERT INTO public.mutation_receipts(operation_id,request_hash,entity_type,resource_id,response)
       VALUES($1,$2,'setlist',$3,$4::jsonb)`, [operationId, requestHash, String(id), JSON.stringify(result)]);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    try { await client.query("ROLLBACK"); } catch { /* keep the original error */ }
    throw error;
  } finally {
    client.release();
  }
}

module.exports = { applySetlistMutation };
