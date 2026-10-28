#!/usr/bin/env node
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createSongEnrichmentProviderClient, DEFAULTS } = require("../server/songEnrichmentProviders.js");
const { MAX_RESPONSE_BYTES, createSongEnrichmentTransport } = require("../server/songEnrichmentTransport.js");
const { resolveRecordingCandidate } = require("../server/existingSongEnrichment.js");
const { definitionKey, normalizeDefinitionName } = require("../server/tagDefinitionNames.js");

const APP_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(APP_ROOT, "../..");
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DIGEST = /^[a-f0-9]{64}$/i;
const SCALAR_FIELDS = ["release_year", "original_key", "bpm"];
const SOURCE_BY_FIELD = Object.freeze({ release_year: "MusicBrainz", original_key: "AcousticBrainz", bpm: "AcousticBrainz" });
const REPORT_VERSION = 1;
const SUMMARY_LIMIT = 250;

function cliError(message, code = "INVALID_ARGUMENTS") {
  const error = new Error(message);
  error.code = code;
  error.safeMessage = message;
  return error;
}

function parseArguments(argv) {
  const values = new Map([
    ["--song-ids", "songIds"], ["--max-attempts", "maxAttempts"],
    ["--musicbrainz-user-agent", "musicBrainzUserAgent"], ["--output-dir", "outputDirectory"],
  ]);
  const parsed = { liveReadOnly: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--live-read-only") {
      if (parsed.liveReadOnly) throw cliError("--live-read-only may be specified once.");
      parsed.liveReadOnly = true;
    } else if (values.has(arg)) {
      const key = values.get(arg);
      if (Object.hasOwn(parsed, key) || !argv[index + 1] || argv[index + 1].startsWith("--")) {
        throw cliError(`Missing or repeated ${arg}.`);
      }
      parsed[key] = argv[++index];
    } else throw cliError("Unknown preview command argument.");
  }
  if (!parsed.liveReadOnly) throw cliError("Preview execution requires --live-read-only.");
  if (!parsed.songIds) throw cliError("Explicit --song-ids are required.");
  const textIds = parsed.songIds.split(",");
  const ids = textIds.map(value => /^\d+$/.test(value) ? Number(value) : NaN);
  if (ids.length < 1 || ids.length > 3 || ids.some(id => !Number.isSafeInteger(id) || id <= 0) || new Set(ids).size !== ids.length) {
    throw cliError("--song-ids must contain 1 to 3 distinct positive integers.");
  }
  parsed.songIds = ids.sort((a, b) => a - b);
  if (!parsed.maxAttempts || !/^\d+$/.test(parsed.maxAttempts) || Number(parsed.maxAttempts) < 1 || Number(parsed.maxAttempts) > 12) {
    throw cliError("Explicit --max-attempts must be an integer from 1 through 12.");
  }
  parsed.maxAttempts = Number(parsed.maxAttempts);
  if (typeof parsed.musicBrainzUserAgent !== "string") {
    throw cliError("Explicit MusicBrainz application/version/contact User-Agent is required.");
  }
  if (/[\r\n]/.test(parsed.musicBrainzUserAgent)) {
    throw cliError("MusicBrainz User-Agent contains invalid header characters.");
  }
  parsed.musicBrainzUserAgent = parsed.musicBrainzUserAgent.trim();
  if (!/^\S[^()]*\/\d+(?:\.\d+){1,2}(?:[-+][\w.-]+)?\s+\([^()]+\)$/.test(parsed.musicBrainzUserAgent)) {
    throw cliError("Explicit MusicBrainz application/version/contact User-Agent is required.");
  }
  try { http.validateHeaderValue("User-Agent", parsed.musicBrainzUserAgent); }
  catch { throw cliError("MusicBrainz User-Agent contains invalid header characters."); }
  if (!parsed.outputDirectory || !parsed.outputDirectory.trim()) throw cliError("Explicit ignored --output-dir is required.");
  return parsed;
}

function validateDatabaseUrl(value) {
  if (typeof value !== "string" || !value.trim()) throw cliError("DATABASE_URL must be explicitly provided in private configuration.", "DATABASE_CONFIG_REQUIRED");
  try {
    const url = new URL(value);
    if (!["postgres:", "postgresql:"].includes(url.protocol) || !url.hostname || !url.pathname || url.pathname === "/") throw new Error();
  } catch {
    throw cliError("DATABASE_URL must be a valid PostgreSQL connection URL.", "DATABASE_CONFIG_INVALID");
  }
  return value;
}

function checkIgnoredDirectory(directory, { repoRoot = REPO_ROOT, runGit = execFileSync } = {}) {
  const absolute = path.resolve(directory);
  const relative = path.relative(repoRoot, absolute);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return false;
  try {
    runGit("git", ["check-ignore", "--quiet", "--no-index", "--", relative], { cwd: repoRoot, stdio: "ignore" });
    return true;
  } catch { return false; }
}

async function createOutputDirectory(directory, { isIgnored = checkIgnoredDirectory, filesystem = fs } = {}) {
  const absolute = path.resolve(directory);
  let realParent;
  try { realParent = await filesystem.realpath(path.dirname(absolute)); }
  catch { throw cliError("Output directory parent must already exist.", "OUTPUT_PARENT_MISSING"); }
  const physicalPath = path.join(realParent, path.basename(absolute));
  if (!(await isIgnored(absolute)) || !(await isIgnored(physicalPath))) {
    throw cliError("Output directory must be ignored by this repository.", "OUTPUT_NOT_IGNORED");
  }
  try { await filesystem.mkdir(absolute, { recursive: false, mode: 0o700 }); }
  catch { throw cliError("Output directory must be a new, exclusively created directory.", "OUTPUT_DIRECTORY_EXISTS"); }
  return absolute;
}

async function captureReadOnlyBaseline(pool, songIds) {
  const client = await pool.connect();
  let beginAttempted = false;
  try {
    beginAttempted = true;
    await client.query("BEGIN READ ONLY");
    const songsResult = await client.query(`SELECT s.id,s.title,s.artist_id,a.name AS artist_name,s.release_year,s.original_key,s.bpm,
        to_char(s.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS updated_at
      FROM public.songs s JOIN public.artists a ON a.id=s.artist_id
      WHERE s.id=ANY($1::int[]) ORDER BY s.id`, [songIds]);
    if (!Array.isArray(songsResult.rows) || songsResult.rows.length !== songIds.length ||
        new Set(songsResult.rows.map(row => row.id)).size !== songIds.length) {
      throw cliError("One or more requested song IDs are absent from the captured database baseline.", "SONG_ID_SET_MISMATCH");
    }
    const genreAssignments = await client.query(`SELECT sg.song_id,g.id,g.name,sg.is_primary FROM public.song_genres sg
        JOIN public.genres g ON g.id=sg.genre_id WHERE sg.song_id=ANY($1::int[]) ORDER BY sg.song_id,g.id`, [songIds]);
    const vibeAssignments = await client.query(`SELECT sv.song_id,v.id,v.name FROM public.song_vibes sv
        JOIN public.vibes v ON v.id=sv.vibe_id WHERE sv.song_id=ANY($1::int[]) ORDER BY sv.song_id,v.id`, [songIds]);
    const genreDefinitions = await client.query("SELECT id,name FROM public.genres ORDER BY id");
    const vibeDefinitions = await client.query("SELECT id,name FROM public.vibes ORDER BY id");
    await client.query("COMMIT");
    const genresBySong = new Map(songIds.map(id => [id, []]));
    for (const row of genreAssignments.rows) genresBySong.get(row.song_id)?.push({ id: row.id, name: row.name, is_primary: row.is_primary });
    const vibesBySong = new Map(songIds.map(id => [id, []]));
    for (const row of vibeAssignments.rows) vibesBySong.get(row.song_id)?.push({ id: row.id, name: row.name });
    return {
      songs: songsResult.rows.map(row => ({
        id: row.id,
        artist_id: row.artist_id,
        title: row.title,
        artist: row.artist_name,
        updated_at: row.updated_at,
        release_year: row.release_year,
        original_key: row.original_key,
        bpm: row.bpm,
        genres: genresBySong.get(row.id),
        vibes: vibesBySong.get(row.id),
      })),
      definitions: { genres: genreDefinitions.rows, vibes: vibeDefinitions.rows },
    };
  } catch (error) {
    if (beginAttempted) {
      try { await client.query("ROLLBACK"); } catch { /* retain the original bounded error */ }
    }
    if (error?.safeMessage) throw error;
    throw cliError("Could not capture the requested songs in a read-only database transaction.", "BASELINE_READ_FAILED");
  } finally {
    client.release();
  }
}

function safeCandidate(candidate) {
  if (!candidate || typeof candidate !== "object" || typeof candidate.mbid !== "string" || !UUID.test(candidate.mbid) ||
      typeof candidate.title !== "string" || typeof candidate.artist !== "string" ||
      typeof candidate.disambiguation !== "string" || typeof candidate.response_digest !== "string" || !DIGEST.test(candidate.response_digest)) return null;
  return {
    mbid: candidate.mbid.toLowerCase(), title: candidate.title, artist: candidate.artist,
    disambiguation: candidate.disambiguation,
    ...(typeof candidate.video === "boolean" ? { video: candidate.video } : {}),
    ...(typeof candidate.first_release_date === "string" ? { first_release_date: candidate.first_release_date } : {}),
    response_digest: candidate.response_digest,
  };
}

function safeReleaseTypeEvidence(lookupMap) {
  if (!(lookupMap instanceof Map)) return [];
  return [...lookupMap.entries()].map(([mbid, lookup]) => {
    const evidence = lookup?.recording?.release_type_evidence;
    if (lookup?.status !== "ok" || typeof mbid !== "string" || !UUID.test(mbid) ||
        !evidence || evidence.mbid?.toLowerCase() !== mbid.toLowerCase() ||
        !Array.isArray(evidence.releases) || !DIGEST.test(evidence.response_digest || "")) return null;
    const releases = evidence.releases.map(release => {
      const group = release?.release_group;
      if (!release || typeof release.id !== "string" || !UUID.test(release.id) ||
          typeof release.title !== "string" || release.title.length > 240 ||
          !(release.date === null || typeof release.date === "string" && /^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(release.date)) ||
          !(release.status === null || typeof release.status === "string" && release.status.length <= 80) ||
          typeof release.disambiguation !== "string" || release.disambiguation.length > 240 ||
          !group || typeof group.id !== "string" || !UUID.test(group.id) ||
          typeof group.title !== "string" || group.title.length > 240 ||
          !(group.primary_type === null || typeof group.primary_type === "string" && group.primary_type.length <= 80) ||
          !Array.isArray(group.secondary_types) || group.secondary_types.some(type => typeof type !== "string" || type.length > 80)) return null;
      return { id: release.id.toLowerCase(), title: release.title, date: release.date, status: release.status,
        disambiguation: release.disambiguation, release_group: { id: group.id.toLowerCase(), title: group.title,
          primary_type: group.primary_type, secondary_types: [...group.secondary_types].sort() } };
    });
    if (releases.some(release => release === null)) return null;
    return { mbid: mbid.toLowerCase(), response_digest: evidence.response_digest,
      first_release_date: typeof evidence.first_release_date === "string" && /^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(evidence.first_release_date)
        ? evidence.first_release_date : null,
      release_count: evidence.release_count, complete: evidence.complete === true, releases };
  }).filter(Boolean).sort((left, right) => left.mbid.localeCompare(right.mbid, "en"));
}

function safeEvidenceItem(item, expectedSource, field) {
  if (!item || typeof item !== "object" || item.source !== expectedSource || typeof item.response_digest !== "string" ||
      !DIGEST.test(item.response_digest)) return null;
  const value = item.value;
  if (!valueKey(field, value)) return null;
  const safeValue = field === "original_key" ? { key_key: value.key_key, key_scale: value.key_scale } : value;
  return {
    value: safeValue,
    source: expectedSource,
    response_digest: item.response_digest,
    ...(expectedSource === "AcousticBrainz" && typeof item.submission_id === "string" && /^[0-9a-f-]{36}:\d+$/.test(item.submission_id)
      ? { submission_id: item.submission_id.toLowerCase() } : {}),
    ...(field === "original_key" && expectedSource === "AcousticBrainz" &&
      typeof item.key_strength === "number" && Number.isFinite(item.key_strength)
      ? { key_strength: item.key_strength } : {}),
  };
}

function safeAcousticOffsets(offsets, count) {
  if (!Array.isArray(offsets) || offsets.length > 3 || !Number.isSafeInteger(count) || count < 0 ||
      !offsets.every((offset, index) => Number.isSafeInteger(offset) && offset >= 0 && offset < count &&
        (index === 0 || offsets[index - 1] < offset))) return null;
  return [...offsets];
}

function missingScalar(song, field) {
  return field === "original_key"
    ? song[field] === null || typeof song[field] === "string" && song[field].trim() === ""
    : song[field] === null;
}

function valueKey(field, value) {
  if (field === "release_year") return Number.isInteger(value) && value >= 1 && value <= 32767 ? `year:${value}` : null;
  if (field === "bpm") {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
    const rounded = Math.floor(value + 0.5);
    return rounded >= 1 && rounded <= 32767 ? `bpm:${rounded}` : null;
  }
  if (field === "original_key") {
    if (typeof value !== "object" || !value || Array.isArray(value)) return null;
    const roots = new Set(["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"]);
    return roots.has(value.key_key) && ["major", "minor"].includes(value.key_scale)
      ? `key:${value.key_key}${value.key_scale === "minor" ? "m" : ""}` : null;
  }
  return null;
}

function resolveTagCandidates(category, evidenceItems, catalog, assigned) {
  const output = { unapproved_additions: [], unknown_labels: [], ambiguous_labels: [] };
  const existingIds = new Set(assigned.map(row => row.id));
  const emittedIds = new Set();
  for (const item of evidenceItems) {
    if (!item || typeof item.name !== "string" || typeof item.source !== "string" ||
        !["MusicBrainz", "AcousticBrainz"].includes(item.source) ||
        typeof item.response_digest !== "string" || !DIGEST.test(item.response_digest)) continue;
    let key;
    try { key = definitionKey(normalizeDefinitionName(item.name, category)); }
    catch {
      output.unknown_labels.push({ label: item.name, evidence: { source: item.source, response_digest: item.response_digest } });
      continue;
    }
    const matches = catalog.filter(row => {
      try { return definitionKey(normalizeDefinitionName(row.name, category)) === key; }
      catch { return false; }
    });
    const evidence = { source: item.source, response_digest: item.response_digest };
    if (matches.length === 0) output.unknown_labels.push({ label: item.name, evidence });
    else if (matches.length > 1) output.ambiguous_labels.push({ label: item.name, evidence });
    else if (!existingIds.has(matches[0].id) && !emittedIds.has(matches[0].id)) {
      emittedIds.add(matches[0].id);
      output.unapproved_additions.push({ id: matches[0].id, name: matches[0].name, evidence: [evidence] });
    } else if (emittedIds.has(matches[0].id)) {
      output.unapproved_additions.find(row => row.id === matches[0].id).evidence.push(evidence);
    }
  }
  return output;
}

function safeRecordingCandidates(candidates) {
  return Array.isArray(candidates) ? candidates.map(safeCandidate).filter(Boolean) : [];
}

const PAGINATION_DIAGNOSTIC_CODES = new Set([
  "unexpected_offset", "unexpected_page_length", "invalid_pagination_field",
  "invalid_candidate", "duplicate_recording_identity", "result_cap_exceeded",
]);
const RECORDING_SELECTION_REASONS = new Set([
  "unique_exact_match", "earliest_release_year", "genre_richness", "canonical_mbid_tiebreak",
  "richness_comparison_incomplete", "no_dated_eligible_recording", "unresolved_version_ambiguity", "no_eligible_recording",
  "release_evidence_unresolved",
]);
const LOOKUP_STATUSES = new Set(["ok", "absent", "invalid_response", "authentication_error", "transient_error", "timeout",
  "budget_exhausted", "cancelled", "network_disabled"]);

function isCompleteSearchResult(search) {
  if (!search || !["ok", "absent"].includes(search.status) || !Array.isArray(search.candidates)) return false;
  const pagination = search.pagination;
  return pagination?.complete === true && Number.isSafeInteger(pagination.expected_total) &&
    pagination.expected_total >= 0 && pagination.expected_total <= 100 &&
    pagination.expected_total === search.candidates.length && pagination.collected_count === search.candidates.length &&
    pagination.attempted_pages === 1 && pagination.validated_pages === 1 &&
    (search.status !== "absent" || search.candidates.length === 0);
}

function safeRecordingMatch(matching) {
  const output = {
    matched: matching?.matched === true,
    reason: typeof matching?.reason === "string" && /^[a-z0-9_]{1,80}$/.test(matching.reason) ? matching.reason : null,
    selection_reason: RECORDING_SELECTION_REASONS.has(matching?.selection_reason) ? matching.selection_reason : null,
    eligible_candidate_count: Number.isSafeInteger(matching?.eligible_candidate_count) && matching.eligible_candidate_count >= 0
      ? matching.eligible_candidate_count : 0,
    exclusion_counts: {},
  };
  if (matching?.exclusion_counts && typeof matching.exclusion_counts === "object" && !Array.isArray(matching.exclusion_counts)) {
    for (const [reason, count] of Object.entries(matching.exclusion_counts).sort(([left], [right]) => left.localeCompare(right, "en"))) {
      if (/^(?:invalid_mbid|live_version|bootleg_version|live_first_release|bootleg_first_release|release_evidence_incomplete|remix_version|acoustic_version|demo_version|music_video_version|rerecorded_version|alternate_version|unresolved_version_ambiguity)$/.test(reason) &&
          Number.isSafeInteger(count) && count > 0) output.exclusion_counts[reason] = count;
    }
  }
  if (output.matched && typeof matching.mbid === "string" && UUID.test(matching.mbid)) {
    output.mbid = matching.mbid.toLowerCase();
    const evidence = matching.release_date_evidence;
    if (evidence && (evidence.first_release_date === null || typeof evidence.first_release_date === "string") &&
        typeof evidence.response_digest === "string" && DIGEST.test(evidence.response_digest)) {
      output.release_date_evidence = {
        first_release_date: evidence.first_release_date,
        response_digest: evidence.response_digest,
      };
    }
  }
  output.earliest_year = Number.isSafeInteger(matching?.earliest_year) && matching.earliest_year >= 1 && matching.earliest_year <= 9999
    ? matching.earliest_year : null;
  output.missing_release_date_count = Number.isSafeInteger(matching?.missing_release_date_count) && matching.missing_release_date_count >= 0
    ? matching.missing_release_date_count : 0;
  output.same_year_candidate_mbids = Array.isArray(matching?.same_year_candidate_mbids)
    ? matching.same_year_candidate_mbids.filter(mbid => typeof mbid === "string" && UUID.test(mbid)).map(mbid => mbid.toLowerCase()).sort() : [];
  output.matched_genre_counts = Array.isArray(matching?.matched_genre_counts)
    ? matching.matched_genre_counts.filter(row => row && typeof row.mbid === "string" && UUID.test(row.mbid) &&
      (row.matched_genre_count === null || Number.isSafeInteger(row.matched_genre_count) && row.matched_genre_count >= 0))
      .map(row => ({ mbid: row.mbid.toLowerCase(), matched_genre_count: row.matched_genre_count }))
      .sort((left, right) => left.mbid < right.mbid ? -1 : left.mbid > right.mbid ? 1 : 0) : [];
  output.richness_comparison_complete = matching?.richness_comparison_complete === true;
  output.tie_break_reason = typeof matching?.tie_break_reason === "string" &&
    /^(?:unique_dated_candidate|earliest_release_year|matched_managed_genre_richness|canonical_mbid|canonical_mbid_provisional)$/.test(matching.tie_break_reason)
    ? matching.tie_break_reason : null;
  if (typeof matching?.provisional_mbid === "string" && UUID.test(matching.provisional_mbid)) {
    output.provisional_mbid = matching.provisional_mbid.toLowerCase();
    const evidence = matching.provisional_release_date_evidence;
    if (evidence && (evidence.first_release_date === null || typeof evidence.first_release_date === "string") &&
        typeof evidence.response_digest === "string" && DIGEST.test(evidence.response_digest)) {
      output.provisional_release_date_evidence = { first_release_date: evidence.first_release_date,
        response_digest: evidence.response_digest };
    }
  }
  return output;
}

function safePagination(search) {
  const value = search?.pagination;
  if (!value || typeof value !== "object" || !Number.isSafeInteger(value.attempted_pages) || value.attempted_pages < 0 ||
      !Number.isSafeInteger(value.validated_pages) || value.validated_pages < 0 ||
      !Number.isSafeInteger(value.collected_count) || value.collected_count < 0 || typeof value.complete !== "boolean" ||
      !(value.expected_total === null || Number.isSafeInteger(value.expected_total) && value.expected_total >= 0)) return null;
  return { expected_total: value.expected_total, attempted_pages: value.attempted_pages,
    validated_pages: value.validated_pages, collected_count: value.collected_count, complete: value.complete };
}

function safePaginationDiagnostic(diagnostic) {
  if (!diagnostic || typeof diagnostic !== "object" || !PAGINATION_DIAGNOSTIC_CODES.has(diagnostic.code)) return null;
  const output = { code: diagnostic.code };
  for (const field of ["requested_offset", "returned_offset", "current_page_total",
    "returned_recording_count", "validated_response_count", "collected_record_count"]) {
    const value = diagnostic[field];
    if (Number.isSafeInteger(value) && value >= 0) output[field] = value;
    else if (value && typeof value === "object" && ["string", "number", "boolean", "null", "array", "object", "undefined"].includes(value.type)) {
      const descriptor = { type: value.type };
      if (value.type === "string" && typeof value.value === "string" && value.value.length <= 32 && /^\s*[+-]?\d+(?:\.\d+)?\s*$/.test(value.value)) descriptor.value = value.value;
      if (value.type === "number" && typeof value.value === "number" && Number.isFinite(value.value)) descriptor.value = value.value;
      if (value.type === "boolean" && typeof value.value === "boolean") descriptor.value = value.value;
      if (value.type === "array" && Number.isSafeInteger(value.length) && value.length >= 0) descriptor.length = value.length;
      output[field] = descriptor;
    }
  }
  if (diagnostic.code === "duplicate_recording_identity" && typeof diagnostic.duplicate_mbid === "string" &&
      UUID.test(diagnostic.duplicate_mbid) && diagnostic.duplicate_scope === "within_page") {
    const safeOccurrence = occurrence => occurrence && occurrence.requested_offset === 0 &&
      Number.isSafeInteger(occurrence.index_in_page) && occurrence.index_in_page >= 0
      ? { requested_offset: occurrence.requested_offset, index_in_page: occurrence.index_in_page } : null;
    const first = safeOccurrence(diagnostic.first_occurrence);
    const repeated = safeOccurrence(diagnostic.repeated_occurrence);
    if (first && repeated) {
      output.duplicate_mbid = diagnostic.duplicate_mbid.toLowerCase();
      output.duplicate_scope = diagnostic.duplicate_scope;
      output.first_occurrence = first;
      output.repeated_occurrence = repeated;
    }
  }
  return output;
}

function buildSongReport(song, search, matching, lookup, audio, definitions, richnessComparison = null, releaseLookups = new Map()) {
  const mbScalars = lookup?.status === "ok" && lookup.recording?.scalars && typeof lookup.recording.scalars === "object"
    ? lookup.recording.scalars : {};
  // B2 may return already validated earlier submissions with a partial/error status.
  const audioScalars = audio?.scalars && typeof audio.scalars === "object" ? audio.scalars : {};
  const scalars = {};
  const rawEvidenceScalars = {};
  for (const field of SCALAR_FIELDS) {
    const source = SOURCE_BY_FIELD[field];
    const providerItems = [...(Array.isArray(mbScalars[field]) ? mbScalars[field] : []),
      ...(Array.isArray(audioScalars[field]) ? audioScalars[field] : [])]
      .filter(item => item?.source === source).map(item => safeEvidenceItem(item, source, field)).filter(Boolean);
    const current = song[field];
    const missing = missingScalar(song, field);
    const keys = new Set(providerItems.map(item => valueKey(field, item.value)).filter(Boolean));
    const evidenceStatus = field === "release_year" ? lookup?.status : audio?.status;
    const partialAudioWithoutCandidate = field !== "release_year" && audio?.status === "ok" &&
      audio.submission_enumeration !== "complete";
    const partialEvidence = providerItems.length > 0 && (evidenceStatus !== "ok" ||
      field !== "release_year" && audio?.submission_enumeration !== "complete");
    rawEvidenceScalars[field] = providerItems.map(({ key_strength, ...item }) => item);
    scalars[field] = {
      current_value: current,
      state: !missing ? "populated_preserved" : providerItems.length
        ? partialEvidence ? "partial_unapproved_candidates" : "unapproved_candidates"
        : partialAudioWithoutCandidate ? "partial_no_candidate"
        : evidenceStatus && !["ok", "absent"].includes(evidenceStatus) ? "provider_unavailable"
          : evidenceStatus ? "no_candidate" : "not_attempted",
      ...(evidenceStatus ? { evidence_status: evidenceStatus } : {}),
      candidates: missing ? providerItems : [],
      conflicting_evidence: missing && keys.size > 1,
    };
  }
  const genreEvidence = lookup?.status === "ok" && Array.isArray(lookup.recording?.genres) ? lookup.recording.genres : [];
  const vibeEvidence = [];
  const genres = resolveTagCandidates("Genre", genreEvidence, definitions.genres, song.genres);
  const vibes = resolveTagCandidates("Vibe", vibeEvidence, definitions.vibes, song.vibes);
  const b1GenreEvidence = genreEvidence.filter(item => item && typeof item.name === "string" &&
    item.source === "MusicBrainz" && typeof item.response_digest === "string" && DIGEST.test(item.response_digest))
    .map(item => ({ name: item.name, source: item.source, response_digest: item.response_digest }));
  const evidence = matching.matched ? {
    mbid: matching.mbid,
    scalars: rawEvidenceScalars,
    genres: b1GenreEvidence,
    vibes: [],
  } : null;
  const lookupReleaseYears = rawEvidenceScalars.release_year.map(item => ({
    value: item.value,
    source: item.source,
    response_digest: item.response_digest,
    origin: lookup?.response_digest === item.response_digest ? "lookup_response" : "search_candidate_fallback",
  }));
  const searchYear = matching.earliest_year ?? null;
  const lookupYearDiscrepancies = lookupReleaseYears.filter(item => item.origin === "lookup_response" && item.value !== searchYear);
  const current = {
    release_year: song.release_year,
    original_key: song.original_key,
    bpm: song.bpm,
    genres: song.genres,
    vibes: song.vibes,
  };
  return {
    song_id: song.id,
    identity: { title: song.title, artist: song.artist },
    base_version: song.updated_at,
    current,
    recording: {
      search_status: search.status,
      match: safeRecordingMatch(matching),
      candidates: isCompleteSearchResult(search) && search.status === "ok" ? safeRecordingCandidates(search.candidates) : [],
      ...(typeof search.reason === "string" && /^[a-z0-9_]{1,80}$/.test(search.reason) ? { reason: search.reason } : {}),
      ...(safePagination(search) ? { pagination: safePagination(search) } : {}),
      ...(safeReleaseTypeEvidence(releaseLookups).length ? { release_type_evidence: safeReleaseTypeEvidence(releaseLookups) } : {}),
      ...(safePaginationDiagnostic(search.validation_diagnostic) ? { validation_diagnostic: safePaginationDiagnostic(search.validation_diagnostic) } : {}),
      ...(matching.matched ? { lookup_status: lookup?.status || "not_attempted" } : {}),
      ...(matching.earliest_year ? { release_date_comparison: {
        search_year: searchYear,
        search_first_release_date: matching.release_date_evidence?.first_release_date ??
          matching.provisional_release_date_evidence?.first_release_date ?? null,
        search_response_digest: matching.release_date_evidence?.response_digest ??
          matching.provisional_release_date_evidence?.response_digest ?? null,
        lookup_release_year_evidence: lookupReleaseYears,
        lookup_year_discrepancy: lookupYearDiscrepancies.length > 0,
      } } : {}),
      ...(richnessComparison ? { richness_comparison: richnessComparison } : {}),
    },
    acousticbrainz: audio ? {
      status: audio.status,
      submission_enumeration: audio.submission_enumeration || "unknown",
      ...(Number.isSafeInteger(audio.submission_count) ? { submission_count: audio.submission_count } : {}),
      ...(Number.isSafeInteger(audio.submission_count) && audio.submission_count >= 0 &&
        safeAcousticOffsets(audio.planned_sample_offsets, audio.submission_count)
        ? { planned_sample_offsets: safeAcousticOffsets(audio.planned_sample_offsets, audio.submission_count) } : {}),
      ...(Number.isSafeInteger(audio.submission_count) && audio.submission_count >= 0 &&
        safeAcousticOffsets(audio.attempted_offsets, audio.submission_count) &&
        safeAcousticOffsets(audio.planned_sample_offsets, audio.submission_count) &&
        safeAcousticOffsets(audio.attempted_offsets, audio.submission_count).every(offset => audio.planned_sample_offsets.includes(offset))
        ? { attempted_offsets: safeAcousticOffsets(audio.attempted_offsets, audio.submission_count) } : {}),
      ...(Number.isSafeInteger(audio.submission_count) && audio.submission_count >= 0 &&
        safeAcousticOffsets(audio.sampled_offsets, audio.submission_count) &&
        safeAcousticOffsets(audio.planned_sample_offsets, audio.submission_count) &&
        safeAcousticOffsets(audio.sampled_offsets, audio.submission_count).every(offset => audio.planned_sample_offsets.includes(offset))
        ? { sampled_offsets: safeAcousticOffsets(audio.sampled_offsets, audio.submission_count) } : {}),
      agreement_scope: !Number.isSafeInteger(audio.submission_count) || audio.submission_count < 0 ? "unknown"
        : audio.submission_count === 0 ? "none" : "sampled_submissions",
      ...(typeof audio.reason === "string" && /^[a-z0-9_]{1,80}$/.test(audio.reason) ? { reason: audio.reason } : {}),
    } : { status: "skipped", submission_enumeration: "not_requested" },
    evidence,
    scalar_candidates: scalars,
    tag_candidates: { genres, vibes },
    approved_preview: null,
    human_review_required: true,
  };
}

function buildNotDispatchedReport(song, reason) {
  return {
    song_id: song.id,
    identity: { title: song.title, artist: song.artist },
    base_version: song.updated_at,
    current: {
      release_year: song.release_year,
      original_key: song.original_key,
      bpm: song.bpm,
      genres: song.genres,
      vibes: song.vibes,
    },
    state: "not_dispatched",
    reason,
    recording: { search_status: "not_dispatched", match: { matched: false, reason }, candidates: [] },
    acousticbrainz: { status: "not_dispatched", submission_enumeration: "not_requested" },
    evidence: null,
    scalar_candidates: Object.fromEntries(SCALAR_FIELDS.map(field => [field, {
      current_value: song[field], state: "not_dispatched", evidence_status: "not_dispatched",
      candidates: [], conflicting_evidence: false,
    }])),
    tag_candidates: {
      genres: { unapproved_additions: [], unknown_labels: [], ambiguous_labels: [] },
      vibes: { unapproved_additions: [], unknown_labels: [], ambiguous_labels: [] },
    },
    approved_preview: null,
    human_review_required: true,
  };
}

async function runPreview({ pool, providerClient, songIds, maxAttempts, clock = Date.now, expectedBaseline = null }) {
  if (!pool || typeof pool.connect !== "function" || !providerClient ||
      !["searchRecordings", "lookupRecording", "getAcousticBrainzEvidence", "getAttemptCounts", "getRunLedger"]
        .every(name => typeof providerClient[name] === "function")) throw new TypeError("Preview dependencies are incomplete.");
  const baselineCapturedAt = new Date(Number(clock())).toISOString();
  const baseline = await captureReadOnlyBaseline(pool, songIds);
  if (expectedBaseline && JSON.stringify(baseline) !== JSON.stringify(expectedBaseline)) {
    throw cliError("The fresh song/catalog snapshot changed before provider acquisition.", "COHORT_DRIFT");
  }
  const songs = [];
  let budgetExhausted = false;
  for (const song of baseline.songs) {
    if (budgetExhausted) {
      songs.push(buildNotDispatchedReport(song, "shared_attempt_limit_reached"));
      continue;
    }
    let lookup = null;
    let audio = null;
    let richnessComparison = null;
    const search = await providerClient.searchRecordings({ title: song.title, artist: song.artist });
    const lookupMap = new Map();
    let matching = isCompleteSearchResult(search)
      ? resolveRecordingCandidate({ title: song.title, artist: song.artist }, search.candidates, search, baseline.definitions.genres,
        { lookups: lookupMap })
      : { matched: false, reason: search?.reason || search?.status || "search_unavailable", selection_reason: null };
    while (!matching.matched && Array.isArray(matching.release_evidence_required_mbids) && matching.release_evidence_required_mbids.length) {
      let dispatched = false;
      for (const mbid of matching.release_evidence_required_mbids) {
        if (lookupMap.has(mbid)) continue;
        if (providerClient.getAttemptCounts().total >= maxAttempts) break;
        const candidate = search.candidates.find(item => item.mbid.toLowerCase() === mbid);
        if (!candidate) { lookupMap.set(mbid, { status: "invalid_response" }); continue; }
        const candidateLookup = await providerClient.lookupRecording({ mbid, searchCandidate: candidate });
        lookupMap.set(mbid, candidateLookup);
        dispatched = true;
        matching = resolveRecordingCandidate({ title: song.title, artist: song.artist }, search.candidates, search,
          baseline.definitions.genres, { lookups: lookupMap });
        if (matching.matched) break;
      }
      if (!dispatched || matching.matched) break;
      const canDispatchAnother = matching.release_evidence_required_mbids?.some(mbid => !lookupMap.has(mbid));
      if (!canDispatchAnother) break;
    }
    if (!matching.matched && matching.earliest_year && matching.same_year_candidate_mbids?.length > 1) {
      const statuses = new Map();
      for (const mbid of matching.same_year_candidate_mbids) {
        const candidate = search.candidates.find(item => item.mbid.toLowerCase() === mbid);
        if (!candidate) { statuses.set(mbid, "invalid_response"); continue; }
        if (lookupMap.has(mbid)) { statuses.set(mbid, lookupMap.get(mbid)?.status || "invalid_response"); continue; }
        if (providerClient.getAttemptCounts().total >= maxAttempts) {
          statuses.set(mbid, "budget_exhausted");
          continue;
        }
        const candidateLookup = await providerClient.lookupRecording({ mbid, searchCandidate: candidate });
        lookupMap.set(mbid, candidateLookup);
        statuses.set(mbid, candidateLookup?.status || "invalid_response");
      }
      matching = resolveRecordingCandidate({ title: song.title, artist: song.artist }, search.candidates, search,
        baseline.definitions.genres, { lookups: lookupMap });
      richnessComparison = {
        earliest_year: matching.earliest_year,
        complete: matching.richness_comparison_complete,
        candidates: matching.same_year_candidate_mbids.map(mbid => ({
          mbid,
          matched_genre_count: matching.matched_genre_counts.find(row => row.mbid === mbid)?.matched_genre_count ?? null,
          lookup_status: LOOKUP_STATUSES.has(statuses.get(mbid)) ? statuses.get(mbid) : "invalid_response",
        })),
        selected_mbid: matching.matched ? matching.mbid : null,
        provisional_mbid: matching.provisional_mbid || null,
        tie_break_reason: matching.tie_break_reason,
      };
    }
    if (matching.matched) {
      lookup = lookupMap.get(matching.mbid) || null;
      if (!lookup && providerClient.getAttemptCounts().total < maxAttempts) {
        lookup = await providerClient.lookupRecording({
          mbid: matching.mbid,
          searchCandidate: search.candidates.find(item => item.mbid.toLowerCase() === matching.mbid),
        });
      } else if (!lookup) lookup = { status: "budget_exhausted" };
      if (song.original_key === null || typeof song.original_key === "string" && !song.original_key.trim() || song.bpm === null) {
        if (providerClient.getAttemptCounts().total < maxAttempts && lookup?.status !== "budget_exhausted") {
          audio = await providerClient.getAcousticBrainzEvidence({ mbid: matching.mbid });
        } else audio = { status: "budget_exhausted", submission_enumeration: "unknown" };
      }
    }
    songs.push(buildSongReport(song, search, matching, lookup, audio, baseline.definitions, richnessComparison, lookupMap));
    budgetExhausted = providerClient.getAttemptCounts().total >= maxAttempts ||
      [search?.status, lookup?.status, audio?.status].includes("budget_exhausted");
  }
  const counts = providerClient.getAttemptCounts();
  return {
    report_version: REPORT_VERSION,
    report_type: "existing_song_enrichment_read_only_preview",
    baseline: {
      captured_at: baselineCapturedAt,
      warning: "Database values and versions are a captured baseline and may become stale before any later review or apply.",
    },
    selected_song_ids: [...songIds].sort((a, b) => a - b),
    limits: {
      total_outbound_attempts: maxAttempts,
      per_attempt_timeout_ms: 10_000,
      maximum_response_bytes: MAX_RESPONSE_BYTES,
      maximum_retry_after_ms: 5_000,
      maximum_retries_per_request: 1,
      gemini_calls: 0,
      cache_hits: 0,
    },
    attempts: { total: counts.total, gemini: 0, ledger: providerClient.getRunLedger() },
    songs,
    approved_apply_manifest: null,
    database_writes: false,
  };
}

function renderSummary(report) {
  const lines = [
    "Existing song enrichment preview (read only)",
    `Captured baseline: ${report.baseline.captured_at}`,
    `Songs: ${report.selected_song_ids.join(", ")}`,
    `Outbound attempts: ${report.attempts.total}/${report.limits.total_outbound_attempts}; Gemini calls: 0; cache hits: 0`,
    "No approved apply manifest was created. No database writes were performed.",
    report.baseline.warning,
    "",
  ];
  for (const song of report.songs) {
    if (lines.length >= SUMMARY_LIMIT) break;
    lines.push(`${song.song_id} — ${song.identity.title} / ${song.identity.artist} — ${song.base_version}`);
    if (song.recording) {
      const match = song.recording.match;
      lines.push(`  Recording: ${match.matched ? `matched ${match.mbid}` : `abstained (${match.reason})`}; search ${song.recording.search_status}.`);
      if (match.selection_reason) {
        const exclusions = Object.entries(match.exclusion_counts || {}).map(([reason, count]) => `${reason}=${count}`);
        lines.push(`  Recording selection: ${match.selection_reason}; eligible ${match.eligible_candidate_count}; earliest year ${match.earliest_year ?? "unknown"}; missing dates ${match.missing_release_date_count}; exclusions ${exclusions.length ? exclusions.join(", ") : "none"}.`);
        if (match.same_year_candidate_mbids.length > 1) {
          const counts = match.matched_genre_counts.map(row => `${row.mbid}=${row.matched_genre_count === null ? "unknown" : row.matched_genre_count}`).join(", ");
          lines.push(`  Earliest-year tied candidates: ${match.same_year_candidate_mbids.join(", ")}; managed Genre counts ${counts || "not assessed"}; richness comparison ${match.richness_comparison_complete ? "complete" : "incomplete"}; ${match.matched ? `selected ${match.mbid}` : `provisional ${match.provisional_mbid || "none"}`}; tie-break ${match.tie_break_reason || "none"}.`);
        }
      }
      if (match.matched && match.release_date_evidence) {
        const release = match.release_date_evidence;
        lines.push(`  Selected release-date evidence: ${release.first_release_date ?? "missing"}; response digest ${release.response_digest}.`);
      }
      if (song.recording.release_date_comparison?.lookup_year_discrepancy) {
        const lookupYears = song.recording.release_date_comparison.lookup_release_year_evidence
          .filter(item => item.origin === "lookup_response").map(item => item.value);
        lines.push(`  Search/lookup release-year discrepancy: search ${song.recording.release_date_comparison.search_year}; lookup ${lookupYears.join(", ")}. Both sources are retained separately.`);
      }
    }
    if (song.recording?.pagination) {
      const pagination = song.recording.pagination;
      lines.push(`  Search responses: ${pagination.validated_pages}/${pagination.attempted_pages} validated/attempted; total ${pagination.expected_total === null ? "unknown" : pagination.expected_total}; collected ${pagination.collected_count}; ${pagination.complete ? "complete" : "incomplete"}.`);
      if (song.recording.validation_diagnostic) {
        const diagnostic = song.recording.validation_diagnostic;
        const details = ["requested_offset", "returned_offset", "current_page_total",
          "returned_recording_count", "validated_response_count", "collected_record_count"]
          .filter(key => Object.hasOwn(diagnostic, key)).map(key => `${key}=${typeof diagnostic[key] === "object" ? JSON.stringify(diagnostic[key]) : diagnostic[key]}`);
        if (diagnostic.code === "duplicate_recording_identity" && diagnostic.duplicate_mbid) {
          details.push(`duplicate_mbid=${diagnostic.duplicate_mbid}`, `duplicate_scope=${diagnostic.duplicate_scope}`,
            `first_occurrence=${JSON.stringify(diagnostic.first_occurrence)}`,
            `repeated_occurrence=${JSON.stringify(diagnostic.repeated_occurrence)}`);
        }
        lines.push(`  Search validation: ${diagnostic.code}${details.length ? ` (${details.join(", ")})` : ""}.`);
      }
    }
    if (song.scalar_candidates) {
      if (song.acousticbrainz && song.acousticbrainz.submission_enumeration !== "not_requested") {
        const audio = song.acousticbrainz;
        const agreement = audio.agreement_scope === "sampled_submissions" && audio.sampled_offsets.length > 0
          ? "agreement scope: sampled submissions only" : "no successful samples; no agreement claim";
        lines.push(`  AcousticBrainz: ${audio.status}; submissions ${audio.submission_count ?? "unknown"}; planned offsets ${(audio.planned_sample_offsets || []).join(", ") || "none"}; attempted offsets ${(audio.attempted_offsets || []).join(", ") || "none"}; sampled offsets ${(audio.sampled_offsets || []).join(", ") || "none"}; enumeration ${audio.submission_enumeration}; ${agreement}.`);
        const strengths = song.scalar_candidates.original_key.candidates
          .filter(item => Number.isFinite(item.key_strength))
          .map(item => `${item.submission_id || "submission unknown"}=${item.key_strength} (${item.response_digest})`);
        if (strengths.length) lines.push(`  Original-key strength evidence: ${strengths.join(", " )}.`);
      }
      for (const field of SCALAR_FIELDS) {
        const result = song.scalar_candidates[field];
        lines.push(`  ${field}: ${result.state}${result.conflicting_evidence ? " (conflicting evidence)" : ""}; ${result.candidates.length} candidate(s).`);
      }
      lines.push(`  Genre additions: ${song.tag_candidates.genres.unapproved_additions.length}; unknown: ${song.tag_candidates.genres.unknown_labels.length}; ambiguous: ${song.tag_candidates.genres.ambiguous_labels.length}.`);
      lines.push(`  Vibe additions: ${song.tag_candidates.vibes.unapproved_additions.length}; unknown: ${song.tag_candidates.vibes.unknown_labels.length}; ambiguous: ${song.tag_candidates.vibes.ambiguous_labels.length}.`);
    } else lines.push(`  State: ${song.state} (${song.reason}).`);
  }
  return `${lines.join("\n")}\n`;
}

async function writeReportFiles(directory, report, filesystem = fs) {
  const created = [];
  try {
    const jsonPath = path.join(directory, "report-v1.json");
    const summaryPath = path.join(directory, "summary.txt");
    for (const [target, content] of [[jsonPath, `${JSON.stringify(report, null, 2)}\n`], [summaryPath, renderSummary(report)]]) {
      const handle = await filesystem.open(target, "wx", 0o600);
      created.push(target);
      try { await handle.writeFile(content, "utf8"); } finally { await handle.close(); }
    }
    return { jsonPath, summaryPath };
  } catch {
    let cleanupFailed = false;
    for (const file of created) {
      try { await filesystem.unlink(file); } catch { cleanupFailed = true; }
    }
    throw cleanupFailed
      ? cliError("Preview artifact writing failed and an owned partial file could not be removed.", "ARTIFACT_CLEANUP_FAILED")
      : cliError("Could not exclusively write the private preview report; owned partial files were removed.", "ARTIFACT_WRITE_FAILED");
  }
}

function providerConfig(args) {
  return {
    maxAttempts: args.maxAttempts,
    maxGeminiAttempts: 1,
    timeoutMs: 10_000,
    maxResponseBytes: MAX_RESPONSE_BYTES,
    geminiMaxInputBytes: 32_000,
    geminiMaxOutputTokens: 1_000,
    maxRetryAfterMs: 5_000,
    retryDelayMs: 1_000,
    musicBrainzMinimumIntervalMs: 1_000,
    musicBrainzUserAgent: args.musicBrainzUserAgent,
    model: DEFAULTS.model,
  };
}

async function main(argv = process.argv.slice(2), dependencies = {}) {
  const args = parseArguments(argv);
  const databaseUrl = validateDatabaseUrl(dependencies.databaseUrl ?? process.env.DATABASE_URL);
  const filesystem = dependencies.filesystem || fs;
  const outputDirectory = await createOutputDirectory(args.outputDirectory, {
    ...dependencies.outputOptions,
    filesystem,
  });
  let pool = dependencies.pool;
  let report;
  let failure = null;
  try {
    if (!pool) {
      const { Pool } = require("../server/node_modules/pg");
      pool = new Pool({ connectionString: databaseUrl, connectionTimeoutMillis: 5_000,
        options: "-c default_transaction_read_only=on" });
    }
    const providerClient = dependencies.providerClient || createSongEnrichmentProviderClient({
      transport: createSongEnrichmentTransport(), config: providerConfig(args), cache: null,
    });
    report = await runPreview({ pool, providerClient, songIds: args.songIds, maxAttempts: args.maxAttempts,
      clock: dependencies.clock || Date.now });
  } catch (error) {
    failure = error;
  } finally {
    if (pool && typeof pool.end === "function") {
      try { await pool.end(); }
      catch (error) { if (!failure) failure = error; }
    }
  }
  if (failure) {
    try { await filesystem.rmdir(outputDirectory); } catch { /* never remove unowned files */ }
    if (failure?.safeMessage) throw failure;
    throw cliError("Read-only enrichment preview failed before report creation.", "PREVIEW_FAILED");
  }
  try {
    const artifacts = await writeReportFiles(outputDirectory, report, filesystem);
    return { report, artifacts };
  } catch (error) {
    try { await filesystem.rmdir(outputDirectory); } catch { /* retain any unowned files in this path */ }
    throw error;
  }
}

if (require.main === module) {
  main().then(({ artifacts }) => {
    process.stdout.write(`Preview report written to ${artifacts.jsonPath}\nSummary written to ${artifacts.summaryPath}\n`);
  }).catch(error => {
    process.stderr.write(`${error.safeMessage || "Read-only enrichment preview failed."}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  buildSongReport,
  captureReadOnlyBaseline,
  checkIgnoredDirectory,
  createOutputDirectory,
  main,
  parseArguments,
  providerConfig,
  renderSummary,
  runPreview,
  validateDatabaseUrl,
  writeReportFiles,
};
