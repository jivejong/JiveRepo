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
  release_year: new Set(["MusicBrainz"]),
  original_key: new Set(["AcousticBrainz"]),
  bpm: new Set(["AcousticBrainz"]),
};

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

function hasInterpretiveVersion(candidate) {
  return ["version", "disambiguation"].some(key =>
    typeof candidate?.[key] === "string" ? candidate[key].trim() !== "" : candidate?.[key] != null);
}

function resolveRecordingCandidate(identity, candidates) {
  const title = normalizeIdentityPart(identity?.title);
  const artist = normalizeIdentityPart(identity?.artist);
  if (!title || !artist || !Array.isArray(candidates)) {
    return { matched: false, reason: "invalid_identity_or_candidates" };
  }

  const exact = candidates.filter(candidate =>
    normalizeIdentityPart(candidate?.title) === title &&
    normalizeIdentityPart(candidate?.artist) === artist);
  if (exact.length === 0) return { matched: false, reason: "no_exact_match" };
  if (exact.length > 1) return { matched: false, reason: "multiple_exact_matches" };

  const candidate = exact[0];
  if (hasInterpretiveVersion(candidate)) {
    return { matched: false, reason: "ambiguous_version_information" };
  }
  if (typeof candidate.mbid !== "string" || !UUID.test(candidate.mbid)) {
    return { matched: false, reason: "invalid_mbid" };
  }
  return { matched: true, reason: null, mbid: candidate.mbid };
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
  return { source: item.source, response_digest: item.response_digest };
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
  const match = valid.find(item => sameRawValue(item.raw, selected.value) &&
    item.reference.source === selected.source &&
    item.reference.response_digest === selected.response_digest);
  if (selectedValue === null || !match) {
    diagnostics.push(diagnostic("selection_not_in_evidence", null, field));
    return null;
  }
  return {
    field,
    before: song[field],
    after: selectedValue,
    evidence: { source: match.reference.source, response_digest: match.reference.response_digest },
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
  if (typeof evidenceMbid !== "string" || !UUID.test(evidenceMbid) ||
      typeof approvedMbid !== "string" || approvedMbid !== evidenceMbid) {
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
