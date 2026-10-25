const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { normalizeReports } = require("./operatorEnrichmentWorkflow.js");

const CHECKPOINT_VERSION = 1;
const POLICY_VERSION = "stage-b-cohort-acquisition-v1";
const MAX_BATCH_SIZE = 10;
const FACTUAL_ATTEMPTS_PER_SONG = 12;
const GEMINI_ATTEMPTS_PER_SONG = 1;
const STATE_MACHINE_VERSION = 3;
const MAX_EVIDENCE_BYTES = 2_000_000;
const SAFE_STOP_CODES = new Set(["AUTHENTICATION_FAILURE", "PROVIDER_OUTAGE", "INVALID_EVIDENCE",
  "CACHE_INVALID", "CHECKPOINT_INVALID", "CHECKPOINT_TAMPERED", "CHECKPOINT_UNSAFE_RESUME", "CHECKPOINT_WRITE_FAILED",
  "COHORT_DRIFT", "BUDGET_ENFORCEMENT", "ACQUISITION_FAILED", "PREVIEW_FAILED"]);
const SAFE_STAGES = new Set(["factual_acquisition", "factual_validation", "factual_evidence_write", "gemini_acquisition",
  "gemini_validation", "gemini_evidence_write", "preview_transition", "checkpoint_transition"]);
const SAFE_PREVIEW_DIAGNOSTICS = new Set(["EVIDENCE_NOT_APPROVED", "EVIDENCE_IDENTITY_REQUIRED", "EVIDENCE_IDENTITY_MISMATCH",
  "PREVIEW_IDENTITY_MISMATCH", "DECISIONS_INVALID", "DECISIONS_UNEVIDENCED", "DECISIONS_STALE_OR_TAMPERED",
  "BASELINE_CHANGED_REPREPARE", "PREPARED_EVIDENCE_MISMATCH", "EVIDENCE_CHANGED", "INVALID_MANIFEST"]);
const SAFE_EXCEPTION = /^(?:interrupted_request_uncertain|recording_match_unresolved|missing_or_uncertain_evidence|missing_values_unresolved|gemini_evidence_unusable|factual_attempt_limit_reached|gemini_attempt_limit_reached|scalar_(?:release_year|original_key|bpm)_(?:recording_identity_not_accepted|recording_identity_unresolved|conflicting_evidence|no_candidate|invalid_candidate)|(?:genre|vibe)_(?:recording_identity_not_accepted|recording_identity_unresolved|invalid_tag_evidence|unknown_definition|ambiguous_definition|no_candidate))$/;

function fail(code) { const error = new Error(code); error.code = code; return error; }
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}
function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function hashJson(value) { return sha256(Buffer.from(canonical(value))); }
function atomicJson(file, value) {
  return fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 }).then(async () => {
    const payload = Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
    const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
    const handle = await fs.open(temporary, "wx", 0o600);
    try { await handle.writeFile(payload); await handle.sync(); } finally { await handle.close(); }
    try {
      for (let attempt = 0; ; attempt += 1) {
        try { await fs.rename(temporary, file); break; }
        catch (error) {
          if (attempt >= 4 || !["EPERM", "EACCES", "EBUSY"].includes(error.code)) throw error;
          await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 10));
        }
      }
    } catch (error) { await fs.unlink(temporary).catch(() => {}); throw error; }
  });
}
function validateManifest(manifest, baseline) {
  if (!manifest || manifest.cohort_manifest_version !== 1 || typeof manifest.cohort_id !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(manifest.cohort_id) || !Array.isArray(manifest.songs) ||
      !manifest.songs.length || !Array.isArray(baseline?.songs) || !baseline?.definitions ||
      !Array.isArray(baseline.definitions.genres) || !Array.isArray(baseline.definitions.vibes)) throw fail("COHORT_INVALID");
  const ids = manifest.songs.map(item => item?.song_id);
  if (ids.some(id => !Number.isSafeInteger(id) || id < 1) || new Set(ids).size !== ids.length) throw fail("COHORT_INVALID");
  if (manifest.songs.length !== baseline.songs.length) throw fail("COHORT_DRIFT");
  const rows = new Map(baseline.songs.map(row => [row.id, row]));
  for (const item of manifest.songs) {
    const row = rows.get(item.song_id);
    if (!row || item.title !== row.title || item.artist !== row.artist) throw fail("COHORT_DRIFT");
  }
  if (ids.some(id => !rows.has(id))) throw fail("COHORT_DRIFT");
  return manifest.songs.map(item => rows.get(item.song_id));
}
function checkpointEnvelope(payload) { return { checkpoint: payload, sha256: hashJson(payload) }; }
async function readCheckpoint(file) {
  let value;
  try {
    const stat = await fs.lstat(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 20_000_000) throw fail("CHECKPOINT_INVALID");
    value = JSON.parse(await fs.readFile(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    if (error.code === "CHECKPOINT_INVALID") throw error;
    throw fail("CHECKPOINT_INVALID");
  }
  if (!value || !value.checkpoint ||
      typeof value.sha256 !== "string" || value.sha256 !== hashJson(value.checkpoint)) throw fail("CHECKPOINT_TAMPERED");
  if (value.checkpoint.checkpoint_version !== CHECKPOINT_VERSION || value.checkpoint.policy_version !== POLICY_VERSION ||
      !value.checkpoint.binding || !Array.isArray(value.checkpoint.songs) || !Array.isArray(value.checkpoint.batches) ||
      !Array.isArray(value.checkpoint.exceptions) || !Number.isSafeInteger(value.checkpoint.factual_attempts) ||
      !Number.isSafeInteger(value.checkpoint.gemini_attempts) ||
      (value.checkpoint.stop_reason !== undefined && !SAFE_STOP_CODES.has(value.checkpoint.stop_reason))) throw fail("CHECKPOINT_INVALID");
  if (value.checkpoint.state_machine_version !== STATE_MACHINE_VERSION) throw fail("CHECKPOINT_UNSAFE_RESUME");
  if (!Number.isSafeInteger(value.checkpoint.factual_attempts_per_song) || value.checkpoint.factual_attempts_per_song < 1 ||
      value.checkpoint.factual_attempts_per_song > FACTUAL_ATTEMPTS_PER_SONG ||
      value.checkpoint.binding.factual_attempts_per_song !== value.checkpoint.factual_attempts_per_song) throw fail("CHECKPOINT_INVALID");
  if ((value.checkpoint.active_stage !== undefined && !SAFE_STAGES.has(value.checkpoint.active_stage)) ||
      (value.checkpoint.active_song_id !== undefined && value.checkpoint.active_song_id !== null &&
        !Number.isSafeInteger(value.checkpoint.active_song_id))) throw fail("CHECKPOINT_INVALID");
  if (value.checkpoint.fatal_stop && (!SAFE_STOP_CODES.has(value.checkpoint.fatal_stop.reason) ||
      !SAFE_STAGES.has(value.checkpoint.fatal_stop.stage) ||
      (value.checkpoint.fatal_stop.song_id !== null && !Number.isSafeInteger(value.checkpoint.fatal_stop.song_id)) ||
      value.checkpoint.fatal_stop.diagnostic_code !== undefined &&
        (value.checkpoint.fatal_stop.stage !== "preview_transition" ||
         !SAFE_PREVIEW_DIAGNOSTICS.has(value.checkpoint.fatal_stop.diagnostic_code)))) throw fail("CHECKPOINT_INVALID");
  return value.checkpoint;
}
async function validateCheckpointState(state, songs, directory, factualAttemptsPerSong) {
  const byId = new Map(state.songs.map(item => [item?.song_id, item]));
  if (byId.size !== state.songs.length || songs.length !== byId.size || songs.some(song => !byId.has(song.id))) throw fail("CHECKPOINT_INVALID");
  let factual = 0, gemini = 0;
  for (const song of songs) {
    const item = byId.get(song.id);
    if (!item || item.base_version !== song.updated_at || !Number.isSafeInteger(item.factual_attempts) ||
        item.factual_attempts < 0 || item.factual_attempts > factualAttemptsPerSong ||
        !Number.isSafeInteger(item.gemini_attempts) || item.gemini_attempts < 0 || item.gemini_attempts > GEMINI_ATTEMPTS_PER_SONG ||
        !["none", "factual", "evidence", "preview"].includes(item.completed_stage) || !Array.isArray(item.exception_codes) ||
        item.exception_codes.some(code => typeof code !== "string" || !SAFE_EXCEPTION.test(code)) ||
        !item.evidence || typeof item.evidence !== "object" || !item.evidence_hashes || typeof item.evidence_hashes !== "object" ||
        !Array.isArray(item.cache_response_hashes)) throw fail("CHECKPOINT_INVALID");
    factual += item.factual_attempts; gemini += item.gemini_attempts;
    const digestList = [];
    for (const [stage, file] of Object.entries(item.evidence)) {
      if (!new Set(["factual", "gemini"]).has(stage) || typeof file !== "string" ||
          !/^[a-f0-9]{64}$/.test(item.evidence_hashes[stage] || "")) throw fail("CHECKPOINT_INVALID");
      const absolute = path.resolve(file), evidenceRoot = path.resolve(directory, "evidence");
      if (!absolute.startsWith(`${evidenceRoot}${path.sep}`)) throw fail("CHECKPOINT_INVALID");
      let bytes, report;
      try {
        const stat = await fs.lstat(absolute), realEvidenceRoot = await fs.realpath(evidenceRoot), realFile = await fs.realpath(absolute);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_EVIDENCE_BYTES || !realFile.startsWith(`${realEvidenceRoot}${path.sep}`)) {
          throw fail("CHECKPOINT_TAMPERED");
        }
        bytes = await fs.readFile(absolute); report = JSON.parse(bytes.toString("utf8"));
      }
      catch { throw fail("CHECKPOINT_TAMPERED"); }
      if (sha256(bytes) !== item.evidence_hashes[stage]) throw fail("CHECKPOINT_TAMPERED");
      assertSanitizedEvidence(report, song.id, stage);
      digestList.push(...collectDigests(report));
    }
    if (item.completed_stage !== "none" && !item.evidence.factual) throw fail("CHECKPOINT_INVALID");
    if (canonical([...new Set(digestList)].sort()) !== canonical(item.cache_response_hashes)) throw fail("CHECKPOINT_TAMPERED");
  }
  if (factual !== state.factual_attempts || gemini !== state.gemini_attempts || state.factual_attempts > songs.length * factualAttemptsPerSong ||
      state.gemini_attempts > songs.length * GEMINI_ATTEMPTS_PER_SONG) throw fail("CHECKPOINT_INVALID");
  const expectedExceptions = state.songs.flatMap(item => item.exception_codes.map(code => ({ song_id: item.song_id, code })));
  if (canonical(expectedExceptions) !== canonical(state.exceptions)) throw fail("CHECKPOINT_INVALID");
  const sizes = new Map();
  for (const batch of state.batches) {
    if (!batch || !Number.isSafeInteger(batch.batch_index) || !Array.isArray(batch.song_ids) || batch.song_ids.length > MAX_BATCH_SIZE ||
        batch.completed_stage !== "preview" || typeof batch.manifest_path !== "string" || !/^[a-f0-9]{64}$/i.test(batch.manifest_sha256 || "")) {
      throw fail("CHECKPOINT_INVALID");
    }
    const manifestPath = path.resolve(batch.manifest_path), cohortRoot = path.resolve(directory);
    if (!manifestPath.startsWith(`${cohortRoot}${path.sep}`)) throw fail("CHECKPOINT_INVALID");
    try {
      const stat = await fs.lstat(manifestPath), realRoot = await fs.realpath(cohortRoot), realManifest = await fs.realpath(manifestPath);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 20_000_000 || !realManifest.startsWith(`${realRoot}${path.sep}`)) {
        throw fail("CHECKPOINT_TAMPERED");
      }
      const savedManifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
      if (hashJson(savedManifest) !== batch.manifest_sha256) throw fail("CHECKPOINT_TAMPERED");
    } catch (error) {
      if (error.code === "CHECKPOINT_TAMPERED") throw error;
      throw fail("CHECKPOINT_TAMPERED");
    }
    const count = batch.song_ids.reduce((sum, id) => sum + (byId.get(id)?.factual_attempts || 0), 0);
    sizes.set(batch.batch_index, count);
    if (count > batch.song_ids.length * factualAttemptsPerSong || batch.song_ids.some(id => !byId.has(id))) throw fail("CHECKPOINT_INVALID");
  }
  if (state.in_flight && (!byId.has(state.in_flight.song_id) || typeof state.in_flight.operation !== "string" ||
      typeof state.in_flight.provider !== "string" || state.in_flight.status !== "uncertain")) throw fail("CHECKPOINT_INVALID");
}
function safeProviderStatus(result) {
  const statuses = [];
  const add = value => { if (typeof value === "string") statuses.push(value); };
  for (const song of result?.songs || []) {
    add(song.recording?.search_status);
    add(song.recording?.lookup_status);
    for (const candidate of song.recording?.richness_comparison?.candidates || []) add(candidate?.lookup_status);
    add(song.acousticbrainz?.status);
  }
  add(result?.gemini_suggestions?.status);
  if (statuses.some(status => ["authentication_error", "unauthorized", "forbidden"].includes(status))) throw fail("AUTHENTICATION_FAILURE");
  if (statuses.some(status => ["transient_error", "network_disabled", "cancelled", "timeout"].includes(status))) throw fail("PROVIDER_OUTAGE");
  if (statuses.some(status => ["invalid_response", "invalid_request"].includes(status))) throw fail("INVALID_EVIDENCE");
}
function assertSanitizedEvidence(report, songId, kind) {
  if (!report || typeof report !== "object" || Array.isArray(report)) throw fail("INVALID_EVIDENCE");
  if (kind === "factual" && (report.report_type !== "existing_song_enrichment_read_only_preview" ||
      !Array.isArray(report.selected_song_ids) || report.selected_song_ids.length !== 1 || report.selected_song_ids[0] !== songId)) throw fail("INVALID_EVIDENCE");
  if (kind === "gemini" && (report.format_version !== 1 || report.mode !== "explicit_gemini_missing_metadata_fallback" ||
      report.selected_song_id !== songId)) throw fail("INVALID_EVIDENCE");
  const forbidden = /(?:chart.?body|lyrics?|raw.?response|credential|api.?key|authorization)/i;
  const inspect = value => {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (forbidden.test(key)) throw fail("INVALID_EVIDENCE");
      inspect(child);
    }
  };
  inspect(report);
}
function validateNormalizedEvidence(report, song) {
  try {
    normalizeReports(song, [{ document: report, bytes: Buffer.from(JSON.stringify(report)), path: "in-memory" }]);
  } catch { throw fail("INVALID_EVIDENCE"); }
}
function usableGeminiEvidence(report, songId) {
  const suggestion = report?.gemini_suggestions;
  return report?.format_version === 1 && report.mode === "explicit_gemini_missing_metadata_fallback" &&
    report.selected_song_id === songId && suggestion?.status === "ok" && suggestion.model_id === "gemini-3.1-flash-lite" &&
    ["missing-metadata-fallback-v1", "missing-metadata-fallback-v2"].includes(suggestion.prompt_version) &&
    ["missing-metadata-schema-v1", "missing-metadata-schema-v2"].includes(suggestion.schema_version) &&
    /^[a-f0-9]{64}$/i.test(suggestion.response_digest || "") && suggestion.scalars &&
    ["release_year", "original_key", "bpm"].every(field => Array.isArray(suggestion.scalars[field])) &&
    Array.isArray(suggestion.genres) && Array.isArray(suggestion.vibes);
}
async function saveEvidenceArtifact({ filesystem, directory, songId, stage, report, item }) {
  const bytes = Buffer.from(`${JSON.stringify(report, null, 2)}\n`);
  if (bytes.byteLength > MAX_EVIDENCE_BYTES) throw fail("INVALID_EVIDENCE");
  const evidencePath = path.join(directory, "evidence", `${songId}-${stage}-v1.json`);
  await filesystem.mkdir(path.dirname(evidencePath), { recursive: true, mode: 0o700 });
  await atomicJson(evidencePath, report);
  item.evidence[stage] = evidencePath;
  item.evidence_hashes[stage] = sha256(bytes);
  item.cache_response_hashes.push(...collectDigests(report));
  item.evidence_hashes = Object.fromEntries(Object.entries(item.evidence_hashes).sort(([a], [b]) => a.localeCompare(b)));
  item.cache_response_hashes = [...new Set(item.cache_response_hashes)].sort();
}

async function runSongEnrichmentCohort({ manifest, baseline, directory, providerClient, acquireFactual, acquireGemini = async () => ({}), processBatch,
  factualAttemptsPerSong = FACTUAL_ATTEMPTS_PER_SONG,
  getCacheHash = async () => null, filesystem = fs, clock = Date.now, checkpointWriter = atomicJson }) {
  if (!providerClient || typeof acquireFactual !== "function" || typeof acquireGemini !== "function" || typeof processBatch !== "function") throw fail("RUNNER_DEPENDENCY_INVALID");
  if (!Number.isSafeInteger(factualAttemptsPerSong) || factualAttemptsPerSong < 1 || factualAttemptsPerSong > FACTUAL_ATTEMPTS_PER_SONG) {
    throw fail("BUDGET_ENFORCEMENT");
  }
  const songs = validateManifest(manifest, baseline);
  const checkpointPath = path.join(directory, "checkpoint-v1.json");
  const checkpointBaseline = { songs: baseline.songs, definitions: baseline.definitions };
  const binding = { cohort_id: manifest.cohort_id, manifest_sha256: hashJson(manifest),
    baseline_sha256: hashJson(checkpointBaseline), cache_sha256: await getCacheHash(), policy_version: POLICY_VERSION,
    factual_attempts_per_song: factualAttemptsPerSong };
  const prior = await readCheckpoint(checkpointPath);
  if (prior && prior.factual_attempts_per_song !== factualAttemptsPerSong) throw fail("CHECKPOINT_UNSAFE_RESUME");
  if (prior && canonical({ ...prior.binding, cache_sha256: null }) !== canonical({ ...binding, cache_sha256: null })) throw fail("COHORT_DRIFT");
  if (prior && prior.binding.cache_sha256 !== binding.cache_sha256) throw fail("CACHE_INVALID");
  if (prior) await validateCheckpointState(prior, songs, directory, factualAttemptsPerSong);
  if (prior?.fatal_stop || prior?.stop_reason || prior?.in_flight || prior?.active_stage) throw fail("CHECKPOINT_UNSAFE_RESUME");
  const state = prior || { checkpoint_version: CHECKPOINT_VERSION, state_machine_version: STATE_MACHINE_VERSION,
    factual_attempts_per_song: factualAttemptsPerSong, policy_version: POLICY_VERSION, binding,
    created_at: new Date(Number(clock())).toISOString(), songs: [], batches: [], factual_attempts: 0, gemini_attempts: 0,
    exceptions: [], in_flight: null };
  const stateById = new Map(state.songs.map(item => [item.song_id, item]));
  for (const [index, row] of songs.entries()) {
    if (!stateById.has(row.id)) stateById.set(row.id, { song_id: row.id, base_version: row.updated_at,
      evidence: {}, evidence_hashes: {}, cache_response_hashes: [], factual_attempts: 0, gemini_attempts: 0,
      completed_stage: "none", exception_codes: [] });
    else if (stateById.get(row.id).base_version !== row.updated_at) throw fail("COHORT_DRIFT");
    stateById.get(row.id).manifest_order = index;
  }
  state.songs = [...stateById.values()].sort((a, b) => a.manifest_order - b.manifest_order);
  const save = () => {
    state.exceptions = state.songs.flatMap(item => item.exception_codes.map(code => ({ song_id: item.song_id, code })));
    return Promise.resolve().then(() => checkpointWriter(checkpointPath, checkpointEnvelope(state)))
      .catch(() => { throw fail("CHECKPOINT_WRITE_FAILED"); });
  };
  let activeStage = "checkpoint_transition", activeSongId = null;
  const enterStage = async (stage, songId = null) => {
    activeStage = SAFE_STAGES.has(stage) ? stage : "checkpoint_transition";
    activeSongId = songId;
    state.active_stage = activeStage;
    state.active_song_id = songId;
    await save();
  };
  const leaveStage = async () => {
    delete state.active_stage;
    delete state.active_song_id;
    activeStage = "checkpoint_transition";
    activeSongId = null;
    await save();
  };
  const persistFatal = async error => {
    const reason = SAFE_STOP_CODES.has(error?.code) ? error.code :
      (activeStage === "preview_transition" ? "PREVIEW_FAILED" : "ACQUISITION_FAILED");
    const stage = SAFE_STAGES.has(activeStage) ? activeStage : "checkpoint_transition";
    state.fatal_stop = { reason, stage, song_id: activeSongId,
      ...(stage === "preview_transition" && SAFE_PREVIEW_DIAGNOSTICS.has(error?.code) ? { diagnostic_code: error.code } : {}) };
    try { await save(); } catch { /* A durable active stage or in-flight reservation still blocks resume. */ }
    const stopped = fail(reason);
    stopped.stage = state.fatal_stop.stage;
    throw stopped;
  };
  try { await save(); } catch (error) { await persistFatal(error); }
  const batches = [];
  for (let start = 0; start < songs.length; start += MAX_BATCH_SIZE) {
    const rows = songs.slice(start, start + MAX_BATCH_SIZE);
    const batchIndex = Math.floor(start / MAX_BATCH_SIZE);
    const batchState = state.songs.slice(start, start + rows.length);
    if (state.batches[batchIndex]?.completed_stage === "preview") { batches.push(state.batches[batchIndex]); continue; }
    let factualInBatch = batchState.reduce((sum, item) => sum + item.factual_attempts, 0);
    const currentBatch = { batch_index: batchIndex, song_ids: rows.map(row => row.id), completed_stage: "acquiring" };
    for (const row of rows) {
      const item = stateById.get(row.id);
      if (item.completed_stage === "evidence") continue;
      const reserve = async ({ provider, operation, gemini = false } = {}) => {
        if (gemini) {
          if (item.gemini_attempts >= GEMINI_ATTEMPTS_PER_SONG) return false;
          if (state.gemini_attempts >= songs.length * GEMINI_ATTEMPTS_PER_SONG) return false;
          item.gemini_attempts += 1; state.gemini_attempts += 1;
        } else {
          const batchLimit = rows.length * factualAttemptsPerSong;
          if (item.factual_attempts >= factualAttemptsPerSong || factualInBatch >= batchLimit ||
              state.factual_attempts >= songs.length * factualAttemptsPerSong) return false;
          item.factual_attempts += 1; factualInBatch += 1; state.factual_attempts += 1;
        }
        state.in_flight = { song_id: row.id, provider: String(provider || "unknown"), operation: String(operation || "unknown"),
          status: "uncertain", gemini: Boolean(gemini) };
        await save();
        return true;
      };
      try {
        let factualReport;
        if (item.evidence.factual) {
          await enterStage("factual_validation", row.id);
          factualReport = JSON.parse(await filesystem.readFile(item.evidence.factual, "utf8"));
        } else {
          await enterStage("factual_acquisition", row.id);
          const factual = await acquireFactual({ song: row, providerClient, existingEvidence: item.evidence,
            reserveAttempt: reserve, signal: undefined });
          factualReport = factual?.factualReport;
          // A returned provider report acknowledges the request even if its evidence
          // later fails validation. Persist that distinction before inspecting it.
          state.in_flight = null;
          state.binding.cache_sha256 = await getCacheHash();
          await save();
          activeStage = "factual_validation";
          state.active_stage = activeStage;
          await save();
          if (!factualReport) throw fail("INVALID_EVIDENCE");
          assertSanitizedEvidence(factualReport, row.id, "factual");
          safeProviderStatus(factualReport);
          validateNormalizedEvidence(factualReport, row);
          const acquisitionCodes = factual.exceptionCodes || [];
          if (!Array.isArray(acquisitionCodes) || acquisitionCodes.some(code => !SAFE_EXCEPTION.test(code))) throw fail("INVALID_EVIDENCE");
          item.exception_codes = [...new Set([...item.exception_codes, ...acquisitionCodes,
            ...(factualReport.attempts?.total >= factualAttemptsPerSong ? ["factual_attempt_limit_reached"] : [])])];
          activeStage = "factual_evidence_write";
          state.active_stage = activeStage;
          await save();
          await saveEvidenceArtifact({ filesystem, directory, songId: row.id, stage: "factual", report: factualReport, item });
          item.completed_stage = "factual";
          state.in_flight = null;
          state.binding.cache_sha256 = await getCacheHash();
          await save();
        }
        if (item.completed_stage === "factual") {
          const matchAccepted = factualReport.songs?.[0]?.recording?.match?.matched === true;
          if (!matchAccepted) {
            item.exception_codes = [...new Set([...item.exception_codes, "recording_match_unresolved"])];
            item.completed_stage = "evidence";
            state.binding.cache_sha256 = await getCacheHash();
            await save();
            await leaveStage();
            continue;
          }
          await enterStage("gemini_acquisition", row.id);
          const geminiResult = await acquireGemini({ song: row, factualReport, providerClient, reserveAttempt: reserve, signal: undefined });
          const geminiReport = geminiResult?.geminiReport;
          state.in_flight = null;
          state.binding.cache_sha256 = await getCacheHash();
          await save();
          activeStage = "gemini_validation";
          state.active_stage = activeStage;
          await save();
          if (geminiReport) {
            assertSanitizedEvidence(geminiReport, row.id, "gemini");
            safeProviderStatus(geminiReport);
            if (geminiReport.gemini_suggestions?.status === "ok") validateNormalizedEvidence(geminiReport, row);
          }
          const geminiCodes = geminiResult?.exceptionCodes || [];
          if (!Array.isArray(geminiCodes) || geminiCodes.some(code => !SAFE_EXCEPTION.test(code))) throw fail("INVALID_EVIDENCE");
          if (geminiReport && usableGeminiEvidence(geminiReport, row.id)) {
            activeStage = "gemini_evidence_write";
            state.active_stage = activeStage;
            await save();
            await saveEvidenceArtifact({ filesystem, directory, songId: row.id, stage: "gemini", report: geminiReport, item });
          } else if (geminiReport?.gemini_suggestions?.status === "ok") {
            throw fail("INVALID_EVIDENCE");
          } else if (geminiReport?.gemini_suggestions?.status === "budget_exhausted") {
            item.exception_codes.push("gemini_attempt_limit_reached");
          } else if (geminiResult?.skipped === true) {
            // No missing scalar or unassigned managed tags remained; this is a completed no-op.
          } else if (geminiReport) {
            item.exception_codes.push("gemini_evidence_unusable");
          } else if (!geminiCodes.includes("gemini_attempt_limit_reached")) {
            item.exception_codes.push("gemini_evidence_unusable");
          }
          item.exception_codes = [...new Set([...item.exception_codes, ...geminiCodes])];
          item.completed_stage = "evidence";
          state.in_flight = null;
          state.binding.cache_sha256 = await getCacheHash();
          await save();
        }
        await leaveStage();
      } catch (error) {
        await persistFatal(error);
      }
    }
    try {
    await enterStage("preview_transition");
    const evidenceRefs = [];
    for (const row of rows) {
      const item = stateById.get(row.id);
      for (const file of Object.values(item.evidence)) evidenceRefs.push({ id: row.id, path: file });
    }
    const result = await processBatch({ rows, evidenceRefs, batchIndex, baseline, directory });
    if (result.decisionReportPath) {
      let decisionReport;
      const reportPath = path.resolve(result.decisionReportPath);
      if (!reportPath.startsWith(`${path.resolve(directory)}${path.sep}`)) throw fail("INVALID_EVIDENCE");
      try {
        const stat = await filesystem.lstat(reportPath);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_EVIDENCE_BYTES) throw fail("INVALID_EVIDENCE");
        decisionReport = JSON.parse(await filesystem.readFile(reportPath, "utf8"));
      }
      catch { throw fail("INVALID_EVIDENCE"); }
      if (decisionReport?.report_version !== 1 || !Array.isArray(decisionReport.songs) ||
          decisionReport.songs.length !== rows.length) throw fail("INVALID_EVIDENCE");
      const reportedIds = decisionReport.songs.map(item => item?.song_id).sort((a, b) => a - b);
      const expectedIds = rows.map(item => item.id).sort((a, b) => a - b);
      if (canonical(reportedIds) !== canonical(expectedIds)) throw fail("INVALID_EVIDENCE");
      for (const report of decisionReport.songs || []) {
        const item = stateById.get(report.song_id);
        if (!item) throw fail("INVALID_EVIDENCE");
        const reasons = [];
        const targetBaseline = rows.find(row => row.id === report.song_id);
        if (!targetBaseline) throw fail("INVALID_EVIDENCE");
        if (report.recording_match?.status === "abstained") reasons.push("recording_match_unresolved");
        for (const field of ["release_year", "original_key", "bpm"]) {
          const scalar = report.scalars?.[field];
          if (scalar?.status === "abstained") reasons.push(`scalar_${field}_${scalar.reason}`);
        }
        for (const [category, key] of [["genre", "genres"], ["vibe", "vibes"]]) {
          if (Array.isArray(report[key])) for (const tag of report[key]) {
            if (tag.status === "abstained") reasons.push(`${category}_${tag.reason}`);
          }
          if (Array.isArray(report[key]) && report[key].length === 0 && !(targetBaseline[key] || []).length) {
            reasons.push(`${category}_no_candidate`);
          }
        }
        if (reasons.some(code => !SAFE_EXCEPTION.test(code))) throw fail("INVALID_EVIDENCE");
        item.exception_codes = [...new Set([...item.exception_codes, ...reasons])];
      }
    }
    currentBatch.completed_stage = "preview";
    currentBatch.prepared_path = result.preparedPath;
    currentBatch.decisions_path = result.decisionsPath;
    currentBatch.manifest_path = result.manifestPath;
    currentBatch.manifest_sha256 = result.manifestSha256;
    currentBatch.completed_at = new Date(Number(clock())).toISOString();
    state.batches[batchIndex] = currentBatch;
    for (const row of rows) stateById.get(row.id).completed_stage = "preview";
    await leaveStage();
    batches.push(currentBatch);
    } catch (error) { await persistFatal(error); }
  }
  return { checkpointPath, cohortId: manifest.cohort_id, songs: state.songs.length, batches,
    factualAttempts: state.factual_attempts, geminiAttempts: state.gemini_attempts,
    exceptions: state.songs.flatMap(item => item.exception_codes.map(code => ({ song_id: item.song_id, code }))) };
}

function collectDigests(value, out = []) {
  if (!value || typeof value !== "object") return out;
  for (const [key, child] of Object.entries(value)) {
    if ((key === "response_digest" || key.endsWith("_response_digest")) && typeof child === "string" && /^[a-f0-9]{64}$/i.test(child)) out.push(child);
    else collectDigests(child, out);
  }
  return out;
}

module.exports = { CHECKPOINT_VERSION, FACTUAL_ATTEMPTS_PER_SONG, GEMINI_ATTEMPTS_PER_SONG, MAX_BATCH_SIZE, POLICY_VERSION,
  STATE_MACHINE_VERSION,
  runSongEnrichmentCohort, validateManifest, hashJson, collectDigests, assertSanitizedEvidence };
