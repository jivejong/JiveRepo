#!/usr/bin/env node
const fs = require("node:fs/promises");
const path = require("node:path");
const http = require("node:http");
const { execFileSync } = require("node:child_process");
const { buildEnrichmentPreview } = require("../server/existingSongEnrichment.js");
const { createSongEnrichmentProviderClient, DEFAULTS } = require("../server/songEnrichmentProviders.js");
const { MAX_RESPONSE_BYTES, createSongEnrichmentTransport } = require("../server/songEnrichmentTransport.js");
const { captureReadOnlyBaseline, createOutputDirectory } = require("./preview-existing-song-enrichment.cjs");
const { definitionKey, normalizeDefinitionName } = require("../server/tagDefinitionNames.js");

const APP_ROOT = path.resolve(__dirname, "..");
const PROJECT_ROOT = path.resolve(APP_ROOT, "..");
const DIGEST = /^[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const GEMINI_FIELDS = Object.freeze({ model_id: "gemini-3.1-flash-lite",
  prompt_version: "missing-metadata-fallback-v2", schema_version: "missing-metadata-schema-v2" });
const GEMINI_VALIDATION_REASONS = new Set(["interaction_envelope_invalid", "model_text_missing", "invalid_json",
  "required_field_missing", "unexpected_field", "invalid_field_type_value", "unrequested_scalar",
  "disallowed_genre_output", "unknown_label", "ambiguous_label", "label_outside_allowed_subset", "invalid_provenance"]);
const VALIDATION_FIELDS = new Set(["response", "release_year", "bpm", "original_key", "genres", "vibes"]);
const VALIDATION_CATEGORIES = new Set(["Genre", "Vibe"]);
const SCALARS = ["release_year", "original_key", "bpm"];
const KEY_ROOTS = new Set(["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"]);

function fail(message, code = "INVALID_ARGUMENTS") {
  const error = new Error(message);
  error.code = code;
  error.safeMessage = message;
  return error;
}

function isWithin(directory, root) {
  const relative = path.relative(root, directory);
  return Boolean(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function parseArguments(argv) {
  const parsed = { optIn: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--gemini-opt-in") {
      if (parsed.optIn) throw fail("--gemini-opt-in may be specified once.");
      parsed.optIn = true;
    } else if (["--song-id", "--output-dir", "--factual-report"].includes(arg)) {
      const key = ({ "--song-id": "songId", "--output-dir": "outputDirectory", "--factual-report": "factualReport" })[arg];
      if (Object.hasOwn(parsed, key) || !argv[i + 1] || argv[i + 1].startsWith("--")) throw fail(`Missing or repeated ${arg}.`);
      parsed[key] = argv[++i];
    } else throw fail("Unknown Gemini fallback preview argument.");
  }
  if (!parsed.optIn) throw fail("Gemini dispatch requires explicit --gemini-opt-in.");
  if (!/^\d+$/.test(parsed.songId || "") || Number(parsed.songId) < 1 || !Number.isSafeInteger(Number(parsed.songId))) {
    throw fail("An explicit positive --song-id is required.");
  }
  parsed.songId = Number(parsed.songId);
  if (!parsed.outputDirectory?.trim()) throw fail("An explicit ignored --output-dir is required.");
  return parsed;
}

function parsePrivateEnv(text) {
  const values = {};
  for (const line of String(text).split(/\r?\n/)) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/.exec(line);
    if (!match || match[1] !== "GEMINI_API_KEY" && match[1] !== "DATABASE_URL" &&
        !["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DB_PORT"].includes(match[1])) continue;
    let value = match[2];
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    values[match[1]] = value;
  }
  return values;
}

async function loadConfiguration({ filesystem = fs, processEnv = process.env, envPath = path.join(APP_ROOT, ".env") } = {}) {
  let fileValues = {};
  try { fileValues = parsePrivateEnv(await filesystem.readFile(envPath, "utf8")); } catch { /* process configuration may be complete */ }
  const key = processEnv.GEMINI_API_KEY || fileValues.GEMINI_API_KEY;
  if (typeof key !== "string" || !key.trim()) throw fail("GEMINI_API_KEY is required in private runtime configuration.", "GEMINI_CREDENTIAL_REQUIRED");
  try { http.validateHeaderValue("x-goog-api-key", key.trim()); }
  catch { throw fail("GEMINI_API_KEY contains invalid HTTP header characters.", "GEMINI_CREDENTIAL_INVALID"); }
  let databaseUrl = processEnv.DATABASE_URL || fileValues.DATABASE_URL;
  if (!databaseUrl && fileValues.POSTGRES_USER && fileValues.POSTGRES_PASSWORD && fileValues.POSTGRES_DB && fileValues.DB_PORT) {
    databaseUrl = `postgresql://${encodeURIComponent(fileValues.POSTGRES_USER)}:${encodeURIComponent(fileValues.POSTGRES_PASSWORD)}@127.0.0.1:${encodeURIComponent(fileValues.DB_PORT)}/${encodeURIComponent(fileValues.POSTGRES_DB)}`;
  }
  if (typeof databaseUrl !== "string" || !databaseUrl.trim()) throw fail("Private database configuration is required.", "DATABASE_CONFIG_REQUIRED");
  try {
    const parsed = new URL(databaseUrl);
    if (!["postgres:", "postgresql:"].includes(parsed.protocol) || !parsed.hostname || parsed.pathname === "/") throw new Error();
  } catch { throw fail("Private database configuration is invalid.", "DATABASE_CONFIG_INVALID"); }
  const contact = processEnv.MUSICBRAINZ_CONTACT || fileValues.MUSICBRAINZ_CONTACT || "local-only";
  const userAgent = `ChordChartManager/1.0.0 (${contact})`;
  try { http.validateHeaderValue("User-Agent", userAgent); } catch { throw fail("Private provider configuration is invalid.", "PROVIDER_CONFIG_INVALID"); }
  if (!/^\S[^()]*\/\d+(?:\.\d+){1,2}(?:[-+][\w.-]+)?\s+\([^()]+\)$/.test(userAgent)) {
    throw fail("Private provider configuration is invalid.", "PROVIDER_CONFIG_INVALID");
  }
  return { apiKey: key.trim(), databaseUrl, musicBrainzUserAgent: userAgent };
}

function validateConfiguration(config) {
  if (!config || typeof config.apiKey !== "string" || !config.apiKey.trim() ||
      typeof config.databaseUrl !== "string" || !config.databaseUrl.trim()) {
    throw fail("Private Gemini and database configuration is required.", "PRIVATE_CONFIGURATION_REQUIRED");
  }
  try { http.validateHeaderValue("x-goog-api-key", config.apiKey.trim()); }
  catch { throw fail("GEMINI_API_KEY contains invalid HTTP header characters.", "GEMINI_CREDENTIAL_INVALID"); }
  if (typeof config.musicBrainzUserAgent !== "string" ||
      !/^\S[^()]*\/\d+(?:\.\d+){1,2}(?:[-+][\w.-]+)?\s+\([^()]+\)$/.test(config.musicBrainzUserAgent)) {
    throw fail("Private provider configuration is invalid.", "PROVIDER_CONFIG_INVALID");
  }
  try {
    const parsed = new URL(config.databaseUrl);
    if (!["postgres:", "postgresql:"].includes(parsed.protocol) || !parsed.hostname || parsed.pathname === "/") throw new Error();
  } catch { throw fail("Private database configuration is invalid.", "DATABASE_CONFIG_INVALID"); }
  return { ...config, apiKey: config.apiKey.trim() };
}

function requestedMissingFields(song, factual = null) {
  return SCALARS.filter(field => (field === "original_key"
    ? song[field] === null || typeof song[field] === "string" && song[field].trim() === ""
    : song[field] === null) && !hasUsableFactualCandidate(factual, field));
}

function hasUsableFactualCandidate(factual, field) {
  const item = factual?.scalar_candidates?.[field];
  if (!item || item.conflicting_evidence !== false || !Array.isArray(item.candidates) || item.candidates.length === 0) return false;
  return new Set(item.candidates.map(candidate => factualScalarKey(field, candidate.value))).size === 1;
}

function factualScalarKey(field, value) {
  return field === "bpm" ? String(Math.floor(value + 0.5)) : JSON.stringify(value);
}

function safeGeminiResult(result, requestedFields) {
  const status = typeof result?.status === "string" && /^[a-z_]{1,40}$/.test(result.status) ? result.status : "invalid_response";
  const digest = typeof result?.response_digest === "string" && DIGEST.test(result.response_digest) ? result.response_digest : null;
  const safeScalars = {};
  for (const field of SCALARS) {
    const items = Array.isArray(result?.scalars?.[field]) ? result.scalars[field] : [];
    safeScalars[field] = requestedFields.includes(field) ? items.filter(item => item?.source === "Gemini" &&
      item.response_digest === digest && Object.entries(GEMINI_FIELDS).every(([key, value]) => item[key] === value) &&
      (field === "release_year" ? Number.isInteger(item.value) && item.value >= 1 && item.value <= 32767
      : field === "bpm" ? typeof item.value === "number" && Number.isFinite(item.value) && item.value > 0 && Math.floor(item.value + 0.5) <= 32767
          : item.value && KEY_ROOTS.has(item.value.key_key) && ["major", "minor"].includes(item.value.key_scale)))
      .map(item => ({ value: field === "original_key" ? { key_key: item.value.key_key, key_scale: item.value.key_scale } : item.value,
        source: "Gemini", response_digest: digest, ...GEMINI_FIELDS })) : [];
  }
  const safeLabels = field => Array.isArray(result?.[field]) ? result[field].filter(item => item?.source === "Gemini" &&
    item.response_digest === digest && Object.entries(GEMINI_FIELDS).every(([key, value]) => item[key] === value) &&
    typeof item.name === "string").map(item => ({ name: item.name, source: "Gemini", response_digest: digest, ...GEMINI_FIELDS })) : [];
  const usage = result?.token_usage && Object.fromEntries(["input_tokens", "output_tokens", "total_tokens"]
    .filter(key => Number.isSafeInteger(result.token_usage[key]) && result.token_usage[key] >= 0)
    .map(key => [key, result.token_usage[key]]));
  const detail = result?.failure_details && typeof result.failure_details === "object" ? result.failure_details : {};
  const safeDetails = {
    ...(typeof detail.failure_phase === "string" && ["validation", "request_start", "request", "response", "timeout", "http", "provider"].includes(detail.failure_phase)
      ? { failure_phase: detail.failure_phase } : {}),
    ...(Number.isInteger(detail.http_status) && detail.http_status >= 100 && detail.http_status <= 599 ? { http_status: detail.http_status } : {}),
    ...(detail.timeout === true ? { timeout: true } : {}),
    ...(["provider_watchdog", "transport"].includes(detail.timeout_source) ? { timeout_source: detail.timeout_source } : {}),
    ...(["ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH", "EAI_AGAIN", "ENOTFOUND", "EPIPE", "RESPONSE_TOO_LARGE", "INVALID_PROVIDER_REQUEST"].includes(detail.transport_error_code)
      ? { transport_error_code: detail.transport_error_code } : {}),
    ...(detail.https_started === true || detail.https_started === false ? { https_started: detail.https_started } : {}),
    ...(GEMINI_VALIDATION_REASONS.has(detail.validation_reason) ? { validation_reason: detail.validation_reason } : {}),
    ...(VALIDATION_FIELDS.has(detail.validation_field) ? { validation_field: detail.validation_field } : {}),
    ...(VALIDATION_CATEGORIES.has(detail.validation_category) ? { validation_category: detail.validation_category } : {}),
  };
  if (status !== "ok") for (const field of SCALARS) safeScalars[field] = [];
  return { status, response_digest: digest, model_id: result?.model_id === GEMINI_FIELDS.model_id ? result.model_id : null,
    prompt_version: result?.prompt_version === GEMINI_FIELDS.prompt_version ? result.prompt_version : null,
    schema_version: result?.schema_version === GEMINI_FIELDS.schema_version ? result.schema_version : null,
    scalars: safeScalars, genres: status === "ok" ? safeLabels("genres") : [], vibes: status === "ok" ? safeLabels("vibes") : [],
    ...(usage && Object.keys(usage).length ? { token_usage: usage } : {}),
    ...(Object.keys(safeDetails).length ? { failure_details: safeDetails } : {}) };
}

function safeAttemptLedger(rows) {
  const codes = new Set(["ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH",
    "EAI_AGAIN", "ENOTFOUND", "EPIPE", "RESPONSE_TOO_LARGE", "INVALID_PROVIDER_REQUEST"]);
  const phases = new Set(["validation", "request_start", "request", "response", "timeout", "http", "provider"]);
  return (Array.isArray(rows) ? rows : []).map(row => {
    const d = row?.failure_details || {};
    const details = {
      ...(phases.has(d.failure_phase) ? { failure_phase: d.failure_phase } : {}),
      ...(Number.isInteger(d.http_status) && d.http_status >= 100 && d.http_status <= 599 ? { http_status: d.http_status } : {}),
      ...(d.timeout === true ? { timeout: true } : {}),
      ...(["provider_watchdog", "transport"].includes(d.timeout_source) ? { timeout_source: d.timeout_source } : {}),
      ...(codes.has(d.transport_error_code) ? { transport_error_code: d.transport_error_code } : {}),
      ...(d.https_started === true || d.https_started === false ? { https_started: d.https_started } : {}),
      ...(GEMINI_VALIDATION_REASONS.has(d.validation_reason) ? { validation_reason: d.validation_reason } : {}),
      ...(VALIDATION_FIELDS.has(d.validation_field) ? { validation_field: d.validation_field } : {}),
      ...(VALIDATION_CATEGORIES.has(d.validation_category) ? { validation_category: d.validation_category } : {}),
    };
    return { provider: row.provider, operation: row.operation, attempt: row.attempt, status: row.status,
      ...(row.token_usage ? { token_usage: row.token_usage } : {}), ...(Object.keys(details).length ? { failure_details: details } : {}) };
  });
}

function safeBaselineSong(song) {
  return { id: song.id, title: song.title, artist: song.artist, updated_at: song.updated_at,
    release_year: song.release_year, original_key: song.original_key, bpm: song.bpm,
    genres: song.genres.map(({ id, name, is_primary }) => ({ id, name, is_primary })),
    vibes: song.vibes.map(({ id, name }) => ({ id, name })) };
}

function unassignedAllowedNames(catalog, assignments, category) {
  const assignedIds = new Set(assignments.map(row => row.id));
  const assignedKeys = new Set(assignments.map(row => definitionKey(normalizeDefinitionName(row.name, category))));
  const counts = new Map();
  for (const row of catalog) {
    const key = definitionKey(normalizeDefinitionName(row.name, category));
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return catalog.filter(row => {
    const key = definitionKey(normalizeDefinitionName(row.name, category));
    return !assignedIds.has(row.id) && !assignedKeys.has(key) && counts.get(key) === 1;
  }).map(row => row.name);
}

function safeFactualEvidence(document, songId) {
  if (document === null || document === undefined) return null;
  if (!document || (document.format_version !== 1 && document.report_version !== 1) || !Array.isArray(document.songs)) {
    throw fail("Saved factual evidence must be a version 1 B4 report.", "FACTUAL_REPORT_INVALID");
  }
  const song = document.songs.find(item => item?.song_id === songId);
  if (!song) throw fail("Saved factual report does not contain the selected song.", "FACTUAL_REPORT_SONG_MISSING");
  const sources = { release_year: "MusicBrainz", original_key: "AcousticBrainz", bpm: "AcousticBrainz" };
  const scalars = {};
  for (const field of SCALARS) {
    const state = song.scalar_candidates?.[field];
    const candidates = Array.isArray(state?.candidates) ? state.candidates.filter(item => item?.source === sources[field] &&
      typeof item.response_digest === "string" && DIGEST.test(item.response_digest) &&
      (field === "release_year" ? Number.isInteger(item.value) && item.value >= 1 && item.value <= 32767
        : field === "bpm" ? typeof item.value === "number" && Number.isFinite(item.value) && item.value > 0 && Math.floor(item.value + 0.5) <= 32767
          : item.value && KEY_ROOTS.has(item.value.key_key) && ["major", "minor"].includes(item.value.key_scale)))
      .map(item => ({ value: field === "original_key" ? { key_key: item.value.key_key, key_scale: item.value.key_scale } : item.value,
        source: sources[field], response_digest: item.response_digest,
        ...(typeof item.submission_id === "string" && /^[0-9a-f-]{36}:\d+$/.test(item.submission_id) ? { submission_id: item.submission_id } : {}),
        ...(field === "original_key" && Number.isFinite(item.key_strength) ? { key_strength: item.key_strength } : {}) })) : [];
    const knownStates = new Set(["populated_preserved", "partial_unapproved_candidates", "unapproved_candidates",
      "partial_no_candidate", "provider_unavailable", "no_candidate", "not_attempted", "not_dispatched"]);
    const knownStatuses = new Set(["ok", "absent", "invalid_response", "authentication_error", "transient_error",
      "timeout", "budget_exhausted", "cancelled", "network_disabled", "not_attempted", "not_dispatched"]);
    scalars[field] = { state: knownStates.has(state?.state) ? state.state : "not_attempted",
      evidence_status: knownStatuses.has(state?.evidence_status) ? state.evidence_status : "not_attempted",
      conflicting_evidence: state?.conflicting_evidence === true, candidates };
  }
  const labels = category => {
    const items = song.evidence?.[category] || [];
    return Array.isArray(items) ? items.filter(item => item && typeof item.name === "string" && item.name.length <= 200 &&
      ["MusicBrainz", "AcousticBrainz"].includes(item.source) && typeof item.response_digest === "string" && DIGEST.test(item.response_digest))
      .map(item => ({ name: item.name, source: item.source, response_digest: item.response_digest })) : [];
  };
  const labelDiagnostics = field => {
    const group = song.tag_candidates?.[field];
    const safeItems = key => Array.isArray(group?.[key]) ? group[key].filter(item => item && typeof item.label === "string" &&
      item.label.length <= 200 && ["MusicBrainz", "AcousticBrainz"].includes(item.evidence?.source) &&
      typeof item.evidence.response_digest === "string" && DIGEST.test(item.evidence.response_digest))
      .map(item => ({ label: item.label, evidence: { source: item.evidence.source, response_digest: item.evidence.response_digest } })) : [];
    return { unknown_labels: safeItems("unknown_labels"), ambiguous_labels: safeItems("ambiguous_labels") };
  };
  return { scalar_candidates: scalars, genre_evidence: labels("genres"), vibe_evidence: labels("vibes"),
    tag_diagnostics: { genres: labelDiagnostics("genres"), vibes: labelDiagnostics("vibes") },
    search_lookup_year_discrepancy: song.recording?.release_date_comparison?.lookup_year_discrepancy === true };
}

function strictKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).every(key => keys.includes(key));
}

function normalizeFactualContext(document, songId) {
  if (document === null || document === undefined) return null;
  if (document.factual_context_version === 1) {
    const keys = ["factual_context_version", "target", "recording", "scalar_candidates", "submission_coverage", "conflicts"];
    if (!strictKeys(document, keys) || Object.keys(document).length !== keys.length ||
        !strictKeys(document.target, ["song_id", "title", "artist"]) || document.target.song_id !== songId ||
        !strictKeys(document.recording, ["mbid", "title", "artist", "disambiguation", "validated", "response_digest"]) ||
        !UUID.test(document.recording.mbid || "") || document.recording.validated !== true ||
        ![document.target.title, document.target.artist, document.recording.title, document.recording.artist]
          .every(value => typeof value === "string" && value.length > 0 && value.length <= 200) ||
        typeof document.recording.disambiguation !== "string" || document.recording.disambiguation.length > 200 ||
        !DIGEST.test(document.recording.response_digest || "") ||
        !strictKeys(document.scalar_candidates, SCALARS) ||
        !strictKeys(document.submission_coverage, ["source", "total_available", "sampled_offsets", "completeness"]) ||
        document.submission_coverage.source !== "AcousticBrainz" || !Number.isSafeInteger(document.submission_coverage.total_available) ||
        document.submission_coverage.total_available < 0 || !Array.isArray(document.submission_coverage.sampled_offsets) ||
        document.submission_coverage.sampled_offsets.length > 3 ||
        !document.submission_coverage.sampled_offsets.every((offset, index, all) => Number.isSafeInteger(offset) && offset >= 0 &&
          offset < document.submission_coverage.total_available && (index === 0 || all[index - 1] < offset)) ||
        !["complete", "partial"].includes(document.submission_coverage.completeness) ||
        (document.submission_coverage.completeness === "complete" && document.submission_coverage.sampled_offsets.length !== document.submission_coverage.total_available) ||
        !strictKeys(document.conflicts, SCALARS) || SCALARS.some(field => typeof document.conflicts[field] !== "boolean")) {
      throw fail("Version 1 factual context is malformed or targets a different song.", "FACTUAL_CONTEXT_INVALID");
    }
    const scalarCandidates = {};
    for (const field of SCALARS) {
      const group = document.scalar_candidates[field];
      if (!strictKeys(group, ["status", "candidates"]) || !["resolved", "conflicting", "unresolved"].includes(group.status) ||
          !Array.isArray(group.candidates) || group.candidates.length > 20) {
        throw fail("Version 1 factual context contains malformed scalar evidence.", "FACTUAL_CONTEXT_INVALID");
      }
      const source = field === "release_year" ? "MusicBrainz" : "AcousticBrainz";
      const candidates = group.candidates.map(candidate => {
        if (!strictKeys(candidate, ["source", "value", "response_digest", "submission_reference"]) || candidate.source !== source ||
            !DIGEST.test(candidate.response_digest || "") || typeof candidate.submission_reference !== "string" ||
            candidate.submission_reference.length > 200 || !candidate.submission_reference) {
          throw fail("Version 1 factual context contains malformed scalar provenance.", "FACTUAL_CONTEXT_INVALID");
        }
        let value = candidate.value;
        if (field === "release_year" && (!Number.isInteger(value) || value < 1 || value > 32767) ||
            field === "bpm" && (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || Math.floor(value + 0.5) > 32767) ||
            field === "original_key" && (!strictKeys(value, ["key_key", "key_scale"]) || Object.keys(value).length !== 2 ||
              !KEY_ROOTS.has(value.key_key) || !["major", "minor"].includes(value.key_scale))) {
          throw fail("Version 1 factual context contains an invalid scalar candidate.", "FACTUAL_CONTEXT_INVALID");
        }
        if (field === "original_key") value = { key_key: value.key_key, key_scale: value.key_scale };
        if (field === "release_year" && candidate.submission_reference !== `recording:${document.recording.mbid}` ||
            field !== "release_year" && !new RegExp(`^${document.recording.mbid}:\\d+$`, "i").test(candidate.submission_reference)) {
          throw fail("Version 1 factual context provenance does not match its recording.", "FACTUAL_CONTEXT_INVALID");
        }
        return { source, value, response_digest: candidate.response_digest, submission_reference: candidate.submission_reference };
      });
      const distinctValues = new Set(candidates.map(item => factualScalarKey(field, item.value))).size;
      const conflicting = distinctValues > 1;
      const expectedStatus = candidates.length === 0 ? "unresolved" : conflicting ? "conflicting" : "resolved";
      if (group.status !== expectedStatus || document.conflicts[field] !== conflicting ||
          candidates.some(item => field === "release_year" ? item.response_digest !== document.recording.response_digest
            : !document.submission_coverage.sampled_offsets.includes(Number(item.submission_reference.split(":").at(-1))))) {
        throw fail("Version 1 factual context conflict state does not match its candidates.", "FACTUAL_CONTEXT_INVALID");
      }
      scalarCandidates[field] = { state: candidates.length ? conflicting ? "partial_unapproved_candidates" : "unapproved_candidates" : "partial_no_candidate",
        evidence_status: candidates.length ? "ok" : "absent", conflicting_evidence: conflicting, candidates };
    }
    return { context_version: 1, target: { ...document.target }, recording: { ...document.recording, mbid: document.recording.mbid.toLowerCase() },
      scalar_candidates: scalarCandidates, submission_coverage: { ...document.submission_coverage }, conflicts: { ...document.conflicts } };
  }
  const legacy = safeFactualEvidence(document, songId);
  return legacy ? { context_version: "b4-v1", ...legacy } : null;
}

function validateFactualIdentity(factual, song) {
  if (!factual || factual.context_version !== 1) return;
  const clean = value => value.trim().toLocaleLowerCase("en-US").replace(/&/g, "and").replace(/\s+/g, " ");
  if (factual.target.song_id !== song.id || clean(factual.target.title) !== clean(song.title) ||
      clean(factual.target.artist) !== clean(song.artist) || clean(factual.recording.title) !== clean(song.title) ||
      clean(factual.recording.artist) !== clean(song.artist)) {
    throw fail("Factual context identity does not match the read-only song baseline.", "FACTUAL_CONTEXT_IDENTITY_MISMATCH");
  }
}

function buildReport(song, gemini, requestedFields, factual = null, preview = null) {
  return { format_version: 1, mode: "explicit_gemini_missing_metadata_fallback", database_writes: false,
    selected_song_id: song.id, baseline: safeBaselineSong(song),
    requested_missing_fields: [...requestedFields],
    factual_evidence: factual,
    gemini_suggestions: { ...gemini, scalar_states: Object.fromEntries(SCALARS.map(field => [field,
      song[field] !== null && song[field] !== undefined && !(typeof song[field] === "string" && !song[field].trim())
        ? "populated_preserved" : hasUsableFactualCandidate(factual, field) ? "factual_candidate_preserved"
          : gemini.status !== "ok" ? "provider_unavailable"
        : gemini.scalars[field].length ? "unapproved_candidate" : "abstained"])) },
    b1_validation_preview: preview,
    human_review_required: true, approved_apply_manifest: null,
    limits: { gemini_attempts: 1, retries: 0, cache: "disabled", max_input_bytes: 16_384,
      max_output_tokens: 1_024, max_response_bytes: MAX_RESPONSE_BYTES, timeout_ms: 10_000 },
  };
}

function renderSummary(report) {
  const song = report.baseline;
  const lines = ["Explicit Gemini missing-metadata fallback preview (unapproved)",
    `Song ${song.id}: ${song.title} / ${song.artist}`, `Version: ${song.updated_at}`,
    `Gemini status: ${report.gemini_suggestions.status}; attempts: ${report.attempts.total}/1; cache: disabled.`,
    `Requested missing scalars: ${report.requested_missing_fields.join(", ") || "none"}`,
    `Suggestions: release_year=${report.gemini_suggestions.scalars.release_year.length}; original_key=${report.gemini_suggestions.scalars.original_key.length}; bpm=${report.gemini_suggestions.scalars.bpm.length}; Genre=${report.gemini_suggestions.genres.length}; Vibe=${report.gemini_suggestions.vibes.length}.`,
    "Suggestions require human review. No apply manifest or database write was created."];
  return `${lines.join("\n")}\n`;
}

async function writeReportFiles(directory, report, filesystem = fs) {
  const created = [];
  try {
    for (const [name, content] of [["report-v1.json", `${JSON.stringify(report, null, 2)}\n`], ["summary.txt", renderSummary(report)]]) {
      const file = path.join(directory, name);
      const handle = await filesystem.open(file, "wx", 0o600);
      created.push(file);
      try { await handle.writeFile(content, "utf8"); } finally { await handle.close(); }
    }
    return { jsonPath: path.join(directory, "report-v1.json"), summaryPath: path.join(directory, "summary.txt") };
  } catch {
    let cleanupFailed = false;
    for (const file of created) try { await filesystem.unlink(file); } catch { cleanupFailed = true; }
    throw fail(cleanupFailed ? "Private report writing failed and an owned partial file could not be removed." :
      "Private report writing failed; owned partial files were removed.", cleanupFailed ? "ARTIFACT_CLEANUP_FAILED" : "ARTIFACT_WRITE_FAILED");
  }
}

async function preparePreview({ pool, songId, factualInput = null, expectedBaseline = null }) {
  const baseline = await captureReadOnlyBaseline(pool, [songId]);
  if (expectedBaseline && JSON.stringify(baseline) !== JSON.stringify(expectedBaseline)) {
    throw fail("The fresh song/catalog snapshot changed before Gemini acquisition.", "COHORT_DRIFT");
  }
  const song = baseline.songs[0];
  const factual = normalizeFactualContext(factualInput, songId);
  validateFactualIdentity(factual, song);
  const missing = requestedMissingFields(song, factual);
  const definitions = baseline.definitions;
  const allowedNames = {
    genres: unassignedAllowedNames(definitions.genres, song.genres, "Genre"),
    vibes: unassignedAllowedNames(definitions.vibes, song.vibes, "Vibe"),
  };
  return { baseline, song, factual, missing, definitions, allowedNames };
}

async function executePreparedPreview({ prepared, providerClient, apiKey, signal, clock = Date.now }) {
  const { baseline, song, factual, missing, definitions, allowedNames } = prepared;
  const result = await providerClient.requestGeminiTags({ mbid: null, title: song.title, artist: song.artist,
    context: {}, factualEvidence: factual, requestedFields: missing, definitions, allowedNames, apiKey, signal });
  const gemini = safeGeminiResult(result, missing);
  const evidence = { mbid: null, scalars: gemini.scalars, genres: gemini.genres, vibes: gemini.vibes };
  const evidenceCount = SCALARS.reduce((count, field) => count + gemini.scalars[field].length, 0) + gemini.genres.length + gemini.vibes.length;
  const validationPreview = evidenceCount ? buildEnrichmentPreview(song, evidence,
    { mbid: null, scalars: {}, genres: [], vibes: [] }, baseline.definitions) : null;
  const counts = providerClient.getAttemptCounts();
  return { report: buildReport(song, gemini, missing, factual, validationPreview), attempts: counts };
}

async function runPreview({ pool, providerClient, songId, apiKey, signal, clock = Date.now, factualInput = null }) {
  const prepared = await preparePreview({ pool, songId, factualInput });
  return executePreparedPreview({ prepared, providerClient, apiKey, signal, clock });
}

function createFallbackProviderClient({ transport, musicBrainzUserAgent }) {
  return createSongEnrichmentProviderClient({
    transport, cache: null,
    config: { maxAttempts: 1, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
      geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000,
      retryDelayMs: 1_000, musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent, model: DEFAULTS.model },
  });
}

async function main(argv = process.argv.slice(2), dependencies = {}) {
  const args = parseArguments(argv);
  const filesystem = dependencies.filesystem || fs;
  const projectRoot = path.resolve(dependencies.projectRoot || PROJECT_ROOT);
  let factual = null;
  if (args.factualReport) {
    const factualPath = path.resolve(args.factualReport);
    if (!isWithin(factualPath, projectRoot)) throw fail("Saved factual report must be inside the Chord Chart Manager project.", "FACTUAL_REPORT_PATH_INVALID");
    try {
      const [physicalRoot, physicalFile] = await Promise.all([filesystem.realpath(projectRoot), filesystem.realpath(factualPath)]);
      if (!isWithin(physicalFile, physicalRoot)) throw fail("Saved factual report must resolve inside the Chord Chart Manager project.", "FACTUAL_REPORT_PATH_INVALID");
      factual = JSON.parse(await filesystem.readFile(physicalFile, "utf8"));
      normalizeFactualContext(factual, args.songId);
    }
    catch (error) { if (error?.safeMessage) throw error; throw fail("Saved factual evidence could not be read or parsed.", "FACTUAL_REPORT_INVALID"); }
  }
  const config = validateConfiguration(dependencies.configuration || await loadConfiguration(dependencies.configurationOptions));
  const outputPath = path.resolve(args.outputDirectory);
  if (!isWithin(outputPath, projectRoot)) throw fail("Output directory must be beneath Chord Chart Manager.", "OUTPUT_PATH_INVALID");
  try {
    const [physicalRoot, physicalParent] = await Promise.all([
      filesystem.realpath(projectRoot), filesystem.realpath(path.dirname(outputPath)),
    ]);
    if (!isWithin(path.join(physicalParent, path.basename(outputPath)), physicalRoot)) {
      throw fail("Output directory must resolve beneath Chord Chart Manager.", "OUTPUT_PATH_INVALID");
    }
  } catch (error) {
    if (error?.safeMessage) throw error;
    throw fail("Output directory parent must already exist.", "OUTPUT_PARENT_MISSING");
  }
  let pool = dependencies.pool;
  let outputDirectory = null;
  let result;
  let failure = null;
  try {
    if (!pool) {
      const { Pool } = require("../server/node_modules/pg");
      pool = new Pool({ connectionString: config.databaseUrl, connectionTimeoutMillis: 5_000,
        options: "-c default_transaction_read_only=on" });
    }
    const providerClient = dependencies.providerClient || createFallbackProviderClient({
      transport: dependencies.transport || createSongEnrichmentTransport(), musicBrainzUserAgent: config.musicBrainzUserAgent,
    });
    const prepared = await preparePreview({ pool, songId: args.songId, factualInput: factual });
    outputDirectory = await createOutputDirectory(args.outputDirectory, { ...dependencies.outputOptions, filesystem });
    result = await executePreparedPreview({ prepared, providerClient, apiKey: config.apiKey,
      signal: dependencies.signal, clock: dependencies.clock || Date.now });
    result.report.attempts = { total: result.attempts.total, gemini: result.attempts.gemini,
      ledger: safeAttemptLedger(providerClient.getRunLedger()) };
  } catch (error) { failure = error; }
  finally {
    if (pool && typeof pool.end === "function") try { await pool.end(); }
    catch { if (!failure) failure = fail("Read-only database pool shutdown failed.", "POOL_SHUTDOWN_FAILED"); }
  }
  if (failure) {
    if (outputDirectory) try { await filesystem.rmdir(outputDirectory); } catch { /* remove only the empty invocation-owned directory */ }
    throw failure?.safeMessage ? failure : fail("Gemini fallback preview failed safely.", "PREVIEW_FAILED");
  }
  try {
    const artifacts = await writeReportFiles(outputDirectory, result.report, filesystem);
    return { report: result.report, artifacts };
  } catch (error) {
    try { await filesystem.rmdir(outputDirectory); } catch { /* preserve files not owned by this invocation */ }
    throw error;
  }
}

if (require.main === module) main().then(({ artifacts }) => {
  process.stdout.write(`Fallback preview written to ${artifacts.jsonPath}\nSummary written to ${artifacts.summaryPath}\n`);
}).catch(error => { process.stderr.write(`${error.safeMessage || "Gemini fallback preview failed."}\n`); process.exitCode = 1; });

module.exports = { buildReport, loadConfiguration, main, parseArguments, parsePrivateEnv, renderSummary, runPreview, preparePreview,
  executePreparedPreview,
  createFallbackProviderClient, safeFactualEvidence, safeGeminiResult, unassignedAllowedNames,
  normalizeFactualContext, requestedMissingFields, validateFactualIdentity,
  validateConfiguration, writeReportFiles };
