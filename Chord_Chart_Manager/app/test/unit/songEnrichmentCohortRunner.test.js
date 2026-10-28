import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { FACTUAL_ATTEMPTS_PER_SONG, hashJson, runSongEnrichmentCohort } = require('../../server/songEnrichmentCohortRunner.js');
const { createSongEnrichmentProviderClient } = require('../../server/songEnrichmentProviders.js');
const { main, parseArgs, providerAttemptBudget } = require('../../scripts/acquire-song-enrichment-cohort.cjs');
const { buildBestEffortDecisions, buildManifest, normalizeReports } = require('../../server/operatorEnrichmentWorkflow.js');
const { captureReadOnlyBaseline, runPreview } = require('../../scripts/preview-existing-song-enrichment.cjs');
const { preparePreview, executePreparedPreview } = require('../../scripts/preview-gemini-fallback.cjs');
const project = path.resolve(import.meta.dirname, '../../..');
const roots = [];
const mbid = '62ec7207-aa84-43b6-949e-8066b6be2cad';
const digest = 'a'.repeat(64);
afterEach(async () => { for (const root of roots.splice(0)) await fs.rm(root, { recursive: true, force: true }); });

async function rootDir() {
  const root = await fs.mkdtemp(path.join(project, 'backups', '.cohort-runner-test-'));
  roots.push(root);
  return root;
}
function fixture(count = 1, firstId = 727) {
  const songs = Array.from({ length: count }, (_, index) => ({ id: firstId + index, title: `Synthetic Song ${index + 1}`,
    artist: `Synthetic Artist ${index + 1}`, updated_at: `2026-10-13T03:17:${String(index).padStart(2, '0')}.000Z`,
    release_year: null, original_key: null, bpm: null, genres: [], vibes: [] }));
  const baseline = { songs, definitions: { genres: [{ id: 1, name: 'Blues Rock' }], vibes: [{ id: 2, name: 'Rocker' }] } };
  const manifest = { cohort_manifest_version: 1, cohort_id: 'synthetic-2026-10',
    songs: songs.map(song => ({ song_id: song.id, title: song.title, artist: song.artist })) };
  return { songs, baseline, manifest };
}
function readPool(currentSong, definitions) {
  return { connect: async () => ({ query: async sql => {
    if (['BEGIN READ ONLY', 'COMMIT', 'ROLLBACK'].includes(sql)) return { rows: [], rowCount: 0 };
    if (sql.includes('FROM public.songs s JOIN public.artists')) return { rows: [{ ...currentSong,
      artist_name: currentSong.artist, updated_at: currentSong.updated_at }], rowCount: 1 };
    if (sql.includes('FROM public.song_genres sg')) return { rows: currentSong.genres.map(row => ({ song_id: currentSong.id, ...row })), rowCount: currentSong.genres.length };
    if (sql.includes('FROM public.song_vibes sv')) return { rows: currentSong.vibes.map(row => ({ song_id: currentSong.id, ...row })), rowCount: currentSong.vibes.length };
    if (sql === 'SELECT id,name FROM public.genres ORDER BY id') return { rows: definitions.genres, rowCount: definitions.genres.length };
    if (sql === 'SELECT id,name FROM public.vibes ORDER BY id') return { rows: definitions.vibes, rowCount: definitions.vibes.length };
    throw new Error(`unexpected query ${sql}`);
  }, release() {} }) };
}
function factual(song, status = 'ok') {
  const matched = status === 'ok';
  return { report_version: 1, report_type: 'existing_song_enrichment_read_only_preview',
    selected_song_ids: [song.id], database_writes: false, approved_apply_manifest: null,
    songs: [{ song_id: song.id, identity: { title: song.title, artist: song.artist },
      recording: { search_status: status, match: { matched, mbid: matched ? mbid : null } },
      evidence: matched ? { mbid,
        scalars: { release_year: [{ value: 1973, source: 'MusicBrainz', response_digest: digest,
          submission_reference: `recording:${mbid}` }], original_key: [], bpm: [] },
        genres: [{ name: 'Blues Rock', source: 'MusicBrainz', response_digest: digest }], vibes: [] } : null,
      human_review_required: true, approved_preview: null }] };
}
function gemini(song) {
  return { format_version: 1, mode: 'explicit_gemini_missing_metadata_fallback', selected_song_id: song.id,
    baseline: { title: song.title, artist: song.artist },
    requested_missing_fields: [],
    gemini_suggestions: { status: 'ok', response_digest: 'b'.repeat(64), model_id: 'gemini-3.1-flash-lite',
      prompt_version: 'missing-metadata-fallback-v2', schema_version: 'missing-metadata-schema-v2',
      scalars: { release_year: [], original_key: [], bpm: [] }, genres: [], vibes: [] },
    database_writes: false, approved_apply_manifest: null, human_review_required: true };
}
const noProcess = async ({ rows, batchIndex, directory }) => {
  const manifest = { synthetic_manifest: true, song_ids: rows.map(row => row.id) };
  const manifestPath = path.join(directory, `synthetic-manifest-${batchIndex}.json`);
  await fs.writeFile(manifestPath, JSON.stringify(manifest));
  return { preparedPath: `p-${rows[0].id}`, decisionsPath: `d-${rows[0].id}`,
    manifestPath, manifestSha256: hashJson(manifest) };
};
const staticCacheHash = async () => 'c'.repeat(64);

describe('resumable evidence cohort runner', () => {
  it('requires the explicit read-only live opt-in before accepting a provider-run command', () => {
    const args = ['--manifest', '.\\backups\\cohort.json', '--output-dir', '.\\backups\\run',
      '--cache-dir', '.\\backups\\cache'];
    expect(() => parseArgs(args)).toThrow(expect.objectContaining({ code: 'LIVE_READ_ONLY_OPT_IN_REQUIRED' }));
    expect(parseArgs(['--live-read-only', ...args]).liveReadOnly).toBe(true);
    expect(parseArgs(['--live-read-only', '--gemini-only', ...args]).acquisitionMode)
      .toBe('gemini_only_after_factual_provider_failure');
    const recoveryArgs = ['--manifest', '.\\backups\\cohort.json', '--output-dir', '.\\backups\\run',
      '--cache-dir', '.\\backups\\cache', '--gemini-recovery-authorization', '.\\backups\\recovery.json'];
    expect(parseArgs(['--live-read-only', '--gemini-only', ...recoveryArgs]).geminiRecoveryAuthorization)
      .toBe('.\\backups\\recovery.json');
    expect(() => parseArgs(['--live-read-only', ...recoveryArgs]))
      .toThrow(expect.objectContaining({ code: 'GEMINI_RECOVERY_AUTHORIZATION_INVALID' }));
  });

  it('runs Gemini connectivity preflight before database access or spend-ledger initialization', async () => {
    const { songs, manifest } = fixture(1, 799), root = await rootDir(), events = [];
    const manifestPath = path.join(root, 'manifest-v1.json');
    await fs.writeFile(manifestPath, JSON.stringify(manifest), { flag: 'wx' });
    const outputDirectory = path.join(root, 'run'), cacheDirectory = path.join(root, 'cache');
    const denied = Object.assign(new Error('safe preflight stop'), { code: 'GEMINI_EGRESS_ENVIRONMENT_BLOCKED' });
    const pool = { connect: async () => { events.push('database'); throw new Error('database must not be reached'); } };
    await expect(main(['--live-read-only', '--manifest', manifestPath, '--output-dir', outputDirectory,
      '--cache-dir', cacheDirectory], {
      pool, config: { databaseUrl: 'postgresql://synthetic@localhost/chords',
        musicBrainzUserAgent: 'ChordChartManager/1.0.0 (offline test)', apiKey: 'synthetic-only-key' },
      geminiConnectivityPreflight: async () => { events.push('preflight'); throw denied; },
      spendLedger: { async initialize() { events.push('spend_initialize'); }, async reserve() { events.push('spend_reserve'); } },
      cache: { async verifyIntegrity() { events.push('cache'); return { sha256: 'd'.repeat(64), entries: 0 }; } },
      transport: async () => { events.push('transport'); return {}; },
    })).rejects.toMatchObject({ code: 'GEMINI_EGRESS_ENVIRONMENT_BLOCKED' });
    expect(events).toEqual(['preflight']);
    expect(songs).toHaveLength(1);
  });

  it('runs Gemini-only after the factual failure guard, records no factual attempts, and binds the mode', async () => {
    const { songs, baseline, manifest } = fixture(1, 781), root = await rootDir();
    manifest.cohort_id = 'lite-gemini-only-2026-10';
    const song = songs[0];
    song.updated_at = '2026-10-13T03:17:00.000000Z';
    let activeReserve = null;
    const reserved = [], providers = [];
    const client = createSongEnrichmentProviderClient({
      cache: { get: async () => null, set: async () => true },
      beforeAttempt: intent => { reserved.push(intent.provider); return activeReserve ? activeReserve(intent) : false; },
      transport: async request => {
        providers.push(request.url.includes('generativelanguage.googleapis.com') ? 'Gemini' : 'non-Gemini');
        expect(request.body.byteLength).toBeLessThanOrEqual(16_384);
        const prompt = request.body.toString('utf8');
        const envelope = JSON.parse(prompt);
        const payload = JSON.parse(envelope.input.slice(envelope.input.indexOf('\n') + 1));
        expect(payload.recording).toEqual({ title: song.title, artist: song.artist, context: {} });
        expect(payload.requested_missing_fields).toEqual(['release_year', 'original_key', 'bpm']);
        expect(payload.allowed_names).toEqual({ genres: ['Blues Rock'], vibes: ['Rocker'] });
        expect(prompt).not.toContain('PRIVATE_CHART_SENTINEL');
        expect(prompt).not.toContain('PRIVATE_LYRICS_SENTINEL');
        expect(envelope.generation_config.max_output_tokens).toBe(1_024);
        return { status: 200, headers: {}, body: Buffer.from(JSON.stringify({ status: 'completed',
          steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({ release_year: 1978,
            original_key: { key_key: 'G', key_scale: 'major' }, bpm: 92, genres: ['Blues Rock'], vibes: ['Rocker'] }) }] }],
          usage: { total_input_tokens: 20, total_output_tokens: 18, total_tokens: 38 } })) };
      },
      config: { maxAttempts: 1, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: 1_048_576,
        geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000, retryDelayMs: 1_000,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (test)' },
    });
    const status = { report_version: 1, report_type: 'factual_acquisition_status', database_writes: false,
      selected_song_ids: [song.id], approved_apply_manifest: null,
      songs: [{ song_id: song.id, identity: { title: song.title, artist: song.artist },
        acquisition: { status: 'not_attempted_due_to_provider_failure', reason: 'factual_provider_failure_guard' },
        human_review_required: true, approved_preview: null }] };
    let normalizedModeEvidence = 'unchecked', decisionStage = 'not-started', runResult, runFailure = null;
    const recoveryAuthorizationSha256 = 'c'.repeat(64);
    const recoveryRunId = 'e'.repeat(64);
    try { runResult = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: client,
      acquisitionMode: 'gemini_only_after_factual_provider_failure', getCacheHash: staticCacheHash,
      recoveryAuthorizationSha256, recoveryRunId,
      processBatch: noProcess,
      acquireFactual: async () => { throw new Error('factual provider path must not run'); },
      acquireGemini: async ({ song: target, factualReport, acceptedRecordingIdentity, reserveAttempt }) => {
        expect(factualReport).toEqual(status);
        expect(acceptedRecordingIdentity).toBe(false);
        activeReserve = reserveAttempt;
        try {
          const prepared = await preparePreview({ pool: readPool({ ...target, chart_content: 'PRIVATE_CHART_SENTINEL',
            lyrics: 'PRIVATE_LYRICS_SENTINEL' }, baseline.definitions), songId: target.id,
            factualInput: factualReport, expectedBaseline: { songs: [target], definitions: baseline.definitions },
            automaticCohort: true, acceptedRecordingIdentity: false });
          expect(prepared.missing).toEqual(['release_year', 'original_key', 'bpm']);
          const executed = await executePreparedPreview({ prepared, providerClient: client, apiKey: 'synthetic-key' });
          try {
            const sourceReports = normalizeReports(target, [
            { path: 'factual-status.json', bytes: Buffer.from(JSON.stringify(factualReport)), document: factualReport },
            { path: 'gemini-report.json', bytes: Buffer.from(JSON.stringify(executed.report)), document: executed.report },
            ]);
            decisionStage = 'decision-build';
            const decisions = buildBestEffortDecisions({ baseline: { songs: [target], definitions: baseline.definitions },
              sourceReports: { [target.id]: sourceReports }, definitions: baseline.definitions });
            decisionStage = 'manifest-build';
            const applyPreview = buildManifest({ baseline: { songs: [target], definitions: baseline.definitions }, decisions: decisions.decisions,
              sourceReports: { [target.id]: sourceReports }, operationId: 'b4a4ba98-cdd2-46d5-b5c2-c15095b49bbf', definitions: baseline.definitions });
            decisionStage = 'approved-mbid';
            expect(applyPreview.songs[0].preview.approved_mbid).toBeNull();
            decisionStage = 'primary-proposal';
            expect(applyPreview.songs[0].preview.primary_proposal).toBeNull();
            decisionStage = 'scalar-fields';
            expect(applyPreview.songs[0].preview.scalar_changes.map(change => change.field)).toEqual(['bpm', 'original_key', 'release_year']);
            decisionStage = 'genre-tags';
            expect(applyPreview.songs[0].preview.tag_additions.genres.map(tag => tag.name)).toEqual(['Blues Rock']);
            decisionStage = 'vibe-tags';
            expect(applyPreview.songs[0].preview.tag_additions.vibes.map(tag => tag.name)).toEqual(['Rocker']);
            decisionStage = 'secondary-genre';
            expect(applyPreview.songs[0].preview.tag_additions.genres.every(tag => !tag.is_primary)).toBe(true);
            normalizedModeEvidence = 'accepted';
          } catch (error) { normalizedModeEvidence = `${decisionStage}:${error.code || error.name || 'untyped'}`; }
          return { geminiReport: { ...executed.report, attempts: { total: executed.attempts.total, gemini: executed.attempts.gemini } } };
        } finally { activeReserve = null; }
      } }); } catch (error) { runFailure = error.code || 'untyped'; }
    expect(runFailure).toBeNull();
    expect(normalizedModeEvidence).toBe('accepted');
    const result = runResult;
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    const savedFactual = JSON.parse(await fs.readFile(checkpoint.songs[0].evidence.factual, 'utf8'));
    const savedGemini = JSON.parse(await fs.readFile(checkpoint.songs[0].evidence.gemini, 'utf8'));
    expect(providers).toEqual(['Gemini']);
    expect(reserved).toEqual(['Gemini']);
    expect(result.factualAttempts).toBe(0);
    expect(result.geminiAttempts).toBe(1);
    expect(checkpoint.binding.acquisition_mode).toBe('gemini_only_after_factual_provider_failure');
    expect(checkpoint.binding.recovery_authorization_sha256).toBe(recoveryAuthorizationSha256);
    expect(checkpoint.binding.recovery_run_id).toBe(recoveryRunId);
    expect(checkpoint.songs[0].factual_attempts).toBe(0);
    expect(savedFactual.songs[0].acquisition.status).toBe('not_attempted_due_to_provider_failure');
    expect(savedGemini.factual_evidence.mbid).toBeUndefined();
    expect(savedGemini.gemini_suggestions.scalars.release_year[0]).toMatchObject({ value: 1978, source: 'Gemini' });
    expect(checkpoint.songs[0].exception_codes).toContain('factual_acquisition_not_attempted_due_to_provider_failure');
    let redispatches = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: client,
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { redispatches += 1; return {}; },
      acquireGemini: async () => { redispatches += 1; return {}; } }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(redispatches).toBe(0);

    const envelopePath = path.join(root, 'checkpoint-v1.json');
    const partialEnvelope = JSON.parse(await fs.readFile(envelopePath, 'utf8'));
    const partial = partialEnvelope.checkpoint;
    const partialSong = partial.songs[0];
    delete partialSong.evidence.gemini;
    delete partialSong.evidence_hashes.gemini;
    partialSong.cache_response_hashes = [];
    partialSong.gemini_attempts = 0;
    partialSong.completed_stage = 'factual';
    partial.gemini_attempts = 0;
    partial.batches = [];
    partial.exceptions = partial.songs.flatMap(item => item.exception_codes.map(code => ({ song_id: item.song_id, code })));
    delete partial.fatal_stop;
    delete partial.stop_reason;
    delete partial.in_flight;
    delete partial.active_stage;
    delete partial.active_song_id;
    await fs.writeFile(envelopePath, JSON.stringify({ checkpoint: partial, sha256: hashJson(partial) }));
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: client,
      acquisitionMode: 'gemini_only_after_factual_provider_failure', recoveryAuthorizationSha256: 'd'.repeat(64),
      recoveryRunId,
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { throw new Error('must not reacquire factual'); },
      acquireGemini: async () => { throw new Error('must not redispatch on changed authorization'); } }))
      .rejects.toMatchObject({ code: 'COHORT_DRIFT' });
    let resumedGeminiCalls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: client,
      acquisitionMode: 'gemini_only_after_factual_provider_failure', getCacheHash: staticCacheHash,
      recoveryAuthorizationSha256,
      recoveryRunId,
      processBatch: noProcess, acquireFactual: async () => { throw new Error('must not reacquire factual'); },
      acquireGemini: async () => { resumedGeminiCalls += 1; throw new Error('synthetic interruption'); } }))
      .rejects.toMatchObject({ code: 'ACQUISITION_FAILED' });
    expect(resumedGeminiCalls).toBe(1);
  });

  it('treats connect-time Gemini EACCES as a fatal local stop, not a provider outage', async () => {
    const { songs, baseline, manifest } = fixture(2, 811), root = await rootDir();
    let geminiCalls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song }) => ({ factualReport: factual(song, 'absent') }),
      acquireGemini: async ({ song, reserveAttempt }) => {
        geminiCalls += 1;
        await reserveAttempt({ provider: 'Gemini', operation: 'missing_metadata_fallback', gemini: true });
        const report = gemini(song);
        report.gemini_suggestions.status = 'transient_error';
        report.gemini_suggestions.failure_details = { failure_phase: 'request', transport_error_code: 'EACCES', https_started: true };
        return { geminiReport: report };
      } })).rejects.toMatchObject({ code: 'GEMINI_EGRESS_ENVIRONMENT_BLOCKED', stage: 'gemini_validation' });
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(geminiCalls).toBe(1);
    expect(checkpoint.gemini_attempts).toBe(1);
    expect(checkpoint.fatal_stop).toEqual({ reason: 'GEMINI_EGRESS_ENVIRONMENT_BLOCKED', stage: 'gemini_validation',
      song_id: songs[0].id });
    expect(checkpoint.songs[0].exception_codes).not.toContain('provider_gemini_transient_failure');
    expect(checkpoint.songs[1].gemini_attempts).toBe(0);
  });

  it('accepts the default factual cap and rejects malformed overrides before database or provider access', async () => {
    const base = ['--live-read-only', '--manifest', '.\\backups\\cohort.json', '--output-dir', '.\\backups\\run',
      '--cache-dir', '.\\backups\\cache'];
    expect(parseArgs(base).factualAttemptsPerSong).toBe(12);
    expect(FACTUAL_ATTEMPTS_PER_SONG).toBe(12);
    let dependencyReads = 0;
    const dependencies = { get pool() { dependencyReads += 1; throw new Error('database accessed'); },
      get config() { dependencyReads += 1; throw new Error('configuration accessed'); },
      get providerClient() { dependencyReads += 1; throw new Error('provider accessed'); } };
    for (const value of ['0', '-1', '1.5', 'abc', '13']) {
      const argv = [...base, '--factual-attempts-per-song', value];
      expect(() => parseArgs(argv)).toThrow(expect.objectContaining({ code: 'FACTUAL_ATTEMPT_CAP_INVALID' }));
      await expect(main(argv, dependencies)).rejects.toMatchObject({ code: 'FACTUAL_ATTEMPT_CAP_INVALID' });
    }
    expect(dependencyReads).toBe(0);
    expect(providerAttemptBudget(3, 9)).toBe(30);
    expect(providerAttemptBudget(3, 12)).toBe(39);
  });

  it('injects a synthetic transport through the accepted provider client and reserves before outbound dispatch', async () => {
    const attempts = [], requests = [], sequence = [];
    const client = createSongEnrichmentProviderClient({
      transport: async request => { sequence.push('dispatch'); requests.push(request.url); return { status: 200, body: Buffer.from('{"count":0,"offset":0,"recordings":[]}') }; },
      beforeAttempt: async intent => { sequence.push('reserved'); attempts.push(intent); return true; },
      cache: { get: async () => null, set: async () => true }, clock: () => 10_000, delay: async () => {},
      config: { maxAttempts: 13, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: 1_000_000,
        geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000, retryDelayMs: 1_000,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (test)' },
    });
    const result = await client.searchRecordings({ title: 'Synthetic Song', artist: 'Synthetic Artist' });
    expect(result.status).toBe('absent');
    expect(attempts).toEqual([{ provider: 'MusicBrainz', operation: 'recording_search', gemini: false, attempt: 1 }]);
    expect(sequence).toEqual(['reserved', 'dispatch']);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toContain('musicbrainz.org');
  });

  it('does not count or dispatch when the provider attempt reservation denies the request', async () => {
    let outbound = 0;
    const client = createSongEnrichmentProviderClient({ transport: async () => { outbound += 1; return { status: 200, body: Buffer.from('{"count":0,"offset":0,"recordings":[]}') }; },
      beforeAttempt: async () => false, cache: { get: async () => null }, clock: () => 10_000, delay: async () => {},
      config: { maxAttempts: 13, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: 1_000_000,
        geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000, retryDelayMs: 1_000,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (test)' } });
    expect((await client.searchRecordings({ title: 'Synthetic Song', artist: 'Synthetic Artist' })).status).toBe('budget_exhausted');
    expect(client.getAttemptCounts()).toEqual({ total: 0, gemini: 0 });
    expect(outbound).toBe(0);
  });

  it('surfaces a typed cohort-drift refusal from the provider reservation hook without dispatch', async () => {
    let outbound = 0;
    const client = createSongEnrichmentProviderClient({ transport: async () => { outbound += 1; return { status: 200, body: Buffer.from('{}') }; },
      beforeAttempt: async () => { const error = new Error('drift'); error.code = 'COHORT_DRIFT'; throw error; },
      cache: { get: async () => null }, clock: () => 10_000, delay: async () => {},
      config: { maxAttempts: 13, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: 1_000_000,
        geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000, retryDelayMs: 1_000,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (test)' } });
    await expect(client.searchRecordings({ title: 'Synthetic Song', artist: 'Synthetic Artist' })).rejects.toMatchObject({ code: 'COHORT_DRIFT' });
    expect(client.getAttemptCounts()).toEqual({ total: 0, gemini: 0 });
    expect(outbound).toBe(0);
  });

  it('checks fresh B4 and Gemini baselines before provider acquisition', async () => {
    const { songs, baseline: source, } = fixture();
    const expectedBaseline = await captureReadOnlyBaseline(readPool(songs[0], source.definitions), [songs[0].id]);
    const changedSong = { ...songs[0], updated_at: '2026-10-13T04:00:00.000Z' };
    let providerCalls = 0;
    const client = { searchRecordings: async () => { providerCalls += 1; return { status: 'absent', candidates: [] }; },
      lookupRecording: async () => { providerCalls += 1; }, getAcousticBrainzEvidence: async () => { providerCalls += 1; },
      getAttemptCounts: () => ({ total: 0, gemini: 0 }), getRunLedger: () => [] };
    const pool = readPool(changedSong, source.definitions);
    await expect(runPreview({ pool, providerClient: client, songIds: [songs[0].id], maxAttempts: 12, expectedBaseline }))
      .rejects.toMatchObject({ code: 'COHORT_DRIFT' });
    await expect(preparePreview({ pool, songId: songs[0].id, expectedBaseline })).rejects.toMatchObject({ code: 'COHORT_DRIFT' });
    expect(providerCalls).toBe(0);
  });

  it('validates the explicit target set and processes sequential batches of at most ten', async () => {
    const { songs, baseline, manifest } = fixture(11), root = await rootDir(), seen = [];
    let active = 0, maximumActive = 0;
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async input => { seen.push(input.rows.map(row => row.id)); return noProcess(input); },
      acquireFactual: async ({ song, reserveAttempt }) => {
        active += 1; maximumActive = Math.max(active, maximumActive);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        active -= 1;
        return { factualReport: factual(song) };
      } });
    expect(seen.map(batch => batch.length)).toEqual([10, 1]);
    expect(maximumActive).toBe(1);
    expect(result.factualAttempts).toBe(11);
    expect(result.geminiAttempts).toBe(0);
    expect(songs).toHaveLength(11);
  });

  it('routes an unresolved recording through the production prepare-decide-preview runner as a valid no-op', async () => {
    const root = await rootDir();
    const song = { id: 738, title: 'Synthetic Unresolved Song', artist: 'Synthetic Artist', artist_id: 91,
      updated_at: '2026-10-24T00:00:00.000000Z', release_year: null, original_key: null, bpm: null,
      genres: [], vibes: [] };
    const definitions = { genres: [{ id: 1, name: 'Rock' }], vibes: [{ id: 2, name: 'Mellow' }] };
    const readOnlyPool = { connect: async () => ({ query: async sql => {
      if (['BEGIN READ ONLY', 'COMMIT', 'ROLLBACK'].includes(sql)) return { rows: [], rowCount: 0 };
      if (sql.includes('FROM public.songs s JOIN public.artists')) return { rows: [{ ...song, artist_name: song.artist }], rowCount: 1 };
      if (sql.includes('FROM public.song_genres sg')) return { rows: [], rowCount: 0 };
      if (sql.includes('FROM public.song_vibes sv')) return { rows: [], rowCount: 0 };
      if (sql === 'SELECT id,name FROM public.genres ORDER BY id') return { rows: definitions.genres, rowCount: 1 };
      if (sql === 'SELECT id,name FROM public.vibes ORDER BY id') return { rows: definitions.vibes, rowCount: 1 };
      throw new Error('unexpected synthetic read-only query');
    }, release() {} }), end: async () => {} };
    class PoolClass { constructor() { return readOnlyPool; } }
    const manifestPath = path.join(root, 'manifest-v1.json');
    const outputDirectory = path.join(root, 'run');
    const cacheDirectory = path.join(root, 'cache');
    await fs.writeFile(manifestPath, JSON.stringify({ cohort_manifest_version: 1, cohort_id: 'synthetic-unresolved-preview',
      songs: [{ song_id: song.id, title: song.title, artist: song.artist }] }), { flag: 'wx' });
    let transportCalls = 0;
    const spendEvents = [];
    const result = await main(['--live-read-only', '--manifest', manifestPath, '--output-dir', outputDirectory,
      '--cache-dir', cacheDirectory], {
      pool: readOnlyPool, PoolClass, config: { databaseUrl: 'postgresql://synthetic@localhost/chords',
        musicBrainzUserAgent: 'ChordChartManager/1.0.0 (offline test)', apiKey: 'synthetic-only-key' },
      geminiConnectivityPreflight: async () => ({ dns: 'ok', tls: 'ok', http_status: 404 }),
      spendLedger: { async initialize(config) { expect(config.model).toBe('gemini-3.1-flash-lite'); },
        async reserve({ cohortId, songId }) { spendEvents.push({ cohortId, songId }); } },
      cache: { get: async () => null, set: async () => true, verifyIntegrity: async () => ({ sha256: 'd'.repeat(64), entries: 0 }) },
      transport: async () => {
        transportCalls += 1;
        const gemini = transportCalls > 1;
        if (gemini) expect(spendEvents).toHaveLength(1);
        const body = gemini ? { status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({
          release_year: null, original_key: null, bpm: null, genres: [], vibes: [],
        }) }] }], usage: { total_input_tokens: 10, total_output_tokens: 10, total_tokens: 20 } }
          : { count: 0, offset: 0, recordings: [] };
        return { status: 200, body: Buffer.from(JSON.stringify(body)) };
      },
    });
    expect(transportCalls).toBe(2);
    expect(spendEvents).toEqual([{ cohortId: 'synthetic-unresolved-preview', songId: song.id }]);
    expect(result.factualAttempts).toBe(1);
    expect(result.geminiAttempts).toBe(1);
    expect(result.exceptions).toContainEqual({ song_id: song.id, code: 'recording_match_unresolved' });
    const manifest = JSON.parse(await fs.readFile(result.batches[0].manifest_path, 'utf8'));
    expect(manifest.songs).toHaveLength(1);
    expect(manifest.songs[0]).toMatchObject({ song_id: song.id, evidence: { mbid: null },
      preview: { has_changes: false, approved_mbid: null, scalar_changes: [],
        tag_additions: { genres: [], vibes: [] } } });
    expect(manifest.songs[0].review_decision).toEqual({ mbid: null, scalars: {}, genres: [], vibes: [] });
  });

  it('enforces 12 factual attempts and one Gemini attempt for each song, with 120 factual per full batch', async () => {
    const { songs, baseline, manifest } = fixture(10), root = await rootDir();
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        for (let index = 0; index < 12; index += 1) await reserveAttempt({ provider: 'MusicBrainz', operation: `attempt_${index}` });
        return { factualReport: { ...factual(song), attempts: { total: 12 } } };
      }, acquireGemini: async ({ song, reserveAttempt }) => {
        await reserveAttempt({ provider: 'Gemini', operation: 'tag_suggestions', gemini: true });
        return { geminiReport: gemini(song) };
      } });
    expect(result.factualAttempts).toBe(120);
    expect(result.geminiAttempts).toBe(10);
    const saved = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(saved.state_machine_version).toBe(3);
    expect(saved.factual_attempts_per_song).toBe(12);
    expect(saved.binding.factual_attempts_per_song).toBe(12);
  });

  it('limits a three-song N=9 replacement to 27 factual reservations including retries within the shared provider budget', async () => {
    const { songs, baseline, manifest } = fixture(3, 736), root = await rootDir();
    const cap = 9, sharedBudget = providerAttemptBudget(songs.length, cap);
    let activeReserve = null, activeSongId = null, totalTransport = 0, musicBrainzPerSong = new Map(), retried = new Set();
    const client = createSongEnrichmentProviderClient({
      cache: { get: async () => null, set: async () => true },
      beforeAttempt: intent => activeReserve ? activeReserve(intent) : false,
      delay: async () => {},
      transport: async request => {
        totalTransport += 1;
        if (request.url.includes('musicbrainz.org')) {
          const calls = (musicBrainzPerSong.get(activeSongId) || 0) + 1;
          musicBrainzPerSong.set(activeSongId, calls);
          if (!retried.has(activeSongId)) { retried.add(activeSongId); return { status: 503, headers: {}, body: Buffer.from('{}') }; }
          return { status: 200, headers: {}, body: Buffer.from('{"count":0,"offset":0,"recordings":[]}') };
        }
        return { status: 200, headers: {}, body: Buffer.from(JSON.stringify({ status: 'completed',
          steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({ release_year: null, genres: [], vibes: [] }) }] }],
          usage: { total_input_tokens: 10, total_output_tokens: 2, total_tokens: 12 } })) };
      },
      config: { maxAttempts: sharedBudget, maxGeminiAttempts: songs.length, timeoutMs: 10_000, maxResponseBytes: 1_000_000,
        geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000, retryDelayMs: 1,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (test)' },
    });
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: client,
      factualAttemptsPerSong: cap, getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        activeReserve = reserveAttempt;
        activeSongId = song.id;
        try {
          for (let index = 0; index < cap + 1; index += 1) {
            const response = await client.searchRecordings({ title: `Synthetic Song ${song.id}`, artist: song.artist });
            if (index === cap) expect(response.status).toBe('budget_exhausted');
          }
          return { factualReport: factual(song), attempts: { total: musicBrainzPerSong.get(song.id) } };
        } finally { activeReserve = null; activeSongId = null; }
      } });
    const saved = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(providerAttemptBudget(songs.length, cap)).toBe(30);
    expect(result.factualAttempts).toBe(27);
    expect(result.geminiAttempts).toBe(0);
    expect(client.getAttemptCounts()).toEqual({ total: 27, gemini: 0 });
    expect(totalTransport).toBe(27);
    expect(saved.factual_attempts).toBe(27);
    expect(saved.gemini_attempts).toBe(0);
    expect(saved.factual_attempts_per_song).toBe(9);
    expect(saved.binding.factual_attempts_per_song).toBe(9);
    expect(saved.songs.map(item => item.factual_attempts)).toEqual([9, 9, 9]);
    expect(saved.songs.map(item => item.gemini_attempts)).toEqual([0, 0, 0]);
    expect([...musicBrainzPerSong.values()]).toEqual([9, 9, 9]);
  });

  it('resumes only with the identical bound cap and rejects missing or changed caps before acquisition', async () => {
    const { baseline, manifest } = fixture(3, 736), root = await rootDir();
    let acquisitions = 0;
    const common = { manifest, baseline, directory: root, factualAttemptsPerSong: 9, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => { acquisitions += 1;
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'synthetic' }); return { factualReport: factual(song) }; } };
    const first = await runSongEnrichmentCohort(common);
    expect(first.factualAttempts).toBe(3);
    await expect(runSongEnrichmentCohort({ ...common, factualAttemptsPerSong: 8 }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    const resumed = await runSongEnrichmentCohort(common);
    expect(resumed.factualAttempts).toBe(3);
    expect(acquisitions).toBe(3);
    const checkpointPath = path.join(root, 'checkpoint-v1.json');
    const envelope = JSON.parse(await fs.readFile(checkpointPath, 'utf8'));
    delete envelope.checkpoint.factual_attempts_per_song;
    envelope.sha256 = hashJson(envelope.checkpoint);
    await fs.writeFile(checkpointPath, JSON.stringify(envelope));
    await expect(runSongEnrichmentCohort(common)).rejects.toMatchObject({ code: 'CHECKPOINT_INVALID' });
    expect(acquisitions).toBe(3);
  });

  it('counts an uncertain reservation against a reduced N=9 checkpoint and refuses resume', async () => {
    const { baseline, manifest } = fixture(3, 736), root = await rootDir();
    let calls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, factualAttemptsPerSong: 9,
      providerClient: {}, getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ reserveAttempt }) => { for (let index = 0; index < 9; index += 1) {
        expect(await reserveAttempt({ provider: 'MusicBrainz', operation: `uncertain_${index}` })).toBe(true); calls += 1;
      } throw new Error('private synthetic interruption'); } }))
      .rejects.toMatchObject({ code: 'ACQUISITION_FAILED' });
    const saved = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(saved.factual_attempts_per_song).toBe(9);
    expect(saved.factual_attempts).toBe(9);
    expect(saved.songs[0].factual_attempts).toBe(9);
    expect(saved.in_flight.status).toBe('uncertain');
    expect(calls).toBe(9);
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, factualAttemptsPerSong: 9,
      providerClient: {}, getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { calls += 1; return {}; } })).rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(calls).toBe(9);
  });

  it('refuses a thirteenth factual attempt before it can be reserved or dispatched', async () => {
    const { baseline, manifest } = fixture(), root = await rootDir();
    let dispatched = 0;
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ reserveAttempt }) => {
        for (let index = 0; index < 13; index += 1) {
          if (!await reserveAttempt({ provider: 'MusicBrainz', operation: 'search' })) break;
          dispatched += 1;
        }
        return { factualReport: { ...factual(baseline.songs[0]), attempts: { total: dispatched } } };
      } });
    const saved = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(dispatched).toBe(12);
    expect(saved.factual_attempts).toBe(12);
    expect(result.exceptions).toContainEqual({ song_id: 727, code: 'factual_attempt_limit_reached' });
  });

  it('keeps interrupted requests uncertain and refuses resume without redispatch', async () => {
    const { songs, baseline, manifest } = fixture(), root = await rootDir();
    let calls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ reserveAttempt }) => { calls += 1; await reserveAttempt({ provider: 'MusicBrainz', operation: 'search' }); throw new Error('private interruption detail'); } }))
      .rejects.toMatchObject({ code: 'ACQUISITION_FAILED', stage: 'factual_acquisition' });
    const saved = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(saved.factual_attempts).toBe(1);
    expect(saved.in_flight).toMatchObject({ status: 'uncertain', song_id: songs[0].id });
    expect(saved.fatal_stop).toEqual({ reason: 'ACQUISITION_FAILED', stage: 'factual_acquisition', song_id: songs[0].id });
    expect(JSON.stringify(saved)).not.toContain('private interruption detail');
    const crashCopy = await rootDir();
    const interruptedEnvelope = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8'));
    delete interruptedEnvelope.checkpoint.fatal_stop;
    delete interruptedEnvelope.checkpoint.active_stage;
    delete interruptedEnvelope.checkpoint.active_song_id;
    interruptedEnvelope.sha256 = hashJson(interruptedEnvelope.checkpoint);
    await fs.writeFile(path.join(crashCopy, 'checkpoint-v1.json'), JSON.stringify(interruptedEnvelope));
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: crashCopy, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { calls += 1; return {}; } })).rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(interruptedEnvelope.checkpoint.factual_attempts).toBe(1);
    expect(calls).toBe(1);
  });

  it('rejects cohort drift, changed cache binding, and checkpoint tampering before acquisition', async () => {
    const { songs, baseline, manifest } = fixture(), root = await rootDir();
    const common = { manifest, baseline, directory: root, providerClient: {}, getCacheHash: staticCacheHash,
      processBatch: noProcess, acquireFactual: async ({ song }) => ({ factualReport: factual(song) }) };
    await runSongEnrichmentCohort(common);
    await expect(runSongEnrichmentCohort({ ...common, baseline: { ...baseline, songs: [{ ...songs[0], updated_at: 'changed' }] } }))
      .rejects.toMatchObject({ code: 'COHORT_DRIFT' });
    await expect(runSongEnrichmentCohort({ ...common, getCacheHash: async () => 'd'.repeat(64) }))
      .rejects.toMatchObject({ code: 'CACHE_INVALID' });
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8'));
    checkpoint.checkpoint.factual_attempts += 1;
    await fs.writeFile(path.join(root, 'checkpoint-v1.json'), JSON.stringify(checkpoint));
    await expect(runSongEnrichmentCohort(common)).rejects.toMatchObject({ code: 'CHECKPOINT_TAMPERED' });
  });

  it('rejects a changed preview manifest on resume', async () => {
    const { baseline, manifest } = fixture(), root = await rootDir();
    const common = { manifest, baseline, directory: root, providerClient: {}, getCacheHash: staticCacheHash,
      processBatch: noProcess, acquireFactual: async ({ song }) => ({ factualReport: factual(song) }) };
    await runSongEnrichmentCohort(common);
    const saved = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    await fs.writeFile(saved.batches[0].manifest_path, '{"changed":true}');
    await expect(runSongEnrichmentCohort(common)).rejects.toMatchObject({ code: 'CHECKPOINT_TAMPERED' });
  });

  it('reuses completed evidence and validates saved evidence hashes on resume', async () => {
    const { baseline, manifest } = fixture(), root = await rootDir();
    let acquisitions = 0, previews = 0;
    const common = { manifest, baseline, directory: root, providerClient: {}, getCacheHash: staticCacheHash,
      processBatch: async input => { previews += 1; return noProcess(input); },
      acquireFactual: async ({ song, reserveAttempt }) => {
        acquisitions += 1; await reserveAttempt({ provider: 'MusicBrainz', operation: 'search' });
        return { factualReport: factual(song) };
      } };
    const first = await runSongEnrichmentCohort(common);
    const checkpointFile = path.join(root, 'checkpoint-v1.json');
    const savedEvidence = JSON.parse(await fs.readFile(checkpointFile, 'utf8')).checkpoint.songs[0].evidence.factual;
    const evidenceBytes = await fs.readFile(savedEvidence);
    await runSongEnrichmentCohort(common);
    expect(acquisitions).toBe(1);
    expect(previews).toBe(1);
    await fs.appendFile(savedEvidence, ' ');
    await expect(runSongEnrichmentCohort(common)).rejects.toMatchObject({ code: 'CHECKPOINT_TAMPERED' });
    expect(first.factualAttempts).toBe(1);
    expect(evidenceBytes.length).toBeGreaterThan(0);
  });

  it('acknowledges a synthetic factual report before a failed next stage and refuses a copied unsafe checkpoint', async () => {
    const { baseline, manifest } = fixture(1, 736), root = await rootDir(), copied = await rootDir();
    let factualCalls = 0, geminiCalls = 0;
    const common = { manifest, baseline, providerClient: {}, getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        factualCalls += 1;
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song) };
      }, acquireGemini: async ({ reserveAttempt }) => {
        geminiCalls += 1;
        await reserveAttempt({ provider: 'Gemini', operation: 'tag_suggestions', gemini: true });
        throw new Error('synthetic private transport detail');
      } };
    await expect(runSongEnrichmentCohort({ ...common, directory: root }))
      .rejects.toMatchObject({ code: 'ACQUISITION_FAILED', stage: 'gemini_acquisition' });
    const checkpointPath = path.join(root, 'checkpoint-v1.json');
    const envelope = JSON.parse(await fs.readFile(checkpointPath, 'utf8'));
    const saved = envelope.checkpoint;
    expect(saved.factual_attempts).toBe(1);
    expect(saved.gemini_attempts).toBe(1);
    expect(saved.songs[0].completed_stage).toBe('factual');
    expect(saved.songs[0].evidence_hashes.factual).toMatch(/^[a-f0-9]{64}$/);
    const evidencePath = saved.songs[0].evidence.factual;
    const evidenceBytes = await fs.readFile(evidencePath);
    const acceptedReport = JSON.parse(evidenceBytes);
    expect(acceptedReport.songs[0].recording.match).toMatchObject({ matched: true, mbid });
    expect(saved.songs[0].cache_response_hashes).toEqual([digest]);
    expect(saved.binding.cache_sha256).toBe('c'.repeat(64));
    expect(evidenceBytes.length).toBeGreaterThan(0);
    expect(JSON.stringify(saved)).not.toContain('synthetic private transport detail');
    expect(saved.fatal_stop).toEqual({ reason: 'ACQUISITION_FAILED', stage: 'gemini_acquisition', song_id: 736 });

    await fs.mkdir(path.join(copied, 'evidence'), { recursive: true });
    const copiedEvidence = path.join(copied, 'evidence', path.basename(evidencePath));
    await fs.copyFile(evidencePath, copiedEvidence);
    saved.songs[0].evidence.factual = copiedEvidence;
    envelope.sha256 = hashJson(saved);
    await fs.writeFile(path.join(copied, 'checkpoint-v1.json'), JSON.stringify(envelope));
    await expect(runSongEnrichmentCohort({ ...common, directory: copied,
      acquireFactual: async () => { factualCalls += 1; throw new Error('must not redispatch'); },
      acquireGemini: async () => { geminiCalls += 1; throw new Error('must not redispatch'); } }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(factualCalls).toBe(1);
    expect(geminiCalls).toBe(1);
  });

  it('reserves durably before Gemini transport, charges transport failure, and validates an acknowledged response', async () => {
    expect(typeof executePreparedPreview).toBe('function');
    const runScenario = async ({ transport }) => {
      const { baseline, manifest } = fixture(1, 736), root = await rootDir();
      let activeReserve = null, reservationWasDurable = false, requestBytes = 0, outputTokens = 0;
      const client = createSongEnrichmentProviderClient({
        cache: { get: async () => null, set: async () => true },
        beforeAttempt: intent => intent.provider === 'Gemini' && activeReserve ? activeReserve(intent) : false,
        transport: async request => {
          const envelope = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8'));
          const checkpoint = envelope.checkpoint;
          reservationWasDurable = envelope.sha256 === hashJson(checkpoint) && checkpoint.active_stage === 'gemini_acquisition' &&
            checkpoint.in_flight?.provider === 'Gemini' && checkpoint.in_flight?.status === 'uncertain' &&
            checkpoint.gemini_attempts === 1 && checkpoint.songs[0].gemini_attempts === 1;
          requestBytes = request.body.byteLength;
          outputTokens = JSON.parse(request.body.toString('utf8')).generation_config.max_output_tokens;
          expect(reservationWasDurable).toBe(true);
          expect(requestBytes).toBeGreaterThan(0);
          expect(requestBytes).toBeLessThanOrEqual(16_384);
          expect(outputTokens).toBe(1_024);
          return transport();
        },
        config: { maxAttempts: 13, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: 1_048_576,
          geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, maxRetryAfterMs: 5_000,
          retryDelayMs: 1_000, musicBrainzMinimumIntervalMs: 1_000,
          musicBrainzUserAgent: 'ChordChartManager/1.0.0 (offline test)' },
      });
      let result, failure;
      try { result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: client,
        getCacheHash: staticCacheHash, processBatch: noProcess,
        acquireFactual: async ({ song, reserveAttempt }) => {
          await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
          return { factualReport: factual(song) };
        },
        acquireGemini: async ({ song, factualReport, reserveAttempt }) => {
          activeReserve = reserveAttempt;
          try {
            const expectedBaseline = { songs: [song], definitions: baseline.definitions };
            const prepared = await preparePreview({ pool: readPool(song, baseline.definitions), songId: song.id,
              factualInput: factualReport, expectedBaseline });
            const executed = await executePreparedPreview({ prepared, providerClient: client, apiKey: 'synthetic-offline-key' });
            return { geminiReport: { ...executed.report, attempts: { total: executed.attempts.total,
              gemini: executed.attempts.gemini } } };
          } finally { activeReserve = null; }
        } }); }
      catch (error) { failure = { code: error.code, stage: error.stage }; }
      return { root, result, failure, reservationWasDurable, requestBytes, outputTokens };
    };
    const failed = await runScenario({ transport: async () => { throw Object.assign(new Error('private synthetic failure'), { code: 'ECONNRESET' }); } });
    const failedCheckpoint = JSON.parse(await fs.readFile(path.join(failed.root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(failed.reservationWasDurable).toBe(true);
    expect(failedCheckpoint.factual_attempts).toBe(1);
    expect(failedCheckpoint.gemini_attempts).toBe(1);
    expect(failedCheckpoint.fatal_stop).toBeUndefined();
    expect(JSON.stringify(failedCheckpoint)).not.toContain('private synthetic failure');
    expect(failed.failure).toBeUndefined();
    expect(failed.result.geminiAttempts).toBe(1);
    expect(failed.result.exceptions).toContainEqual({ song_id: 736, code: 'provider_gemini_transient_failure' });
    expect(failedCheckpoint.songs[0].exception_codes).toContain('provider_gemini_transient_failure');

    const accepted = await runScenario({ transport: async () => ({ status: 200, headers: {}, body: Buffer.from(JSON.stringify({
      status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({
        release_year: null, bpm: null, original_key: null, genres: [], vibes: [] }) }] }],
      usage: { total_input_tokens: 12, total_output_tokens: 7, total_tokens: 19 },
    })) }) });
    const acceptedCheckpoint = JSON.parse(await fs.readFile(path.join(accepted.root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(accepted.reservationWasDurable).toBe(true);
    expect(accepted.result.geminiAttempts).toBe(1);
    expect(acceptedCheckpoint.fatal_stop).toBeUndefined();
    expect(acceptedCheckpoint.songs[0].completed_stage).toBe('preview');
    expect(acceptedCheckpoint.songs[0].evidence_hashes.gemini).toMatch(/^[a-f0-9]{64}$/);
    expect(acceptedCheckpoint.batches[0].completed_stage).toBe('preview');
  });

  it('rejects a legacy checkpoint shape without dispatching or changing it', async () => {
    const { baseline, manifest } = fixture(1, 736), root = await rootDir();
    const checkpoint = { checkpoint_version: 1, policy_version: 'stage-b-cohort-acquisition-v1', binding: {},
      songs: [], batches: [], exceptions: [], factual_attempts: 0, gemini_attempts: 0 };
    const envelope = { checkpoint, sha256: hashJson(checkpoint) };
    const file = path.join(root, 'checkpoint-v1.json');
    const originalBytes = Buffer.from(JSON.stringify(envelope));
    await fs.writeFile(file, originalBytes);
    let providerCalls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { providerCalls += 1; return {}; }, acquireGemini: async () => ({}) }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(providerCalls).toBe(0);
    expect(await fs.readFile(file)).toEqual(originalBytes);
  });

  it('persists sanitized pre-provider, checkpoint-write, and preview-transition failures', async () => {
    const { baseline, manifest } = fixture();
    const beforeProvider = await rootDir();
    let providerCalls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: beforeProvider, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { providerCalls += 1; throw new Error('private preflight detail'); } }))
      .rejects.toMatchObject({ code: 'ACQUISITION_FAILED', stage: 'factual_acquisition' });
    let saved = JSON.parse(await fs.readFile(path.join(beforeProvider, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(saved.factual_attempts).toBe(0);
    expect(saved.fatal_stop).toEqual({ reason: 'ACQUISITION_FAILED', stage: 'factual_acquisition', song_id: 727 });
    expect(JSON.stringify(saved)).not.toContain('private preflight detail');
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: beforeProvider, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess, acquireFactual: async () => { providerCalls += 1; return {}; } }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(providerCalls).toBe(1);

    const checkpointFailure = await rootDir();
    let checkpointWrites = 0, acknowledgedCalls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: checkpointFailure, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess, acquireFactual: async ({ song, reserveAttempt }) => {
        acknowledgedCalls += 1;
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'search' });
        return { factualReport: factual(song) };
      },
      checkpointWriter: async (...args) => {
        checkpointWrites += 1;
        if (checkpointWrites === 4) throw new Error('private filesystem detail');
        const fsPromises = await import('node:fs/promises');
        const payload = args[1];
        await fsPromises.writeFile(args[0], JSON.stringify(payload));
      } })).rejects.toMatchObject({ code: 'CHECKPOINT_WRITE_FAILED', stage: 'factual_acquisition' });
    saved = JSON.parse(await fs.readFile(path.join(checkpointFailure, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(saved.fatal_stop.reason).toBe('CHECKPOINT_WRITE_FAILED');
    expect(saved.fatal_stop.stage).toBe('factual_acquisition');
    expect(saved.factual_attempts).toBe(1);
    expect(acknowledgedCalls).toBe(1);

    const previewFailure = await rootDir();
    const previewError = Object.assign(new Error('private preview details'), { code: 'EVIDENCE_NOT_APPROVED' });
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: previewFailure, providerClient: {},
      getCacheHash: staticCacheHash, acquireFactual: async ({ song }) => ({ factualReport: factual(song) }),
      processBatch: async () => { throw previewError; } }))
      .rejects.toMatchObject({ code: 'PREVIEW_FAILED', stage: 'preview_transition' });
    saved = JSON.parse(await fs.readFile(path.join(previewFailure, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(saved.fatal_stop).toEqual({ reason: 'PREVIEW_FAILED', stage: 'preview_transition', song_id: null,
      diagnostic_code: 'EVIDENCE_NOT_APPROVED' });
    expect(JSON.stringify(saved)).not.toContain('private preview details');
  });

  it('continues to the next song after one song-level transient provider failure', async () => {
    const { songs, baseline, manifest } = fixture(2), root = await rootDir();
    const dispatched = [];
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, song.id === songs[0].id ? 'transient_error' : 'ok') };
      }, acquireGemini: async () => ({ skipped: true }) });
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(dispatched).toEqual(songs.map(song => song.id));
    expect(result.factualAttempts).toBe(2);
    expect(result.exceptions).toContainEqual({ song_id: songs[0].id, code: 'provider_musicbrainz_transient_failure' });
    expect(checkpoint.songs[0].exception_codes).toContain('provider_musicbrainz_transient_failure');
    expect(checkpoint.fatal_stop).toBeUndefined();
    expect(checkpoint.batches[0].completed_stage).toBe('preview');
    expect(checkpoint.songs.every(song => song.completed_stage === 'preview')).toBe(true);
  });

  it('requests one Gemini fallback for an unresolved factual identity and saves the acknowledged report', async () => {
    const { songs, baseline, manifest } = fixture(1), root = await rootDir();
    let geminiCalls = 0;
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, 'absent') };
      },
      acquireGemini: async ({ song, reserveAttempt }) => {
        geminiCalls += 1;
        await reserveAttempt({ provider: 'Gemini', operation: 'missing_metadata_fallback', gemini: true });
        return { geminiReport: gemini(song) };
      } });
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(geminiCalls).toBe(1);
    expect(result.geminiAttempts).toBe(1);
    expect(checkpoint.songs[0].gemini_attempts).toBe(1);
    expect(checkpoint.songs[0].completed_stage).toBe('preview');
    expect(checkpoint.songs[0].evidence.gemini).toMatch(/-gemini-v1\.json$/);
    expect(checkpoint.fatal_stop).toBeUndefined();
  });

  it('requests provisional factual fields from Gemini in the cohort path when B1 identity is not accepted', async () => {
    const { songs, baseline } = fixture(1), song = songs[0];
    const saved = factual(song, 'ok');
    saved.songs[0].scalar_candidates = Object.fromEntries(['release_year', 'original_key', 'bpm'].map(field => [field,
      { current_value: null, state: field === 'release_year' ? 'unapproved_candidates' : 'no_candidate',
        candidates: field === 'release_year' ? [{ value: 1973, source: 'MusicBrainz', response_digest: digest,
          submission_id: `recording:${mbid}` }] : [], conflicting_evidence: false }]));
    const prepared = await preparePreview({ pool: readPool(song, baseline.definitions), songId: song.id,
      factualInput: saved, automaticCohort: true, acceptedRecordingIdentity: false });
    expect(prepared.missing).toContain('release_year');
    expect(prepared.requestFactualEvidence).toBeNull();
    let captured;
    await executePreparedPreview({ prepared, apiKey: 'synthetic-only-key', providerClient: {
      async requestGeminiTags(args) {
        captured = args;
        return { status: 'ok', response_digest: 'b'.repeat(64), model_id: 'gemini-3.1-flash-lite',
          prompt_version: 'missing-metadata-fallback-v2', schema_version: 'missing-metadata-schema-v2',
          scalars: { release_year: [], original_key: [], bpm: [] }, genres: [], vibes: [] };
      }, getAttemptCounts: () => ({ total: 1, gemini: 1 }),
    } });
    expect(captured.requestedFields).toContain('release_year');
    expect(captured.factualEvidence).toBeNull();
  });

  it('records upstream invalid_response as a charged song exception and continues', async () => {
    const { songs, baseline, manifest } = fixture(2), root = await rootDir();
    const dispatched = [];
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, song.id === songs[0].id ? 'invalid_response' : 'ok') };
      }, acquireGemini: async () => ({ skipped: true }) });
    const envelope = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8'));
    const checkpoint = envelope.checkpoint;
    expect(dispatched).toEqual(songs.map(song => song.id));
    expect(result.factualAttempts).toBe(2);
    expect(result.exceptions).toContainEqual({ song_id: songs[0].id, code: 'provider_musicbrainz_invalid_response' });
    expect(checkpoint.songs[0].factual_attempts).toBe(1);
    expect(checkpoint.songs[0].completed_stage).toBe('preview');
    expect(checkpoint.fatal_stop).toBeUndefined();
    expect(envelope.sha256).toBe(hashJson(checkpoint));
  });

  it('stops after invalid_response from the same provider on two consecutive songs', async () => {
    const { songs, baseline, manifest } = fixture(3), root = await rootDir();
    const dispatched = [];
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, song.id === songs[2].id ? 'ok' : 'invalid_response') };
      } })).rejects.toMatchObject({ code: 'PROVIDER_OUTAGE', stage: 'factual_validation' });
    const envelope = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8'));
    const checkpoint = envelope.checkpoint;
    expect(dispatched).toEqual(songs.slice(0, 2).map(song => song.id));
    expect(checkpoint.factual_attempts).toBe(2);
    expect(checkpoint.fatal_stop).toEqual({ reason: 'PROVIDER_OUTAGE', stage: 'factual_validation',
      song_id: songs[1].id, origin: 'provider_invalid_response' });
    expect(checkpoint.exceptions).toContainEqual({ song_id: songs[0].id, code: 'provider_musicbrainz_invalid_response' });
    expect(checkpoint.exceptions).toContainEqual({ song_id: songs[1].id, code: 'provider_musicbrainz_invalid_response' });
    expect(envelope.sha256).toBe(hashJson(checkpoint));
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { dispatched.push(999); return {}; } }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(dispatched).toHaveLength(2);
  });

  it('keeps provider invalid_request fatal with a safe factual origin', async () => {
    const { songs, baseline, manifest } = fixture(2), root = await rootDir();
    const dispatched = [];
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must stop before preview'); },
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, 'invalid_request') };
      } })).rejects.toMatchObject({ code: 'INVALID_EVIDENCE', stage: 'factual_validation' });
    const envelope = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8'));
    expect(dispatched).toEqual([songs[0].id]);
    expect(envelope.checkpoint.fatal_stop).toEqual({ reason: 'INVALID_EVIDENCE', stage: 'factual_validation',
      song_id: songs[0].id, origin: 'provider_invalid_request' });
    expect(envelope.checkpoint.songs[0].factual_attempts).toBe(1);
    expect(envelope.sha256).toBe(hashJson(envelope.checkpoint));
    expect(JSON.stringify(envelope.checkpoint)).not.toContain('private');
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { dispatched.push(999); return {}; } }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(dispatched).toEqual([songs[0].id]);
    envelope.checkpoint.fatal_stop.origin = 'private exception detail';
    envelope.sha256 = hashJson(envelope.checkpoint);
    await fs.writeFile(path.join(root, 'checkpoint-v1.json'), JSON.stringify(envelope));
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { dispatched.push(998); return {}; } }))
      .rejects.toMatchObject({ code: 'CHECKPOINT_INVALID' });
    expect(dispatched).toEqual([songs[0].id]);
  });

  it('stops after the same provider fails on two consecutive distinct songs', async () => {
    const { songs, baseline, manifest } = fixture(3), root = await rootDir();
    const dispatched = [];
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must stop before preview'); },
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, 'transient_error') };
      } })).rejects.toMatchObject({ code: 'PROVIDER_OUTAGE', stage: 'factual_validation' });
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(dispatched).toEqual(songs.slice(0, 2).map(song => song.id));
    expect(checkpoint.factual_attempts).toBe(2);
    expect(checkpoint.fatal_stop).toEqual({ reason: 'PROVIDER_OUTAGE', stage: 'factual_validation', song_id: songs[1].id });
    expect(checkpoint.songs.slice(0, 2).map(song => song.factual_attempts)).toEqual([1, 1]);
    expect(checkpoint.songs.slice(0, 2).every(song => song.exception_codes.includes('provider_musicbrainz_transient_failure'))).toBe(true);
    expect(checkpoint.exceptions).toContainEqual({ song_id: songs[0].id, code: 'provider_musicbrainz_transient_failure' });
    expect(checkpoint.exceptions).toContainEqual({ song_id: songs[1].id, code: 'provider_musicbrainz_transient_failure' });
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async () => { dispatched.push(999); return {}; } })).rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(dispatched).toEqual(songs.slice(0, 2).map(song => song.id));

    const privateEvidenceRoot = await rootDir();
    await expect(runSongEnrichmentCohort({ ...fixture(1), directory: privateEvidenceRoot, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song }) => ({ factualReport: { ...factual(song), lyrics: 'PRIVATE LYRICS' } }) }))
      .rejects.toMatchObject({ code: 'INVALID_EVIDENCE' });
    expect(await fs.readFile(path.join(privateEvidenceRoot, 'checkpoint-v1.json'), 'utf8')).not.toContain('PRIVATE LYRICS');
  });

  it('carries a provider outage across a validated checkpoint resume boundary', async () => {
    const { songs, baseline, manifest } = fixture(2), originalRoot = await rootDir(), resumedRoot = await rootDir();
    const completed = await runSongEnrichmentCohort({ manifest, baseline, directory: originalRoot, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: noProcess,
      acquireFactual: async ({ song, reserveAttempt }) => {
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, song.id === songs[0].id ? 'transient_error' : 'ok') };
      }, acquireGemini: async () => ({ skipped: true }) });
    expect(completed.batches).toHaveLength(1);
    const envelope = JSON.parse(await fs.readFile(path.join(originalRoot, 'checkpoint-v1.json'), 'utf8'));
    const checkpoint = envelope.checkpoint;
    const previous = checkpoint.songs[0];
    const previousEvidenceSource = previous.evidence.factual;
    await fs.mkdir(path.join(resumedRoot, 'evidence'), { recursive: true });
    const previousEvidencePath = path.join(resumedRoot, 'evidence', path.basename(previousEvidenceSource));
    await fs.copyFile(previousEvidenceSource, previousEvidencePath);
    previous.evidence.factual = previousEvidencePath;
    previous.completed_stage = 'evidence';
    const pending = checkpoint.songs[1];
    pending.evidence = {};
    pending.evidence_hashes = {};
    pending.cache_response_hashes = [];
    pending.factual_attempts = 0;
    pending.gemini_attempts = 0;
    pending.completed_stage = 'none';
    pending.exception_codes = [];
    checkpoint.factual_attempts = previous.factual_attempts;
    checkpoint.gemini_attempts = previous.gemini_attempts;
    checkpoint.batches = [];
    checkpoint.exceptions = checkpoint.songs.flatMap(item => item.exception_codes.map(code => ({ song_id: item.song_id, code })));
    delete checkpoint.active_stage;
    delete checkpoint.active_song_id;
    delete checkpoint.fatal_stop;
    checkpoint.in_flight = null;
    await fs.writeFile(path.join(resumedRoot, 'checkpoint-v1.json'), JSON.stringify({ checkpoint,
      sha256: hashJson(checkpoint) }));

    const dispatched = [];
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: resumedRoot, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must stop before preview'); },
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        return { factualReport: factual(song, 'transient_error') };
      } })).rejects.toMatchObject({ code: 'PROVIDER_OUTAGE', stage: 'factual_validation' });
    const resumed = JSON.parse(await fs.readFile(path.join(resumedRoot, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(dispatched).toEqual([songs[1].id]);
    expect(resumed.fatal_stop).toEqual({ reason: 'PROVIDER_OUTAGE', stage: 'factual_validation', song_id: songs[1].id });
    expect(resumed.exceptions).toContainEqual({ song_id: songs[0].id, code: 'provider_musicbrainz_transient_failure' });
    expect(resumed.exceptions).toContainEqual({ song_id: songs[1].id, code: 'provider_musicbrainz_transient_failure' });
  });

  it('records an unusable Gemini report without passing it to T27 evidence validation', async () => {
    const { baseline, manifest } = fixture(), root = await rootDir();
    let seenRefs;
    const result = await runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async input => { seenRefs = input.evidenceRefs; return noProcess(input); },
      acquireFactual: async ({ song }) => ({ factualReport: factual(song) }),
      acquireGemini: async ({ song }) => ({ geminiReport: { ...gemini(song), gemini_suggestions: {
        ...gemini(song).gemini_suggestions, status: 'absent', model_id: null, prompt_version: null, schema_version: null, response_digest: null,
      } } }) });
    expect(seenRefs.map(item => path.basename(item.path))).toEqual(['727-factual-v1.json']);
    expect(result.exceptions).toContainEqual({ song_id: 727, code: 'gemini_evidence_unusable' });
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(checkpoint.fatal_stop).toBeUndefined();
    expect(checkpoint.songs[0].exception_codes).toContain('gemini_evidence_unusable');
    expect(checkpoint.batches[0].completed_stage).toBe('preview');
  });

  it('halts cohort dispatch when an ok Gemini report fails the Stage B evidence validator', async () => {
    const { baseline, manifest } = fixture(2), root = await rootDir();
    let geminiCalls = 0;
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must not preview invalid evidence'); },
      acquireFactual: async ({ song }) => ({ factualReport: factual(song) }),
      acquireGemini: async ({ song }) => {
        geminiCalls += 1;
        const report = gemini(song);
        report.gemini_suggestions.response_digest = 'invalid';
        return { geminiReport: report };
      } })).rejects.toMatchObject({ code: 'INVALID_EVIDENCE' });
    expect(geminiCalls).toBe(1);
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(checkpoint.fatal_stop).toMatchObject({ reason: 'INVALID_EVIDENCE', stage: 'gemini_validation', song_id: 727 });
  });

  it('stops on structurally malformed B4 evidence before later cohort dispatch', async () => {
    const { songs, baseline, manifest } = fixture(2), root = await rootDir();
    const dispatched = [];
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must not preview'); },
      acquireFactual: async ({ song, reserveAttempt }) => {
        dispatched.push(song.id);
        await reserveAttempt({ provider: 'MusicBrainz', operation: 'recording_search' });
        const report = factual(song);
        report.songs[0].evidence.scalars.release_year[0].source = 'UnknownProvider';
        return { factualReport: report };
      } })).rejects.toMatchObject({ code: 'INVALID_EVIDENCE' });
    expect(dispatched).toEqual([songs[0].id]);
    const checkpoint = JSON.parse(await fs.readFile(path.join(root, 'checkpoint-v1.json'), 'utf8')).checkpoint;
    expect(checkpoint.fatal_stop).toEqual({ reason: 'INVALID_EVIDENCE', stage: 'factual_validation',
      song_id: songs[0].id, origin: 'local_evidence_validation' });
    expect(checkpoint.songs[0].factual_attempts).toBe(1);
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must not preview'); },
      acquireFactual: async () => { dispatched.push(999); return {}; } })).rejects.toMatchObject({ code: 'CHECKPOINT_UNSAFE_RESUME' });
    expect(dispatched).toEqual([songs[0].id]);
  });

  it('stops when a B1 tie-break lookup reports authentication failure', async () => {
    const { baseline, manifest } = fixture(), root = await rootDir();
    const report = factual(baseline.songs[0]);
    report.songs[0].recording.richness_comparison = { candidates: [{ lookup_status: 'authentication_error' }] };
    await expect(runSongEnrichmentCohort({ manifest, baseline, directory: root, providerClient: {},
      getCacheHash: staticCacheHash, processBatch: async () => { throw new Error('must stop before preview'); },
      acquireFactual: async () => ({ factualReport: report }) })).rejects.toMatchObject({ code: 'AUTHENTICATION_FAILURE' });
  });
});
