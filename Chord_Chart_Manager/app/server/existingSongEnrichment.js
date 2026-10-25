const { definitionKey, normalizeDefinitionName } = require("./tagDefinitionNames");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const VERSION = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{6})Z$/;
const NUMERIC_FIELDS = new Set(["release_year", "bpm"]);
const SCALAR_FIELDS = ["release_year", "original_key", "bpm"];
const KEYS = new Set([
  "C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#",
  "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb",
]);
const SUPPORTED_KEYS = new Set([...KEYS, ...[...KEYS].map(key => `${key}m`)]);
const SOURCES = new Set(["MusicBrainz", "AcousticBrainz", "Gemini"]);
const SCALAR_SOURCES = {
  release_year: new Set(["MusicBrainz", "Gemini"]),
  original_key: new Set(["AcousticBrainz", "Gemini"]),
  bpm: new Set(["AcousticBrainz", "Gemini"]),
};
const GEMINI_MODEL_ID = "gemini-3.1-flash-lite";
const GEMINI_PROMPT_VERSIONS = new Set(["missing-metadata-fallback-v1", "missing-metadata-fallback-v2"]);
const GEMINI_SCHEMA_VERSIONS = new Set(["missing-metadata-schema-v1", "missing-metadata-schema-v2"]);

/*
 * Pure preview input contract:
 * songSnapshot = { id, updated_at, release_year, original_key, bpm,
 *                  genres: [{id,name,is_primary}], vibes: [{id,name}] }
 * evidence = { mbid, scalars: { field: [{value,source,response_digest}] },
 *              genres: [{name,source,response_digest}],
 *              vibes: [{name,source,response_digest}] }
 * reviewDecision = { mbid, scalars: { field: {value,source,response_digest} },
 *                    genres: [label], vibes: [label], primaryGenre?: label }
 * definitions = { genres: [{id,name}], vibes: [{id,name}] }
 *
 * Tag names use tagDefinitionNames.js normalization. Numeric candidates are
 * JS numbers; key candidates are application key strings or AcousticBrainz
 * {key_key,key_scale} pairs. Only source names in SOURCES and short opaque
 * response digest references can enter the preview output.
 */

function codedError(code, message) {
  const error = new TypeError(message);
  error.code = code;
  return error;
}

function normalizeIdentityPart(value) {
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/\s+/g, " ");
  return normalized ? normalized.toLocaleLowerCase() : null;
}

const VERSION_EXCLUSION_MARKERS = [
  ["live_version", /\blive\b/i],
  ["remix_version", /\bremix(?:ed)?\b/i],
  ["acoustic_version", /\bacoustic\b/i],
  ["demo_version", /\bdemo\b/i],
  ["music_video_version", /\bmusic(?:[-\u2010-\u2015\u2212]|\s+)video\b/i],
  ["rerecorded_version", /\bre[-\u2010-\u2015\u2212]?record(?:ed|ing)\b/i],
  ["alternate_version", /\b(?:alternate|alternative|alt)(?:\s+(?:version|recording|take|mix))?\b/i],
];
const SELECTION_REASONS = Object.freeze({
  unique: "unique_exact_match",
  earliest: "earliest_release_year",
  genreRichness: "genre_richness",
  canonicalMbid: "canonical_mbid_tiebreak",
  richnessIncomplete: "richness_comparison_incomplete",
  missingDate: "no_dated_eligible_recording",
  unresolvedVersion: "unresolved_version_ambiguity",
  noEligible: "no_eligible_recording",
});

function titleVersionQualifiers(title) {
  const qualifiers = [];
  for (const match of title.matchAll(/\(([^()]*)\)|\[([^\[\]]*)\]/g)) qualifiers.push(match[1] ?? match[2]);
  const separatedSuffix = /\s[-\u2013\u2014:]\s*([^\n]+)$/.exec(title);
  if (separatedSuffix) qualifiers.push(separatedSuffix[1]);
  const trailingVersionMarker = /(?:^|\s)(live|remix(?:ed)?|acoustic|demo|music(?:[-\u2010-\u2015\u2212]|\s+)video|re[-\u2010-\u2015\u2212]?record(?:ed|ing)|(?:alternate|alternative|alt)(?:\s+(?:version|recording|take|mix))?)\s*$/i.exec(title);
  if (trailingVersionMarker) qualifiers.push(trailingVersionMarker[1]);
  return qualifiers;
}

function classifyRecordingVersion(candidate) {
  const titleQualifiers = titleVersionQualifiers(candidate.title);
  const disambiguation = typeof candidate.disambiguation === "string" ? candidate.disambiguation.trim() : "";
  const version = typeof candidate.version === "string" ? candidate.version.trim() : "";
  const malformedVersionText = candidate.disambiguation != null && typeof candidate.disambiguation !== "string" ||
    candidate.version != null && typeof candidate.version !== "string";
  const markedText = [...titleQualifiers, disambiguation, version].join(" ");
  for (const [reason, marker] of VERSION_EXCLUSION_MARKERS) {
    if (marker.test(markedText)) return { eligible: false, reason };
  }
  if (malformedVersionText || version && !/^(?:studio|studio recording)$/i.test(version) ||
      disambiguation && !/^(?:studio|studio recording)$/i.test(disambiguation)) {
    return { eligible: false, reason: "unresolved_version_ambiguity" };
  }
  return { eligible: true, reason: null };
}

function releaseDateRange(value) {
  if (typeof value !== "string") return null;
  const match = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = match[2] === undefined ? null : Number(match[2]);
  const day = match[3] === undefined ? null : Number(match[3]);
  if (year < 1) return null;
  if (month === null) return { start: year * 10000 + 101, end: year * 10000 + 1231 };
  if (month < 1 || month > 12) return null;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (day === null) return { start: year * 10000 + month * 100 + 1, end: year * 10000 + month * 100 + days[month - 1] };
  if (day < 1 || day > days[month - 1]) return null;
  const exact = year * 10000 + month * 100 + day;
  return { start: exact, end: exact };
}

function isCompleteSearchResult(searchResult, candidates) {
  if (!searchResult || !["ok", "absent"].includes(searchResult.status) || !Array.isArray(candidates)) return false;
  const pagination = searchResult.pagination;
  return pagination?.complete === true && Number.isSafeInteger(pagination.expected_total) &&
    pagination.expected_total >= 0 && pagination.expected_total <= 100 &&
    pagination.expected_total === candidates.length && pagination.collected_count === candidates.length &&
    Number.isSafeInteger(pagination.attempted_pages) && pagination.attempted_pages >= 1 && pagination.attempted_pages <= 4 &&
    Number.isSafeInteger(pagination.validated_pages) && pagination.validated_pages >= 1 &&
    pagination.validated_pages === pagination.attempted_pages &&
    (searchResult.status !== "absent" || candidates.length === 0);
}

function genreDefinitionIndex(definitions) {
  if (!Array.isArray(definitions)) return null;
  const grouped = new Map();
  const ids = new Set();
  try {
    for (const definition of definitions) {
      if (!definition || !Number.isInteger(definition.id) || definition.id < 1 || ids.has(definition.id) ||
          typeof definition.name !== "string") return null;
      ids.add(definition.id);
      const key = definitionKey(normalizeDefinitionName(definition.name, "Genre"));
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(definition.id);
    }
  } catch { return null; }
  return grouped;
}

function lookupForMbid(lookups, mbid) {
  if (lookups instanceof Map) return lookups.get(mbid) ?? lookups.get(mbid.toLowerCase()) ?? null;
  if (lookups && typeof lookups === "object" && !Array.isArray(lookups)) return lookups[mbid.toLowerCase()] ?? null;
  return null;
}

function countMatchedGenres(lookup, mbid, definitionsByKey) {
  if (!lookup || lookup.status !== "ok" || typeof lookup.recording?.mbid !== "string" ||
      lookup.recording.mbid.toLowerCase() !== mbid.toLowerCase() ||
      !Array.isArray(lookup.recording.genres) || !definitionsByKey) return null;
  const matchedIds = new Set();
  for (const item of lookup.recording.genres) {
    if (!item || item.association !== "genre" || item.source !== "MusicBrainz" || typeof item.name !== "string" ||
        typeof item.response_digest !== "string" || !/^[a-f0-9]{64}$/i.test(item.response_digest)) continue;
    try {
      const matches = definitionsByKey.get(definitionKey(normalizeDefinitionName(item.name, "Genre"))) || [];
      if (matches.length === 1) matchedIds.add(matches[0]);
    } catch { /* Unknown/invalid labels do not add richness. */ }
  }
  return matchedIds.size;
}

function resolveRecordingCandidate(identity, candidates, searchResult = null, genreDefinitions = null, ranking = {}) {
  const title = normalizeIdentityPart(identity?.title);
  const artist = normalizeIdentityPart(identity?.artist);
  if (!title || !artist || !Array.isArray(candidates)) {
    return { matched: false, reason: "invalid_identity_or_candidates", selection_reason: null,
      eligible_candidate_count: 0, exclusion_counts: {}, missing_release_date_count: 0, earliest_year: null,
      same_year_candidate_mbids: [], matched_genre_counts: [], richness_comparison_complete: false,
      provisional_mbid: null, tie_break_reason: null };
  }

  if (searchResult !== null && !isCompleteSearchResult(searchResult, candidates)) {
    return { matched: false, reason: "incomplete_search_results", selection_reason: null,
      eligible_candidate_count: 0, exclusion_counts: {}, missing_release_date_count: 0, earliest_year: null,
      same_year_candidate_mbids: [], matched_genre_counts: [], richness_comparison_complete: false,
      provisional_mbid: null, tie_break_reason: null };
  }

  const exact = candidates.filter(candidate =>
    normalizeIdentityPart(candidate?.title) === title &&
    normalizeIdentityPart(candidate?.artist) === artist);
  const counts = new Map();
  const eligible = [];
  const exclude = reason => counts.set(reason, (counts.get(reason) || 0) + 1);
  for (const candidate of exact) {
    if (!candidate || typeof candidate.mbid !== "string" || !UUID.test(candidate.mbid)) {
      exclude("invalid_mbid");
      continue;
    }
    const version = classifyRecordingVersion(candidate);
    if (!version.eligible) {
      exclude(version.reason);
      continue;
    }
    eligible.push({ candidate, dateRange: releaseDateRange(candidate.first_release_date) });
  }

  const exclusionCounts = Object.fromEntries([...counts.entries()].sort(([left], [right]) => left.localeCompare(right, "en")));
  const canonicalMbid = candidate => candidate.mbid.toLowerCase();
  const result = (matched, reason, selectionReason, details = {}) => ({
    matched, reason, selection_reason: selectionReason, eligible_candidate_count: eligible.length,
    exclusion_counts: exclusionCounts,
    missing_release_date_count: eligible.filter(item => !item.dateRange).length,
    earliest_year: details.earliestYear ?? null,
    same_year_candidate_mbids: details.sameYearCandidates?.map(item => canonicalMbid(item.candidate)).sort() || [],
    matched_genre_counts: details.genreCounts || [],
    richness_comparison_complete: details.richnessComplete ?? false,
    provisional_mbid: details.provisional?.candidate ? canonicalMbid(details.provisional.candidate) : null,
    tie_break_reason: details.tieBreakReason ?? null,
    ...(details.selected ? {
      mbid: canonicalMbid(details.selected.candidate),
      release_date_evidence: {
        first_release_date: typeof details.selected.candidate.first_release_date === "string" && details.selected.dateRange
          ? details.selected.candidate.first_release_date : null,
        response_digest: typeof details.selected.candidate.response_digest === "string" ? details.selected.candidate.response_digest : null,
      },
    } : {}),
    ...(details.provisional ? { provisional_release_date_evidence: {
      first_release_date: typeof details.provisional.candidate.first_release_date === "string" && details.provisional.dateRange
        ? details.provisional.candidate.first_release_date : null,
      response_digest: typeof details.provisional.candidate.response_digest === "string" ? details.provisional.candidate.response_digest : null,
    } } : {}),
  });

  if (eligible.length === 0) {
    const hasUnresolved = counts.has("unresolved_version_ambiguity");
    return result(false, hasUnresolved ? "unresolved_version_ambiguity" : exact.length === 0 ? "no_exact_match" : "no_eligible_recording",
      hasUnresolved ? SELECTION_REASONS.unresolvedVersion : SELECTION_REASONS.noEligible);
  }

  const dated = eligible.filter(item => item.dateRange);
  if (dated.length === 0) return result(false, "missing_or_invalid_release_evidence", SELECTION_REASONS.missingDate);
  const earliestYear = Math.min(...dated.map(item => Math.floor(item.dateRange.start / 10000)));
  const sameYear = dated.filter(item => Math.floor(item.dateRange.start / 10000) === earliestYear)
    .sort((left, right) => canonicalMbid(left.candidate) < canonicalMbid(right.candidate) ? -1 :
      canonicalMbid(left.candidate) > canonicalMbid(right.candidate) ? 1 : 0);
  if (sameYear.length === 1) {
    const selected = sameYear[0];
    return result(true, null, eligible.length === 1 ? SELECTION_REASONS.unique : SELECTION_REASONS.earliest,
      { selected, earliestYear, sameYearCandidates: sameYear, richnessComplete: true,
        tieBreakReason: eligible.length === 1 ? "unique_dated_candidate" : "earliest_release_year" });
  }

  const definitionsByKey = genreDefinitionIndex(genreDefinitions);
  const lookups = ranking?.lookups;
  const genreCounts = sameYear.map(item => {
    const mbid = canonicalMbid(item.candidate);
    const count = countMatchedGenres(lookupForMbid(lookups, mbid), mbid, definitionsByKey);
    return { mbid, matched_genre_count: count };
  });
  const richnessComplete = genreCounts.every(item => item.matched_genre_count !== null);
  const provisional = sameYear[0];
  if (!richnessComplete) return result(false, "genre_richness_incomplete", SELECTION_REASONS.richnessIncomplete,
    { earliestYear, sameYearCandidates: sameYear, genreCounts, richnessComplete: false, provisional,
      tieBreakReason: "canonical_mbid_provisional" });

  const maxRichness = Math.max(...genreCounts.map(item => item.matched_genre_count));
  const richest = sameYear.filter(item => genreCounts.find(row => row.mbid === canonicalMbid(item.candidate)).matched_genre_count === maxRichness);
  const selected = richest[0];
  const selectionReason = richest.length === 1 ? SELECTION_REASONS.genreRichness : SELECTION_REASONS.canonicalMbid;
  return result(true, null, selectionReason, { selected, earliestYear, sameYearCandidates: sameYear,
    genreCounts, richnessComplete: true,
    tieBreakReason: richest.length === 1 ? "matched_managed_genre_richness" : "canonical_mbid" });
}

function validVersion(value) {
  if (typeof value !== "string") return false;
  const parts = VERSION.exec(value);
  if (!parts) return false;
  const [, year, month, day, hour, minute, second] = parts;
  const y = Number(year);
  const m = Number(month);
  const d = Number(day);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return y >= 1 && m >= 1 && m <= 12 && d >= 1 && d <= monthDays[m - 1] &&
    Number(hour) <= 23 && Number(minute) <= 59 && Number(second) <= 59;
}

function diagnostic(code, category = null, field = null, label = null) {
  return { code, category, field, label };
}

function compareStableStrings(left, right) {
  const collation = left.localeCompare(right, "en");
  return collation || (left === right ? 0 : left < right ? -1 : 1);
}

function compareDiagnostics(left, right) {
  return ["code", "category", "field", "label"].map(key =>
    compareStableStrings(String(left[key] ?? ""), String(right[key] ?? "")))
    .find(result => result !== 0) ?? 0;
}

function evidenceReference(item, allowedSources = SOURCES) {
  if (!item || typeof item.source !== "string" || !allowedSources.has(item.source) ||
      typeof item.response_digest !== "string" ||
      !/^[A-Za-z0-9._:-]{1,160}$/.test(item.response_digest)) return null;
  if (item.source === "Gemini") {
    if (!/^[a-f0-9]{64}$/.test(item.response_digest) || item.model_id !== GEMINI_MODEL_ID ||
        !GEMINI_PROMPT_VERSIONS.has(item.prompt_version) || !GEMINI_SCHEMA_VERSIONS.has(item.schema_version)) return null;
    return { source: item.source, response_digest: item.response_digest, model_id: item.model_id,
      prompt_version: item.prompt_version, schema_version: item.schema_version };
  }
  if (item.submission_id !== undefined && (typeof item.submission_id !== "string" ||
      item.source === "AcousticBrainz" && !/^[0-9a-f-]{36}:\d+$/i.test(item.submission_id) ||
      item.source === "MusicBrainz" && !/^recording:[0-9a-f-]{36}$/i.test(item.submission_id) || item.source === "Gemini")) return null;
  return { source: item.source, response_digest: item.response_digest,
    ...(item.source !== "Gemini" && item.submission_id ? { submission_id: item.submission_id.toLowerCase() } : {}) };
}

function geminiOnlyEvidence(evidence) {
  const items = [
    ...SCALAR_FIELDS.flatMap(field => Array.isArray(evidence?.scalars?.[field]) ? evidence.scalars[field] : []),
    ...(Array.isArray(evidence?.genres) ? evidence.genres : []),
    ...(Array.isArray(evidence?.vibes) ? evidence.vibes : []),
  ];
  return items.length > 0 && items.every(item => item?.source === "Gemini" && evidenceReference(item) !== null);
}

function normalizedScalarValue(field, value) {
  if (field === "release_year") {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 32767) return null;
    return value;
  }
  if (field === "bpm") {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
    const rounded = Math.floor(value + 0.5);
    return rounded >= 1 && rounded <= 32767 ? rounded : null;
  }
  if (field === "original_key") {
    let key = null;
    if (typeof value === "string") {
      key = value.trim();
    } else if (value && typeof value === "object" && !Array.isArray(value) &&
        typeof value.key_key === "string" && typeof value.key_scale === "string") {
      const root = value.key_key.trim();
      const scale = value.key_scale.trim().toLowerCase();
      if (KEYS.has(root) && (scale === "major" || scale === "minor")) {
        key = scale === "minor" ? `${root}m` : root;
      }
    }
    return key && SUPPORTED_KEYS.has(key) ? key : null;
  }
  return null;
}

function sameRawValue(left, right) {
  if (typeof left === "number" && typeof right === "number") return Object.is(left, right);
  if (typeof left === "string" && typeof right === "string") return left === right;
  if (left && right && typeof left === "object" && typeof right === "object") {
    return left.key_key === right.key_key && left.key_scale === right.key_scale;
  }
  return left === right;
}

function currentScalarIsEmpty(song, field, diagnostics) {
  if (!Object.hasOwn(song, field) || song[field] === undefined) {
    diagnostics.push(diagnostic("missing_existing_field", null, field));
    return false;
  }
  const value = song[field];
  if (NUMERIC_FIELDS.has(field)) {
    if (value === null) return true;
    if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 32767) {
      diagnostics.push(diagnostic("malformed_existing_value", null, field));
    }
    return false;
  }
  if (value === null) return true;
  if (typeof value === "string") return value.trim() === "";
  diagnostics.push(diagnostic("malformed_existing_value", null, field));
  return false;
}

function listEvidenceValues(values) {
  if (values === undefined || values === null) return [];
  return Array.isArray(values) ? values : [values];
}

function valueFingerprint(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return JSON.stringify({ key_key: value.key_key, key_scale: value.key_scale });
  }
  return `${typeof value}:${String(value)}`;
}

function resolveScalar(field, song, evidence, reviewDecision, diagnostics) {
  if (!currentScalarIsEmpty(song, field, diagnostics)) return null;
  const candidates = listEvidenceValues(evidence.scalars?.[field]);
  const selected = reviewDecision.scalars?.[field];
  if (candidates.length === 0) {
    if (selected !== undefined) diagnostics.push(diagnostic("selection_not_in_evidence", null, field));
    return null;
  }

  const valid = [];
  for (const item of candidates) {
    const reference = evidenceReference(item, SCALAR_SOURCES[field]);
    const after = item && normalizedScalarValue(field, item.value);
    if (!reference || after === null) {
      diagnostics.push(diagnostic("invalid_candidate", null, field));
      continue;
    }
    valid.push({ raw: item.value, after, reference });
  }

  const distinctValues = new Set(valid.map(item => valueFingerprint(item.after)));
  if (distinctValues.size > 1 && selected === undefined) {
    diagnostics.push(diagnostic("conflicting_evidence_unresolved", null, field));
    return null;
  }
  if (selected === undefined) {
    diagnostics.push(diagnostic("candidate_unapproved", null, field));
    return null;
  }

  const selectedValue = normalizedScalarValue(field, selected.value);
  const match = valid.find(item => sameRawValue(item.raw, selected.value) && item.reference.source === selected.source &&
    item.reference.response_digest === selected.response_digest &&
    (item.reference.submission_id === undefined || selected.submission_id === item.reference.submission_id) &&
    ["model_id", "prompt_version", "schema_version"].every(key => selected[key] === undefined || selected[key] === item.reference[key]));
  if (selectedValue === null || !match) {
    diagnostics.push(diagnostic("selection_not_in_evidence", null, field));
    return null;
  }
  return {
    field,
    before: song[field],
    after: selectedValue,
    evidence: { ...match.reference },
  };
}

function canonicalDefinitions(definitions, category, diagnostics) {
  const field = category === "Genre" ? "genres" : "vibes";
  if (!Array.isArray(definitions?.[field])) {
    diagnostics.push(diagnostic("invalid_definition_catalog", category));
    return null;
  }
  const rows = definitions[field];
  const grouped = new Map();
  const seenIds = new Set();
  let invalid = false;
  for (const row of rows) {
    if (!row || !Number.isInteger(row.id) || row.id < 1 || typeof row.name !== "string") {
      diagnostics.push(diagnostic("invalid_definition", category));
      invalid = true;
      continue;
    }
    if (seenIds.has(row.id)) {
      diagnostics.push(diagnostic("invalid_definition", category));
      invalid = true;
      continue;
    }
    seenIds.add(row.id);
    let name;
    try { name = normalizeDefinitionName(row.name, category); }
    catch { diagnostics.push(diagnostic("invalid_definition", category)); invalid = true; continue; }
    const key = definitionKey(name);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push({ id: row.id, name: row.name });
  }
  return invalid ? null : grouped;
}

function currentAssignments(song, category, diagnostics) {
  const field = category === "Genre" ? "genres" : "vibes";
  if (!Array.isArray(song[field])) {
    diagnostics.push(diagnostic("invalid_existing_assignments", category));
    return null;
  }
  const assignments = [];
  let invalid = false;
  for (const row of song[field]) {
    const id = row && row.id;
    const name = row && row.name;
    if (!Number.isInteger(id) || id < 1 || typeof name !== "string") {
      diagnostics.push(diagnostic("invalid_existing_assignment", category));
      invalid = true;
      continue;
    }
    if (category === "Genre" && typeof row.is_primary !== "boolean") {
      diagnostics.push(diagnostic("invalid_existing_primary_state", category));
      invalid = true;
    }
    assignments.push({ id, name, is_primary: category === "Genre" ? row.is_primary : false });
  }
  if (category === "Genre" && assignments.filter(item => item.is_primary).length > 1) {
    diagnostics.push(diagnostic("invalid_existing_primary_state", category));
    invalid = true;
  }
  return invalid ? null : assignments;
}

function evidenceLabels(evidence, category) {
  const key = category === "Genre" ? "genres" : "vibes";
  return Array.isArray(evidence[key]) ? evidence[key] : [];
}

function buildTagAdditions(category, selectedLabels, song, evidence, definitions, diagnostics) {
  const current = currentAssignments(song, category, diagnostics);
  const groupedDefinitions = canonicalDefinitions(definitions, category, diagnostics);
  if (current === null || groupedDefinitions === null) return [];
  const evidenceByKey = new Map();
  for (const item of evidenceLabels(evidence, category)) {
    if (!item || typeof item.name !== "string") {
      diagnostics.push(diagnostic("invalid_tag_evidence", category));
      continue;
    }
    let key;
    try { key = definitionKey(normalizeDefinitionName(item.name, category)); }
    catch { diagnostics.push(diagnostic("invalid_tag_evidence", category)); continue; }
    const reference = evidenceReference(item);
    if (!reference) {
      diagnostics.push(diagnostic("invalid_tag_evidence", category, null, item.name));
      continue;
    }
    if (!evidenceByKey.has(key)) evidenceByKey.set(key, []);
    evidenceByKey.get(key).push({ label: item.name, reference });
  }

  const selectedByKey = new Map();
  for (const label of Array.isArray(selectedLabels) ? selectedLabels : []) {
    if (typeof label !== "string") {
      diagnostics.push(diagnostic("invalid_selected_tag", category));
      continue;
    }
    let key;
    try { key = definitionKey(normalizeDefinitionName(label, category)); }
    catch { diagnostics.push(diagnostic("invalid_selected_tag", category, null, label)); continue; }
    const previous = selectedByKey.get(key);
    if (previous === undefined || compareStableStrings(label, previous) < 0) {
      selectedByKey.set(key, label);
    }
  }

  const currentIds = new Set(current.map(item => item.id));
  const currentNames = new Set();
  for (const item of current) {
    try { currentNames.add(definitionKey(normalizeDefinitionName(item.name, category))); }
    catch { /* Reported by current assignment validation above only for shape. */ }
  }
  for (const [key, labels] of evidenceByKey) {
    const matches = groupedDefinitions.get(key) || [];
    if (matches.length === 0) {
      const label = [...labels.map(item => item.label)].sort(compareStableStrings)[0];
      diagnostics.push(diagnostic("unknown_tag_definition", category, null, label));
    } else if (matches.length > 1) {
      const label = [...labels.map(item => item.label)].sort(compareStableStrings)[0];
      diagnostics.push(diagnostic("ambiguous_tag_definition", category, null, label));
    }
  }
  const additions = [];
  for (const [key, label] of selectedByKey) {
    const matches = groupedDefinitions.get(key) || [];
    const sourced = evidenceByKey.get(key) || [];
    if (!sourced.length) {
      diagnostics.push(diagnostic("tag_selection_not_in_evidence", category, null, label));
      continue;
    }
    if (matches.length !== 1) continue;
    const definition = matches[0];
    if (currentIds.has(definition.id) || currentNames.has(key)) continue;
    const references = [...new Map(sourced.map(item => [
      `${item.reference.source}\u0000${item.reference.response_digest}`,
      item.reference,
    ])).values()].sort((a, b) => compareStableStrings(a.source, b.source) ||
      compareStableStrings(a.response_digest, b.response_digest));
    additions.push({ id: definition.id, name: definition.name, evidence: references });
  }
  return additions.sort((a, b) => a.id - b.id || compareStableStrings(a.name, b.name));
}

function isExplicitNoOpDecision(reviewDecision) {
  return Boolean(reviewDecision && typeof reviewDecision === "object" && !Array.isArray(reviewDecision) &&
    (reviewDecision.mbid === null || reviewDecision.mbid === undefined) &&
    reviewDecision.scalars && typeof reviewDecision.scalars === "object" && !Array.isArray(reviewDecision.scalars) &&
    Object.keys(reviewDecision.scalars).length === 0 &&
    Array.isArray(reviewDecision.genres) && reviewDecision.genres.length === 0 &&
    Array.isArray(reviewDecision.vibes) && reviewDecision.vibes.length === 0 &&
    (reviewDecision.primaryGenre === undefined || reviewDecision.primaryGenre === null) &&
    Object.keys(reviewDecision).every(key => ["mbid", "scalars", "genres", "vibes", "primaryGenre"].includes(key)));
}

function buildEnrichmentPreview(songSnapshot, evidence, reviewDecision, definitions) {
  if (!songSnapshot || typeof songSnapshot !== "object" || Array.isArray(songSnapshot) ||
      !Number.isInteger(songSnapshot.id) || songSnapshot.id < 1) {
    throw codedError("INVALID_SNAPSHOT", "songSnapshot must have a positive integer id");
  }
  if (!validVersion(songSnapshot.updated_at)) {
    throw codedError("INVALID_BASE_VERSION", "updated_at must be an exact six-digit UTC version token");
  }
  const evidenceMbid = evidence?.mbid;
  const approvedMbid = reviewDecision?.mbid;
  const hasRecordingIdentity = typeof evidenceMbid === "string" && UUID.test(evidenceMbid) && approvedMbid === evidenceMbid;
  const geminiOnly = evidenceMbid === null && approvedMbid === null && geminiOnlyEvidence(evidence);
  const explicitNoOp = !hasRecordingIdentity && !geminiOnly && evidenceMbid === null &&
    approvedMbid === null && isExplicitNoOpDecision(reviewDecision);
  if (!hasRecordingIdentity && !geminiOnly && !explicitNoOp) {
    throw codedError("EVIDENCE_NOT_APPROVED", "reviewDecision must approve the exact evidence MBID");
  }

  const diagnostics = [];
  const existingGenres = currentAssignments(songSnapshot, "Genre", diagnostics);
  if (existingGenres === null) {
    const uniqueDiagnostics = [...new Map(diagnostics.map(item => [JSON.stringify(item), item])).values()];
    uniqueDiagnostics.sort(compareDiagnostics);
    return {
      format_version: 1,
      song_id: songSnapshot.id,
      base_version: songSnapshot.updated_at,
      approved_mbid: evidenceMbid,
      scalar_changes: [],
      tag_additions: { genres: [], vibes: [] },
      primary_proposal: null,
      diagnostics: uniqueDiagnostics,
      has_changes: false,
    };
  }
  const scalars = [];
  for (const field of SCALAR_FIELDS) {
    const change = resolveScalar(field, songSnapshot, evidence, reviewDecision, diagnostics);
    if (change && !sameRawValue(change.before, change.after)) scalars.push(change);
  }

  const genreAdditions = buildTagAdditions("Genre", reviewDecision.genres,
    songSnapshot, evidence, definitions, diagnostics);
  const vibeAdditions = buildTagAdditions("Vibe", reviewDecision.vibes,
    songSnapshot, evidence, definitions, diagnostics);

  const existingPrimary = existingGenres.filter(item => item.is_primary);
  let primaryProposal = null;
  if (reviewDecision.primaryGenre !== undefined && reviewDecision.primaryGenre !== null) {
    let selectedKey = null;
    try { selectedKey = definitionKey(normalizeDefinitionName(reviewDecision.primaryGenre, "Genre")); }
    catch { /* Rejected below with a stable diagnostic. */ }
    const allGenreDefinitions = canonicalDefinitions(definitions, "Genre", diagnostics);
    const candidateDefinition = selectedKey && allGenreDefinitions ? allGenreDefinitions.get(selectedKey) || [] : [];
    const resultIds = new Set([...existingGenres.map(item => item.id), ...genreAdditions.map(item => item.id)]);
    if (!allGenreDefinitions || candidateDefinition.length !== 1 || !resultIds.has(candidateDefinition[0].id)) {
      throw codedError("INVALID_PRIMARY_SELECTION", "primaryGenre must identify a unique Genre in the resulting assignment set");
    }
    if (existingPrimary.length > 0) {
      if (existingPrimary.length !== 1 || existingPrimary[0].id !== candidateDefinition[0].id) {
        throw codedError("EXISTING_PRIMARY_PRESERVED", "an existing primary Genre cannot be changed by enrichment");
      }
    } else {
      primaryProposal = { genre_id: candidateDefinition[0].id, name: candidateDefinition[0].name };
    }
  }

  const uniqueDiagnostics = [...new Map(diagnostics.map(item => [JSON.stringify(item), item])).values()];
  uniqueDiagnostics.sort(compareDiagnostics);
  const result = {
    format_version: 1,
    song_id: songSnapshot.id,
    base_version: songSnapshot.updated_at,
    approved_mbid: evidenceMbid,
    scalar_changes: scalars.sort((a, b) => compareStableStrings(a.field, b.field)),
    tag_additions: {
      genres: genreAdditions,
      vibes: vibeAdditions,
    },
    primary_proposal: primaryProposal,
    diagnostics: uniqueDiagnostics,
  };
  result.has_changes = result.scalar_changes.length > 0 || genreAdditions.length > 0 ||
    vibeAdditions.length > 0 || primaryProposal !== null;
  return result;
}

module.exports = { resolveRecordingCandidate, buildEnrichmentPreview };
