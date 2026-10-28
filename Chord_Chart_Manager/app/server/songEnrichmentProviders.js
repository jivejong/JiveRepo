const crypto = require("node:crypto");
const { definitionKey, normalizeDefinitionName } = require("./tagDefinitionNames");

const DEFAULTS = Object.freeze({
  model: "gemini-3.1-flash-lite",
  maxAttempts: 12,
  maxGeminiAttempts: 2,
  timeoutMs: 10_000,
  maxResponseBytes: 1_000_000,
  geminiMaxInputBytes: 32_000,
  geminiMaxOutputTokens: 1_000,
  maxRetryAfterMs: 30_000,
  retryDelayMs: 1_000,
  musicBrainzMinimumIntervalMs: 1_000,
});
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ACOUSTIC_KEY_NAMES = new Set(["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]);
const MUSICBRAINZ_SEARCH_MAX_RESULTS = 100;
const MUSICBRAINZ_SEARCH_LIMIT = MUSICBRAINZ_SEARCH_MAX_RESULTS;
const MUSICBRAINZ_SEARCH_PARSER_VERSION = "musicbrainz-search-single-response-v1";
const MUSICBRAINZ_RECORDING_LOOKUP_PARSER_VERSION = "musicbrainz-recording-lookup-release-type-v1";
const MUSICBRAINZ_LOOKUP_RELEASE_MAX_RESULTS = 25;
const GEMINI_PROMPT_VERSION = "missing-metadata-fallback-v2";
const GEMINI_SCHEMA_VERSION = "missing-metadata-schema-v2";
const GEMINI_VALIDATION_REASONS = new Set(["interaction_envelope_invalid", "model_text_missing", "invalid_json",
  "required_field_missing", "unexpected_field", "invalid_field_type_value", "unrequested_scalar",
  "disallowed_genre_output", "unknown_label", "ambiguous_label", "label_outside_allowed_subset", "invalid_provenance"]);
function buildGeminiSchema(requestedFields, allowedNames) {
  const scalarSchemas = {
    release_year: { type: ["integer", "null"] },
    bpm: { type: ["number", "null"], minimum: 0 },
    original_key: { type: ["object", "null"], properties: {
      key_key: { type: "string", enum: ["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"] },
      key_scale: { type: "string", enum: ["major", "minor"] },
    }, required: ["key_key", "key_scale"], additionalProperties: false },
  };
  const properties = {};
  for (const field of ["release_year", "bpm", "original_key"]) {
    properties[field] = requestedFields.includes(field) ? scalarSchemas[field] : { type: "null" };
  }
  properties.genres = { type: "array", items: { type: "string" }, maxItems: allowedNames.genres.length === 0 ? 0 : 3 };
  properties.vibes = { type: "array", items: { type: "string" }, maxItems: allowedNames.vibes.length === 0 ? 0 : 3 };
  return {
    type: "object",
    properties,
    required: ["release_year", "bpm", "original_key", "genres", "vibes"],
    additionalProperties: false,
  };
}

const GEMINI_SCHEMA = Object.freeze(buildGeminiSchema(["release_year", "bpm", "original_key"], { genres: ["*"], vibes: ["*"] }));

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function positiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value <= 0) throw new TypeError(`${name} must be a positive integer.`);
}

function validateConfig(config) {
  if (!isRecord(config)) throw new TypeError("Provider config must be an object.");
  const required = ["maxAttempts", "maxGeminiAttempts", "timeoutMs", "maxResponseBytes",
    "geminiMaxInputBytes", "geminiMaxOutputTokens"];
  for (const name of required) {
    if (!Object.prototype.hasOwnProperty.call(config, name)) throw new TypeError(`${name} must be explicitly configured.`);
    positiveInteger(config[name], name);
  }
  if (config.model !== undefined && config.model !== DEFAULTS.model) throw new TypeError(`Only ${DEFAULTS.model} is supported.`);
  const merged = { ...DEFAULTS, ...config };
  // An explicit undefined must not erase the only supported model default.
  if (merged.model === undefined) merged.model = DEFAULTS.model;
  for (const name of ["maxAttempts", "maxGeminiAttempts", "timeoutMs", "maxResponseBytes",
    "geminiMaxInputBytes", "geminiMaxOutputTokens", "maxRetryAfterMs", "retryDelayMs", "musicBrainzMinimumIntervalMs"]) {
    positiveInteger(merged[name], name);
  }
  if (merged.maxGeminiAttempts > merged.maxAttempts) throw new TypeError("maxGeminiAttempts cannot exceed maxAttempts.");
  if (merged.geminiMaxOutputTokens > 65_536) throw new TypeError("geminiMaxOutputTokens exceeds the model output-token limit.");
  if (typeof merged.musicBrainzUserAgent !== "string" ||
      !/^\S[^()]*\/\d+(?:\.\d+){1,2}(?:[-+][\w.-]+)?\s+\([^()]+\)$/.test(merged.musicBrainzUserAgent.trim())) {
    throw new TypeError("MusicBrainz User-Agent must identify the application, version, and contact.");
  }
  if (merged.musicBrainzMinimumIntervalMs < 1000) throw new TypeError("MusicBrainz requests must be spaced by at least 1000ms.");
  return merged;
}

function jsonBytes(value) {
  return Buffer.from(JSON.stringify(value), "utf8");
}

function responseBytes(value) {
  if (typeof value === "string") return Buffer.from(value, "utf8");
  if (Buffer.isBuffer(value)) return value;
  if (value instanceof Uint8Array) return Buffer.from(value);
  return null;
}

function responseDigest(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function parseJson(bytes) {
  try { return JSON.parse(bytes.toString("utf8")); } catch { return null; }
}

function queryEscape(value) {
  return String(value).replace(/([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)/g, "\\$1");
}

function queryString(value) {
  return encodeURIComponent(value).replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}

function normalizeIdentity(value) {
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/\s+/g, " ");
  return normalized ? normalized.toLocaleLowerCase() : null;
}

function creditedArtist(value) {
  if (!Array.isArray(value)) return null;
  let result = "";
  for (const credit of value) {
    if (typeof credit === "string") {
      result += credit;
      continue;
    }
    if (!isRecord(credit) || !isRecord(credit.artist)) return null;
    const name = typeof credit.name === "string" ? credit.name : credit.artist.name;
    if (typeof name !== "string") return null;
    result += name + (typeof credit.joinphrase === "string" ? credit.joinphrase : "");
  }
  return result.trim() || null;
}

function validMusicBrainzDate(value) {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(value);
  if (!parts) return false;
  const year = Number(parts[1]);
  if (year < 1 || year > 32767) return false;
  if (parts[2] === undefined) return true;
  const month = Number(parts[2]);
  if (month < 1 || month > 12) return false;
  if (parts[3] === undefined) return true;
  const day = Number(parts[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= monthDays[month - 1];
}

function normalizeMusicBrainzSearch(body, digest) {
  const requestedOffset = 0;
  const safeField = value => {
    if (value === null) return { type: "null" };
    if (typeof value === "number" || typeof value === "boolean") return { type: typeof value, value };
    if (typeof value === "string") return /^\s*[+-]?\d+(?:\.\d+)?\s*$/.test(value) && value.length <= 32
      ? { type: "string", value } : { type: "string" };
    if (Array.isArray(value)) return { type: "array", length: value.length };
    return { type: typeof value };
  };
  const diagnostic = (code, values = {}) => ({
    code,
    requested_offset: requestedOffset,
    ...(Number.isSafeInteger(body?.count) && body.count >= 0 ? { current_page_total: body.count } :
      body && Object.hasOwn(body, "count") ? { current_page_total: safeField(body.count) } : {}),
    ...(body && Object.hasOwn(body, "offset") ? { returned_offset: Number.isSafeInteger(body.offset) && body.offset >= 0 ? body.offset : safeField(body.offset) } : {}),
    ...(Array.isArray(body?.recordings) ? { returned_recording_count: body.recordings.length } :
      body && Object.hasOwn(body, "recordings") ? { returned_recording_count: safeField(body.recordings) } : {}),
    ...values,
  });
  const invalidPage = (code, details = {}) => ({ status: "invalid_response", reason: "incomplete_search_results",
    candidates: [], validation_diagnostic: diagnostic(code, details) });
  if (!isRecord(body) || !Number.isSafeInteger(body.count) || body.count < 0 ||
      !Number.isSafeInteger(body.offset) || body.offset < 0 || !Array.isArray(body.recordings)) {
    return invalidPage("invalid_pagination_field");
  }
  if (body.offset !== requestedOffset) return invalidPage("unexpected_offset");
  if (body.count > MUSICBRAINZ_SEARCH_MAX_RESULTS) return invalidPage("result_cap_exceeded");
  if (body.recordings.length !== body.count) {
    return invalidPage("unexpected_page_length");
  }
  if (body.count === 0) return { status: "absent", candidates: [], response_digest: digest, count: 0, offset: 0 };
  const candidates = [];
  const seen = new Map();
  for (let indexInPage = 0; indexInPage < body.recordings.length; indexInPage += 1) {
    const row = body.recordings[indexInPage];
    const artist = creditedArtist(row?.["artist-credit"]);
    if (!isRecord(row) || typeof row.id !== "string" || !UUID.test(row.id) ||
        typeof row.title !== "string" || !artist ||
        row.disambiguation != null && typeof row.disambiguation !== "string" ||
        row.video != null && typeof row.video !== "boolean") return invalidPage("invalid_candidate");
    const mbid = row.id.toLowerCase();
    if (seen.has(mbid)) return invalidPage("duplicate_recording_identity", {
      duplicate_mbid: mbid,
      duplicate_scope: "within_page",
      first_occurrence: { requested_offset: requestedOffset, index_in_page: seen.get(mbid) },
      repeated_occurrence: { requested_offset: requestedOffset, index_in_page: indexInPage },
    });
    seen.set(mbid, indexInPage);
    candidates.push({
      mbid,
      title: row.title,
      artist,
      disambiguation: typeof row.disambiguation === "string" ? row.disambiguation : "",
      ...(typeof row.video === "boolean" ? { video: row.video } : {}),
      response_digest: digest,
      ...(validMusicBrainzDate(row["first-release-date"])
        ? { first_release_date: row["first-release-date"] }
        : {}),
    });
  }
  return { status: "ok", candidates, response_digest: digest, count: body.count, offset: body.offset };
}

function normalizeReleaseTypeEvidence(body, expectedMbid, digest) {
  const releases = body.releases;
  const releaseCount = body["release-count"];
  const firstReleaseDate = body["first-release-date"];
  const base = { mbid: expectedMbid.toLowerCase(), response_digest: digest,
    first_release_date: validMusicBrainzDate(firstReleaseDate) ? firstReleaseDate : null,
    release_count: Number.isSafeInteger(releaseCount) && releaseCount >= 0 ? releaseCount : null,
    releases: [], complete: false };
  if (firstReleaseDate != null && !validMusicBrainzDate(firstReleaseDate)) return base;
  if (!Array.isArray(releases) || !Number.isSafeInteger(releaseCount) || releaseCount < releases.length ||
      releaseCount > MUSICBRAINZ_LOOKUP_RELEASE_MAX_RESULTS || releases.length !== releaseCount) return base;
  const groups = Array.isArray(body["release-groups"]) ? body["release-groups"] : [];
  const output = [];
  for (const release of releases) {
    if (!isRecord(release) || typeof release.id !== "string" || !UUID.test(release.id) ||
        typeof release.title !== "string" || !release.title.trim() ||
        release.date != null && !validMusicBrainzDate(release.date) ||
        release.status != null && typeof release.status !== "string" ||
        release.disambiguation != null && typeof release.disambiguation !== "string") return base;
    let group = release["release-group"];
    if (typeof group === "string") group = groups.find(item => typeof item?.id === "string" && item.id.toLowerCase() === group.toLowerCase());
    if (!isRecord(group)) group = groups.find(item => item?.id === release["release-group-id"]);
    if (!isRecord(group) || typeof group.id !== "string" || !UUID.test(group.id) ||
        typeof group.title !== "string" || !group.title.trim() ||
        group["primary-type"] != null && typeof group["primary-type"] !== "string" ||
        !Array.isArray(group["secondary-types"]) || group["secondary-types"].some(type => typeof type !== "string")) return base;
    output.push({ id: release.id.toLowerCase(), title: release.title,
      date: typeof release.date === "string" ? release.date : null,
      status: typeof release.status === "string" ? release.status : null,
      disambiguation: typeof release.disambiguation === "string" ? release.disambiguation : "",
      release_group: { id: group.id.toLowerCase(), title: group.title,
        primary_type: typeof group["primary-type"] === "string" ? group["primary-type"] : null,
        secondary_types: [...group["secondary-types"]].sort() } });
  }
  return { ...base, releases: output, complete: true };
}

function normalizeMusicBrainzLookup(body, expectedMbid, digest, searchCandidate = null) {
  if (!isRecord(body) || typeof body.id !== "string" || !UUID.test(body.id) ||
      body.id.toLowerCase() !== expectedMbid.toLowerCase() || typeof body.title !== "string" ||
      !creditedArtist(body["artist-credit"])) return { status: "invalid_response" };
  const scalars = { release_year: [] };
  let releaseDate = body["first-release-date"];
  let releaseDigest = digest;
  if (!validMusicBrainzDate(releaseDate) && searchCandidate?.mbid?.toLowerCase() === expectedMbid.toLowerCase() &&
      validMusicBrainzDate(searchCandidate.first_release_date) &&
      typeof searchCandidate.response_digest === "string" && /^[a-f0-9]{64}$/i.test(searchCandidate.response_digest)) {
    releaseDate = searchCandidate.first_release_date;
    releaseDigest = searchCandidate.response_digest;
  }
  const dateMatch = validMusicBrainzDate(releaseDate) ? /^(\d{4})/.exec(releaseDate) : null;
  if (dateMatch) {
    scalars.release_year.push({ value: Number(dateMatch[1]), source: "MusicBrainz", response_digest: releaseDigest });
  }
  const genres = [];
  for (const [field, association] of [["genres", "genre"], ["tags", "tag"]]) {
    if (body[field] !== undefined) {
      if (!Array.isArray(body[field])) return { status: "invalid_response" };
      for (const item of body[field]) {
        if (!isRecord(item) || typeof item.name !== "string" || !item.name.trim()) return { status: "invalid_response" };
        genres.push({ name: item.name, source: "MusicBrainz", response_digest: digest, association });
      }
    }
  }
  return {
    status: "ok",
    recording: {
      mbid: body.id.toLowerCase(),
      title: body.title,
      artist: creditedArtist(body["artist-credit"]),
      disambiguation: typeof body.disambiguation === "string" ? body.disambiguation : "",
      video: typeof body.video === "boolean" ? body.video : null,
      release_type_evidence: normalizeReleaseTypeEvidence(body, expectedMbid, digest),
      scalars,
      genres,
    },
    response_digest: digest,
  };
}

function unwrapAcousticDocument(body, mbid, offset) {
  if (!isRecord(body)) return null;
  const normalizedMbid = mbid.toLowerCase();
  if (body.mbid !== undefined && (typeof body.mbid !== "string" || !UUID.test(body.mbid) || body.mbid.toLowerCase() !== normalizedMbid)) return null;
  if (body.offset !== undefined && body.offset !== offset) return null;
  const metadata = body.metadata;
  if (metadata !== undefined) {
    if (!isRecord(metadata)) return null;
    const tags = metadata.tags;
    if (tags !== undefined && !isRecord(tags)) return null;
    const recordingIds = tags?.musicbrainz_recordingid;
    if (recordingIds !== undefined && (!Array.isArray(recordingIds) || recordingIds.length === 0 ||
        recordingIds.some(id => typeof id !== "string" || !UUID.test(id) || id.toLowerCase() !== normalizedMbid))) return null;
  }
  return body;
}

function normalizeAcousticSubmission(document, mbid, offset, digest) {
  const evidence = { original_key: [], bpm: [] };
  const rhythm = document.rhythm;
  if (rhythm !== undefined) {
    if (!isRecord(rhythm)) return null;
    if (rhythm.bpm !== undefined) {
      if (typeof rhythm.bpm !== "number" || !Number.isFinite(rhythm.bpm) || rhythm.bpm <= 0) return null;
      evidence.bpm.push({ value: rhythm.bpm, source: "AcousticBrainz", response_digest: digest, submission_id: `${mbid.toLowerCase()}:${offset}` });
    }
  }
  const tonal = document.tonal;
  if (tonal !== undefined) {
    if (!isRecord(tonal)) return null;
    const hasKey = tonal.key_key !== undefined || tonal.key_scale !== undefined;
    if (hasKey) {
      if (typeof tonal.key_key !== "string" || typeof tonal.key_scale !== "string" ||
          !ACOUSTIC_KEY_NAMES.has(tonal.key_key) || !["major", "minor", "none"].includes(tonal.key_scale)) return null;
      if (tonal.key_strength !== undefined && (typeof tonal.key_strength !== "number" || !Number.isFinite(tonal.key_strength))) return null;
      if (tonal.key_scale !== "none") {
        evidence.original_key.push({
          value: { key_key: tonal.key_key, key_scale: tonal.key_scale },
          source: "AcousticBrainz",
          response_digest: digest,
          submission_id: `${mbid.toLowerCase()}:${offset}`,
          ...(tonal.key_strength !== undefined ? { key_strength: tonal.key_strength } : {}),
        });
      }
    }
  }
  return evidence;
}

function mapGeminiText(body) {
  const usage = safeInteractionsUsage(body?.usage);
  if (!isRecord(body) || body.status !== "completed" || !Array.isArray(body.steps)) return { text: null, usage, validation_reason: "interaction_envelope_invalid" };
  const modelSteps = body.steps.filter(step => isRecord(step) && step.type === "model_output");
  if (modelSteps.length !== 1 || !Array.isArray(modelSteps[0].content)) return { text: null, usage, validation_reason: "interaction_envelope_invalid" };
  const textParts = modelSteps[0].content.filter(part => isRecord(part) && part.type === "text" && typeof part.text === "string");
  if (textParts.length === 0) return { text: null, usage, validation_reason: "model_text_missing" };
  if (textParts.length !== 1) return { text: null, usage, validation_reason: "interaction_envelope_invalid" };
  return { text: textParts[0].text, usage };
}

function resolveGeminiFallback(jsonText, definitions, allowedNames, digest, requestedFields) {
  let parsed;
  try { parsed = JSON.parse(jsonText); } catch { return validationFailure("invalid_json"); }
  const fields = ["release_year", "bpm", "original_key", "genres", "vibes"];
  if (!isRecord(parsed)) return validationFailure("invalid_field_type_value", { validation_field: "response" });
  const keys = Object.keys(parsed);
  const missingField = fields.find(field => !Object.hasOwn(parsed, field));
  if (missingField) return validationFailure("required_field_missing", { validation_field: missingField });
  if (keys.some(field => !fields.includes(field))) return validationFailure("unexpected_field", { validation_field: "response" });
  for (const field of ["genres", "vibes"]) {
    if (!Array.isArray(parsed[field]) || parsed[field].length > 3) return validationFailure("invalid_field_type_value", { validation_field: field });
  }
  const provenance = { source: "Gemini", response_digest: digest, model_id: DEFAULTS.model,
    prompt_version: GEMINI_PROMPT_VERSION, schema_version: GEMINI_SCHEMA_VERSION };
  const output = { scalars: { release_year: [], bpm: [], original_key: [] }, genres: [], vibes: [] };
  const roots = new Set(["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"]);
  for (const field of ["release_year", "bpm", "original_key"]) {
    const value = parsed[field];
    if (!requestedFields.includes(field) && value !== null) return validationFailure("unrequested_scalar", { validation_field: field });
    if (value === null) continue;
    if (field === "release_year" && (!Number.isInteger(value) || value < 1 || value > 32767)) return validationFailure("invalid_field_type_value", { validation_field: field });
    if (field === "bpm" && (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || Math.floor(value + 0.5) < 1 || Math.floor(value + 0.5) > 32767)) return validationFailure("invalid_field_type_value", { validation_field: field });
    if (field === "original_key" && (!isRecord(value) || Object.keys(value).sort().join(",") !== "key_key,key_scale" ||
      !roots.has(value.key_key) || !["major", "minor"].includes(value.key_scale))) return validationFailure("invalid_field_type_value", { validation_field: field });
    output.scalars[field].push({ value, ...provenance });
  }
  if (parsed.genres.length > 0 && allowedNames.genres.length === 0) return validationFailure("disallowed_genre_output", { validation_category: "Genre" });
  for (const [category, field] of [["Genre", "genres"], ["Vibe", "vibes"]]) {
    if (!Array.isArray(definitions[field]) || !Array.isArray(allowedNames?.[field])) return validationFailure("invalid_field_type_value", { validation_category: category });
    const lookup = new Map();
    for (const row of definitions[field]) {
      const name = typeof row === "string" ? row : row?.name;
      if (typeof name !== "string") return validationFailure("invalid_field_type_value", { validation_category: category });
      let key;
      try { key = definitionKey(normalizeDefinitionName(name, category)); } catch { return validationFailure("invalid_field_type_value", { validation_category: category }); }
      if (!lookup.has(key)) lookup.set(key, []);
      lookup.get(key).push(name);
    }
    const allowed = new Set();
    for (const label of allowedNames[field]) {
      if (typeof label !== "string") return validationFailure("invalid_field_type_value", { validation_category: category });
      let key;
      try { key = definitionKey(normalizeDefinitionName(label, category)); } catch { return validationFailure("invalid_field_type_value", { validation_category: category }); }
      const matches = lookup.get(key) || [];
      if (matches.length > 1) return validationFailure("ambiguous_label", { validation_category: category });
      if (matches.length !== 1 || matches[0] !== label) return validationFailure("invalid_field_type_value", { validation_category: category });
      allowed.add(key);
    }
    const emitted = new Set();
    for (const label of parsed[field]) {
      if (typeof label !== "string") return validationFailure("invalid_field_type_value", { validation_category: category });
      let key;
      try { key = definitionKey(normalizeDefinitionName(label, category)); } catch { return validationFailure("invalid_field_type_value", { validation_category: category }); }
      const matches = lookup.get(key) || [];
      if (matches.length > 1) return validationFailure("ambiguous_label", { validation_category: category });
      if (matches.length === 0) return validationFailure("unknown_label", { validation_category: category });
      if (!allowed.has(key)) return validationFailure("label_outside_allowed_subset", { validation_category: category });
      if (!emitted.has(key)) {
        output[field].push({ name: matches[0], ...provenance });
        emitted.add(key);
      }
    }
  }
  return { output };
}

function validationFailure(validation_reason, identifiers = {}) {
  return { validation_reason, ...identifiers };
}

function safeTokenUsage(value) {
  if (!isRecord(value)) return null;
  const output = {};
  for (const [source, target] of [["promptTokenCount", "input_tokens"], ["candidatesTokenCount", "output_tokens"], ["totalTokenCount", "total_tokens"]]) {
    if (Number.isSafeInteger(value[source]) && value[source] >= 0) output[target] = value[source];
  }
  return Object.keys(output).length ? output : null;
}

function safeInteractionsUsage(value) {
  if (!isRecord(value) || !Number.isSafeInteger(value.total_input_tokens) || value.total_input_tokens < 0 ||
      !Number.isSafeInteger(value.total_output_tokens) || value.total_output_tokens < 0 ||
      !Number.isSafeInteger(value.total_tokens) || value.total_tokens < 0 ||
      value.total_input_tokens + value.total_output_tokens !== value.total_tokens) return null;
  return { input_tokens: value.total_input_tokens, output_tokens: value.total_output_tokens, total_tokens: value.total_tokens };
}

function readHeader(headers, name) {
  if (headers && typeof headers.get === "function") return headers.get(name);
  if (!isRecord(headers)) return null;
  const key = Object.keys(headers).find(item => item.toLowerCase() === name.toLowerCase());
  return key ? String(headers[key]) : null;
}

function retryAfterMs(value, now) {
  if (typeof value !== "string" || !value.trim()) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) {
    const seconds = Number(trimmed);
    return Number.isSafeInteger(seconds) && seconds <= Number.MAX_SAFE_INTEGER / 1000 ? seconds * 1000 : null;
  }
  const match = /^(Sun|Mon|Tue|Wed|Thu|Fri|Sat), (\d{2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT$/.exec(trimmed);
  if (!match) return null;
  const timestamp = Date.parse(trimmed);
  if (!Number.isFinite(timestamp) || new Date(timestamp).toUTCString() !== trimmed) return null;
  return Math.max(0, timestamp - now);
}

function defaultDelay(milliseconds, { signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(Object.assign(new Error("Aborted"), { name: "AbortError" }));
      return;
    }
    const finish = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(Object.assign(new Error("Aborted"), { name: "AbortError" }));
    };
    const timer = setTimeout(finish, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function result(status, extra = {}) {
  return { status, ...extra };
}

function evidenceResult(status, mbid, scalars = {}, genres = [], vibes = [], extra = {}) {
  return { status, mbid, scalars, genres, vibes, ...extra };
}

function invalidGeminiResult(mbid, validation_reason, identifiers = {}) {
  return evidenceResult("invalid_response", mbid, {}, [], [], { failure_details: {
    failure_phase: "validation", validation_reason, ...identifiers,
  } });
}

async function networkDisabledTransport() {
  const error = new Error("Outbound provider transport is disabled.");
  error.code = "NETWORK_DISABLED";
  throw error;
}

function createSongEnrichmentProviderClient({
  transport = networkDisabledTransport,
  clock = Date.now,
  delay = defaultDelay,
  config = {},
  cache = null,
  beforeAttempt = null,
} = {}) {
  if (typeof transport !== "function" || typeof clock !== "function" || typeof delay !== "function") throw new TypeError("Provider dependencies must be functions.");
  if (beforeAttempt !== null && typeof beforeAttempt !== "function") throw new TypeError("beforeAttempt must be a function when provided.");
  const settings = validateConfig(config);
  const ledger = [];
  let totalAttempts = 0;
  let geminiAttempts = 0;
  let mbNextStart = 0;
  let acousticNotBefore = 0;
  let mbQueue = Promise.resolve();
  let attemptReservationQueue = Promise.resolve();

  async function reserveAttempt(provider, operation, gemini) {
    const previous = attemptReservationQueue;
    let release;
    attemptReservationQueue = new Promise(resolve => { release = resolve; });
    await previous;
    try {
      if (!hasBudget(provider, gemini)) return 0;
      const allowed = beforeAttempt ? await beforeAttempt({ provider, operation, gemini, attempt: totalAttempts + 1 }) : true;
      if (allowed === false) return 0;
      totalAttempts += 1;
      if (gemini) geminiAttempts += 1;
      return totalAttempts;
    } finally { release(); }
  }

  function record(provider, operation, attempt, status, tokenUsage = null, failureDetails = null) {
    const index = ledger.length;
    ledger.push({ provider, operation, attempt, status, ...(tokenUsage ? { token_usage: tokenUsage } : {}),
      ...(failureDetails ? { failure_details: failureDetails } : {}) });
    return index;
  }

  const safeTransportCodes = new Set(["ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "EACCES", "EHOSTUNREACH", "ENETUNREACH",
    "EAI_AGAIN", "ENOTFOUND", "EPIPE", "RESPONSE_TOO_LARGE", "INVALID_PROVIDER_REQUEST"]);
  function safeFailureDetails(details, defaults = {}) {
    const source = details && typeof details === "object" ? details : {};
    const code = safeTransportCodes.has(source.transport_error_code) ? source.transport_error_code : undefined;
    const phase = ["validation", "request_start", "request", "response", "timeout", "http", "provider"].includes(source.failure_phase)
      ? source.failure_phase : defaults.failure_phase;
    const status = Number.isInteger(source.http_status) && source.http_status >= 100 && source.http_status <= 599
      ? source.http_status : defaults.http_status;
    const timeoutSource = ["provider_watchdog", "transport"].includes(source.timeout_source) ? source.timeout_source : defaults.timeout_source;
    const validationReason = GEMINI_VALIDATION_REASONS.has(source.validation_reason) ? source.validation_reason
      : GEMINI_VALIDATION_REASONS.has(defaults.validation_reason) ? defaults.validation_reason : undefined;
    const validationField = ["response", "release_year", "bpm", "original_key", "genres", "vibes"].includes(source.validation_field) ? source.validation_field
      : ["response", "release_year", "bpm", "original_key", "genres", "vibes"].includes(defaults.validation_field) ? defaults.validation_field : undefined;
    const validationCategory = ["Genre", "Vibe"].includes(source.validation_category) ? source.validation_category
      : ["Genre", "Vibe"].includes(defaults.validation_category) ? defaults.validation_category : undefined;
    return { ...(phase ? { failure_phase: phase } : {}), ...(status ? { http_status: status } : {}),
      ...(source.timeout === true || defaults.timeout === true ? { timeout: true } : {}),
      ...(timeoutSource ? { timeout_source: timeoutSource } : {}), ...(code ? { transport_error_code: code } : {}),
      ...(validationReason ? { validation_reason: validationReason } : {}), ...(validationField ? { validation_field: validationField } : {}),
      ...(validationCategory ? { validation_category: validationCategory } : {}),
      ...(source.https_started === true || defaults.https_started === true ? { https_started: true } :
        source.https_started === false || defaults.https_started === false ? { https_started: false } : {}) };
  }

  function hasBudget(provider, gemini) {
    return totalAttempts < settings.maxAttempts && !(gemini && geminiAttempts >= settings.maxGeminiAttempts);
  }

  function retryPermitted(retry, provider, gemini) {
    return retry === 0 && hasBudget(provider, gemini);
  }

  function updateFetchedStatus(fetched, status, tokenUsage = undefined, validation = null) {
    if (!Number.isInteger(fetched?.ledger_index)) return;
    const item = ledger[fetched.ledger_index];
    if (!item) return;
    item.status = status;
    if (tokenUsage) item.token_usage = tokenUsage;
    if (validation) item.failure_details = safeFailureDetails(null, validation);
  }

  function ledgerSnapshot() {
    return ledger.map(item => ({ ...item, ...(item.token_usage ? { token_usage: { ...item.token_usage } } : {}),
      ...(item.failure_details ? { failure_details: { ...item.failure_details } } : {}) }));
  }

  async function cachedRequest(key) {
    if (!cache || typeof cache.get !== "function") return null;
    try { return await cache.get(key); } catch { return null; }
  }

  async function cacheResponse(key, status, bytes) {
    if (!cache || typeof cache.set !== "function") return;
    try { await cache.set(key, { status, response: bytes }); } catch { /* cache failures do not alter provider results */ }
  }

  async function send(provider, operation, request, { signal, gemini = false, musicBrainz = false, onAttempt = null } = {}) {
    const run = async () => {
      let retryFailure = null;
      for (let retry = 0; retry <= 1; retry += 1) {
        if (signal?.aborted) return { status: "cancelled" };
        if (!hasBudget(provider, gemini)) return retryFailure || { status: "budget_exhausted" };
        if (transport === networkDisabledTransport) {
          record(provider, operation, 0, "network_disabled");
          return { status: "network_disabled" };
        }
        if (musicBrainz) {
          const now = Number(clock());
          const waitMs = Math.max(0, mbNextStart - now);
          if (waitMs > 0) {
            try { await delay(waitMs, { signal, kind: "musicbrainz_spacing" }); }
            catch { return { status: signal?.aborted ? "cancelled" : "transient_error" }; }
            if (signal?.aborted) return { status: "cancelled" };
          }
        }
        if (provider === "AcousticBrainz") {
          const now = Number(clock());
          const waitMs = Math.max(0, acousticNotBefore - now);
          if (waitMs > settings.maxRetryAfterMs) return { status: "transient_error" };
          if (waitMs > 0) {
            try { await delay(waitMs, { signal, kind: "provider_rate_limit" }); }
            catch { return { status: signal?.aborted ? "cancelled" : "transient_error" }; }
            if (signal?.aborted) return { status: "cancelled" };
          }
        }
        if (signal?.aborted) return { status: "cancelled" };
        if (!hasBudget(provider, gemini)) return retryFailure || { status: "budget_exhausted" };
        let attempt = null;
        let timeoutController;
        let removeParentAbort = null;
        let response;
        try {
          timeoutController = new AbortController();
          const abort = () => timeoutController.abort();
          signal?.addEventListener("abort", abort, { once: true });
          removeParentAbort = () => signal?.removeEventListener("abort", abort);
          if (signal?.aborted) {
            removeParentAbort();
            timeoutController.abort();
            return { status: "cancelled" };
          }
          if (totalAttempts >= settings.maxAttempts || (gemini && geminiAttempts >= settings.maxGeminiAttempts)) {
            removeParentAbort();
            timeoutController.abort();
            return { status: "budget_exhausted" };
          }
          // The reservation hook can durably record intent before dispatch. The
          // queue keeps the shared client budget safe if callers overlap requests.
          const reserved = await reserveAttempt(provider, operation, gemini);
          if (!reserved) {
            removeParentAbort();
            timeoutController.abort();
            // A cohort reservation hook may refuse a retry after the first
            // provider failure because its stricter per-song cap is spent.
            // Preserve that acknowledged failure instead of misclassifying
            // the retry refusal as exhaustion of the shared/provider budget.
            return retryFailure || { status: "budget_exhausted" };
          }
          attempt = reserved;
          onAttempt?.(attempt);
          const timeout = Symbol("timeout");
          let transportPromise;
          try {
            if (musicBrainz) mbNextStart = Math.max(Number(clock()), mbNextStart) + settings.musicBrainzMinimumIntervalMs;
            transportPromise = Promise.resolve(transport({ ...request, signal: timeoutController.signal,
              timeoutMs: settings.timeoutMs, maxResponseBytes: settings.maxResponseBytes }));
          } catch (error) {
            transportPromise = Promise.reject(error);
          }
          const timeoutPromise = delay(settings.timeoutMs, { signal: timeoutController.signal, kind: "timeout" })
            .then(() => timeout, () => timeout);
          response = await Promise.race([transportPromise, timeoutPromise]);
          removeParentAbort();
          if (signal?.aborted) {
            timeoutController.abort();
            record(provider, operation, attempt, "cancelled");
            return { status: "cancelled" };
          }
          if (response === timeout) {
            timeoutController.abort();
            const failureDetails = safeFailureDetails(null, { failure_phase: "timeout", timeout: true,
              timeout_source: "provider_watchdog" });
            record(provider, operation, attempt, "transient_error", null, failureDetails);
            return { status: "transient_error", reason: "timeout", failure_details: failureDetails };
          }
          timeoutController.abort();
          const statusCode = response?.status;
          let acousticResetWait = null;
          if (provider === "AcousticBrainz") {
            const remaining = readHeader(response.headers, "x-ratelimit-remaining");
            const reset = readHeader(response.headers, "x-ratelimit-reset-in");
            const remainingIsZero = remaining !== null && /^\s*0\s*$/.test(remaining);
            if (reset !== null && /^\d+(?:\.\d+)?$/.test(reset.trim())) {
              acousticResetWait = Math.ceil(Number(reset.trim()) * 1000);
              if (statusCode === 429 || remainingIsZero) {
                acousticNotBefore = Math.max(acousticNotBefore, Number(clock()) + acousticResetWait);
              }
            } else if (reset !== null) {
              acousticResetWait = NaN;
            }
          }
          if (!Number.isInteger(statusCode)) {
            const invalid = provider === "Gemini" && operation === "missing_metadata_fallback"
              ? safeFailureDetails(null, { failure_phase: "response", validation_reason: "interaction_envelope_invalid" }) : null;
            record(provider, operation, attempt, "invalid_response", null, invalid);
            return { status: "invalid_response", ...(invalid ? { failure_details: invalid } : {}) };
          }
          const statusFailureDetails = safeFailureDetails(null, { failure_phase: "http", http_status: statusCode, https_started: true });
          if (statusCode === 401 || statusCode === 403) {
            record(provider, operation, attempt, "authentication_error", null, statusFailureDetails);
            return { status: "authentication_error", failure_details: statusFailureDetails };
          }
          if (statusCode === 429 || statusCode >= 500 && statusCode <= 599) {
            const failureDetails = safeFailureDetails(null, { failure_phase: "http", http_status: statusCode, https_started: true });
            const supplied = readHeader(response.headers, "retry-after");
            const wait = supplied != null ? retryAfterMs(supplied, Number(clock()))
              : statusCode === 429 && acousticResetWait !== null ? acousticResetWait
                : settings.retryDelayMs;
            if (supplied != null && wait === null) {
              record(provider, operation, attempt, "transient_error", null, failureDetails);
              return { status: "transient_error", reason: "invalid_retry_after", failure_details: failureDetails };
            }
            if (wait !== null && wait > settings.maxRetryAfterMs) {
              record(provider, operation, attempt, "transient_error", null, failureDetails);
              return { status: "transient_error", reason: "retry_after_exceeds_cap", failure_details: failureDetails };
            }
            if (wait !== null && wait <= settings.maxRetryAfterMs && retryPermitted(retry, provider, gemini)) {
              record(provider, operation, attempt, "transient_error", null, failureDetails);
              retryFailure = { status: "transient_error", failure_details: failureDetails };
              try { await delay(wait, { signal, kind: "retry_after" }); }
              catch { return { status: signal?.aborted ? "cancelled" : "transient_error" }; }
              if (!hasBudget(provider, gemini)) return retryFailure;
              continue;
            }
            record(provider, operation, attempt, "transient_error", null, failureDetails);
            return { status: "transient_error", failure_details: failureDetails,
              ...(supplied != null ? { reason: "retry_limit" } : {}) };
          }
          if (statusCode < 200 || statusCode >= 300) {
            record(provider, operation, attempt, "invalid_response", null, statusFailureDetails);
            return { status: "invalid_response", failure_details: statusFailureDetails };
          }
          const bytes = responseBytes(response.body);
          if (!bytes || bytes.byteLength > settings.maxResponseBytes) {
            const invalid = provider === "Gemini" && operation === "missing_metadata_fallback"
              ? safeFailureDetails(statusFailureDetails, { validation_reason: "interaction_envelope_invalid" }) : statusFailureDetails;
            record(provider, operation, attempt, "invalid_response", null, invalid);
            return { status: "invalid_response", failure_details: invalid };
          }
          let body;
          let bodyJsonValid = true;
          try { body = JSON.parse(bytes.toString("utf8")); } catch { bodyJsonValid = false; }
          if (!bodyJsonValid || body === null && !(provider === "Gemini" && operation === "missing_metadata_fallback")) {
            const invalid = provider === "Gemini" && operation === "missing_metadata_fallback"
              ? safeFailureDetails(statusFailureDetails, { validation_reason: bodyJsonValid ? "interaction_envelope_invalid" : "invalid_json",
                validation_field: "response" }) : statusFailureDetails;
            record(provider, operation, attempt, "invalid_response", null, invalid);
            return { status: "invalid_response", failure_details: invalid };
          }
          // The Gemini Interactions endpoint has its own usage contract. Do not
          // interpret its response through generateContent's usageMetadata.
          const usage = provider === "Gemini" && operation === "missing_metadata_fallback"
            ? safeInteractionsUsage(body?.usage) : safeTokenUsage(body.usageMetadata);
          const ledger_index = record(provider, operation, attempt, "ok", usage);
          return { status: "ok", body, bytes, digest: responseDigest(bytes), usage, ledger_index };
        } catch (error) {
          removeParentAbort?.();
          timeoutController?.abort();
          if (attempt === null) {
            if (error?.code === "COHORT_DRIFT" || error?.code === "CHECKPOINT_INVALID" || error?.code === "CHECKPOINT_TAMPERED" ||
                ["GEMINI_SPEND_LIMIT", "GEMINI_SPEND_LEDGER_INVALID", "GEMINI_SPEND_LEDGER_LOCKED",
                  "GEMINI_SPEND_POLICY_UNVERIFIED"].includes(error?.code)) throw error;
            return { status: signal?.aborted ? "cancelled" : "transient_error" };
          }
          if (signal?.aborted || error?.name === "AbortError") {
            record(provider, operation, attempt, "cancelled");
            return { status: "cancelled" };
          }
          if (error?.code === "NETWORK_DISABLED") {
            record(provider, operation, attempt, "network_disabled");
            return { status: "network_disabled" };
          }
          const failureDetails = safeFailureDetails(error?.failure_details);
          if (retryPermitted(retry, provider, gemini)) {
            record(provider, operation, attempt, "transient_error", null, failureDetails);
            retryFailure = { status: "transient_error", failure_details: failureDetails };
            try { await delay(settings.retryDelayMs, { signal, kind: "retry" }); }
            catch { return { status: signal?.aborted ? "cancelled" : "transient_error" }; }
            if (!hasBudget(provider, gemini)) return retryFailure;
            continue;
          }
          record(provider, operation, attempt, "transient_error", null, failureDetails);
          return { status: "transient_error", failure_details: failureDetails };
        }
      }
      return { status: "transient_error" };
    };
    if (!musicBrainz) return run();
    const previous = mbQueue;
    let release;
    mbQueue = new Promise(resolve => { release = resolve; });
    await previous;
    try { return await run(); } finally { release(); }
  }

  async function requestCached(provider, operation, request, key, options) {
    const cached = await cachedRequest(key);
    if (cached && ["ok", "absent"].includes(cached.status)) {
      const body = parseJson(responseBytes(cached.response));
      if (body !== null) return { status: "ok", body, bytes: responseBytes(cached.response), digest: cached.response_digest, cache_hit: true };
    }
    const fetched = await send(provider, operation, request, options);
    return { ...fetched, cache_key: key };
  }

  function musicBrainzHeaders() {
    return { Accept: "application/json", "User-Agent": settings.musicBrainzUserAgent };
  }

  async function searchRecordings({ title, artist, signal } = {}) {
    const normalizedTitle = normalizeIdentity(title);
    const normalizedArtist = normalizeIdentity(artist);
    if (!normalizedTitle || !normalizedArtist) return result("invalid_response", { candidates: [] });
    const query = `recording:"${queryEscape(title.trim())}" AND artist:"${queryEscape(artist.trim())}"`;
    let expectedTotal = null;
    let attemptedResponses = 0;
    let validatedResponses = 0;
    let collectedCount = 0;
    const pagination = complete => ({ expected_total: expectedTotal, attempted_pages: attemptedResponses,
      validated_pages: validatedResponses, collected_count: collectedCount, complete });
    const failed = (status, reason = undefined, validationDiagnostic = undefined) => result(status, {
      candidates: [], pagination: pagination(false), ...(reason ? { reason } : {}),
      ...(validationDiagnostic ? { validation_diagnostic: { ...validationDiagnostic,
        validated_response_count: validatedResponses, collected_record_count: collectedCount } } : {}),
    });
    if (signal?.aborted) return failed("cancelled");
    const offset = 0;
    const url = `https://musicbrainz.org/ws/2/recording/?query=${queryString(query)}&fmt=json&limit=${MUSICBRAINZ_SEARCH_LIMIT}&offset=${offset}`;
    const cacheRequest = { provider: "MusicBrainz", operation: "recording_search",
      parserVersion: MUSICBRAINZ_SEARCH_PARSER_VERSION,
      parameters: { title, artist, query, limit: MUSICBRAINZ_SEARCH_LIMIT, offset } };
    attemptedResponses += 1;
    const fetched = await requestCached("MusicBrainz", "recording_search",
      { url, method: "GET", headers: musicBrainzHeaders() }, cacheRequest, { signal, musicBrainz: true });
    if (signal?.aborted) return failed("cancelled");
    if (fetched.status !== "ok") return failed(fetched.status, fetched.reason);

    const parsed = normalizeMusicBrainzSearch(parseJson(fetched.bytes), fetched.digest);
    updateFetchedStatus(fetched, parsed.status);
    if (parsed.status !== "ok" && parsed.status !== "absent") {
      if (parsed.validation_diagnostic) {
        parsed.validation_diagnostic.validated_response_count = 0;
        parsed.validation_diagnostic.collected_record_count = 0;
      }
      return failed(parsed.status, parsed.reason, parsed.validation_diagnostic);
    }
    expectedTotal = parsed.count;
    validatedResponses = 1;
    collectedCount = parsed.candidates.length;
    if (!fetched.cache_hit) await cacheResponse(cacheRequest, parsed.status, fetched.bytes);
    if (signal?.aborted) return failed("cancelled");
    if (parsed.status === "absent") {
      return result("absent", { candidates: [], response_digest: fetched.digest, cache_hit: Boolean(fetched.cache_hit),
        pagination: pagination(true) });
    }
    return { status: "ok", candidates: parsed.candidates, response_digest: fetched.digest,
      cache_hit: Boolean(fetched.cache_hit), pagination: pagination(true) };
  }

  async function lookupRecording({ mbid, searchCandidate = null, signal } = {}) {
    if (typeof mbid !== "string" || !UUID.test(mbid)) return result("invalid_response");
    const normalized = mbid.toLowerCase();
    const url = `https://musicbrainz.org/ws/2/recording/${encodeURIComponent(normalized)}?inc=artist-credits+genres+tags+releases+release-groups&fmt=json`;
    const cacheRequest = { provider: "MusicBrainz", operation: "recording_lookup",
      parserVersion: MUSICBRAINZ_RECORDING_LOOKUP_PARSER_VERSION, parameters: { mbid: normalized } };
    const fetched = await requestCached("MusicBrainz", "recording_lookup", { url, method: "GET", headers: musicBrainzHeaders() }, cacheRequest,
      { signal, musicBrainz: true });
    if (fetched.status !== "ok") return evidenceResult(fetched.status, normalized, {}, [], [], fetched.reason ? { reason: fetched.reason } : {});
    const parsed = normalizeMusicBrainzLookup(parseJson(fetched.bytes), normalized, fetched.digest, searchCandidate);
    updateFetchedStatus(fetched, parsed.status);
    if (parsed.status === "ok" && !fetched.cache_hit) await cacheResponse(cacheRequest, "ok", fetched.bytes);
    if (parsed.status !== "ok") return evidenceResult(parsed.status, normalized);
    return {
      ...evidenceResult("ok", normalized, parsed.recording.scalars, parsed.recording.genres, [], {
        response_digest: parsed.response_digest,
        cache_hit: Boolean(fetched.cache_hit),
      }),
      recording: parsed.recording,
    };
  }

  async function acousticRequest(mbid, offset, signal, validatedSubmissionCount = null, onAttempt = null) {
    if (signal?.aborted) return { status: "cancelled" };
    const base = `https://acousticbrainz.org/api/v1/${encodeURIComponent(mbid.toLowerCase())}`;
    const url = offset === null ? `${base}/count` : `${base}/low-level?n=${offset}`;
    const cacheRequest = {
      provider: "AcousticBrainz",
      operation: offset === null ? "recording_count" : "recording_submission",
      parameters: offset === null ? { mbid } : { mbid, offset },
    };
    const cached = await cachedRequest(cacheRequest);
    if (signal?.aborted) return { status: "cancelled" };
    if (cached) {
      const bytes = responseBytes(cached.response);
      const body = bytes && parseJson(bytes);
      if (isRecord(body)) return { status: "ok", body, bytes, digest: cached.response_digest, cache_hit: true, cache_key: cacheRequest };
    }
    const fetched = await send("AcousticBrainz", offset === null ? "count" : "submission", {
      url, method: "GET", headers: { Accept: "application/json" },
      ...(offset === null ? {} : { acousticbrainzSubmissionCount: validatedSubmissionCount }),
    }, { signal, onAttempt });
    return { ...fetched, cache_key: cacheRequest };
  }

  async function getAcousticBrainzEvidence({ mbid, signal } = {}) {
    if (typeof mbid !== "string" || !UUID.test(mbid)) return evidenceResult("invalid_response", null,
      { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown" });
    const id = mbid.toLowerCase();
    const countResponse = await acousticRequest(id, null, signal);
    if (signal?.aborted) return evidenceResult("cancelled", id,
      { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown" });
    if (countResponse.status !== "ok") return evidenceResult(countResponse.status, id,
      { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown", ...(countResponse.reason ? { reason: countResponse.reason } : {}) });
    if (typeof countResponse.body.mbid !== "string" || countResponse.body.mbid.toLowerCase() !== id ||
        !Number.isSafeInteger(countResponse.body.count) || countResponse.body.count < 0) {
      updateFetchedStatus(countResponse, "invalid_response");
      return evidenceResult("invalid_response", id, { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown" });
    }
    const count = countResponse.body.count;
    const plannedSampleOffsets = count === 0 ? [] : [...new Set([0, Math.floor((count - 1) / 2), count - 1])].sort((a, b) => a - b);
    if (count === 0) {
      updateFetchedStatus(countResponse, "absent");
      if (!countResponse.cache_hit) await cacheResponse(countResponse.cache_key, "absent", countResponse.bytes);
      if (signal?.aborted) return evidenceResult("cancelled", id, { original_key: [], bpm: [] }, [], [], {
        submission_enumeration: "complete", submission_count: 0, planned_sample_offsets: [], attempted_offsets: [],
        sampled_offsets: [], agreement_scope: "none",
      });
      return evidenceResult("absent", id, { original_key: [], bpm: [] }, [], [], {
        submission_enumeration: "complete", submission_count: 0, planned_sample_offsets: [], attempted_offsets: [], sampled_offsets: [],
        agreement_scope: "none", response_digest: countResponse.digest, cache_hit: Boolean(countResponse.cache_hit),
      });
    }
    if (!countResponse.cache_hit) await cacheResponse(countResponse.cache_key, "ok", countResponse.bytes);
    const evidence = { original_key: [], bpm: [] };
    const attemptedOffsets = [];
    const sampledOffsets = [];
    const partialResult = (status, extra = {}) => evidenceResult(status, id, evidence, [], [], {
      submission_enumeration: "partial", submission_count: count,
      planned_sample_offsets: plannedSampleOffsets, attempted_offsets: attemptedOffsets, sampled_offsets: sampledOffsets,
      agreement_scope: "sampled_submissions", ...extra,
    });
    if (signal?.aborted) return partialResult("cancelled");
    for (const offset of plannedSampleOffsets) {
      if (signal?.aborted) return partialResult("cancelled");
      const fetched = await acousticRequest(id, offset, signal, count, () => {
        if (!attemptedOffsets.includes(offset)) attemptedOffsets.push(offset);
      });
      if (signal?.aborted) return partialResult("cancelled");
      if (fetched.status !== "ok") return evidenceResult(fetched.status, id, evidence, [], [], {
        submission_enumeration: "partial", submission_count: count,
        planned_sample_offsets: plannedSampleOffsets, attempted_offsets: attemptedOffsets, sampled_offsets: sampledOffsets,
        agreement_scope: "sampled_submissions", ...(fetched.reason ? { reason: fetched.reason } : {}),
      });
      const document = unwrapAcousticDocument(fetched.body, id, offset);
      if (!document) {
        updateFetchedStatus(fetched, "invalid_response");
        return partialResult("invalid_response");
      }
      const parsed = normalizeAcousticSubmission(document, id, offset, fetched.digest);
      if (!parsed) {
        updateFetchedStatus(fetched, "invalid_response");
        return partialResult("invalid_response");
      }
      updateFetchedStatus(fetched, "ok");
      evidence.original_key.push(...parsed.original_key);
      evidence.bpm.push(...parsed.bpm);
      sampledOffsets.push(offset);
      if (!fetched.cache_hit) await cacheResponse(fetched.cache_key, "ok", fetched.bytes);
      if (signal?.aborted) return partialResult("cancelled");
    }
    if (signal?.aborted) return partialResult("cancelled");
    const complete = sampledOffsets.length === count;
    return evidenceResult("ok", id, evidence, [], [], {
      submission_enumeration: complete ? "complete" : "partial", submission_count: count,
      planned_sample_offsets: plannedSampleOffsets, attempted_offsets: attemptedOffsets, sampled_offsets: sampledOffsets,
      agreement_scope: "sampled_submissions",
    });
  }

function safeGeminiFactualEvidence(value) {
  const keysAre = (item, allowed) => isRecord(item) && Object.keys(item).every(key => allowed.includes(key));
  if (!isRecord(value)) return null;
  if (value.context_version === 1 && keysAre(value, ["context_version", "target", "recording", "scalar_candidates", "submission_coverage", "conflicts"]) &&
      keysAre(value.target, ["song_id", "title", "artist"]) && Number.isSafeInteger(value.target.song_id) &&
      keysAre(value.recording, ["mbid", "title", "artist", "disambiguation", "validated", "response_digest"]) && UUID.test(value.recording.mbid || "") &&
      isRecord(value.submission_coverage) && Number.isSafeInteger(value.submission_coverage.total_available) &&
      Array.isArray(value.submission_coverage.sampled_offsets) && isRecord(value.scalar_candidates) && isRecord(value.conflicts)) {
    const scalars = {};
    for (const field of ["release_year", "original_key", "bpm"]) {
      const group = value.scalar_candidates[field];
      if (!keysAre(group, ["state", "evidence_status", "conflicting_evidence", "candidates"]) ||
          !Array.isArray(group.candidates) || typeof group.conflicting_evidence !== "boolean") return null;
      const source = field === "release_year" ? "MusicBrainz" : "AcousticBrainz";
      const candidates = [];
      for (const item of group.candidates) {
        if (!keysAre(item, ["source", "value", "response_digest", "submission_reference"]) || item.source !== source ||
            !/^[a-f0-9]{64}$/.test(item.response_digest || "") || typeof item.submission_reference !== "string") return null;
        if (field === "original_key") {
          if (!keysAre(item.value, ["key_key", "key_scale"]) || !["major", "minor"].includes(item.value.key_scale)) return null;
          candidates.push({ ...item, value: { key_key: item.value.key_key, key_scale: item.value.key_scale } });
        } else candidates.push({ ...item });
      }
      scalars[field] = { state: typeof group.state === "string" ? group.state : "unresolved",
        conflicting_evidence: group.conflicting_evidence,
        candidates };
    }
    const coverage = value.submission_coverage;
    if (value.recording.validated !== true || !/^[a-f0-9]{64}$/.test(value.recording.response_digest || "") ||
        !["title", "artist"].every(key => typeof value.target[key] === "string") ||
        !["title", "artist", "disambiguation"].every(key => typeof value.recording[key] === "string") ||
        coverage.source !== "AcousticBrainz" || !["complete", "partial"].includes(coverage.completeness) ||
        !coverage.sampled_offsets.every((offset, index, all) => Number.isSafeInteger(offset) && offset >= 0 &&
          offset < coverage.total_available && (index === 0 || all[index - 1] < offset)) ||
        !["release_year", "original_key", "bpm"].every(field => typeof value.conflicts[field] === "boolean")) return null;
    return { factual_context_version: 1, target: { song_id: value.target.song_id, title: value.target.title, artist: value.target.artist },
      recording: { mbid: value.recording.mbid, title: value.recording.title, artist: value.recording.artist,
        disambiguation: value.recording.disambiguation, validated: true, response_digest: value.recording.response_digest },
      scalar_candidates: scalars, submission_coverage: { source: coverage.source, total_available: coverage.total_available,
        sampled_offsets: [...coverage.sampled_offsets], completeness: coverage.completeness },
      conflicts: Object.fromEntries(["release_year", "original_key", "bpm"].map(field => [field, value.conflicts[field]])) };
  }
  if (value.context_version === "b4-v1" && isRecord(value.scalar_candidates)) {
    return { factual_context_version: "b4-v1", scalar_candidates: value.scalar_candidates,
      genre_evidence: value.genre_evidence, vibe_evidence: value.vibe_evidence,
      tag_diagnostics: value.tag_diagnostics, search_lookup_year_discrepancy: value.search_lookup_year_discrepancy };
  }
  return null;
}

function geminiPayload({ title, artist, context, allowedNames, requestedFields }) {
    const safeContext = {};
    if (isRecord(context)) {
      if (Number.isInteger(context.release_year) && context.release_year >= 1 && context.release_year <= 32767) safeContext.release_year = context.release_year;
      if (["studio", "live", "remix", "acoustic", "demo", "unknown"].includes(context.recording_type)) safeContext.recording_type = context.recording_type;
    }
    const genres = Array.isArray(allowedNames?.genres) ? [...allowedNames.genres] : [];
    const vibes = Array.isArray(allowedNames?.vibes) ? [...allowedNames.vibes] : [];
    const factualEvidence = safeGeminiFactualEvidence(context?.factual_evidence);
    return {
      recording: { title, artist, context: safeContext },
      requested_missing_fields: [...requestedFields],
      allowed_names: { genres, vibes },
      ...(factualEvidence ? { factual_evidence: factualEvidence } : {}),
    };
  }

  async function requestGeminiTags({ mbid = null, title, artist, context, factualEvidence = null, requestedFields = [], definitions,
    allowedNames = definitions, apiKey, signal } = {}) {
    if (mbid !== null && (typeof mbid !== "string" || !UUID.test(mbid)) || typeof title !== "string" || !title.trim() ||
        typeof artist !== "string" || !artist.trim() || !isRecord(definitions) ||
        !Array.isArray(definitions.genres) || !Array.isArray(definitions.vibes) || !Array.isArray(requestedFields) ||
        requestedFields.some(field => !["release_year", "bpm", "original_key"].includes(field)) ||
        new Set(requestedFields).size !== requestedFields.length || !isRecord(allowedNames) ||
        !Array.isArray(allowedNames.genres) || !Array.isArray(allowedNames.vibes)) return invalidGeminiResult(null, "invalid_field_type_value", { validation_field: "response" });
    if (typeof apiKey !== "string" || !apiKey.trim()) return evidenceResult("authentication_error", mbid, {}, [], []);
    if (factualEvidence !== null && safeGeminiFactualEvidence(factualEvidence) === null) return invalidGeminiResult(mbid, "invalid_provenance", { validation_field: "response" });
    const canonicalDefinitions = { genres: [], vibes: [] };
    for (const [category, field] of [["Genre", "genres"], ["Vibe", "vibes"]]) {
      try {
        canonicalDefinitions[field] = definitions[field].map(row => {
          const name = typeof row === "string" ? row : row?.name;
          normalizeDefinitionName(name, category);
          return { name };
        });
      } catch { return invalidGeminiResult(mbid, "invalid_field_type_value", { validation_category: category }); }
    }
    const safeAllowedNames = { genres: [], vibes: [] };
    for (const [category, field] of [["Genre", "genres"], ["Vibe", "vibes"]]) {
      const fullNames = canonicalDefinitions[field].map(row => row.name);
      const groups = new Map();
      for (const name of fullNames) {
        const key = definitionKey(normalizeDefinitionName(name, category));
        groups.set(key, (groups.get(key) || 0) + 1);
      }
      const seen = new Set();
      for (const label of allowedNames[field]) {
        const labelName = typeof label === "string" ? label : label?.name;
        if (typeof labelName !== "string") return invalidGeminiResult(mbid, "invalid_field_type_value", { validation_category: category });
        let key;
        try { key = definitionKey(normalizeDefinitionName(labelName, category)); }
        catch { return invalidGeminiResult(mbid, "invalid_field_type_value", { validation_category: category }); }
        const matches = canonicalDefinitions[field].filter(row => definitionKey(normalizeDefinitionName(row.name, category)) === key);
        if (matches.length > 1) return invalidGeminiResult(mbid, "ambiguous_label", { validation_category: category });
        if (matches.length === 0) return invalidGeminiResult(mbid, "unknown_label", { validation_category: category });
        if (matches[0].name !== labelName || seen.has(key)) return invalidGeminiResult(mbid, "label_outside_allowed_subset", { validation_category: category });
        seen.add(key);
        safeAllowedNames[field].push(matches[0].name);
      }
    }
    const payload = geminiPayload({ title, artist, context: { ...(context || {}), factual_evidence: factualEvidence }, allowedNames: safeAllowedNames, requestedFields });
    const instruction = "Suggest only missing scalar values listed in requested_missing_fields and Genre/Vibe labels from the matching supplied catalogs. Vibes describe subjective musical, listening, or performance character. Choose only appropriate supplied labels; return an empty array when none fit. Use null for scalar abstention or any scalar not requested, and empty arrays to abstain on labels. Do not return identifiers, citations, confidence, explanations, or fields outside the schema. Model suggestions are unverified candidates and must not resolve factual evidence conflicts.";
    const body = {
      model: settings.model,
      input: `${instruction}\n${JSON.stringify(payload)}`,
      system_instruction: instruction,
      store: false,
      response_format: { type: "text", mime_type: "application/json", schema: buildGeminiSchema(requestedFields, payload.allowed_names) },
      generation_config: { max_output_tokens: settings.geminiMaxOutputTokens },
    };
    const bytes = jsonBytes(body);
    if (bytes.byteLength > settings.geminiMaxInputBytes) return invalidGeminiResult(mbid, "invalid_field_type_value", { validation_field: "response" });
    if (!Array.isArray(payload.allowed_names.genres) || !Array.isArray(payload.allowed_names.vibes)) return invalidGeminiResult(mbid, "invalid_field_type_value", { validation_field: "response" });
    if (requestedFields.length === 0 && payload.allowed_names.genres.length === 0 && payload.allowed_names.vibes.length === 0) return evidenceResult("ok", mbid, {}, [], [], { skipped: "no_missing_fields_or_unassigned_definitions" });
    const cacheRequest = {
      provider: "Gemini", operation: "missing_metadata_fallback",
      parameters: { title, artist, context: { ...payload.recording.context,
        factual_evidence: payload.factual_evidence ?? null }, requestedFields },
      parserVersion: "2", model: settings.model, promptVersion: GEMINI_PROMPT_VERSION,
      schemaVersion: GEMINI_SCHEMA_VERSION,
      definitionCatalog: { genres: payload.allowed_names.genres, vibes: payload.allowed_names.vibes },
      validationCatalog: { genres: canonicalDefinitions.genres.map(row => row.name),
        vibes: canonicalDefinitions.vibes.map(row => row.name) },
    };
    const request = {
      url: `https://generativelanguage.googleapis.com/v1beta/interactions`,
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "x-goog-api-key": apiKey },
      body: bytes,
    };
    let fetched = await cachedRequest(cacheRequest);
    if (fetched?.status === "ok") {
      const cachedBytes = responseBytes(fetched.response);
      if (cachedBytes && parseJson(cachedBytes) !== null) {
        fetched = { status: "ok", body: parseJson(cachedBytes), bytes: cachedBytes,
          digest: fetched.response_digest, cache_hit: true };
      } else {
        fetched = null;
      }
    } else {
      fetched = null;
    }
    if (!fetched) {
      fetched = await send("Gemini", "missing_metadata_fallback", request, { signal, gemini: true });
    }
    if (fetched.status !== "ok") return evidenceResult(fetched.status, mbid, {}, [], [], {
      ...(fetched.reason ? { reason: fetched.reason } : {}),
      ...(fetched.failure_details ? { failure_details: fetched.failure_details } : {}),
    });
    const bodyResponse = fetched.body ?? parseJson(fetched.bytes);
    const text = mapGeminiText(bodyResponse);
    const usage = text.usage;
    if (!text.text) {
      const failureDetails = safeFailureDetails(null, { failure_phase: "response", validation_reason: text.validation_reason || "model_text_missing" });
      updateFetchedStatus(fetched, "invalid_response", usage, failureDetails);
      return evidenceResult("invalid_response", mbid, {}, [], [], { failure_details: failureDetails, ...(usage ? { token_usage: usage } : {}) });
    }
    if (usage) updateFetchedStatus(fetched, fetched.status, usage);
    const parsed = resolveGeminiFallback(text.text, canonicalDefinitions, safeAllowedNames, fetched.digest, requestedFields);
    if (!parsed.output) {
      const failureDetails = safeFailureDetails(null, { failure_phase: "response", ...parsed });
      updateFetchedStatus(fetched, "invalid_response", usage, failureDetails);
      return evidenceResult("invalid_response", mbid, {}, [], [], { failure_details: failureDetails, ...(usage ? { token_usage: usage } : {}) });
    }
    if (!fetched.cache_hit) await cacheResponse(cacheRequest, "ok", fetched.bytes);
    updateFetchedStatus(fetched, "ok", usage);
    return evidenceResult("ok", mbid, parsed.output.scalars, parsed.output.genres, parsed.output.vibes, {
      response_digest: fetched.digest, model_id: settings.model, prompt_version: GEMINI_PROMPT_VERSION,
      schema_version: GEMINI_SCHEMA_VERSION, token_usage: usage, cache_hit: Boolean(fetched.cache_hit),
    });
  }

  return Object.freeze({
    searchRecordings,
    lookupRecording,
    getAcousticBrainzEvidence,
    requestGeminiTags,
    getRunLedger: ledgerSnapshot,
    getAttemptCounts: () => ({ total: totalAttempts, gemini: geminiAttempts }),
  });
}

module.exports = {
  DEFAULTS,
  GEMINI_PROMPT_VERSION,
  GEMINI_SCHEMA_VERSION,
  buildGeminiSchema,
  createSongEnrichmentProviderClient,
  normalizeAcousticSubmission,
  normalizeMusicBrainzLookup,
  normalizeMusicBrainzSearch,
  queryEscape,
  retryAfterMs,
};
