#!/usr/bin/env node
const fs = require("node:fs/promises");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^[a-f0-9]{64}$/;
const APP_ROOT = path.resolve(__dirname, "..");
const PROJECT_ROOT = path.resolve(APP_ROOT, "..");

function invalid() { throw new Error("Saved factual trial is malformed or has mismatched recording evidence."); }
function identity(value) { return String(value).trim().toLocaleLowerCase("en-US").replace(/&/g, "and").replace(/\s+/g, " "); }
function scalarKey(field, value) { return field === "bpm" ? String(Math.floor(value + 0.5)) : JSON.stringify(value); }

function convertSavedTrial(trial) {
  const choice = trial?.explicit_recording_choice;
  const lookup = trial?.lookup;
  const acoustic = trial?.acousticbrainz;
  const mbid = choice?.mbid;
  if (trial?.report_version !== 1 || !Number.isSafeInteger(trial.target?.song_id) || trial.target.song_id < 1 ||
      ![trial.target.title, trial.target.artist].every(value => typeof value === "string" && value.length > 0 && value.length <= 200) ||
      typeof mbid !== "string" || !UUID.test(mbid) || ![choice.title, choice.artist].every(value => typeof value === "string" && value.length > 0 && value.length <= 200) ||
      typeof choice.disambiguation !== "string" || choice.disambiguation.length > 200 ||
      lookup?.status !== "ok" || lookup.identity_validated !== true || lookup.mbid?.toLowerCase() !== mbid.toLowerCase() ||
      lookup.title !== choice.title || lookup.artist !== choice.artist || lookup.disambiguation !== choice.disambiguation ||
      identity(choice.artist) !== identity(trial.target.artist) ||
      !DIGEST.test(lookup.response_digest || "") || acoustic?.status !== "ok" || acoustic.mbid?.toLowerCase() !== mbid.toLowerCase() ||
      !Number.isSafeInteger(acoustic.submission_count) || acoustic.submission_count < 1 ||
      !Array.isArray(acoustic.sampled_offsets) || acoustic.sampled_offsets.length > 3 ||
      !acoustic.sampled_offsets.every((offset, index, all) => Number.isSafeInteger(offset) && offset >= 0 && offset < acoustic.submission_count &&
        (index === 0 || all[index - 1] < offset)) || !["complete", "partial"].includes(acoustic.submission_enumeration)) invalid();

  const scalarCandidates = { release_year: [], original_key: [], bpm: [] };
  for (const item of lookup.release_year_candidates || []) {
    if (item.source !== "MusicBrainz" || !Number.isInteger(item.value) || item.value < 1 || item.value > 32767 ||
      !DIGEST.test(item.response_digest || "") || item.response_digest !== lookup.response_digest) invalid();
    scalarCandidates.release_year.push({ source: item.source, value: item.value, response_digest: item.response_digest,
      submission_reference: `recording:${mbid.toLowerCase()}` });
  }
  for (const field of ["original_key", "bpm"]) {
    const candidates = acoustic.scalars?.[field];
    if (!Array.isArray(candidates)) invalid();
    for (const item of candidates) {
      if (item.source !== "AcousticBrainz" || !DIGEST.test(item.response_digest || "") ||
          typeof item.submission_id !== "string" || !new RegExp(`^${mbid}:\\d+$`, "i").test(item.submission_id) ||
          !acoustic.sampled_offsets.includes(Number(item.submission_id.split(":").at(-1)))) invalid();
      if (field === "original_key" && (!item.value || typeof item.value.key_key !== "string" ||
          !["major", "minor"].includes(item.value.key_scale)) || field === "bpm" &&
          (typeof item.value !== "number" || !Number.isFinite(item.value) || item.value <= 0)) invalid();
      scalarCandidates[field].push({ source: item.source, value: field === "original_key"
        ? { key_key: item.value.key_key, key_scale: item.value.key_scale } : item.value,
      response_digest: item.response_digest, submission_reference: item.submission_id.toLowerCase() });
    }
  }
  const conflicts = Object.fromEntries(Object.entries(scalarCandidates).map(([field, items]) =>
    [field, new Set(items.map(item => scalarKey(field, item.value))).size > 1]));
  const group = (field, candidates) => ({ status: !candidates.length ? "unresolved" : conflicts[field] ? "conflicting" : "resolved", candidates });
  return {
    factual_context_version: 1,
    target: { song_id: trial.target.song_id, title: trial.target.title, artist: trial.target.artist },
    recording: { mbid: mbid.toLowerCase(), title: choice.title, artist: choice.artist,
      disambiguation: choice.disambiguation, validated: true, response_digest: lookup.response_digest },
    scalar_candidates: { release_year: group("release_year", scalarCandidates.release_year),
      original_key: group("original_key", scalarCandidates.original_key), bpm: group("bpm", scalarCandidates.bpm) },
    submission_coverage: { source: "AcousticBrainz", total_available: acoustic.submission_count,
      sampled_offsets: [...acoustic.sampled_offsets], completeness: acoustic.submission_enumeration },
    conflicts,
  };
}

function isIgnored(file) {
  try { execFileSync("git", ["check-ignore", "--quiet", "--no-index", "--", path.relative(PROJECT_ROOT, file)], { cwd: PROJECT_ROOT, stdio: "ignore" }); return true; }
  catch { return false; }
}

async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 2) throw new Error("Usage: node scripts/convert-factual-trial.cjs <saved-trial.json> <new-ignored-context.json>");
  const input = path.resolve(argv[0]);
  const output = path.resolve(argv[1]);
  const backups = path.join(PROJECT_ROOT, "backups");
  if (!input.startsWith(`${PROJECT_ROOT}${path.sep}`) || !output.startsWith(`${backups}${path.sep}`) || !isIgnored(output)) {
    throw new Error("Input must be inside the project and output must be a new ignored file under backups/.");
  }
  const converted = convertSavedTrial(JSON.parse(await fs.readFile(input, "utf8")));
  await fs.writeFile(output, `${JSON.stringify(converted, null, 2)}\n`, { flag: "wx", mode: 0o600 });
  return output;
}

if (require.main === module) main().then(file => process.stdout.write(`Factual context written: ${file}\n`))
  .catch(() => { process.stderr.write("Factual trial conversion failed safely.\n"); process.exitCode = 1; });

module.exports = { convertSavedTrial };
