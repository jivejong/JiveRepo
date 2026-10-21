const crypto = require("node:crypto");
const { protectedSnapshotSha256, verifyRestoreProof } = require("./mainEnrichmentSafety.js");
const { buildEnrichmentPreview } = require("./existingSongEnrichment");
const { definitionKey, lockTagDefinitionNames, normalizeDefinitionName } = require("./tagDefinitionNames");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const VERSION = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{6})Z$/;
const SOURCES = new Set(["MusicBrainz", "AcousticBrainz", "Gemini"]);
const FIELD_SOURCES = {
  release_year: new Set(["MusicBrainz", "Gemini"]),
  original_key: new Set(["AcousticBrainz", "Gemini"]),
  bpm: new Set(["AcousticBrainz", "Gemini"]),
};
const GEMINI_PROVENANCE = Object.freeze({ model_id: "gemini-3.1-flash-lite",
  prompt_version: "missing-metadata-fallback-v2", schema_version: "missing-metadata-schema-v2" });
const LEGACY_GEMINI_PROMPT_VERSIONS = new Set(["missing-metadata-fallback-v1", GEMINI_PROVENANCE.prompt_version]);
const SUPPORTED_GEMINI_SCHEMA_VERSIONS = new Set(["missing-metadata-schema-v1", GEMINI_PROVENANCE.schema_version]);
const SCALAR_FIELDS = ["release_year", "original_key", "bpm"];
const SCALAR_SQL_COLUMNS = Object.freeze({ release_year: "release_year", original_key: "original_key", bpm: "bpm" });
const MAX_INTEGER_ID = 2_147_483_647;
const UNCHANGED_TABLES = ["artists", "genres", "vibes", "setlists", "setlist_songs", "mutation_receipts", "schema_migrations"];
const TABLES = [
  ["artists", "public.artists", "id::text", "id"],
  ["genres", "public.genres", "id::text", "id"],
  ["vibes", "public.vibes", "id::text", "id"],
  ["songs", "public.songs", "id::text", "id"],
  ["song_genres", "public.song_genres", "song_id::text || ':' || genre_id::text", "song_id,genre_id"],
  ["song_vibes", "public.song_vibes", "song_id::text || ':' || vibe_id::text", "song_id,vibe_id"],
  ["setlists", "public.setlists", "id::text", "id"],
  ["setlist_songs", "public.setlist_songs", "id::text", "id"],
  ["mutation_receipts", "public.mutation_receipts", "operation_id::text", "operation_id"],
  ["schema_migrations", "public.schema_migrations", "version::text", "version"],
  ["song_enrichment_runs", "public.song_enrichment_runs", "operation_id::text", "operation_id"],
];

function fail(code, message, songId) {
  const error = new Error(message);
  error.name = "SongEnrichmentApplyError";
  error.code = code;
  if (Number.isSafeInteger(songId)) error.song_id = songId;
  return error;
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}

function exactKeys(value, required, optional = []) {
  if (!isRecord(value)) return false;
  const allowed = new Set([...required, ...optional]);
  return required.every(key => Object.hasOwn(value, key)) &&
    Object.keys(value).every(key => allowed.has(key));
}

function validVersion(value) {
  if (typeof value !== "string") return false;
  const match = VERSION.exec(value);
  if (!match) return false;
  const [, y, mo, d, h, mi, s] = match;
  const year = Number(y), month = Number(mo), day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] &&
    Number(h) <= 23 && Number(mi) <= 59 && Number(s) <= 59;
}

function validateJsonTree(value, seen = new Set()) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) {
    if (seen.has(value)) return false;
    if (Reflect.ownKeys(value).some(key => key !== "length" &&
        (typeof key !== "string" || !/^(0|[1-9]\d*)$/.test(key) || Number(key) >= value.length ||
          !Object.getOwnPropertyDescriptor(value, key)?.enumerable || !Object.hasOwn(Object.getOwnPropertyDescriptor(value, key) || {}, "value"))) ||
        Array.from({ length: value.length }, (_, index) => Object.hasOwn(value, index)).some(hasIndex => !hasIndex)) return false;
    seen.add(value);
    const valid = value.every(item => validateJsonTree(item, seen));
    seen.delete(value);
    return valid;
  }
  if (!isRecord(value)) return false;
  if (seen.has(value)) return false;
  if (Reflect.ownKeys(value).some(key => typeof key !== "string" ||
      !Object.getOwnPropertyDescriptor(value, key)?.enumerable ||
      !Object.hasOwn(Object.getOwnPropertyDescriptor(value, key) || {}, "value"))) return false;
  seen.add(value);
  const valid = Object.values(value).every(item => validateJsonTree(item, seen));
  seen.delete(value);
  return valid;
}

function validEvidenceRef(item, allowed = SOURCES) {
  if (!item || !allowed.has(item.source) || typeof item.response_digest !== "string" ||
      !/^[A-Za-z0-9._:-]{1,160}$/.test(item.response_digest)) return false;
  if (item.source === "Gemini") return exactKeys(item, ["source", "response_digest", ...Object.keys(GEMINI_PROVENANCE)]) &&
    /^[a-f0-9]{64}$/.test(item.response_digest) && item.model_id === GEMINI_PROVENANCE.model_id &&
    LEGACY_GEMINI_PROMPT_VERSIONS.has(item.prompt_version) && SUPPORTED_GEMINI_SCHEMA_VERSIONS.has(item.schema_version);
  if (item.submission_id !== undefined && (typeof item.submission_id !== "string" ||
      item.source === "AcousticBrainz" && !/^[0-9a-f-]{36}:\d+$/i.test(item.submission_id) ||
      item.source === "MusicBrainz" && !/^recording:[0-9a-f-]{36}$/i.test(item.submission_id) || item.source === "Gemini")) return false;
  return exactKeys(item, ["source", "response_digest"], ["submission_id"]);
}

function isGeminiOnlyEvidence(evidence) {
  const items = [
    ...Object.values(evidence.scalars).flat(), ...evidence.genres, ...evidence.vibes,
  ];
  return items.length > 0 && items.every(item => item.source === "Gemini");
}

function validCandidateValue(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean" ||
      typeof value === "number" && Number.isFinite(value)) return true;
  return exactKeys(value, ["key_key", "key_scale"]) &&
    typeof value.key_key === "string" && typeof value.key_scale === "string";
}

function validateEvidence(evidence, songId) {
  if (!exactKeys(evidence, ["mbid", "scalars", "genres", "vibes"]) ||
      evidence.mbid !== null && (typeof evidence.mbid !== "string" || !UUID.test(evidence.mbid))) {
    throw fail("INVALID_MANIFEST", "Evidence shape is invalid.", songId);
  }
  {
    if (!isRecord(evidence.scalars) || Object.keys(evidence.scalars).some(key => !SCALAR_FIELDS.includes(key))) {
      throw fail("INVALID_MANIFEST", "Scalar evidence shape is invalid.", songId);
    }
    for (const [field, candidates] of Object.entries(evidence.scalars)) {
      if (!Array.isArray(candidates) || !candidates.every(item => {
        if (!exactKeys(item, ["value", "source", "response_digest"], ["submission_id", "association", ...Object.keys(GEMINI_PROVENANCE)]) ||
            !validCandidateValue(item.value) || !SOURCES.has(item.source) ||
            typeof item.response_digest !== "string" || !/^[A-Za-z0-9._:-]{1,160}$/.test(item.response_digest)) return false;
        if (item.submission_id !== undefined && (typeof item.submission_id !== "string" || item.submission_id.length > 200)) return false;
        if (item.association !== undefined && !["genre", "tag"].includes(item.association)) return false;
        return (item.source === "Gemini" ? exactKeys(item, ["value", "source", "response_digest", ...Object.keys(GEMINI_PROVENANCE)]) &&
          validEvidenceRef({ source: item.source, response_digest: item.response_digest,
            model_id: item.model_id, prompt_version: item.prompt_version, schema_version: item.schema_version }) :
          !Object.keys(GEMINI_PROVENANCE).some(key => Object.hasOwn(item, key))) &&
          FIELD_SOURCES[field].has(item.source) && (field === "original_key"
          ? typeof item.value === "string" || exactKeys(item.value, ["key_key", "key_scale"]) &&
            typeof item.value.key_key === "string" && typeof item.value.key_scale === "string"
          : typeof item.value === "number" && Number.isFinite(item.value));
      })) throw fail("INVALID_MANIFEST", "Scalar evidence candidate is invalid.", songId);
    }
  }
  for (const field of ["genres", "vibes"]) {
    const labels = evidence[field];
    if (!Array.isArray(labels) || !labels.every(item =>
      exactKeys(item, ["name", "source", "response_digest"], ["association", ...Object.keys(GEMINI_PROVENANCE)]) &&
      typeof item.name === "string" && SOURCES.has(item.source) &&
      typeof item.response_digest === "string" && /^[A-Za-z0-9._:-]{1,160}$/.test(item.response_digest) &&
      (item.source === "Gemini" ? exactKeys(item, ["name", "source", "response_digest", ...Object.keys(GEMINI_PROVENANCE)]) &&
        validEvidenceRef({ source: item.source, response_digest: item.response_digest,
          model_id: item.model_id, prompt_version: item.prompt_version, schema_version: item.schema_version }) :
        !Object.keys(GEMINI_PROVENANCE).some(key => Object.hasOwn(item, key))) &&
      (item.association === undefined || ["genre", "tag"].includes(item.association)))) {
      throw fail("INVALID_MANIFEST", "Tag evidence shape is invalid.", songId);
    }
    for (const item of labels || []) {
      try { normalizeDefinitionName(item.name, field === "genres" ? "Genre" : "Vibe"); }
      catch { throw fail("INVALID_MANIFEST", "Tag evidence label is invalid.", songId); }
    }
  }
}

function validateDecision(decision, songId) {
  if (!exactKeys(decision, ["mbid", "scalars", "genres", "vibes"], ["primaryGenre"]) ||
      decision.mbid !== null && (typeof decision.mbid !== "string" || !UUID.test(decision.mbid))) {
    throw fail("INVALID_MANIFEST", "Review decision shape is invalid.", songId);
  }
  {
    if (!isRecord(decision.scalars) || Object.keys(decision.scalars).some(key => !SCALAR_FIELDS.includes(key))) {
      throw fail("INVALID_MANIFEST", "Scalar review selection shape is invalid.", songId);
    }
    for (const [field, selection] of Object.entries(decision.scalars)) {
      const validValue = field === "original_key"
        ? typeof selection?.value === "string" || exactKeys(selection?.value, ["key_key", "key_scale"]) &&
          typeof selection.value.key_key === "string" && typeof selection.value.key_scale === "string"
        : typeof selection?.value === "number" && Number.isFinite(selection.value);
      if (!exactKeys(selection, ["value", "source", "response_digest"], ["submission_id", "model_id", "prompt_version", "schema_version"]) || !validValue ||
          !SOURCES.has(selection.source) || typeof selection.response_digest !== "string" ||
          !/^[A-Za-z0-9._:-]{1,160}$/.test(selection.response_digest)) {
        throw fail("INVALID_MANIFEST", "Scalar review selection is invalid.", songId);
      }
      const selectedRef = { source: selection.source, response_digest: selection.response_digest,
        ...(selection.submission_id !== undefined ? { submission_id: selection.submission_id } : {}),
        ...(selection.model_id !== undefined ? { model_id: selection.model_id } : {}),
        ...(selection.prompt_version !== undefined ? { prompt_version: selection.prompt_version } : {}),
        ...(selection.schema_version !== undefined ? { schema_version: selection.schema_version } : {}) };
      const suppliedGeminiProvenance=["model_id","prompt_version","schema_version"].some(key=>selection[key]!==undefined);
      if (selection.source === "Gemini" && suppliedGeminiProvenance && !validEvidenceRef(selectedRef) ||
          Object.keys(selection).some(key => !["value","source","response_digest","submission_id","model_id","prompt_version","schema_version"].includes(key)) ||
          selection.submission_id !== undefined && !validEvidenceRef({source:selection.source,response_digest:selection.response_digest,submission_id:selection.submission_id})) {
        throw fail("INVALID_MANIFEST", "Scalar review provenance is invalid.", songId);
      }
    }
  }
  for (const field of ["genres", "vibes"]) {
    if (!Array.isArray(decision[field]) || !decision[field].every(name => typeof name === "string")) {
      throw fail("INVALID_MANIFEST", "Tag review selections must be string lists.", songId);
    }
    for (const name of decision[field] || []) {
      try { normalizeDefinitionName(name, field === "genres" ? "Genre" : "Vibe"); }
      catch { throw fail("INVALID_MANIFEST", "Tag review selection is invalid.", songId); }
    }
  }
  if (decision.primaryGenre !== undefined && decision.primaryGenre !== null && typeof decision.primaryGenre !== "string") {
    throw fail("INVALID_MANIFEST", "Primary Genre selection must be text or null.", songId);
  }
}

function validatePreview(preview, songId) {
  const top = ["format_version", "song_id", "base_version", "approved_mbid", "scalar_changes", "tag_additions", "primary_proposal", "diagnostics", "has_changes"];
  if (!exactKeys(preview, top) || preview.format_version !== 1 || preview.song_id !== songId ||
      !validVersion(preview.base_version) || preview.approved_mbid !== null &&
        (typeof preview.approved_mbid !== "string" || !UUID.test(preview.approved_mbid)) ||
      !Array.isArray(preview.scalar_changes) || !Array.isArray(preview.diagnostics) || typeof preview.has_changes !== "boolean" ||
      !exactKeys(preview.tag_additions, ["genres", "vibes"]) ||
      !Array.isArray(preview.tag_additions.genres) || !Array.isArray(preview.tag_additions.vibes)) {
    throw fail("INVALID_MANIFEST", "Reviewed preview shape is invalid.", songId);
  }
  for (const change of preview.scalar_changes) {
    if (!exactKeys(change, ["field", "before", "after", "evidence"]) || !SCALAR_FIELDS.includes(change.field) ||
        !validCandidateValue(change.before) || !validCandidateValue(change.after) || !validEvidenceRef(change.evidence)) {
      throw fail("INVALID_MANIFEST", "Reviewed scalar delta is invalid.", songId);
    }
  }
  for (const category of ["genres", "vibes"]) for (const addition of preview.tag_additions[category]) {
    if (!exactKeys(addition, ["id", "name", "evidence"]) || !Number.isSafeInteger(addition.id) || addition.id < 1 || addition.id > MAX_INTEGER_ID ||
        typeof addition.name !== "string" || !Array.isArray(addition.evidence) || !addition.evidence.every(ref => validEvidenceRef(ref))) {
      throw fail("INVALID_MANIFEST", "Reviewed tag delta is invalid.", songId);
    }
  }
  if (preview.primary_proposal !== null && (!exactKeys(preview.primary_proposal, ["genre_id", "name"]) ||
      !Number.isSafeInteger(preview.primary_proposal.genre_id) || preview.primary_proposal.genre_id < 1 || preview.primary_proposal.genre_id > MAX_INTEGER_ID ||
      typeof preview.primary_proposal.name !== "string")) {
    throw fail("INVALID_MANIFEST", "Reviewed primary proposal is invalid.", songId);
  }
  for (const item of preview.diagnostics) {
    if (!exactKeys(item, ["code", "category", "field", "label"]) || typeof item.code !== "string" ||
        ![null, "Genre", "Vibe"].includes(item.category) || !(item.field === null || SCALAR_FIELDS.includes(item.field)) ||
        !(item.label === null || typeof item.label === "string")) {
      throw fail("INVALID_MANIFEST", "Reviewed diagnostics are invalid.", songId);
    }
  }
}

function validateManifest(manifest) {
  if (!exactKeys(manifest, ["format_version", "operation_id", "songs"]) || manifest.format_version !== 1 ||
      typeof manifest.operation_id !== "string" || !UUID.test(manifest.operation_id) ||
      !Array.isArray(manifest.songs) || manifest.songs.length < 1 || manifest.songs.length > 25 || !validateJsonTree(manifest)) {
    throw fail("INVALID_MANIFEST", "Enrichment manifest shape is invalid.");
  }
  const ids = new Set();
  for (const item of manifest.songs) {
    if (!exactKeys(item, ["song_id", "base_version", "identity", "evidence", "review_decision", "preview"]) ||
        !Number.isSafeInteger(item.song_id) || item.song_id < 1 || item.song_id > MAX_INTEGER_ID || ids.has(item.song_id) || !validVersion(item.base_version) ||
        !exactKeys(item.identity, ["title", "artist_id", "artist_name"]) || typeof item.identity.title !== "string" || !item.identity.title.trim() ||
        !Number.isSafeInteger(item.identity.artist_id) || item.identity.artist_id < 1 || item.identity.artist_id > MAX_INTEGER_ID ||
        typeof item.identity.artist_name !== "string" || !item.identity.artist_name.trim()) {
      throw fail("INVALID_MANIFEST", "Enrichment song entry is invalid.", item?.song_id);
    }
    ids.add(item.song_id);
    validateEvidence(item.evidence, item.song_id);
    validateDecision(item.review_decision, item.song_id);
    validatePreview(item.preview, item.song_id);
    const geminiOnly = isGeminiOnlyEvidence(item.evidence);
    if (item.evidence.mbid === null && !geminiOnly) {
      throw fail("EVIDENCE_IDENTITY_REQUIRED", "Factual-provider evidence requires an approved recording MBID.", item.song_id);
    }
    if (item.evidence.mbid !== item.review_decision.mbid) {
      throw fail("EVIDENCE_IDENTITY_MISMATCH", "Evidence and review decision recording identities must agree.", item.song_id);
    }
    if (item.preview.approved_mbid !== item.evidence.mbid || item.preview.base_version !== item.base_version) {
      throw fail("PREVIEW_IDENTITY_MISMATCH", "Preview identity and version must match the reviewed evidence.", item.song_id);
    }
  }
  return [...manifest.songs].sort((a, b) => a.song_id - b.song_id);
}

function privateJsonCopy(value) {
  return JSON.parse(JSON.stringify(value));
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function requestHash(manifest) {
  return crypto.createHash("sha256").update(stableJson(manifest)).digest("hex");
}

function allRelevantNames(items, category, current = []) {
  const field = category === "Genre" ? "genres" : "vibes";
  const names = [...current.map(row => row.name)];
  for (const item of items) {
    for (const evidence of item.evidence[field] || []) names.push(evidence.name);
    for (const selected of item.review_decision[field] || []) names.push(selected);
    if (field === "genres") {
      for (const addition of item.preview.tag_additions.genres) names.push(addition.name);
      if (item.preview.primary_proposal) names.push(item.preview.primary_proposal.name);
      if (item.review_decision.primaryGenre) names.push(item.review_decision.primaryGenre);
    }
  }
  return [...new Set(names)];
}

async function readAssignments(client, ids) {
  const [genres, vibes] = await Promise.all([
    client.query(`SELECT sg.song_id, g.id, g.name, sg.is_primary
      FROM public.song_genres sg JOIN public.genres g ON g.id=sg.genre_id
      WHERE sg.song_id=ANY($1::int[]) ORDER BY sg.song_id,g.id`, [ids]),
    client.query(`SELECT sv.song_id, v.id, v.name
      FROM public.song_vibes sv JOIN public.vibes v ON v.id=sv.vibe_id
      WHERE sv.song_id=ANY($1::int[]) ORDER BY sv.song_id,v.id`, [ids]),
  ]);
  const byId = new Map(ids.map(id => [id, { genres: [], vibes: [] }]));
  for (const row of genres.rows) byId.get(row.song_id).genres.push({ id: row.id, name: row.name, is_primary: row.is_primary });
  for (const row of vibes.rows) byId.get(row.song_id).vibes.push({ id: row.id, name: row.name });
  return byId;
}

async function validateScratchOwnership(client) {
  const { rows } = await client.query(`SELECT current_database() AS database_name, current_user AS current_user_name,
      owner.rolname AS owner_name, shobj_description(db.oid,'pg_database') AS marker
    FROM pg_database db JOIN pg_roles owner ON owner.oid=db.datdba
    WHERE db.datname=current_database()`);
  const row = rows[0];
  const runId = process.env.CCM_T11_RUN_ID;
  if (!row || row.database_name !== "ccm_test" || row.owner_name !== row.current_user_name ||
      typeof runId !== "string" || !UUID.test(runId) || row.marker !== `CCM T11 owned scratch run ${runId}`) {
    throw fail("SCRATCH_OWNERSHIP_REQUIRED", "Song enrichment apply requires the current T11-owned ccm_test database.");
  }
}

async function lockRelevantDefinitions(client, items, assignments) {
  const genreNames = allRelevantNames(items, "Genre", items.flatMap(item => assignments.get(item.song_id).genres));
  const vibeNames = allRelevantNames(items, "Vibe", items.flatMap(item => assignments.get(item.song_id).vibes));
  await lockTagDefinitionNames(client, "Genre", genreNames);
  await lockTagDefinitionNames(client, "Vibe", vibeNames);
  const genreRows = (await client.query("SELECT id,name FROM public.genres ORDER BY id")).rows;
  const vibeRows = (await client.query("SELECT id,name FROM public.vibes ORDER BY id")).rows;
  const genreKeys = new Set(genreNames.map(name => definitionKey(normalizeDefinitionName(name, "Genre"))));
  const vibeKeys = new Set(vibeNames.map(name => definitionKey(normalizeDefinitionName(name, "Vibe"))));
  const keyFor = (name, category) => {
    try { return definitionKey(normalizeDefinitionName(name, category)); } catch { return null; }
  };
  const genreIds = [...new Set([...genreRows.filter(row => genreKeys.has(keyFor(row.name, "Genre"))).map(row => row.id),
    ...items.flatMap(item => assignments.get(item.song_id).genres.map(row => row.id))])].sort((a, b) => a - b);
  const vibeIds = [...new Set([...vibeRows.filter(row => vibeKeys.has(keyFor(row.name, "Vibe"))).map(row => row.id),
    ...items.flatMap(item => assignments.get(item.song_id).vibes.map(row => row.id))])].sort((a, b) => a - b);
  const lockedGenres = genreIds.length ? (await client.query("SELECT id,name FROM public.genres WHERE id=ANY($1::int[]) ORDER BY id FOR SHARE", [genreIds])).rows : [];
  const lockedVibes = vibeIds.length ? (await client.query("SELECT id,name FROM public.vibes WHERE id=ANY($1::int[]) ORDER BY id FOR SHARE", [vibeIds])).rows : [];
  if (lockedGenres.length !== genreIds.length || lockedVibes.length !== vibeIds.length) throw fail("DEFINITION_CHANGED", "A reviewed tag definition changed during apply.");
  const lockedGenreIds = new Set(lockedGenres.map(row => row.id));
  const lockedVibeIds = new Set(lockedVibes.map(row => row.id));
  if (genreIds.some(id => !lockedGenreIds.has(id)) || vibeIds.some(id => !lockedVibeIds.has(id))) throw fail("DEFINITION_CHANGED", "A reviewed tag definition changed during apply.");
  return { genres: genreRows, vibes: vibeRows };
}

function snapshotSql() {
  return TABLES.map(([name, table, key, order]) =>
    `SELECT '${name}'::text AS table_name, (${key}) AS row_key, to_jsonb(r) AS row_data FROM ${table} r`).join(" UNION ALL ");
}

async function captureBefore(client) {
  await client.query(`CREATE TEMP TABLE b3_enrichment_before ON COMMIT DROP AS ${snapshotSql()}`);
  return (await client.query(`SELECT names.table_name,
      md5(COALESCE(string_agg(b.row_key || ':' || md5(b.row_data::text), E'\\n' ORDER BY b.row_key),'')) AS digest
    FROM unnest($1::text[]) AS names(table_name) LEFT JOIN b3_enrichment_before b USING(table_name)
    GROUP BY names.table_name ORDER BY names.table_name`, [TABLES.map(([name]) => name)])).rows;
}

function liveTableSql() {
  return snapshotSql();
}

async function verifyUnchangedTable(client, name) {
  const query = `WITH before_rows AS (
      SELECT row_key,row_data FROM b3_enrichment_before WHERE table_name=$1
    ), live_rows AS (SELECT row_key,row_data FROM b3_enrichment_after WHERE table_name=$1), differences AS (
      (SELECT * FROM before_rows EXCEPT ALL SELECT * FROM live_rows)
      UNION ALL (SELECT * FROM live_rows EXCEPT ALL SELECT * FROM before_rows)
    ) SELECT EXISTS(SELECT 1 FROM differences) AS changed`;
  const result = await client.query(query, [name]);
  if (result.rows[0].changed) throw fail("PRESERVATION_FAILED", "An unrelated protected database row changed.");
}

async function verifyTargetSongRows(client, items) {
  for (const item of items) {
    const allowedFields = new Set(item.regenerated.scalar_changes.map(change => change.field));
    const allowed = ["updated_at", ...SCALAR_FIELDS.filter(field => allowedFields.has(field)).map(field => SCALAR_SQL_COLUMNS[field])];
    const result = await client.query(`SELECT (b.row_data - $2::text[]) IS NOT DISTINCT FROM
        (a.row_data - $2::text[]) AS same
      FROM b3_enrichment_before b JOIN b3_enrichment_after a ON a.table_name='songs' AND a.row_key=b.row_key
      WHERE b.table_name='songs' AND b.row_key=$1::text`, [item.song_id, allowed]);
    if (result.rowCount !== 1 || result.rows[0].same !== true) {
      throw fail("PRESERVATION_FAILED", "A target song changed outside its approved fields.", item.song_id);
    }
  }
}

async function verifyNonTargetSongs(client, ids) {
  const query = `WITH before_rows AS (
      SELECT row_key,row_data FROM b3_enrichment_before WHERE table_name='songs' AND NOT (row_key::int=ANY($1::int[]))
    ), live_rows AS (SELECT row_key,row_data FROM b3_enrichment_after WHERE table_name='songs' AND NOT (row_key::int=ANY($1::int[]))), differences AS (
      (SELECT * FROM before_rows EXCEPT ALL SELECT * FROM live_rows)
      UNION ALL (SELECT * FROM live_rows EXCEPT ALL SELECT * FROM before_rows)
    ) SELECT EXISTS(SELECT 1 FROM differences) AS changed`;
  const result = await client.query(query, [ids]);
  if (result.rows[0].changed) throw fail("PRESERVATION_FAILED", "A nontarget song row changed.");
}

async function verifyNonTargetAssignments(client, ids) {
  for (const name of ["song_genres", "song_vibes"]) {
    const query = `WITH before_rows AS (
        SELECT row_key,row_data FROM b3_enrichment_before WHERE table_name=$1 AND NOT ((row_data->>'song_id')::int=ANY($2::int[]))
      ), live_rows AS (SELECT row_key,row_data FROM b3_enrichment_after WHERE table_name=$1 AND NOT ((row_data->>'song_id')::int=ANY($2::int[]))), differences AS (
        (SELECT * FROM before_rows EXCEPT ALL SELECT * FROM live_rows)
        UNION ALL (SELECT * FROM live_rows EXCEPT ALL SELECT * FROM before_rows)
      ) SELECT EXISTS(SELECT 1 FROM differences) AS changed`;
    const result = await client.query(query, [name, ids]);
    if (result.rows[0].changed) throw fail("PRESERVATION_FAILED", "A nontarget song assignment changed.");
  }
}

function expectedAssignments(item, before) {
  const genres = new Map(before.genres.map(row => [row.id, { id: row.id, name: row.name, is_primary: row.is_primary }]));
  const vibes = new Map(before.vibes.map(row => [row.id, { id: row.id, name: row.name }]));
  for (const addition of item.preview.tag_additions.genres) genres.set(addition.id, {
    id: addition.id, name: addition.name,
    is_primary: item.preview.primary_proposal?.genre_id === addition.id,
  });
  for (const addition of item.preview.tag_additions.vibes) vibes.set(addition.id, { id: addition.id, name: addition.name });
  if (item.preview.primary_proposal) {
    for (const row of genres.values()) row.is_primary = row.id === item.preview.primary_proposal.genre_id;
  }
  return {
    genres: [...genres.values()].sort((a, b) => a.id - b.id),
    vibes: [...vibes.values()].sort((a, b) => a.id - b.id),
  };
}

async function verifyTargetAssignments(client, items, beforeAssignments, definitions) {
  const actual = new Map(items.map(item => [item.song_id, { genres: [], vibes: [] }]));
  const rows = await client.query(`SELECT table_name,row_data FROM b3_enrichment_after
    WHERE table_name IN ('song_genres','song_vibes') AND (row_data->>'song_id')::int=ANY($1::int[])
    ORDER BY table_name,(row_data->>'song_id')::int,
      CASE WHEN table_name='song_genres' THEN (row_data->>'genre_id')::int ELSE (row_data->>'vibe_id')::int END`, [items.map(item => item.song_id)]);
  const genreNames = new Map(definitions.genres.map(row => [row.id, row.name]));
  const vibeNames = new Map(definitions.vibes.map(row => [row.id, row.name]));
  for (const { table_name, row_data: row } of rows.rows) {
    if (table_name === "song_genres") actual.get(row.song_id).genres.push({ id: row.genre_id, name: genreNames.get(row.genre_id), is_primary: row.is_primary });
    else actual.get(row.song_id).vibes.push({ id: row.vibe_id, name: vibeNames.get(row.vibe_id) });
  }
  for (const item of items) {
    const expected = expectedAssignments(item, beforeAssignments.get(item.song_id));
    if (stableJson(actual.get(item.song_id)) !== stableJson(expected)) {
      throw fail("PRESERVATION_FAILED", "A target song assignment differs from its approved delta.", item.song_id);
    }
  }
}

async function verifySongRunRows(client, items, beforeAssignments, definitions, operationId, requestHash, manifest, response) {
  await client.query(`CREATE TEMP TABLE b3_enrichment_after ON COMMIT DROP AS ${liveTableSql()}`);
  await verifyTargetSongRows(client, items);
  await verifyNonTargetSongs(client, items.map(item => item.song_id));
  await verifyNonTargetAssignments(client, items.map(item => item.song_id));
  for (const name of UNCHANGED_TABLES) await verifyUnchangedTable(client, name);
  const runs = await verifyUnchangedTableExcluding(client, "song_enrichment_runs", operationId);
  if (!runs) throw fail("PRESERVATION_FAILED", "Another enrichment run record changed.");
  await verifyTargetAssignments(client, items, beforeAssignments, definitions);
  const own = await client.query(`SELECT request_hash,manifest=$2::jsonb AS manifest_matches,
      response=$3::jsonb AS response_matches
    FROM public.song_enrichment_runs WHERE operation_id=$1`, [operationId, JSON.stringify(manifest), JSON.stringify(response)]);
  if (own.rowCount !== 1 || own.rows[0].request_hash !== requestHash || !own.rows[0].manifest_matches || !own.rows[0].response_matches) {
    throw fail("PRESERVATION_FAILED", "The enrichment run record does not match this operation.");
  }
}

async function verifyUnchangedTableExcluding(client, name, excludeOperationId) {
  const query = `WITH before_rows AS (
      SELECT row_key,row_data FROM b3_enrichment_before WHERE table_name=$1
    ), live_rows AS (SELECT row_key,row_data FROM b3_enrichment_after WHERE table_name=$1 AND row_key<>lower($2)), differences AS (
      (SELECT * FROM before_rows EXCEPT ALL SELECT * FROM live_rows)
      UNION ALL (SELECT * FROM live_rows EXCEPT ALL SELECT * FROM before_rows)
    ) SELECT EXISTS(SELECT 1 FROM differences) AS changed`;
  const result = await client.query(query, [name, excludeOperationId]);
  return !result.rows[0].changed;
}

async function currentSongs(client, ids) {
  const result = await client.query(`SELECT s.id, s.title, s.artist_id, a.name AS artist_name,
      s.release_year, s.original_key, s.bpm,
      to_char(s.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
    FROM public.songs s JOIN public.artists a ON a.id=s.artist_id
    WHERE s.id=ANY($1::int[]) ORDER BY s.id`, [ids]);
  return result.rows;
}

async function verifyFinalTargetValues(client, items, response) {
  const results = new Map(response.songs.map(item => [item.song_id, item]));
  for (const item of items) {
    const result = results.get(item.song_id);
    const expected = Object.fromEntries(SCALAR_FIELDS.map(field => [field, item.snapshot[field]]));
    for (const change of item.regenerated.scalar_changes) {
      if (!Object.hasOwn(SCALAR_SQL_COLUMNS, change.field)) throw fail("PRESERVATION_FAILED", "A reviewed scalar field is not writable.", item.song_id);
      expected[SCALAR_SQL_COLUMNS[change.field]] = change.after;
    }
    const row = await client.query(`SELECT release_year,original_key,bpm,
        to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS version
      FROM public.songs WHERE id=$1`, [item.song_id]);
    if (row.rowCount !== 1 || SCALAR_FIELDS.some(field => row.rows[0][field] !== expected[field]) ||
        row.rows[0].version !== result.resulting_version) {
      throw fail("PRESERVATION_FAILED", "A target scalar or final version differs from the committed response.", item.song_id);
    }
  }
}

async function applyEnrichmentTransaction(pool, manifest, authorizeConnection, {
  authorizeInsideTransaction = true, authorizeBeforeLock = false, authorizeNewOperation = null,
} = {}) {
  if (!pool || typeof pool.connect !== "function") throw fail("INVALID_POOL", "An explicit database pool is required.");
  if (!validateJsonTree(manifest)) throw fail("INVALID_MANIFEST", "Enrichment manifest must be plain JSON data.");
  const privateManifest = privateJsonCopy(manifest);
  const orderedItems = validateManifest(privateManifest);
  const hash = requestHash(privateManifest);
  const operationId = privateManifest.operation_id.toLowerCase();
  let client;
  let inTransaction = false;
  let sessionOperationLock = false;
  try {
    client = await pool.connect();
    if (authorizeBeforeLock) await authorizeConnection(client, privateManifest);
    if (!authorizeInsideTransaction) await authorizeConnection(client, privateManifest);
    await client.query("SELECT pg_advisory_lock(hashtext($1),hashtext($2))", ["ccm-song-enrichment-operation", operationId]);
    sessionOperationLock = true;
    await client.query("BEGIN ISOLATION LEVEL SERIALIZABLE");
    inTransaction = true;
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))", ["ccm-song-enrichment-operation", operationId]);
    // Check the scratch owner or main restore/snapshot gate inside the same
    // serializable transaction as the apply, closing the preflight race window.
    if (authorizeInsideTransaction) await authorizeConnection(client, privateManifest);
    const prior = await client.query("SELECT request_hash,response FROM public.song_enrichment_runs WHERE operation_id=$1", [operationId]);
    if (prior.rowCount) {
      if (prior.rows[0].request_hash !== hash) throw fail("OPERATION_ID_REUSED", "Operation ID was used for a different manifest.");
      await client.query("COMMIT");
      inTransaction = false;
      return prior.rows[0].response;
    }
    if (authorizeNewOperation) await authorizeNewOperation(client, privateManifest);

    const ids = orderedItems.map(item => item.song_id);
    const initialAssignments = await readAssignments(client, ids);
    const definitions = await lockRelevantDefinitions(client, orderedItems, initialAssignments);
    const lockedIds = await client.query("SELECT id FROM public.songs WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE", [ids]);
    if (lockedIds.rowCount !== ids.length) {
      const found = new Set(lockedIds.rows.map(row => row.id));
      const missing = ids.find(id => !found.has(id));
      throw fail("SONG_NOT_FOUND", "A target song no longer exists.", missing);
    }
    const artistIds = [...new Set(orderedItems.map(item => item.identity.artist_id))].sort((a, b) => a - b);
    const lockedArtists = await client.query("SELECT id,name FROM public.artists WHERE id=ANY($1::int[]) ORDER BY id FOR SHARE", [artistIds]);
    if (lockedArtists.rowCount !== artistIds.length) throw fail("SONG_IDENTITY_CHANGED", "A target artist no longer exists.");
    const songs = await currentSongs(client, ids);
    const currentAssignmentRows = await readAssignments(client, ids);
    const bySong = new Map(songs.map(song => [song.id, song]));
    const previews = [];
    for (const item of orderedItems) {
      const row = bySong.get(item.song_id);
      if (!row || row.title !== item.identity.title || row.artist_id !== item.identity.artist_id || row.artist_name !== item.identity.artist_name) {
        throw fail("SONG_IDENTITY_CHANGED", "Target song identity no longer matches the reviewed manifest.", item.song_id);
      }
      if (row.updated_at !== item.base_version) throw fail("STALE_VERSION", "Target song version no longer matches the reviewed manifest.", item.song_id);
      const snapshot = { id: row.id, updated_at: row.updated_at, release_year: row.release_year,
        original_key: row.original_key, bpm: row.bpm,
        genres: currentAssignmentRows.get(item.song_id).genres, vibes: currentAssignmentRows.get(item.song_id).vibes };
      let regenerated;
      try { regenerated = buildEnrichmentPreview(snapshot, item.evidence, item.review_decision, definitions); }
      catch { throw fail("PREVIEW_REBUILD_FAILED", "The reviewed preview could not be rebuilt from current state.", item.song_id); }
      if (stableJson(regenerated) !== stableJson(item.preview)) throw fail("PREVIEW_MISMATCH", "The supplied preview differs from the current reviewed evidence and definitions.", item.song_id);
      const invalidCurrent = new Set(["invalid_existing_assignments", "invalid_existing_assignment", "invalid_existing_primary_state",
        "malformed_existing_value", "missing_existing_field", "invalid_definition_catalog", "invalid_definition"]);
      if (regenerated.diagnostics.some(entry => invalidCurrent.has(entry.code))) {
        throw fail("INVALID_CURRENT_SNAPSHOT", "Current song or definition state is not valid for enrichment.", item.song_id);
      }
      if (regenerated.diagnostics.some(entry => ["invalid_candidate", "selection_not_in_evidence", "invalid_tag_evidence",
        "invalid_selected_tag", "tag_selection_not_in_evidence"].includes(entry.code))) {
        throw fail("INVALID_REVIEW_EVIDENCE", "Reviewed evidence or selections are not valid for application.", item.song_id);
      }
      previews.push({ ...item, regenerated, snapshot });
    }

    const beforeHashes = await captureBefore(client);
    const beforeAssignments = currentAssignmentRows;
    const results = [];
    for (const item of previews) {
      const delta = {
        scalar_changes: item.regenerated.scalar_changes,
        tag_additions: item.regenerated.tag_additions,
        primary_proposal: item.regenerated.primary_proposal,
      };
      const changed = delta.scalar_changes.length > 0 || delta.tag_additions.genres.length > 0 ||
        delta.tag_additions.vibes.length > 0 || delta.primary_proposal !== null;
      if (changed) {
        for (const addition of delta.tag_additions.genres) {
          const primary = delta.primary_proposal?.genre_id === addition.id;
          await client.query(`INSERT INTO public.song_genres(song_id,genre_id,is_primary) VALUES($1,$2,$3)
            ON CONFLICT(song_id,genre_id) DO NOTHING`, [item.song_id, addition.id, primary]);
        }
        for (const addition of delta.tag_additions.vibes) {
          await client.query(`INSERT INTO public.song_vibes(song_id,vibe_id) VALUES($1,$2)
            ON CONFLICT(song_id,vibe_id) DO NOTHING`, [item.song_id, addition.id]);
        }
        if (delta.primary_proposal && !delta.tag_additions.genres.some(addition => addition.id === delta.primary_proposal.genre_id)) {
          await client.query("UPDATE public.song_genres SET is_primary=true WHERE song_id=$1 AND genre_id=$2", [item.song_id, delta.primary_proposal.genre_id]);
        }
        const changes = delta.scalar_changes;
        const parameters = [item.song_id];
        const sets = [];
        for (const change of changes) {
          if (!Object.hasOwn(SCALAR_SQL_COLUMNS, change.field)) throw fail("INVALID_REVIEW_EVIDENCE", "A regenerated scalar field is not writable.", item.song_id);
          parameters.push(change.after);
          sets.push(`${SCALAR_SQL_COLUMNS[change.field]}=$${parameters.length}`);
        }
        sets.push("updated_at=updated_at");
        await client.query(`UPDATE public.songs SET ${sets.join(",")} WHERE id=$1`, parameters);
      }
      results.push({ song_id: item.song_id, status: changed ? "applied" : "no_op", delta,
        resulting_version: null });
    }

    const versionRows = await client.query(`SELECT id,to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
      FROM public.songs WHERE id=ANY($1::int[]) ORDER BY id`, [ids]);
    const versions = new Map(versionRows.rows.map(row => [row.id, row.updated_at]));
    for (const result of results) result.resulting_version = versions.get(result.song_id);
    for (const item of previews) {
      const result = results.find(entry => entry.song_id === item.song_id);
      if (result.status === "no_op" && result.resulting_version !== item.base_version) throw fail("PRESERVATION_FAILED", "A no-op changed the song version.", item.song_id);
      if (result.status === "applied" && result.resulting_version === item.base_version) throw fail("PRESERVATION_FAILED", "An applied delta did not advance the song version.", item.song_id);
    }

    const response = { operation_id: privateManifest.operation_id, request_hash: hash, songs: results };
    await client.query(`INSERT INTO public.song_enrichment_runs(operation_id,request_hash,manifest,response)
      VALUES($1,$2,$3::jsonb,$4::jsonb)`, [operationId, hash, JSON.stringify(privateManifest), JSON.stringify(response)]);
    await verifySongRunRows(client, previews, beforeAssignments, definitions, operationId, hash, privateManifest, response);
    await verifyFinalTargetValues(client, previews, response);
    const afterHashes = (await client.query(`SELECT names.table_name,
        md5(COALESCE(string_agg(a.row_key || ':' || md5(a.row_data::text), E'\\n' ORDER BY a.row_key),'')) AS digest
      FROM unnest($1::text[]) AS names(table_name) LEFT JOIN b3_enrichment_after a USING(table_name)
      GROUP BY names.table_name ORDER BY names.table_name`, [TABLES.map(([name]) => name)])).rows;
    const beforeByName = new Map(beforeHashes.map(row => [row.table_name, row.digest]));
    const afterByName = new Map(afterHashes.map(row => [row.table_name, row.digest]));
    for (const name of UNCHANGED_TABLES) {
      if (beforeByName.get(name) !== afterByName.get(name)) throw fail("PRESERVATION_FAILED", "A protected table hash changed.");
    }
    // Hashes remain server-side digests; row JSON (including charts) is never materialized in JavaScript.
    if (!beforeHashes.length || !afterHashes.length) throw fail("PRESERVATION_FAILED", "Canonical row snapshots are incomplete.");
    await client.query("COMMIT");
    inTransaction = false;
    return response;
  } catch (error) {
    if (inTransaction) {
      try { await client.query("ROLLBACK"); } catch { /* preserve the original stable failure */ }
    }
    if (error?.name === "SongEnrichmentApplyError") throw error;
    throw fail("APPLY_FAILED", "Song enrichment apply failed and was rolled back.");
  } finally {
    if (client && sessionOperationLock) {
      try {
        await client.query("SELECT pg_advisory_unlock(hashtext($1),hashtext($2))", ["ccm-song-enrichment-operation", operationId]);
      } catch {
        client.release(new Error("Could not release the enrichment operation lock; discard this connection."));
        client = null;
      }
    }
    if (client) client.release();
  }
}

async function applySongEnrichment(pool, manifest) {
  return applyEnrichmentTransaction(pool, manifest, validateScratchOwnership, { authorizeInsideTransaction: false });
}

function validateMainApplyInputs(manifest, gate) {
  if (!gate || gate.apply_main !== true || typeof gate.expected_database !== "string" ||
      !/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(gate.expected_database) ||
      typeof gate.manifest_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(gate.manifest_sha256) ||
      !Array.isArray(gate.target_ids) || !gate.manifest_path ||
      gate.restore_proof !== null && gate.restore_proof !== undefined && !isRecord(gate.restore_proof)) {
    throw fail("MAIN_APPLY_GATE_REQUIRED", "Main enrichment requires explicit opt-in and reviewed backup proof.");
  }
  const ordered = validateManifest(manifest);
  const ids = ordered.map(item => item.song_id);
  if (ids.length > 25 || JSON.stringify(ids) !== JSON.stringify([...gate.target_ids].sort((a,b)=>a-b)) ||
      requestHash(manifest) !== gate.manifest_sha256) {
    throw fail("MAIN_APPLY_MANIFEST_MISMATCH", "Main apply target set or canonical manifest hash does not match the reviewed inputs.");
  }
  if (gate.restore_proof && (gate.restore_proof.target_database !== gate.expected_database ||
      gate.restore_proof.manifest_sha256 !== gate.manifest_sha256)) {
    throw fail("RESTORE_PROOF_MISMATCH", "Restore proof is not bound to this database and manifest.");
  }
  return ids;
}

async function authorizeMainConnection(client, gate) {
  const identity = (await client.query(`SELECT current_database() AS database_name,current_user AS current_user_name,
      owner.rolname AS owner_name FROM pg_database db JOIN pg_roles owner ON owner.oid=db.datdba
    WHERE db.datname=current_database()`)).rows[0];
  if (!identity || identity.database_name !== gate.expected_database) throw fail("DATABASE_IDENTITY_MISMATCH", "Connected database does not match the explicit expected database.");
  const migration = await client.query("SELECT 1 FROM public.schema_migrations WHERE version='0004'");
  if (migration.rowCount !== 1) throw fail("MIGRATION_REQUIRED", "Migration 0004 is absent; main apply stopped before writes.");
}

async function authorizeMainNewOperation(client, gate) {
  if (!gate.restore_proof) throw fail("RESTORE_PROOF_REQUIRED", "A fresh signed backup and restore proof is required for a new main operation.");
  if (gate.restore_proof.target_database !== gate.expected_database ||
      gate.restore_proof.manifest_sha256 !== gate.manifest_sha256) {
    throw fail("RESTORE_PROOF_MISMATCH", "Restore proof is not bound to this database and manifest.");
  }
  const currentSnapshot = await protectedSnapshotSha256(client);
  if (currentSnapshot !== gate.restore_proof.live_snapshot_sha256) throw fail("MAIN_SNAPSHOT_CHANGED", "Complete protected database snapshot changed after restore verification.");
}

async function applyMainSongEnrichment(pool, manifest, gate) {
  if (!validateJsonTree(manifest) || !validateJsonTree(gate)) throw fail("INVALID_MANIFEST", "Main apply inputs must be plain JSON data.");
  const privateManifest = privateJsonCopy(manifest);
  const privateGate = privateJsonCopy(gate);
  validateMainApplyInputs(privateManifest, privateGate);
  return applyEnrichmentTransaction(pool, privateManifest, client => authorizeMainConnection(client, privateGate), {
    authorizeBeforeLock: true,
    authorizeNewOperation: async client => {
      try { await verifyRestoreProof(privateGate.restore_proof); }
      catch { throw fail("RESTORE_PROOF_INVALID", "Fresh signed backup and restore verification is required."); }
      return authorizeMainNewOperation(client, privateGate);
    },
  });
}

module.exports = { applyMainSongEnrichment, applySongEnrichment, authorizeMainConnection, stableJson,
  validateMainApplyInputs, validateManifest };
