const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");

const CACHE_VERSION = 1;
const CACHE_VERSION_KEY = "song-enrichment-cache-v1";
const MUSICBRAINZ_SEARCH_PARSER_VERSION = "musicbrainz-search-single-response-v1";
const SUCCESS_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const ABSENCE_TTL_MS = 60 * 60 * 1000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function compareStableStrings(left, right) {
  const collated = left.localeCompare(right, "en");
  return collated || (left === right ? 0 : left < right ? -1 : 1);
}

function normalizeIdentity(value) {
  if (typeof value !== "string") throw new TypeError("Cache identity must be text.");
  const normalized = value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
  if (!normalized) throw new TypeError("Cache identity must not be empty.");
  return normalized;
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort(compareStableStrings).map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function normalizeCacheRequest(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) throw new TypeError("Cache request is invalid.");
  const { provider, operation, parameters, parserVersion = "1", model = null,
    promptVersion = null, schemaVersion = null, definitionCatalog = null } = request;
  if (!new Set(["MusicBrainz", "AcousticBrainz", "Gemini"]).has(provider) || typeof operation !== "string" || !operation) {
    throw new TypeError("Cache provider or operation is invalid.");
  }
  if (typeof parserVersion !== "string" || !parserVersion) throw new TypeError("Parser version is required.");
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) throw new TypeError("Cache parameters are required.");
  let normalizedParameters;
  if (provider === "MusicBrainz" && operation === "recording_search") {
    const artist = normalizeIdentity(parameters.artist);
    const title = normalizeIdentity(parameters.title);
    const query = parameters.query === undefined
      ? `recording:"${title}" AND artist:"${artist}"`
      : normalizeIdentity(parameters.query);
    const limit = parameters.limit === undefined ? 100 : parameters.limit;
    const offset = parameters.offset === undefined ? 0 : parameters.offset;
    if (limit !== 100) throw new TypeError("MusicBrainz search cache limit must be 100.");
    if (offset !== 0) throw new TypeError("MusicBrainz search cache offset must be zero.");
    normalizedParameters = { artist, limit, offset, query, title };
  } else if ((provider === "MusicBrainz" && operation === "recording_lookup") ||
      (provider === "AcousticBrainz" && ["recording_count", "recording_submission"].includes(operation))) {
    if (typeof parameters.mbid !== "string" || !UUID.test(parameters.mbid)) throw new TypeError("A valid recording MBID is required.");
    normalizedParameters = { mbid: parameters.mbid.toLowerCase() };
    if (provider === "AcousticBrainz" && operation === "recording_submission") {
      if (!Number.isSafeInteger(parameters.offset) || parameters.offset < 0) throw new TypeError("A valid submission offset is required.");
      normalizedParameters.offset = parameters.offset;
    }
  } else if (provider === "Gemini" && operation === "genre_vibe_candidates") {
    if (typeof model !== "string" || !model || typeof promptVersion !== "string" || !promptVersion ||
        typeof schemaVersion !== "string" || !schemaVersion || !definitionCatalog ||
        !Array.isArray(definitionCatalog.genres) || !Array.isArray(definitionCatalog.vibes)) {
      throw new TypeError("Gemini cache version and definition catalog are required.");
    }
    if (typeof parameters.mbid !== "string" || !UUID.test(parameters.mbid)) throw new TypeError("A valid recording MBID is required.");
    normalizedParameters = {
      artist: normalizeIdentity(parameters.artist),
      context: parameters.context ?? null,
      mbid: parameters.mbid.toLowerCase(),
      title: normalizeIdentity(parameters.title),
    };
  } else {
    throw new TypeError("Unsupported provider cache operation.");
  }
  const normalized = { cacheVersion: CACHE_VERSION_KEY, provider, operation,
    parserVersion: provider === "MusicBrainz" && operation === "recording_search"
      ? MUSICBRAINZ_SEARCH_PARSER_VERSION : parserVersion,
    parameters: normalizedParameters };
  if (provider === "Gemini") {
    normalized.model = model;
    normalized.promptVersion = promptVersion;
    normalized.schemaVersion = schemaVersion;
    normalized.definitionCatalog = {
      genres: definitionCatalog.genres.map(name => name).sort(compareStableStrings),
      vibes: definitionCatalog.vibes.map(name => name).sort(compareStableStrings),
    };
  }
  return normalized;
}

function cacheKey(request) {
  return crypto.createHash("sha256").update(stableJson(normalizeCacheRequest(request))).digest("hex");
}

function digest(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function entryBytes(response) {
  if (typeof response === "string") return Buffer.from(response, "utf8");
  if (Buffer.isBuffer(response)) return response;
  if (response instanceof Uint8Array) return Buffer.from(response);
  throw new TypeError("Cached response must be text or bytes.");
}

function privateModeSupported() {
  return process.platform !== "win32";
}

async function createSongEnrichmentCache({ directory, clock = Date.now, maxEntryBytes = 2_000_000 } = {}) {
  if (typeof directory !== "string" || !directory.trim()) throw new TypeError("An explicit cache directory is required.");
  if (typeof clock !== "function") throw new TypeError("Cache clock must be a function.");
  if (!Number.isSafeInteger(maxEntryBytes) || maxEntryBytes < 1) throw new TypeError("maxEntryBytes must be a positive integer.");
  const root = path.resolve(directory);

  async function get(request) {
    const key = cacheKey(request);
    const file = path.join(root, `${key}.json`);
    let bytes;
    try {
      const stat = await fs.lstat(file);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > maxEntryBytes) return null;
      bytes = await fs.readFile(file);
    } catch (error) {
      if (error.code === "ENOENT" || error.code === "EACCES" || error.code === "EPERM") return null;
      throw error;
    }
    if (bytes.byteLength > maxEntryBytes) return null;
    let entry;
    try { entry = JSON.parse(bytes.toString("utf8")); } catch { return null; }
    if (!entry || entry.cache_version !== CACHE_VERSION || entry.cache_key !== key ||
        !["ok", "absent"].includes(entry.status) || !Number.isFinite(entry.fetched_at) ||
        !Number.isFinite(entry.expires_at) || !Number.isSafeInteger(entry.response_bytes) ||
        entry.expires_at - entry.fetched_at !== (entry.status === "absent" ? ABSENCE_TTL_MS : SUCCESS_TTL_MS) ||
        typeof entry.response !== "string") return null;
    const response = Buffer.from(entry.response, "base64");
    if (response.byteLength !== entry.response_bytes || response.byteLength > maxEntryBytes ||
        digest(response) !== entry.response_digest) return null;
    if (entry.expires_at <= Number(clock())) return null;
    return { status: entry.status, response, response_digest: entry.response_digest, fetched_at: entry.fetched_at };
  }

  async function set(request, { status, response } = {}) {
    if (!new Set(["ok", "absent"]).has(status)) throw new TypeError("Only successful or confirmed-absent responses may be cached.");
    const key = cacheKey(request);
    const raw = entryBytes(response);
    if (raw.byteLength > maxEntryBytes) return false;
    const fetchedAt = Number(clock());
    if (!Number.isFinite(fetchedAt)) throw new TypeError("Cache clock must return a finite timestamp.");
    const expiresAt = fetchedAt + (status === "absent" ? ABSENCE_TTL_MS : SUCCESS_TTL_MS);
    const responseDigest = digest(raw);
    const entry = {
      cache_version: CACHE_VERSION,
      cache_key: key,
      status,
      fetched_at: fetchedAt,
      expires_at: expiresAt,
      response_bytes: raw.byteLength,
      response_digest: responseDigest,
      response: raw.toString("base64"),
    };
    const bytes = Buffer.from(JSON.stringify(entry), "utf8");
    if (bytes.byteLength > maxEntryBytes) return false;
    await fs.mkdir(root, { recursive: true, mode: 0o700 });
    const temporary = path.join(root, `.${key}.${process.pid}.${crypto.randomUUID()}.tmp`);
    let replaced = false;
    try {
      await fs.writeFile(temporary, bytes, { flag: "wx", mode: 0o600 });
      await fs.rename(temporary, path.join(root, `${key}.json`));
      replaced = true;
      if (privateModeSupported()) await fs.chmod(path.join(root, `${key}.json`), 0o600).catch(() => {});
      return true;
    } catch {
      return false;
    } finally {
      if (!replaced) await fs.unlink(temporary).catch(() => {});
    }
  }

  return Object.freeze({ directory: root, get, set, cacheKey });
}

module.exports = {
  ABSENCE_TTL_MS,
  CACHE_VERSION,
  CACHE_VERSION_KEY,
  SUCCESS_TTL_MS,
  cacheKey,
  createSongEnrichmentCache,
  normalizeCacheRequest,
};
