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
const MAX_ACOUSTIC_SUBMISSIONS = 100;
const GEMINI_PROMPT_VERSION = "genre-vibe-candidate-v1";
const GEMINI_SCHEMA_VERSION = "genre-vibe-schema-v1";
const GEMINI_SCHEMA = Object.freeze({
  type: "object",
  properties: {
    genres: { type: "array", items: { type: "string" } },
    vibes: { type: "array", items: { type: "string" } },
  },
  required: ["genres", "vibes"],
  additionalProperties: false,
});

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
  if (!isRecord(body) || !Number.isSafeInteger(body.count) || body.count < 0 ||
      !Number.isSafeInteger(body.offset) || body.offset < 0 || !Array.isArray(body.recordings)) {
    return { status: "invalid_response", reason: "incomplete_search_results", candidates: [] };
  }
  if (body.offset !== 0 || body.count !== body.recordings.length) {
    return { status: "invalid_response", reason: "incomplete_search_results", candidates: [] };
  }
  if (body.count === 0) return { status: "absent", candidates: [], response_digest: digest };
  const candidates = [];
  for (const row of body.recordings) {
    const artist = creditedArtist(row?.["artist-credit"]);
    if (!isRecord(row) || typeof row.id !== "string" || !UUID.test(row.id) ||
        typeof row.title !== "string" || !artist) return { status: "invalid_response" };
    candidates.push({
      mbid: row.id.toLowerCase(),
      title: row.title,
      artist,
      disambiguation: typeof row.disambiguation === "string" ? row.disambiguation : "",
      response_digest: digest,
      ...(validMusicBrainzDate(row["first-release-date"])
        ? { first_release_date: row["first-release-date"] }
        : {}),
    });
  }
  return { status: "ok", candidates, response_digest: digest };
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
      if (tonal.key_scale !== "none") {
        evidence.original_key.push({
          value: { key_key: tonal.key_key, key_scale: tonal.key_scale },
          source: "AcousticBrainz",
          response_digest: digest,
          submission_id: `${mbid.toLowerCase()}:${offset}`,
        });
      }
    }
  }
  return evidence;
}

function mapGeminiText(body) {
  if (!isRecord(body) || body.status !== "completed" || !Array.isArray(body.steps)) return null;
  const modelSteps = body.steps.filter(step => isRecord(step) && step.type === "model_output");
  if (modelSteps.length !== 1 || !Array.isArray(modelSteps[0].content)) return null;
  const textParts = modelSteps[0].content.filter(part => isRecord(part) && part.type === "text" && typeof part.text === "string");
  if (textParts.length !== 1) return null;
  const usage = isRecord(body.usage) ? {
    promptTokenCount: body.usage.total_input_tokens,
    candidatesTokenCount: body.usage.total_output_tokens,
    totalTokenCount: body.usage.total_tokens,
  } : null;
  return { text: textParts[0].text, usage };
}

function resolveGeminiTags(jsonText, definitions, digest) {
  const parsed = parseJson(Buffer.from(jsonText, "utf8"));
  if (!isRecord(parsed) || Object.keys(parsed).sort().join(",") !== "genres,vibes" ||
      !Array.isArray(parsed.genres) || !Array.isArray(parsed.vibes)) return null;
  const output = { genres: [], vibes: [] };
  for (const [category, field] of [["Genre", "genres"], ["Vibe", "vibes"]]) {
    if (!Array.isArray(definitions[field])) return null;
    const lookup = new Map();
    for (const row of definitions[field]) {
      const name = typeof row === "string" ? row : row?.name;
      if (typeof name !== "string") return null;
      let key;
      try { key = definitionKey(normalizeDefinitionName(name, category)); } catch { return null; }
      if (!lookup.has(key)) lookup.set(key, []);
      lookup.get(key).push(name);
    }
    const emitted = new Set();
    for (const label of parsed[field]) {
      if (typeof label !== "string") return null;
      let key;
      try { key = definitionKey(normalizeDefinitionName(label, category)); } catch { return null; }
      const matches = lookup.get(key) || [];
      if (matches.length !== 1) return null;
      if (!emitted.has(key)) {
        output[field].push({ name: matches[0], source: "Gemini", response_digest: digest });
        emitted.add(key);
      }
    }
  }
  return output;
}

function safeTokenUsage(value) {
  if (!isRecord(value)) return null;
  const output = {};
  for (const [source, target] of [["promptTokenCount", "input_tokens"], ["candidatesTokenCount", "output_tokens"], ["totalTokenCount", "total_tokens"]]) {
    if (Number.isSafeInteger(value[source]) && value[source] >= 0) output[target] = value[source];
  }
  return Object.keys(output).length ? output : null;
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
} = {}) {
  if (typeof transport !== "function" || typeof clock !== "function" || typeof delay !== "function") throw new TypeError("Provider dependencies must be functions.");
  const settings = validateConfig(config);
  const ledger = [];
  let totalAttempts = 0;
  let geminiAttempts = 0;
  let mbNextStart = 0;
  let acousticNotBefore = 0;
  let mbQueue = Promise.resolve();

  function record(provider, operation, attempt, status, tokenUsage = null) {
    const index = ledger.length;
    ledger.push({ provider, operation, attempt, status, ...(tokenUsage ? { token_usage: tokenUsage } : {}) });
    return index;
  }

  function updateFetchedStatus(fetched, status, tokenUsage = undefined) {
    if (!Number.isInteger(fetched?.ledger_index)) return;
    const item = ledger[fetched.ledger_index];
    if (!item) return;
    item.status = status;
    if (tokenUsage) item.token_usage = tokenUsage;
  }

  function ledgerSnapshot() {
    return ledger.map(item => ({ ...item, ...(item.token_usage ? { token_usage: { ...item.token_usage } } : {}) }));
  }

  async function cachedRequest(key) {
    if (!cache || typeof cache.get !== "function") return null;
    try { return await cache.get(key); } catch { return null; }
  }

  async function cacheResponse(key, status, bytes) {
    if (!cache || typeof cache.set !== "function") return;
    try { await cache.set(key, { status, response: bytes }); } catch { /* cache failures do not alter provider results */ }
  }

  async function send(provider, operation, request, { signal, gemini = false, musicBrainz = false } = {}) {
    const run = async () => {
      for (let retry = 0; retry <= 1; retry += 1) {
        if (signal?.aborted) return { status: "cancelled" };
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
          // JavaScript runs reservation, start-time update and transport invocation
          // without yielding, so concurrent calls cannot reuse a remaining slot.
          totalAttempts += 1;
          if (gemini) geminiAttempts += 1;
          attempt = totalAttempts;
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
            record(provider, operation, attempt, "transient_error");
            return { status: "transient_error", reason: "timeout" };
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
            record(provider, operation, attempt, "invalid_response");
            return { status: "invalid_response" };
          }
          if (statusCode === 401 || statusCode === 403) {
            record(provider, operation, attempt, "authentication_error");
            return { status: "authentication_error" };
          }
          if (statusCode === 429 || statusCode >= 500 && statusCode <= 599) {
            const supplied = readHeader(response.headers, "retry-after");
            const wait = supplied != null ? retryAfterMs(supplied, Number(clock()))
              : statusCode === 429 && acousticResetWait !== null ? acousticResetWait
                : settings.retryDelayMs;
            if (supplied != null && wait === null) {
              record(provider, operation, attempt, "transient_error");
              return { status: "transient_error", reason: "invalid_retry_after" };
            }
            if (wait !== null && wait > settings.maxRetryAfterMs) {
              record(provider, operation, attempt, "transient_error");
              return { status: "transient_error", reason: "retry_after_exceeds_cap" };
            }
            if (retry === 0 && wait !== null && wait <= settings.maxRetryAfterMs) {
              record(provider, operation, attempt, "transient_error");
              try { await delay(wait, { signal, kind: "retry_after" }); }
              catch { return { status: signal?.aborted ? "cancelled" : "transient_error" }; }
              continue;
            }
            record(provider, operation, attempt, "transient_error");
            return { status: "transient_error", ...(supplied != null ? { reason: "retry_limit" } : {}) };
          }
          if (statusCode < 200 || statusCode >= 300) {
            record(provider, operation, attempt, "invalid_response");
            return { status: "invalid_response" };
          }
          const bytes = responseBytes(response.body);
          if (!bytes || bytes.byteLength > settings.maxResponseBytes) {
            record(provider, operation, attempt, "invalid_response");
            return { status: "invalid_response" };
          }
          const body = parseJson(bytes);
          if (body === null) {
            record(provider, operation, attempt, "invalid_response");
            return { status: "invalid_response" };
          }
          const usage = safeTokenUsage(body.usageMetadata);
          const ledger_index = record(provider, operation, attempt, "ok", usage);
          return { status: "ok", body, bytes, digest: responseDigest(bytes), usage, ledger_index };
        } catch (error) {
          removeParentAbort?.();
          timeoutController?.abort();
          if (attempt === null) return { status: signal?.aborted ? "cancelled" : "transient_error" };
          if (signal?.aborted || error?.name === "AbortError") {
            record(provider, operation, attempt, "cancelled");
            return { status: "cancelled" };
          }
          if (error?.code === "NETWORK_DISABLED") {
            record(provider, operation, attempt, "network_disabled");
            return { status: "network_disabled" };
          }
          if (retry === 0) {
            record(provider, operation, attempt, "transient_error");
            try { await delay(settings.retryDelayMs, { signal, kind: "retry" }); }
            catch { return { status: signal?.aborted ? "cancelled" : "transient_error" }; }
            continue;
          }
          record(provider, operation, attempt, "transient_error");
          return { status: "transient_error" };
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
    const url = `https://musicbrainz.org/ws/2/recording/?query=${queryString(query)}&fmt=json&limit=25`;
    const cacheRequest = { provider: "MusicBrainz", operation: "recording_search", parameters: { title, artist } };
    const fetched = await requestCached("MusicBrainz", "recording_search", { url, method: "GET", headers: musicBrainzHeaders() }, cacheRequest,
      { signal, musicBrainz: true });
    if (!["ok"].includes(fetched.status)) return result(fetched.status, { candidates: [], ...(fetched.reason ? { reason: fetched.reason } : {}) });
    const parsed = normalizeMusicBrainzSearch(parseJson(fetched.bytes), fetched.digest);
    updateFetchedStatus(fetched, parsed.status);
    if (!fetched.cache_hit && ["ok", "absent"].includes(parsed.status)) await cacheResponse(cacheRequest, parsed.status, fetched.bytes);
    if (parsed.status === "absent") return result("absent", { candidates: [], response_digest: fetched.digest, cache_hit: Boolean(fetched.cache_hit) });
    if (parsed.status !== "ok") return result(parsed.status, { candidates: [], ...(parsed.reason ? { reason: parsed.reason } : {}) });
    return { ...parsed, cache_hit: Boolean(fetched.cache_hit) };
  }

  async function lookupRecording({ mbid, searchCandidate = null, signal } = {}) {
    if (typeof mbid !== "string" || !UUID.test(mbid)) return result("invalid_response");
    const normalized = mbid.toLowerCase();
    const url = `https://musicbrainz.org/ws/2/recording/${encodeURIComponent(normalized)}?inc=genres+tags&fmt=json`;
    const cacheRequest = { provider: "MusicBrainz", operation: "recording_lookup", parameters: { mbid: normalized } };
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

  async function acousticRequest(mbid, offset, signal) {
    const base = `https://acousticbrainz.org/api/v1/${encodeURIComponent(mbid.toLowerCase())}`;
    const url = offset === null ? `${base}/count` : `${base}/low-level?n=${offset}`;
    const cacheRequest = {
      provider: "AcousticBrainz",
      operation: offset === null ? "recording_count" : "recording_submission",
      parameters: offset === null ? { mbid } : { mbid, offset },
    };
    const cached = await cachedRequest(cacheRequest);
    if (cached) {
      const bytes = responseBytes(cached.response);
      const body = bytes && parseJson(bytes);
      if (isRecord(body)) return { status: "ok", body, bytes, digest: cached.response_digest, cache_hit: true, cache_key: cacheRequest };
    }
    const fetched = await send("AcousticBrainz", offset === null ? "count" : "submission", {
      url, method: "GET", headers: { Accept: "application/json" },
    }, { signal });
    return { ...fetched, cache_key: cacheRequest };
  }

  async function getAcousticBrainzEvidence({ mbid, signal } = {}) {
    if (typeof mbid !== "string" || !UUID.test(mbid)) return evidenceResult("invalid_response", null,
      { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown" });
    const id = mbid.toLowerCase();
    const countResponse = await acousticRequest(id, null, signal);
    if (countResponse.status !== "ok") return evidenceResult(countResponse.status, id,
      { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown", ...(countResponse.reason ? { reason: countResponse.reason } : {}) });
    if (typeof countResponse.body.mbid !== "string" || countResponse.body.mbid.toLowerCase() !== id ||
        !Number.isSafeInteger(countResponse.body.count) || countResponse.body.count < 0) {
      updateFetchedStatus(countResponse, "invalid_response");
      return evidenceResult("invalid_response", id, { original_key: [], bpm: [] }, [], [], { submission_enumeration: "unknown" });
    }
    const count = countResponse.body.count;
    if (count === 0) {
      updateFetchedStatus(countResponse, "absent");
      if (!countResponse.cache_hit) await cacheResponse(countResponse.cache_key, "absent", countResponse.bytes);
      return evidenceResult("absent", id, { original_key: [], bpm: [] }, [], [], {
        submission_enumeration: "complete", response_digest: countResponse.digest, cache_hit: Boolean(countResponse.cache_hit),
      });
    }
    if (!countResponse.cache_hit) await cacheResponse(countResponse.cache_key, "ok", countResponse.bytes);
    if (count > MAX_ACOUSTIC_SUBMISSIONS) {
      updateFetchedStatus(countResponse, "budget_exhausted");
      return evidenceResult("budget_exhausted", id, { original_key: [], bpm: [] }, [], [], { submission_enumeration: "partial" });
    }
    const evidence = { original_key: [], bpm: [] };
    for (let offset = 0; offset < count; offset += 1) {
      const fetched = await acousticRequest(id, offset, signal);
      if (fetched.status !== "ok") return evidenceResult(fetched.status, id, evidence, [], [], {
        submission_enumeration: "partial", ...(fetched.reason ? { reason: fetched.reason } : {}),
      });
      const document = unwrapAcousticDocument(fetched.body, id, offset);
      if (!document) {
        updateFetchedStatus(fetched, "invalid_response");
        return evidenceResult("invalid_response", id, evidence, [], [], { submission_enumeration: "partial" });
      }
      const parsed = normalizeAcousticSubmission(document, id, offset, fetched.digest);
      if (!parsed) {
        updateFetchedStatus(fetched, "invalid_response");
        return evidenceResult("invalid_response", id, evidence, [], [], { submission_enumeration: "partial" });
      }
      updateFetchedStatus(fetched, "ok");
      if (!fetched.cache_hit) await cacheResponse(fetched.cache_key, "ok", fetched.bytes);
      evidence.original_key.push(...parsed.original_key);
      evidence.bpm.push(...parsed.bpm);
    }
    return evidenceResult("ok", id, evidence, [], [], { submission_enumeration: "complete", submission_count: count });
  }

  function geminiPayload({ mbid, title, artist, context, definitions }) {
    const safeContext = {};
    if (isRecord(context)) {
      if (context.release_year === null || Number.isInteger(context.release_year) && context.release_year >= 1 && context.release_year <= 32767) {
        if (context.release_year !== undefined) safeContext.release_year = context.release_year;
      }
      if (typeof context.recording_type === "string") safeContext.recording_type = context.recording_type;
    }
    const genres = Array.isArray(definitions?.genres) ? definitions.genres.map(row => typeof row === "string" ? row : row?.name).filter(name => typeof name === "string") : [];
    const vibes = Array.isArray(definitions?.vibes) ? definitions.vibes.map(row => typeof row === "string" ? row : row?.name).filter(name => typeof name === "string") : [];
    return {
      recording: { mbid, title, artist, context: safeContext },
      allowed_names: { genres, vibes },
    };
  }

  async function requestGeminiTags({ mbid, title, artist, context, definitions, apiKey, signal } = {}) {
    if (typeof mbid !== "string" || !UUID.test(mbid) || typeof title !== "string" || !title.trim() ||
        typeof artist !== "string" || !artist.trim() || !isRecord(definitions) ||
        !Array.isArray(definitions.genres) || !Array.isArray(definitions.vibes)) return evidenceResult("invalid_response", null, {}, [], []);
    const canonicalDefinitions = { genres: [], vibes: [] };
    for (const [category, field] of [["Genre", "genres"], ["Vibe", "vibes"]]) {
      try {
        canonicalDefinitions[field] = definitions[field].map(row => {
          const name = typeof row === "string" ? row : row?.name;
          normalizeDefinitionName(name, category);
          return { name };
        });
      } catch { return evidenceResult("invalid_response", mbid, {}, [], []); }
    }
    const payload = geminiPayload({ mbid: mbid.toLowerCase(), title, artist, context, definitions: canonicalDefinitions });
    const body = {
      model: settings.model,
      input: [{ type: "text", text: `Suggest only directly supported Genre and Vibe labels from the supplied catalogs. Return empty arrays when unsure. Do not return scalars, identifiers, explanations, or other fields.\n${JSON.stringify(payload)}` }],
      system_instruction: "Suggest only directly supported Genre and Vibe labels from the supplied catalogs. Return empty arrays when unsure. Do not return scalars, identifiers, explanations, or other fields.",
      store: false,
      response_format: { type: "text", mime_type: "application/json", schema: GEMINI_SCHEMA },
      generation_config: { max_output_tokens: settings.geminiMaxOutputTokens },
    };
    const bytes = jsonBytes(body);
    if (bytes.byteLength > settings.geminiMaxInputBytes) return evidenceResult("invalid_response", mbid, {}, [], []);
    if (!Array.isArray(payload.allowed_names.genres) || !Array.isArray(payload.allowed_names.vibes)) return evidenceResult("invalid_response", mbid, {}, [], []);
    if (payload.allowed_names.genres.length === 0 && payload.allowed_names.vibes.length === 0) return evidenceResult("ok", mbid, {}, [], [], { skipped: "empty_definition_catalog" });
    const cacheRequest = {
      provider: "Gemini", operation: "genre_vibe_candidates",
      parameters: { mbid, title, artist, context: payload.recording.context },
      parserVersion: "1", model: settings.model, promptVersion: GEMINI_PROMPT_VERSION,
      schemaVersion: GEMINI_SCHEMA_VERSION,
      definitionCatalog: { genres: payload.allowed_names.genres, vibes: payload.allowed_names.vibes },
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
      if (typeof apiKey !== "string" || !apiKey.trim()) return evidenceResult("authentication_error", mbid, {}, [], []);
      fetched = await send("Gemini", "genre_vibe_candidates", request, { signal, gemini: true });
    }
    if (fetched.status !== "ok") return evidenceResult(fetched.status, mbid, {}, [], [], fetched.reason ? { reason: fetched.reason } : {});
    const bodyResponse = fetched.body ?? parseJson(fetched.bytes);
    const text = mapGeminiText(bodyResponse);
    if (!text) {
      updateFetchedStatus(fetched, "invalid_response");
      return evidenceResult("invalid_response", mbid, {}, [], []);
    }
    const parsed = resolveGeminiTags(text.text, canonicalDefinitions, fetched.digest);
    if (!parsed) {
      updateFetchedStatus(fetched, "invalid_response");
      return evidenceResult("invalid_response", mbid, {}, [], []);
    }
    if (!fetched.cache_hit) await cacheResponse(cacheRequest, "ok", fetched.bytes);
    const usage = safeTokenUsage(text.usage);
    updateFetchedStatus(fetched, "ok", usage);
    return evidenceResult("ok", mbid, {}, parsed.genres, parsed.vibes, {
      response_digest: fetched.digest, token_usage: usage, cache_hit: Boolean(fetched.cache_hit),
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
  createSongEnrichmentProviderClient,
  normalizeAcousticSubmission,
  normalizeMusicBrainzLookup,
  normalizeMusicBrainzSearch,
  queryEscape,
  retryAfterMs,
};
