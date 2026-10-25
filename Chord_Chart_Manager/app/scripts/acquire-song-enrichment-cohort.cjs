#!/usr/bin/env node
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { Pool } = require("../server/node_modules/pg");
const { createSongEnrichmentCache } = require("../server/songEnrichmentCache.js");
const { createSongEnrichmentProviderClient } = require("../server/songEnrichmentProviders.js");
const { createSongEnrichmentTransport, MAX_RESPONSE_BYTES } = require("../server/songEnrichmentTransport.js");
const { runPreview, captureReadOnlyBaseline, checkIgnoredDirectory } = require("./preview-existing-song-enrichment.cjs");
const { loadConfiguration, preparePreview, executePreparedPreview } = require("./preview-gemini-fallback.cjs");
const operator = require("./operator-song-enrichment.cjs");
const { runSongEnrichmentCohort } = require("../server/songEnrichmentCohortRunner.js");

const ROOT = path.resolve(__dirname, "../..");
function error(code) { const value = new Error(code); value.code = code; return value; }
function providerAttemptBudget(songCount, factualAttemptsPerSong) {
  return (factualAttemptsPerSong + 1) * songCount;
}
function parseArgs(argv) {
  const values = new Set(["--manifest", "--output-dir", "--cache-dir", "--factual-attempts-per-song"]), options = {};
  let liveReadOnly = false;
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index];
    if (name === "--live-read-only" && !liveReadOnly) { liveReadOnly = true; continue; }
    if (!values.has(name) || !argv[index + 1] || argv[index + 1].startsWith("--")) throw error("USAGE");
    const key = ({ "--manifest": "manifest", "--output-dir": "outputDir", "--cache-dir": "cacheDir",
      "--factual-attempts-per-song": "factualAttemptsPerSong" })[name];
    if (options[key]) throw error("USAGE");
    options[key] = argv[++index];
  }
  if (!options.manifest || !options.outputDir || !options.cacheDir) throw error("USAGE");
  if (!liveReadOnly) throw error("LIVE_READ_ONLY_OPT_IN_REQUIRED");
  if (options.factualAttemptsPerSong !== undefined &&
      (!/^[1-9]\d*$/.test(options.factualAttemptsPerSong) || Number(options.factualAttemptsPerSong) > 12)) {
    throw error("FACTUAL_ATTEMPT_CAP_INVALID");
  }
  options.factualAttemptsPerSong = options.factualAttemptsPerSong === undefined ? 12 : Number(options.factualAttemptsPerSong);
  return { ...options, liveReadOnly };
}
function insideBackups(target) {
  const backups = path.join(ROOT, "backups");
  const absolute = path.resolve(target);
  return absolute.startsWith(`${backups}${path.sep}`) && checkIgnoredDirectory(absolute);
}
function physicallyWithin(root, target) {
  const relative = path.relative(root, target);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
async function validatePhysicalBackupPath(target, { kind, mustExist }) {
  const absolute = path.resolve(target), backups = path.join(ROOT, "backups");
  if (!insideBackups(absolute)) throw error("ARTIFACT_NOT_PRIVATE");
  const [realBackups, realParent] = await Promise.all([fs.realpath(backups), fs.realpath(path.dirname(absolute))]);
  if (!physicallyWithin(realBackups, realParent)) throw error("ARTIFACT_NOT_PRIVATE");
  try {
    const stat = await fs.lstat(absolute);
    if (stat.isSymbolicLink() || (kind === "file" ? !stat.isFile() : !stat.isDirectory())) throw error("ARTIFACT_INVALID");
    const realTarget = await fs.realpath(absolute);
    if (!physicallyWithin(realBackups, realTarget)) throw error("ARTIFACT_NOT_PRIVATE");
  } catch (failure) {
    if (failure.code !== "ENOENT" || mustExist) throw failure;
  }
  return absolute;
}
async function requirePrivateFile(file) {
  return validatePhysicalBackupPath(file, { kind: "file", mustExist: true });
}
async function ensurePrivateDirectory(directory, { create = false } = {}) {
  const absolute = await validatePhysicalBackupPath(directory, { kind: "directory", mustExist: !create });
  try {
    const stat = await fs.lstat(absolute);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw error("ARTIFACT_INVALID");
  } catch (failure) {
    if (failure.code !== "ENOENT" || !create) throw failure;
    await fs.mkdir(absolute, { recursive: false, mode: 0o700 });
  }
  return absolute;
}
async function writeAtomic(file, value) {
  const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  const handle = await fs.open(temporary, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`); await handle.sync(); } finally { await handle.close(); }
  try { await fs.rename(temporary, file); } catch (failure) { await fs.unlink(temporary).catch(() => {}); throw failure; }
}
function songScopedClient(client, start) {
  const scopedLedger = () => client.getRunLedger().slice(start);
  const methods = ["searchRecordings", "lookupRecording", "getAcousticBrainzEvidence", "requestGeminiTags"];
  return Object.fromEntries([...methods.map(name => [name, client[name].bind(client)]),
    ["getRunLedger", scopedLedger], ["getAttemptCounts", () => {
      const ledger = scopedLedger();
      return { total: ledger.filter(item => item.provider !== "Gemini" && item.attempt > 0).length,
        gemini: ledger.filter(item => item.provider === "Gemini" && item.attempt > 0).length };
    }]]);
}
async function main(argv = process.argv.slice(2), dependencies = {}) {
  const args = parseArgs(argv);
  const factualAttemptsPerSong = args.factualAttemptsPerSong;
  const manifestPath = await requirePrivateFile(path.resolve(args.manifest));
  const outputDirectory = await ensurePrivateDirectory(args.outputDir, { create: true });
  const cacheDirectory = await ensurePrivateDirectory(args.cacheDir, { create: true });
  let manifest;
  try { manifest = JSON.parse(await fs.readFile(manifestPath, "utf8")); } catch { throw error("COHORT_INVALID"); }
  if (!manifest || manifest.cohort_manifest_version !== 1 || typeof manifest.cohort_id !== "string" ||
      !Array.isArray(manifest.songs) || !manifest.songs.length || manifest.songs.some(item =>
        !item || !Number.isSafeInteger(item.song_id) || item.song_id < 1 || typeof item.title !== "string" ||
        typeof item.artist !== "string") || new Set(manifest.songs.map(item => item.song_id)).size !== manifest.songs.length) {
    throw error("COHORT_INVALID");
  }
  const checkpointPath = path.join(outputDirectory, "checkpoint-v1.json");
  const outputEntries = await fs.readdir(outputDirectory);
  if (outputEntries.length && !outputEntries.includes("checkpoint-v1.json")) throw error("OUTPUT_DIRECTORY_NOT_OWNED");
  const config = dependencies.config || await loadConfiguration();
  const databaseUrl = config.databaseUrl;
  const pool = dependencies.pool || new Pool({ connectionString: databaseUrl, connectionTimeoutMillis: 5_000,
    options: "-c default_transaction_read_only=on" });
  const cache = dependencies.cache || await createSongEnrichmentCache({ directory: cacheDirectory });
  let client;
  let activeReserve = null;
  let activeSongId = null;
  const cacheHitsBySong = new Map();
  const trackedCache = {
    get: async request => {
      const entry = await cache.get(request);
      if (entry && activeSongId) cacheHitsBySong.set(activeSongId, (cacheHitsBySong.get(activeSongId) || 0) + 1);
      return entry;
    },
    set: cache.set.bind(cache),
  };
  const transport = dependencies.transport || createSongEnrichmentTransport();
  try {
    const baseline = await captureReadOnlyBaseline(pool, manifest.songs.map(item => item.song_id));
    try { await cache.verifyIntegrity(); } catch { throw error("CACHE_INVALID"); }
    client = dependencies.providerClient || createSongEnrichmentProviderClient({ transport,
      beforeAttempt: async intent => {
        if (!activeSongId) return activeReserve ? activeReserve(intent) : true;
        const expectedSong = baseline.songs.find(item => item.id === activeSongId);
        if (!expectedSong) throw error("COHORT_DRIFT");
        const current = await captureReadOnlyBaseline(pool, [activeSongId]);
        const expected = { songs: [expectedSong], definitions: baseline.definitions };
        if (JSON.stringify(current) !== JSON.stringify(expected)) throw error("COHORT_DRIFT");
        return activeReserve ? activeReserve(intent) : true;
      },
      cache: trackedCache, config: { maxAttempts: providerAttemptBudget(manifest.songs.length, factualAttemptsPerSong), maxGeminiAttempts: manifest.songs.length,
      timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES, geminiMaxInputBytes: 16_384,
        geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000, retryDelayMs: 1_000,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: config.musicBrainzUserAgent } });
    const acquireFactual = async ({ song, reserveAttempt }) => {
      activeReserve = reserveAttempt;
      activeSongId = song.id;
      try {
        const start = client.getRunLedger().length;
        const factualClient = songScopedClient(client, start);
        const factualCacheStart = cacheHitsBySong.get(song.id) || 0;
        const expectedBaseline = { songs: [baseline.songs.find(item => item.id === song.id)], definitions: baseline.definitions };
        const factualRaw = await runPreview({ pool, providerClient: factualClient, songIds: [song.id], maxAttempts: factualAttemptsPerSong,
          expectedBaseline });
        const factualCacheHits = (cacheHitsBySong.get(song.id) || 0) - factualCacheStart;
        const factualReport = { ...factualRaw, limits: { ...factualRaw.limits, cache_hits: factualCacheHits } };
        return { factualReport };
      } finally { activeReserve = null; activeSongId = null; }
    };
    const acquireGemini = async ({ song, factualReport, reserveAttempt }) => {
      activeReserve = reserveAttempt;
      activeSongId = song.id;
      try {
        const expectedBaseline = { songs: [baseline.songs.find(item => item.id === song.id)], definitions: baseline.definitions };
        const prepared = await preparePreview({ pool, songId: song.id, factualInput: factualReport, expectedBaseline });
        if (!prepared.missing.length && !prepared.allowedNames.genres.length && !prepared.allowedNames.vibes.length) return { skipped: true };
        const geminiCacheStart = cacheHitsBySong.get(song.id) || 0;
        const gemini = await executePreparedPreview({ prepared, providerClient: client, apiKey: config.apiKey });
        const geminiCacheHits = (cacheHitsBySong.get(song.id) || 0) - geminiCacheStart;
        const geminiReport = { ...gemini.report, limits: { ...gemini.report.limits,
          cache: "private_response_cache", cache_hits: geminiCacheHits } };
        return { geminiReport };
      } finally { activeReserve = null; activeSongId = null; }
    };
    const processBatch = async ({ rows, evidenceRefs, batchIndex }) => {
      const runId = crypto.randomUUID();
      const base = path.join(outputDirectory, `batch-${String(batchIndex + 1).padStart(3, "0")}-${runId}`);
      await fs.mkdir(base, { recursive: false, mode: 0o700 });
      const evidence = evidenceRefs.map(item => `${item.id}=${item.path}`);
      const expectedBaseline = { songs: [...rows].sort((left, right) => left.id - right.id), definitions: baseline.definitions };
      const prepared = await operator.prepare({ ids: rows.map(row => row.id), evidence, outputDir: path.join(base, "prepare") },
        { databaseUrl, PoolClass: dependencies.PoolClass || Pool, expectedBaseline });
      const decisions = await operator.decide({ prepared: prepared.preparedPath, outputDir: path.join(base, "decide") },
        { databaseUrl, PoolClass: dependencies.PoolClass || Pool });
      const preview = await operator.preview({ prepared: prepared.preparedPath, decisions: decisions.decisionsPath,
        outputDir: path.join(base, "preview") }, { databaseUrl, PoolClass: dependencies.PoolClass || Pool });
      return { preparedPath: prepared.preparedPath, decisionsPath: decisions.decisionsPath,
        decisionReportPath: decisions.reportPath, manifestPath: preview.manifestPath, manifestSha256: preview.manifestSha256 };
    };
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: outputDirectory, providerClient: client,
      factualAttemptsPerSong,
      acquireFactual, acquireGemini, processBatch, getCacheHash: async () => {
        try { return (await cache.verifyIntegrity()).sha256; } catch { throw error("CACHE_INVALID"); }
      },
      clock: dependencies.clock || Date.now });
    const summaryPath = path.join(outputDirectory, "summary-v1.json");
    await writeAtomic(summaryPath, { cohort_id: result.cohortId, songs: result.songs,
      batches: result.batches.map(batch => ({ batch_index: batch.batch_index, song_ids: batch.song_ids,
        completed_stage: batch.completed_stage, manifest_sha256: batch.manifest_sha256 })),
      factual_attempts: result.factualAttempts, gemini_attempts: result.geminiAttempts,
      exceptions: result.exceptions });
    process.stdout.write(`${JSON.stringify({ checkpointPath: result.checkpointPath, summaryPath,
      batchesCompleted: result.batches.length, factualAttempts: result.factualAttempts, geminiAttempts: result.geminiAttempts })}\n`);
    return result;
  } catch (failure) {
    if (["CACHE_INVALID"].includes(failure.code)) throw failure;
    throw failure;
  } finally {
    if (!dependencies.pool) await pool.end();
  }
}
if (require.main === module) main().catch(failure => {
  const allowedCodes = new Set(["USAGE", "LIVE_READ_ONLY_OPT_IN_REQUIRED", "ARTIFACT_NOT_PRIVATE", "ARTIFACT_INVALID",
    "OUTPUT_DIRECTORY_NOT_OWNED", "COHORT_INVALID", "FACTUAL_ATTEMPT_CAP_INVALID", "COHORT_DRIFT", "CACHE_INVALID", "CHECKPOINT_INVALID",
    "CHECKPOINT_TAMPERED", "CHECKPOINT_UNSAFE_RESUME", "CHECKPOINT_WRITE_FAILED", "AUTHENTICATION_FAILURE",
    "PROVIDER_OUTAGE", "INVALID_EVIDENCE", "BUDGET_ENFORCEMENT", "ACQUISITION_FAILED", "PREVIEW_FAILED"]);
  const allowedStages = new Set(["setup_or_resume", "factual_acquisition", "factual_validation", "factual_evidence_write",
    "gemini_acquisition", "gemini_validation", "gemini_evidence_write", "preview_transition", "checkpoint_transition"]);
  const code = allowedCodes.has(failure?.code) ? failure.code : "ACQUISITION_FAILED";
  const stage = allowedStages.has(failure?.stage) ? failure.stage : "setup_or_resume";
  process.stderr.write(`Cohort evidence acquisition stopped at ${stage}: ${code}\n`);
  process.exitCode = 1;
});
module.exports = { main, parseArgs, providerAttemptBudget, songScopedClient };
