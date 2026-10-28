const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");

const MODEL_ID = "gemini-3.1-flash-lite";
const POLICY_VERSION = "lite-gemini-spend-v1";
const PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing";
const PRICING_CHECKED_ON = "2026-10-27";
const PRICING_VALID_THROUGH = "2026-10-28";
const INPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS = 250_000;
const OUTPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS = 1_500_000;
const MAX_INPUT_BYTES = 16_384;
// Conservatively reserve one input token per allowed UTF-8 byte.
const MAX_INPUT_TOKENS = MAX_INPUT_BYTES;
const MAX_OUTPUT_TOKENS = 1_024;
const MAX_RESPONSE_BYTES = 1_048_576;
const REQUEST_TIMEOUT_MS = 10_000;
const MAX_SPEND_MICRO_USD = 2_000_000;
// One campaign-scoped exception: a single recovery reservation for each of
// these exact Lite IDs, bound to the one prior reservation and its artifacts.
const RECOVERY_SONG_IDS = Object.freeze([735, 736, 737, 738, 755]);
const MAX_REQUEST_MICRO_USD = Number((BigInt(MAX_INPUT_TOKENS) * BigInt(INPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS) +
  BigInt(MAX_OUTPUT_TOKENS) * BigInt(OUTPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS)) / 1_000_000n);
const SAFE_CODES = new Set(["GEMINI_SPEND_LIMIT", "GEMINI_SPEND_LEDGER_INVALID", "GEMINI_SPEND_LEDGER_LOCKED",
  "GEMINI_SPEND_POLICY_UNVERIFIED"]);
const LEGACY_GEMINI_REPORT_TYPES = new Set(["gemini_vibe_only_preview", "gemini_genre_only_preview"]);

function fail(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function policy() {
  return {
    model_id: MODEL_ID,
    pricing_source: PRICING_SOURCE,
    pricing_checked_on: PRICING_CHECKED_ON,
    pricing_valid_through: PRICING_VALID_THROUGH,
    input_price_micro_usd_per_million_tokens: INPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS,
    output_price_micro_usd_per_million_tokens: OUTPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS,
    max_input_bytes: MAX_INPUT_BYTES,
    reserved_input_token_ceiling: MAX_INPUT_TOKENS,
    max_output_tokens: MAX_OUTPUT_TOKENS,
    max_response_bytes: MAX_RESPONSE_BYTES,
    timeout_ms: REQUEST_TIMEOUT_MS,
    maximum_spend_micro_usd: MAX_SPEND_MICRO_USD,
    maximum_request_micro_usd: MAX_REQUEST_MICRO_USD,
  };
}

function priorPolicyShape() {
  const previous = policy();
  delete previous.pricing_valid_through;
  return previous;
}

function previouslyVerifiedPolicyShape() {
  const previous = policy();
  previous.pricing_checked_on = "2026-10-26";
  previous.pricing_valid_through = "2026-10-26";
  return previous;
}

function verifyProviderLimits(config, { today = new Date().toISOString().slice(0, 10) } = {}) {
  if (!config || config.model !== MODEL_ID || config.geminiMaxInputBytes !== MAX_INPUT_BYTES ||
      config.geminiMaxOutputTokens !== MAX_OUTPUT_TOKENS || config.maxResponseBytes !== MAX_RESPONSE_BYTES ||
      config.timeoutMs !== REQUEST_TIMEOUT_MS || !Number.isSafeInteger(config.maxGeminiAttempts) ||
      config.maxGeminiAttempts < 1 || !/^\d{4}-\d{2}-\d{2}$/.test(today) ||
      today < PRICING_CHECKED_ON || today > PRICING_VALID_THROUGH) throw fail("GEMINI_SPEND_POLICY_UNVERIFIED");
}

async function listFiles(root, filesystem, relative = "") {
  const result = [];
  let entries;
  try { entries = await filesystem.readdir(path.join(root, relative), { withFileTypes: true }); }
  catch { throw fail("GEMINI_SPEND_LEDGER_INVALID"); }
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const rel = path.join(relative, entry.name);
    if (entry.isSymbolicLink()) throw fail("GEMINI_SPEND_LEDGER_INVALID");
    if (entry.isDirectory()) {
      if (/^\.?(?:cohort-runner-test|gemini-spend-test)[-_]/i.test(entry.name)) continue;
      result.push(...await listFiles(root, filesystem, rel));
    } else if (entry.isFile() && ["checkpoint-v1.json", "report-v1.json"].includes(entry.name)) result.push(rel);
  }
  return result;
}

function validCheckpoint(envelope) {
  if (!envelope || !envelope.checkpoint || typeof envelope.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/i.test(envelope.sha256)) return false;
  const state = envelope.checkpoint;
  const knownStateMachine = [2, 3].includes(state.state_machine_version);
  const legacyZeroGeminiState = state.state_machine_version === undefined && state.gemini_attempts === 0 &&
    state.songs?.every(song => song.gemini_attempts === 0);
  return envelope.sha256 === sha256(canonical(state)) && state.checkpoint_version === 1 &&
    (knownStateMachine || legacyZeroGeminiState) && typeof state.binding?.cohort_id === "string" &&
    Array.isArray(state.songs) && Number.isSafeInteger(state.gemini_attempts) && state.gemini_attempts >= 0 &&
    state.gemini_attempts <= state.songs.length && state.songs.every(song => Number.isSafeInteger(song.gemini_attempts) &&
      song.gemini_attempts >= 0 && song.gemini_attempts <= 1) &&
    state.songs.reduce((sum, song) => sum + song.gemini_attempts, 0) === state.gemini_attempts;
}

function hasGeminiReportMarker(report) {
  return report?.mode === "explicit_gemini_missing_metadata_fallback" ||
    LEGACY_GEMINI_REPORT_TYPES.has(report?.report_type) ||
    Object.hasOwn(report || {}, "gemini_suggestions") || Object.hasOwn(report || {}, "gemini");
}

function legacyGeminiAttempts(report) {
  if (!LEGACY_GEMINI_REPORT_TYPES.has(report?.report_type)) return null;
  const limits = report.limits;
  const attempts = report.result?.attempts;
  if (report.report_version !== 1 || limits?.model !== MODEL_ID || limits.attempts_allowed !== 1 ||
      limits.retries_allowed !== 0 || limits.cache !== false || limits.max_input_bytes !== MAX_INPUT_BYTES ||
      limits.max_output_tokens !== MAX_OUTPUT_TOKENS || limits.timeout_ms !== REQUEST_TIMEOUT_MS ||
      limits.max_response_bytes !== MAX_RESPONSE_BYTES || !attempts || !Number.isSafeInteger(attempts.total) ||
      attempts.total < 0 || attempts.total > 1 || !Number.isSafeInteger(attempts.gemini) ||
      attempts.gemini < 0 || attempts.gemini > 1 || attempts.gemini !== attempts.total) {
    throw fail("GEMINI_SPEND_LEDGER_INVALID");
  }
  return attempts.gemini;
}

async function scanPriorReservations(backupsRoot, filesystem = fs) {
  const files = await listFiles(backupsRoot, filesystem);
  const checkpointDirs = new Set(files.filter(file => path.basename(file) === "checkpoint-v1.json")
    .map(file => path.dirname(file)));
  const sources = [];
  const evidenceSources = [];
  let attempts = 0;
  const reservedSongIds = new Set();
  let unattributedReservations = 0;
  for (const relativePath of files) {
    let document;
    try { document = JSON.parse(await filesystem.readFile(path.join(backupsRoot, relativePath), "utf8")); }
    catch { throw fail("GEMINI_SPEND_LEDGER_INVALID"); }
    let count = 0;
    if (path.basename(relativePath) === "checkpoint-v1.json") {
      if (!validCheckpoint(document)) throw fail("GEMINI_SPEND_LEDGER_INVALID");
      count = document.checkpoint.gemini_attempts;
    } else {
      if (checkpointDirs.has(path.dirname(relativePath))) continue;
      const legacyCount = legacyGeminiAttempts(document);
      const marked = hasGeminiReportMarker(document);
      const value = document?.attempts?.gemini;
      if (legacyCount === null && marked && (!Number.isSafeInteger(value) || value < 0 || !Number.isSafeInteger(document.attempts.total) ||
          document.attempts.total < value)) throw fail("GEMINI_SPEND_LEDGER_INVALID");
      if (value !== undefined && (!Number.isSafeInteger(value) || value < 0)) throw fail("GEMINI_SPEND_LEDGER_INVALID");
      count = legacyCount === null ? (Number.isSafeInteger(value) ? value : 0) : legacyCount;
    }
    if (count > 0) {
      let ids = [];
      let songAttempts = [];
      if (path.basename(relativePath) === "checkpoint-v1.json") {
        songAttempts = document.checkpoint.songs.filter(song => song.gemini_attempts > 0 && Number.isSafeInteger(song.song_id))
          .map(song => ({ song_id: song.song_id, attempts: song.gemini_attempts }));
        ids = songAttempts.map(song => song.song_id);
      } else {
        const report = document;
        if (Number.isSafeInteger(report.selected_song_id)) ids = [report.selected_song_id];
        else if (Array.isArray(report.selected_song_ids)) ids = report.selected_song_ids.filter(Number.isSafeInteger);
        else if (Number.isSafeInteger(report.song?.id)) ids = [report.song.id];
        else if (Array.isArray(report.songs)) ids = report.songs.filter(song => Number.isSafeInteger(song?.song_id)).map(song => song.song_id);
        if (ids.length && count === ids.length) songAttempts = [...new Set(ids)].map(song_id => ({ song_id, attempts: 1 }));
        else if (ids.length === 1) songAttempts = [{ song_id: ids[0], attempts: count }];
      }
      for (const id of new Set(ids)) reservedSongIds.add(id);
      unattributedReservations += Math.max(0, count - new Set(ids).size);
      const bytes = await filesystem.readFile(path.join(backupsRoot, relativePath));
      const source = { path: relativePath.split(path.sep).join("/"), sha256: sha256(bytes), reservations: count };
      sources.push(source);
      evidenceSources.push({ ...source, songs: songAttempts,
        ...(path.basename(relativePath) === "checkpoint-v1.json" ? {
          cohort_id: document.checkpoint.binding.cohort_id,
          ...(typeof document.checkpoint.binding.recovery_authorization_sha256 === "string"
            ? { recovery_authorization_sha256: document.checkpoint.binding.recovery_authorization_sha256 } : {}),
          ...(typeof document.checkpoint.binding.recovery_run_id === "string"
            ? { recovery_run_id: document.checkpoint.binding.recovery_run_id } : {}),
        } : {}) });
      attempts += count;
    }
  }
  return { sources, evidenceSources, attempts, reservedSongIds: [...reservedSongIds].sort((a, b) => a - b), unattributedReservations };
}

async function scanUnseededLegacyReservations(backupsRoot, seededSources, filesystem = fs) {
  const alreadySeeded = new Map(seededSources.map(source => [source.path, source.sha256]));
  const additions = [];
  for (const relativePath of await listFiles(backupsRoot, filesystem)) {
    if (path.basename(relativePath) !== "report-v1.json") continue;
    let report;
    try { report = JSON.parse(await filesystem.readFile(path.join(backupsRoot, relativePath), "utf8")); }
    catch { throw fail("GEMINI_SPEND_LEDGER_INVALID"); }
    if (!LEGACY_GEMINI_REPORT_TYPES.has(report?.report_type)) continue;
    const reservations = legacyGeminiAttempts(report);
    if (!reservations) continue;
    const bytes = await filesystem.readFile(path.join(backupsRoot, relativePath));
    const source = { path: relativePath.split(path.sep).join("/"), sha256: sha256(bytes), reservations };
    const previousHash = alreadySeeded.get(source.path);
    if (previousHash && previousHash !== source.sha256) throw fail("GEMINI_SPEND_LEDGER_INVALID");
    if (!previousHash) additions.push(source);
  }
  return additions;
}

function ledgerPayload(ledger) {
  const { sha256: ignored, ...payload } = ledger;
  return payload;
}

function validateLedger(ledger) {
  const exactKeys = (value, expected) => value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...expected].sort().join(",");
  const ledgerKeys = ["ledger_version", "policy_version", "policy", "created_at", "seed_sources", "seeded_reservations",
    "reservations", "reserved_micro_usd", "sha256"];
  if (!ledger || ledger.ledger_version !== 1 || ledger.policy_version !== POLICY_VERSION ||
      !exactKeys(ledger, ledgerKeys) || canonical(ledger.policy) !== canonical(policy()) ||
      !Number.isSafeInteger(Date.parse(ledger.created_at)) || !Array.isArray(ledger.seed_sources) ||
      !Number.isSafeInteger(ledger.seeded_reservations) || ledger.seeded_reservations < 0 ||
      !Array.isArray(ledger.reservations) || !Number.isSafeInteger(ledger.reserved_micro_usd) ||
      ledger.sha256 !== sha256(canonical(ledgerPayload(ledger)))) throw fail("GEMINI_SPEND_LEDGER_INVALID");
  const ids = new Set();
  const sources = new Set();
  for (const source of ledger.seed_sources) {
    if (!exactKeys(source, ["path", "sha256", "reservations"]) || typeof source.path !== "string" ||
        !/^[a-f0-9]{64}$/.test(source.sha256) || path.isAbsolute(source.path) ||
        !Number.isSafeInteger(source.reservations) || source.reservations <= 0 || sources.has(source.path)) {
      throw fail("GEMINI_SPEND_LEDGER_INVALID");
    }
    sources.add(source.path);
  }
  if (ledger.seed_sources.reduce((sum, source) => sum + source.reservations, 0) !== ledger.seeded_reservations) {
    throw fail("GEMINI_SPEND_LEDGER_INVALID");
  }
  let amount = ledger.seeded_reservations * MAX_REQUEST_MICRO_USD;
  for (const item of ledger.reservations) {
    const regularKeys = ["reservation_id", "cohort_id", "song_id", "reserved_at", "maximum_cost_micro_usd", "state"];
    const recoveryKeys = [...regularKeys, "recovery_parent", "recovery_authorization_sha256", "recovery_run_id"];
    if (!(exactKeys(item, regularKeys) || exactKeys(item, recoveryKeys)) ||
        typeof item.reservation_id !== "string" || !/^[0-9a-f-]{36}$/i.test(item.reservation_id) || ids.has(item.reservation_id) ||
        typeof item.cohort_id !== "string" || !item.cohort_id.trim() || !Number.isSafeInteger(item.song_id) || item.song_id < 1 ||
        !Number.isSafeInteger(Date.parse(item.reserved_at)) || item.maximum_cost_micro_usd !== MAX_REQUEST_MICRO_USD ||
        item.state !== "reserved_uncertain" || (exactKeys(item, recoveryKeys) &&
          (typeof item.recovery_parent !== "string" || !item.recovery_parent ||
           !/^[a-f0-9]{64}$/.test(item.recovery_authorization_sha256) ||
           !/^[a-f0-9]{64}$/.test(item.recovery_run_id)))) throw fail("GEMINI_SPEND_LEDGER_INVALID");
    ids.add(item.reservation_id);
    if (item.recovery_parent !== undefined && (!RECOVERY_SONG_IDS.includes(item.song_id) ||
        ledger.reservations.some(other => other !== item && other.song_id === item.song_id && other.recovery_parent !== undefined))) {
      throw fail("GEMINI_SPEND_LEDGER_INVALID");
    }
    amount += item.maximum_cost_micro_usd;
  }
  if (amount !== ledger.reserved_micro_usd || amount > MAX_SPEND_MICRO_USD) throw fail("GEMINI_SPEND_LEDGER_INVALID");
}

function validateRecoveryAuthorizationShape(authorization, authorizationSha256, cohortId, songIds) {
  const exactKeys = (value, expected) => value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...expected].sort().join(",");
  if (!/^[a-f0-9]{64}$/.test(authorizationSha256 || "") || !authorization || authorization.version !== 1 ||
      authorization.cohort_id !== cohortId || !Array.isArray(authorization.songs) ||
      !exactKeys(authorization, ["version", "cohort_id", "songs"])) throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
  const records = authorization.songs;
  if (records.length !== RECOVERY_SONG_IDS.length ||
      canonical(records.map(item => item?.song_id).sort((a, b) => a - b)) !== canonical([...RECOVERY_SONG_IDS])) {
    throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
  }
  if (!Array.isArray(songIds) || RECOVERY_SONG_IDS.some(id => !songIds.includes(id))) {
    throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
  }
  for (const item of records) {
    if (!exactKeys(item, ["song_id", "prior_reservation_id", "prior_sources"]) ||
        !Array.isArray(item.prior_sources) || !Number.isSafeInteger(item.song_id) ||
        (item.prior_reservation_id !== null && (typeof item.prior_reservation_id !== "string" ||
          !/^[0-9a-f-]{36}$/i.test(item.prior_reservation_id)))) throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
    for (const source of item.prior_sources) {
      if (!(exactKeys(source, ["path", "sha256", "attempts"]) ||
            exactKeys(source, ["path", "sha256", "attempts", "mirror_reservation_id"])) ||
          typeof source.path !== "string" || path.isAbsolute(source.path) || source.path.includes("..") ||
          !/^[a-f0-9]{64}$/.test(source.sha256 || "") || source.attempts !== 1 ||
          (source.mirror_reservation_id !== undefined && source.mirror_reservation_id !== item.prior_reservation_id)) {
        throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
      }
    }
  }
  return records;
}

function validateRecoveryAgainstSnapshot({ authorization, authorizationSha256, recoveryRunId, cohortId, songIds, ledger, prior }) {
  if (!/^[a-f0-9]{64}$/.test(recoveryRunId || "")) throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
  const records = validateRecoveryAuthorizationShape(authorization, authorizationSha256, cohortId, songIds);
  if (prior.unattributedReservations !== 0) throw fail("GEMINI_SPEND_LEDGER_INVALID");
  for (const item of records) {
    const ledgerEntries = ledger.reservations.filter(entry => entry.song_id === item.song_id);
    const historicalEntries = ledgerEntries.filter(entry => entry.recovery_parent === undefined);
    const recoveryEntries = ledgerEntries.filter(entry => entry.recovery_parent !== undefined);
    const recoveryParent = item.prior_reservation_id ||
      `${item.prior_sources[0]?.path}:${item.prior_sources[0]?.sha256}`;
    if (item.prior_reservation_id === null) {
      if (historicalEntries.length !== 0 || item.prior_sources.length !== 1 ||
          item.prior_sources[0].mirror_reservation_id !== undefined) throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
    } else if (historicalEntries.length !== 1 || historicalEntries[0].reservation_id !== item.prior_reservation_id ||
        item.prior_sources.length < 1 ||
        item.prior_sources.some(source => source.mirror_reservation_id !== item.prior_reservation_id)) {
      throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
    }
    if (recoveryEntries.length > 1 || recoveryEntries.some(entry => entry.recovery_parent !== recoveryParent ||
        entry.recovery_authorization_sha256 !== authorizationSha256 || entry.recovery_run_id !== recoveryRunId)) {
      throw fail("GEMINI_PRIOR_RESERVATION");
    }
    const actual = prior.evidenceSources.filter(source => !(source.cohort_id === cohortId &&
      source.recovery_authorization_sha256 === authorizationSha256 && source.recovery_run_id === recoveryRunId))
      .flatMap(source => source.songs.filter(song => song.song_id === item.song_id)
      .map(song => ({ path: source.path, sha256: source.sha256, attempts: song.attempts,
        ...(item.prior_reservation_id ? { mirror_reservation_id: item.prior_reservation_id } : {}) })));
    const expected = item.prior_sources.map(source => ({ ...source }));
    if (canonical(actual.sort((a, b) => a.path.localeCompare(b.path))) !==
        canonical(expected.sort((a, b) => a.path.localeCompare(b.path)))) throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
    if (!prior.reservedSongIds.includes(item.song_id)) throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
  }
  return true;
}

async function writeAtomic(filePath, value, filesystem = fs) {
  const temporary = `${filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
  let handle;
  try {
    handle = await filesystem.open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.close();
    handle = null;
    await filesystem.rename(temporary, filePath);
  } catch {
    if (handle) await handle.close().catch(() => {});
    await filesystem.unlink(temporary).catch(() => {});
    throw fail("GEMINI_SPEND_LEDGER_INVALID");
  }
}

function createGeminiSpendLedger({ ledgerPath, backupsRoot = path.dirname(ledgerPath), filesystem = fs,
  clock = () => new Date().toISOString() } = {}) {
  if (typeof ledgerPath !== "string" || typeof backupsRoot !== "string" || typeof clock !== "function") {
    throw fail("GEMINI_SPEND_POLICY_UNVERIFIED");
  }
  const lockPath = `${ledgerPath}.lock`;

  async function withLock(callback) {
    let lockHandle;
    try { lockHandle = await filesystem.open(lockPath, "wx", 0o600); }
    catch { throw fail("GEMINI_SPEND_LEDGER_LOCKED"); }
    try {
      await lockHandle.writeFile(JSON.stringify({ pid: process.pid, host: os.hostname(), acquired_at: clock() }));
      await lockHandle.sync();
      return await callback();
    } finally {
      await lockHandle.close().catch(() => {});
      await filesystem.unlink(lockPath).catch(() => {});
    }
  }

  async function readOrSeed() {
    let bytes;
    try {
      const stat = await filesystem.lstat(ledgerPath);
      if (stat.isSymbolicLink() || !stat.isFile()) throw fail("GEMINI_SPEND_LEDGER_INVALID");
    } catch (error) { if (error.code !== "ENOENT") throw error?.code === "GEMINI_SPEND_LEDGER_INVALID" ? error : fail("GEMINI_SPEND_LEDGER_INVALID"); }
    try { bytes = await filesystem.readFile(ledgerPath); }
    catch (error) {
      if (error.code !== "ENOENT") throw fail("GEMINI_SPEND_LEDGER_INVALID");
      const seed = await scanPriorReservations(backupsRoot, filesystem);
      const ledger = { ledger_version: 1, policy_version: POLICY_VERSION, policy: policy(), created_at: clock(),
        seed_sources: seed.sources, seeded_reservations: seed.attempts, reservations: [],
        reserved_micro_usd: seed.attempts * MAX_REQUEST_MICRO_USD };
      if (ledger.reserved_micro_usd > MAX_SPEND_MICRO_USD) throw fail("GEMINI_SPEND_LIMIT");
      ledger.sha256 = sha256(canonical(ledgerPayload(ledger)));
      validateLedger(ledger);
      await writeAtomic(ledgerPath, ledger, filesystem);
      return ledger;
    }
    let ledger;
    try { ledger = JSON.parse(bytes.toString("utf8")); } catch { throw fail("GEMINI_SPEND_LEDGER_INVALID"); }
    let policyMigrated = false;
    if ([priorPolicyShape(), previouslyVerifiedPolicyShape()].some(previous =>
      canonical(ledger?.policy) === canonical(previous))) {
      if (ledger.sha256 !== sha256(canonical(ledgerPayload(ledger)))) throw fail("GEMINI_SPEND_LEDGER_INVALID");
      ledger.policy = policy();
      delete ledger.sha256;
      ledger.sha256 = sha256(canonical(ledgerPayload(ledger)));
      validateLedger(ledger);
      policyMigrated = true;
    }
    validateLedger(ledger);
    for (const source of ledger.seed_sources) {
      let sourceBytes;
      try {
        const sourcePath = path.resolve(backupsRoot, source.path);
        const relativePath = path.relative(backupsRoot, sourcePath);
        if (relativePath === ".." || relativePath.startsWith(`..${path.sep}`) || path.isAbsolute(relativePath)) {
          throw fail("GEMINI_SPEND_LEDGER_INVALID");
        }
        const stat = await filesystem.lstat(sourcePath);
        if (stat.isSymbolicLink() || !stat.isFile()) throw fail("GEMINI_SPEND_LEDGER_INVALID");
        sourceBytes = await filesystem.readFile(sourcePath);
      }
      catch { throw fail("GEMINI_SPEND_LEDGER_INVALID"); }
      if (sha256(sourceBytes) !== source.sha256) throw fail("GEMINI_SPEND_LEDGER_INVALID");
    }
    // Reconcile the two known pre-ledger report formats. They carry an explicit
    // per-report attempt ledger but predate the shared ledger's top-level shape.
    const legacyAdditions = await scanUnseededLegacyReservations(backupsRoot, ledger.seed_sources, filesystem);
    if (legacyAdditions.length) {
      const additionalAttempts = legacyAdditions.reduce((sum, source) => sum + source.reservations, 0);
      const addedCost = additionalAttempts * MAX_REQUEST_MICRO_USD;
      if (ledger.reserved_micro_usd + addedCost > MAX_SPEND_MICRO_USD) throw fail("GEMINI_SPEND_LIMIT");
      ledger.seed_sources.push(...legacyAdditions);
      ledger.seeded_reservations += additionalAttempts;
      ledger.reserved_micro_usd += addedCost;
    }
    if (policyMigrated || legacyAdditions.length) {
      delete ledger.sha256;
      ledger.sha256 = sha256(canonical(ledgerPayload(ledger)));
      validateLedger(ledger);
      await writeAtomic(ledgerPath, ledger, filesystem);
    }
    return ledger;
  }

  return Object.freeze({
    async reservedSongIds() {
      return withLock(async () => {
        const ledger = await readOrSeed();
        const prior = await scanPriorReservations(backupsRoot, filesystem);
        const ids = new Set(prior.reservedSongIds);
        for (const item of ledger.reservations) ids.add(item.song_id);
        return { songIds: [...ids].sort((a, b) => a - b), unattributedReservations: prior.unattributedReservations };
      });
    },
    async initialize(providerConfig) {
      verifyProviderLimits(providerConfig);
      return withLock(async () => {
        const ledger = await readOrSeed();
        return { seededReservations: ledger.seeded_reservations, reservedMicroUsd: ledger.reserved_micro_usd,
          remainingMicroUsd: MAX_SPEND_MICRO_USD - ledger.reserved_micro_usd, ledgerSha256: ledger.sha256 };
      });
    },
    async validateRecoveryAuthorization({ authorization, authorizationSha256, recoveryRunId, cohortId, songIds }) {
      return withLock(async () => {
        const ledger = await readOrSeed();
        const prior = await scanPriorReservations(backupsRoot, filesystem);
        return validateRecoveryAgainstSnapshot({ authorization, authorizationSha256, recoveryRunId, cohortId, songIds, ledger, prior });
      });
    },
    async reserve({ providerConfig, cohortId, songId, recoveryAuthorization, recoveryAuthorizationSha256, recoveryRunId }) {
      verifyProviderLimits(providerConfig);
      if (typeof cohortId !== "string" || !cohortId.trim() || !Number.isSafeInteger(songId) || songId < 1) {
        throw fail("GEMINI_SPEND_POLICY_UNVERIFIED");
      }
      return withLock(async () => {
        const ledger = await readOrSeed();
        const prior = await scanPriorReservations(backupsRoot, filesystem);
        const recoveryRecord = recoveryAuthorization?.songs?.find(item => item?.song_id === songId);
        if (ledger.reservations.some(item => item.song_id === songId && item.recovery_parent !== undefined)) {
          throw fail("GEMINI_PRIOR_RESERVATION");
        }
        if (recoveryRecord) {
          validateRecoveryAgainstSnapshot({ authorization: recoveryAuthorization, authorizationSha256: recoveryAuthorizationSha256,
            recoveryRunId, cohortId, songIds: recoveryAuthorization.songs.map(item => item?.song_id), ledger, prior });
          if (recoveryRecord.prior_reservation_id === null && recoveryRecord.prior_sources.length !== 1) {
            throw fail("GEMINI_RECOVERY_AUTHORIZATION_INVALID");
          }
        } else if (prior.reservedSongIds.includes(songId) ||
            ledger.reservations.some(item => item.song_id === songId)) throw fail("GEMINI_PRIOR_RESERVATION");
        if (ledger.reserved_micro_usd + MAX_REQUEST_MICRO_USD > MAX_SPEND_MICRO_USD) throw fail("GEMINI_SPEND_LIMIT");
        ledger.reservations.push({ reservation_id: crypto.randomUUID(), cohort_id: cohortId, song_id: songId,
          reserved_at: clock(), maximum_cost_micro_usd: MAX_REQUEST_MICRO_USD, state: "reserved_uncertain",
          ...(recoveryRecord ? { recovery_parent: recoveryRecord.prior_reservation_id ||
            `${recoveryRecord.prior_sources[0].path}:${recoveryRecord.prior_sources[0].sha256}`,
          recovery_authorization_sha256: recoveryAuthorizationSha256, recovery_run_id: recoveryRunId } : {}) });
        ledger.reserved_micro_usd += MAX_REQUEST_MICRO_USD;
        delete ledger.sha256;
        ledger.sha256 = sha256(canonical(ledgerPayload(ledger)));
        validateLedger(ledger);
        await writeAtomic(ledgerPath, ledger, filesystem);
        return { reservedMicroUsd: MAX_REQUEST_MICRO_USD, totalReservedMicroUsd: ledger.reserved_micro_usd,
          ledgerSha256: ledger.sha256 };
      });
    },
  });
}

module.exports = { MAX_INPUT_BYTES, MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, MAX_REQUEST_MICRO_USD,
  MAX_RESPONSE_BYTES, MAX_SPEND_MICRO_USD, MODEL_ID, POLICY_VERSION, PRICING_CHECKED_ON, PRICING_VALID_THROUGH,
  RECOVERY_SONG_IDS, createGeminiSpendLedger,
  scanPriorReservations, verifyProviderLimits };
