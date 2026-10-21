const crypto = require("node:crypto");
const { buildEnrichmentPreview } = require("./existingSongEnrichment.js");
const { definitionKey } = require("./tagDefinitionNames.js");
const { normalizeFactualContext, validateFactualIdentity } = require("../scripts/preview-gemini-fallback.cjs");

const SCALARS = ["release_year", "original_key", "bpm"];
const GEMINI = { model_id: "gemini-3.1-flash-lite", prompt_version: "missing-metadata-fallback-v2",
  schema_version: "missing-metadata-schema-v2" };
const DIGEST = /^[a-f0-9]{64}$/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ROOTS = new Set(["C","B#","C#","Db","D","D#","Eb","E","Fb","E#","F","F#","Gb","G","G#","Ab","A","A#","Bb","B","Cb"]);
const PROMPTS = new Set(["missing-metadata-fallback-v1","missing-metadata-fallback-v2"]);
const SCHEMAS = new Set(["missing-metadata-schema-v1","missing-metadata-schema-v2"]);

function fail(code) { const error = new Error(code); error.code = code; return error; }
function hashBytes(value) { return crypto.createHash("sha256").update(value).digest("hex"); }
function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stableJson(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}
function sameTarget(target, song) { return target?.song_id === (song.id ?? song.song_id) && target?.title === song.title && target?.artist === song.artist; }
function reportEvidence(report, songId, context) {
  const evidence = { mbid: null, scalars: Object.fromEntries(SCALARS.map(field => [field, []])), genres: [], vibes: [] };
  const addScalar = (field, item) => {
    const sourceAllowed=field==="release_year"?["MusicBrainz","Gemini"]:field==="original_key"||field==="bpm"?["AcousticBrainz","Gemini"]:[];
    if (!item || !sourceAllowed.includes(item.source) || !DIGEST.test(item.response_digest || "")) throw fail("EVIDENCE_INVALID");
    const value = field === "original_key" && item.value && typeof item.value === "object"
      ? { key_key: item.value.key_key, key_scale: item.value.key_scale } : item.value;
    if(field==="release_year"&&(!Number.isInteger(value)||value<1||value>32767)||field==="bpm"&&(typeof value!=="number"||!Number.isFinite(value)||value<=0||Math.floor(value+0.5)>32767)||
       field==="original_key"&&!(typeof value==="string"&&/^(?:C|B#|C#|Db|D|D#|Eb|E|Fb|E#|F|F#|Gb|G|G#|Ab|A|A#|Bb|B|Cb)(?:m)?$/.test(value)||value&&ROOTS.has(value.key_key)&&["major","minor"].includes(value.key_scale)))throw fail("EVIDENCE_INVALID");
    const candidate = { value, source: item.source, response_digest: item.response_digest };
    if (item.submission_id || item.submission_reference) candidate.submission_id = item.submission_id || item.submission_reference;
    if (item.source === "Gemini") {
      const provenance={model_id:item.model_id||report.gemini_suggestions?.model_id,prompt_version:item.prompt_version||report.gemini_suggestions?.prompt_version,
        schema_version:item.schema_version||report.gemini_suggestions?.schema_version};
      if(provenance.model_id!==GEMINI.model_id||!PROMPTS.has(provenance.prompt_version)||!SCHEMAS.has(provenance.schema_version))throw fail("EVIDENCE_INVALID");
      Object.assign(candidate,provenance);
    }
    evidence.scalars[field].push(candidate);
  };
  const addLabels = (category, items) => {
    if (!Array.isArray(items)) throw fail("EVIDENCE_INVALID");
    for (const item of items) {
      if (!item || typeof item.name !== "string" || !DIGEST.test(item.response_digest || "") ||
          !["MusicBrainz", "AcousticBrainz", "Gemini"].includes(item.source)) throw fail("EVIDENCE_INVALID");
      const entry = { name: item.name, source: item.source, response_digest: item.response_digest };
      if (item.source === "Gemini") {
        const provenance={model_id:item.model_id||report.gemini_suggestions?.model_id,prompt_version:item.prompt_version||report.gemini_suggestions?.prompt_version,
          schema_version:item.schema_version||report.gemini_suggestions?.schema_version};
        if(provenance.model_id!==GEMINI.model_id||!PROMPTS.has(provenance.prompt_version)||!SCHEMAS.has(provenance.schema_version))throw fail("EVIDENCE_INVALID");
        Object.assign(entry,provenance);
      }
      evidence[category].push(entry);
    }
  };

  if (report?.factual_context_version === 1) {
    const normalized = normalizeFactualContext(report, songId);
    if (!sameTarget(normalized.target, context.target || normalized.target)) throw fail("EVIDENCE_IDENTITY_MISMATCH");
    validateFactualIdentity(normalized, { id: songId, title: context.target?.title || normalized.target.title,
      artist: context.target?.artist || normalized.target.artist });
    evidence.mbid = normalized.recording.mbid;
    for (const field of SCALARS) for (const item of normalized.scalar_candidates[field].candidates) addScalar(field, {
      ...item, submission_id: item.submission_reference,
    });
    return evidence;
  }
  if (report?.report_version === 1 && report?.report_type === "existing_song_enrichment_read_only_preview") {
    if (report.database_writes !== false || report.approved_apply_manifest !== null ||
        !Array.isArray(report.selected_song_ids) || !report.selected_song_ids.includes(songId) || !Array.isArray(report.songs)) {
      throw fail("EVIDENCE_INVALID");
    }
    const item = report.songs.find(row => row.song_id === songId);
    if (!item || item.human_review_required !== true || item.approved_preview !== null ||
        !sameTarget({ song_id: songId, ...item.identity }, context.target)) throw fail("EVIDENCE_IDENTITY_MISMATCH");
    // Actual B4 abstentions have evidence:null. Preserve their unresolved state without
    // manufacturing a recording match, MBID, scalar or tag candidate.
    if (item.evidence === null) {
      if (item.recording?.match?.matched !== false) throw fail("EVIDENCE_INVALID");
      return evidence;
    }
    if (!item.evidence || !UUID.test(item.evidence.mbid || "") || item.recording?.match?.matched !== true ||
        typeof item.recording.match.mbid !== "string" ||
        item.recording.match.mbid.toLowerCase() !== item.evidence.mbid.toLowerCase() || !item.evidence.scalars ||
        !Array.isArray(item.evidence.genres) || !Array.isArray(item.evidence.vibes) ||
        SCALARS.some(field => !Array.isArray(item.evidence.scalars[field]))) throw fail("EVIDENCE_INVALID");
    evidence.mbid = item.evidence.mbid.toLowerCase();
    for (const field of SCALARS) for (const candidate of item.evidence.scalars[field]) addScalar(field, candidate);
    addLabels("genres", item.evidence.genres); addLabels("vibes", item.evidence.vibes);
    return evidence;
  }
  if (report?.format_version === 1 && report?.mode === "explicit_gemini_missing_metadata_fallback") {
    if (report.database_writes !== false || report.approved_apply_manifest !== null || report.selected_song_id !== songId ||
        !sameTarget({ song_id: songId, title: report.baseline?.title, artist: report.baseline?.artist }, context.target)) throw fail("EVIDENCE_IDENTITY_MISMATCH");
    const factual = report.factual_evidence;
    if (factual) {
      if (factual.context_version === 1) {
        const raw={factual_context_version:1,target:factual.target,recording:factual.recording,
          scalar_candidates:Object.fromEntries(SCALARS.map(field=>[field,{status:factual.scalar_candidates?.[field]?.conflicting_evidence?"conflicting":factual.scalar_candidates?.[field]?.candidates?.length?"resolved":"unresolved",
            candidates:factual.scalar_candidates?.[field]?.candidates?.map(c=>({source:c.source,value:c.value,response_digest:c.response_digest,
              submission_reference:c.submission_reference||c.submission_id}))}])),submission_coverage:factual.submission_coverage,conflicts:factual.conflicts};
        const checked=normalizeFactualContext(raw,songId);
        if(!sameTarget(checked.target,context.target))throw fail("EVIDENCE_IDENTITY_MISMATCH");
        validateFactualIdentity(checked,{id:songId,title:context.target.title,artist:context.target.artist});
        evidence.mbid = checked.recording.mbid;
        for (const field of SCALARS) for (const candidate of checked.scalar_candidates[field].candidates) addScalar(field, {
          ...candidate, submission_id: candidate.submission_reference,
        });
      } else if (factual.context_version === "b4-v1") {
        evidence.mbid = report.b1_validation_preview?.approved_mbid || report.recording?.match?.mbid || null;
        for (const field of SCALARS) for (const candidate of factual.scalar_candidates?.[field]?.candidates || []) addScalar(field, {
          ...candidate, submission_id: candidate.submission_id,
        });
        addLabels("genres", factual.genre_evidence || []); addLabels("vibes", factual.vibe_evidence || []);
      } else throw fail("EVIDENCE_INVALID");
    }
    const g = report.gemini_suggestions;
    if (!g || g.status !== "ok" || g.model_id !== GEMINI.model_id || !PROMPTS.has(g.prompt_version) ||
        !SCHEMAS.has(g.schema_version) || !DIGEST.test(g.response_digest || "")) throw fail("EVIDENCE_INVALID");
    if(!g.scalars||SCALARS.some(field=>!Array.isArray(g.scalars[field]))||!Array.isArray(g.genres)||!Array.isArray(g.vibes))throw fail("EVIDENCE_INVALID");
    for (const field of SCALARS) for (const candidate of g.scalars[field]) addScalar(field, candidate);
    addLabels("genres", g.genres); addLabels("vibes", g.vibes);
    return evidence;
  }
  if (report?.report_version === 1 && report?.report_type === "gemini_vibe_only_preview") {
    if (report.database_writes !== false || report.approval_or_apply_manifest !== null || report.song?.id !== songId ||
        report.song.title !== context.target.title || report.song.artist !== context.target.artist || report.result?.status !== "ok" ||
        report.limits?.model !== GEMINI.model_id) throw fail("EVIDENCE_IDENTITY_MISMATCH");
    addLabels("vibes", report.result.vibes.map(item => ({ ...item,
      model_id: item.model_id, prompt_version: item.prompt_version, schema_version: item.schema_version })));
    return evidence;
  }
  if (report?.report_version === 1 && report?.report_type === "gemini_genre_only_preview") {
    const request = report.request;
    const result = report.result;
    if (report.database_writes !== false || report.approval_or_apply_manifest !== null ||
        report.song?.id !== songId || report.song.title !== context.target.title ||
        report.song.artist !== context.target.artist || report.limits?.model !== GEMINI.model_id) {
      throw fail("EVIDENCE_IDENTITY_MISMATCH");
    }
    if (!Array.isArray(request?.requested_scalars) || request.requested_scalars.length !== 0 ||
        !Array.isArray(request.allowed_genres) || !Array.isArray(request.allowed_vibes) || request.allowed_vibes.length !== 0 ||
        !result || result.status !== "ok" || !DIGEST.test(result.response_digest || "") ||
        !Array.isArray(result.genres) || result.genres.length > 3 ||
        result.vibes !== undefined && (!Array.isArray(result.vibes) || result.vibes.length !== 0) ||
        Object.hasOwn(result, "scalars")) throw fail("EVIDENCE_INVALID");
    if (request.allowed_genres.some(name => typeof name !== "string" || !name.trim() || name.length > 120) ||
        new Set(request.allowed_genres).size !== request.allowed_genres.length) throw fail("EVIDENCE_INVALID");
    const allowedGenres = new Set(request.allowed_genres), selectedGenres = new Set();
    for (const item of result.genres) {
      if (!item || item.source !== "Gemini" || typeof item.name !== "string" || item.name.length > 120 || !allowedGenres.has(item.name) ||
          typeof item.response_digest !== "string" || item.response_digest.toLowerCase() !== result.response_digest.toLowerCase() ||
          selectedGenres.has(item.name)) throw fail("EVIDENCE_INVALID");
      selectedGenres.add(item.name);
    }
    addLabels("genres", result.genres.map(item => ({ ...item,
      model_id: item.model_id, prompt_version: item.prompt_version, schema_version: item.schema_version })));
    return evidence;
  }
  // The converter's factual-context wrapper is also accepted when loaded as a saved JSON artifact.
  if (report?.context_version === 1 && report?.target) return reportEvidence({ ...report, factual_context_version: 1 }, songId, context);
  throw fail("EVIDENCE_FORMAT_UNSUPPORTED");
}

function normalizeReports(song, reports) {
  const evidence = { mbid: null, scalars: Object.fromEntries(SCALARS.map(field => [field, []])), genres: [], vibes: [] };
  const sources = [];
  const source_context = [];
  for (const item of reports) {
    const normalized = reportEvidence(item.document, song.id, { target: { song_id: song.id, title: song.title, artist: song.artist } });
    if (normalized.mbid) {
      if (evidence.mbid && evidence.mbid !== normalized.mbid) throw fail("EVIDENCE_IDENTITY_MISMATCH");
      evidence.mbid = normalized.mbid;
    }
    for (const field of SCALARS) for (const candidate of normalized.scalars[field]) {
      if (!evidence.scalars[field].some(current => stableJson(current) === stableJson(candidate))) evidence.scalars[field].push(candidate);
    }
    for (const category of ["genres", "vibes"]) for (const candidate of normalized[category]) {
      if (!evidence[category].some(current => stableJson(current) === stableJson(candidate))) evidence[category].push(candidate);
    }
    sources.push({ path: item.path, sha256: hashBytes(item.bytes), bytes: item.bytes.length });
    const factual = item.document?.factual_context_version === 1 ? item.document
      : item.document?.factual_evidence?.context_version === 1 ? item.document.factual_evidence
        : item.document?.factual_evidence?.context_version === "b4-v1" ? item.document.factual_evidence : null;
    const b4Song = item.document?.songs?.find(row => row.song_id === song.id);
    if (factual) source_context.push({ recording_mbid: factual.recording?.mbid || normalized.mbid,
      submission_coverage: factual.submission_coverage || null,
      conflicts: factual.conflicts || Object.fromEntries(SCALARS.map(field => [field,
        factual.scalar_candidates?.[field]?.conflicting_evidence === true])),
      scalar_states: Object.fromEntries(SCALARS.map(field => [field, factual.scalar_candidates?.[field]?.state ||
        factual.scalar_candidates?.[field]?.status || "unknown"])) });
    else if (b4Song) {
      const tagGroup = category => {
        const group=b4Song.tag_candidates?.[category]||{};
        const clean=key=>Array.isArray(group[key])?group[key].filter(item=>item&&typeof item.label==="string"&&item.label.length<=200&&
          item.evidence&&["MusicBrainz","AcousticBrainz"].includes(item.evidence.source)&&DIGEST.test(item.evidence.response_digest||""))
          .map(item=>({label:item.label,source:item.evidence.source,response_digest:item.evidence.response_digest})):[];
        return {unknown_labels:clean("unknown_labels"),ambiguous_labels:clean("ambiguous_labels")};
      };
      source_context.push({ recording_mbid: b4Song.recording?.match?.mbid || null,
      submission_coverage: b4Song.acousticbrainz ? { source: "AcousticBrainz", total_available: b4Song.acousticbrainz.submission_count ?? null,
        sampled_offsets: b4Song.acousticbrainz.sampled_offsets || [], completeness: b4Song.acousticbrainz.submission_enumeration || "unknown" } : null,
      conflicts: Object.fromEntries(SCALARS.map(field => [field, b4Song.scalar_candidates?.[field]?.conflicting_evidence === true])),
      scalar_states: Object.fromEntries(SCALARS.map(field => [field, b4Song.scalar_candidates?.[field]?.state || "unknown"])),
      tag_diagnostics:{genres:tagGroup("genres"),vibes:tagGroup("vibes")} });
    }
  }
  return { evidence, sources, source_context };
}

function emptyDecision(evidence) {
  return { mbid: null, scalars: {}, genres: [], vibes: [] };
}

function buildDecisionTemplate({ baseline, sourceReports, definitions }) {
  return { decision_file_version: 1,
    targets: baseline.songs.map(song => ({ song_id: song.id, title: song.title, artist: song.artist,
      base_version: song.updated_at, evidence_sha256: sourceReports[song.id].sources.map(x => x.sha256).sort(),
      definitions_sha256: hashBytes(Buffer.from(stableJson(definitions))),
      source_context: sourceReports[song.id].source_context, evidence: sourceReports[song.id].evidence,
      decision: emptyDecision(sourceReports[song.id].evidence) })) };
}

function validateDecisionAgainstEvidence(selection, candidates) {
  return candidates.some(candidate => stableJson(candidate) === stableJson(selection));
}
function resolveSelectedLabels(labels, candidates, catalog, category) {
  if (!Array.isArray(labels) || labels.some(label => typeof label !== "string")) throw fail("DECISIONS_UNEVIDENCED");
  const output = [], selectedKeys = new Set();
  for (const label of labels) {
    let key;
    try { key=definitionKey(label); } catch { throw fail("DECISIONS_UNEVIDENCED"); }
    const evidenced = candidates.some(candidate => {
      try { return definitionKey(candidate.name)===key; } catch { return false; }
    });
    const definitions = catalog.filter(definition => {
      try { return definitionKey(definition.name)===key; } catch { return false; }
    });
    if (!evidenced || definitions.length !== 1 || selectedKeys.has(key)) {
      throw fail(definitions.length > 1 ? "DECISIONS_TAG_AMBIGUOUS" : "DECISIONS_UNEVIDENCED");
    }
    output.push(label);selectedKeys.add(key);
  }
  return output;
}

function buildManifest({ baseline, decisions, sourceReports, operationId, definitions }) {
  if (decisions?.decision_file_version !== 1 || !Array.isArray(decisions.targets) ||
      decisions.targets.length !== baseline.songs.length || !UUID.test(operationId || "")) throw fail("DECISIONS_INVALID");
  const expectedDefinitionsHash = hashBytes(Buffer.from(stableJson(definitions)));
  const songs = baseline.songs.map(song => {
    const record = decisions.targets.find(item => item.song_id === song.id);
    const source = sourceReports[song.id];
    if (!record || record.title !== song.title || record.artist !== song.artist || record.base_version !== song.updated_at ||
        stableJson(record.evidence_sha256) !== stableJson(source.sources.map(x => x.sha256).sort()) || record.definitions_sha256 !== expectedDefinitionsHash ||
        stableJson(record.source_context) !== stableJson(source.source_context) ||
        stableJson(record.evidence) !== stableJson(source.evidence)) throw fail("DECISIONS_STALE_OR_TAMPERED");
    const decision = record.decision;
    if (!decision || !Array.isArray(decision.genres) || !Array.isArray(decision.vibes) || !decision.scalars || typeof decision.scalars !== "object" ||
        Object.keys(decision.scalars).some(field => !SCALARS.includes(field)) ||
        SCALARS.some(field => decision.scalars[field] !== undefined && !validateDecisionAgainstEvidence(decision.scalars[field], source.evidence.scalars[field])) ||
        decision.primaryGenre !== undefined && typeof decision.primaryGenre !== "string") throw fail("DECISIONS_UNEVIDENCED");
    const resolvedDecision = { ...decision,
      genres: resolveSelectedLabels(decision.genres, source.evidence.genres, definitions.genres, "Genre"),
      vibes: resolveSelectedLabels(decision.vibes, source.evidence.vibes, definitions.vibes, "Vibe"),
    };
    if (resolvedDecision.primaryGenre !== undefined && !resolvedDecision.genres.includes(resolvedDecision.primaryGenre)) throw fail("DECISIONS_UNEVIDENCED");
    const preview = buildEnrichmentPreview(song, source.evidence, resolvedDecision, definitions);
    return { song_id: song.id, base_version: song.updated_at,
      identity: { title: song.title, artist_id: song.artist_id, artist_name: song.artist },
      evidence: source.evidence, review_decision: resolvedDecision, preview };
  });
  return { format_version: 1, operation_id: operationId, songs };
}

module.exports = { buildDecisionTemplate, buildManifest, hashBytes, normalizeReports, reportEvidence, stableJson, validateDecisionAgainstEvidence };
