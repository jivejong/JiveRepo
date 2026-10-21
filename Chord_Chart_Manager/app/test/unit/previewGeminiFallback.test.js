import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const fallback = require('../../scripts/preview-gemini-fallback.cjs');
const { convertSavedTrial } = require('../../scripts/convert-factual-trial.cjs');
const { createSongEnrichmentProviderClient } = require('../../server/songEnrichmentProviders.js');
const { MAX_RESPONSE_BYTES } = require('../../server/songEnrichmentTransport.js');
const UA = 'ChordChartManager/1.0.0 (local-only)';
const temporaries = [];

function baselineSong(overrides = {}) {
  return { id: 73, title: 'Synthetic Song', artist: 'Synthetic Artist', updated_at: '2026-10-17T01:02:03.123456Z',
    release_year: null, original_key: null, bpm: null, genres: [], vibes: [], ...overrides };
}

function savedTrialFixture() {
  const mbid = '62ec7207-aa84-43b6-949e-8066b6be2cad';
  const digest = (letter) => letter.repeat(64);
  return { report_version: 1, target: { song_id: 727, title: 'Beer Drinkers and Hell Raisers', artist: 'ZZ Top' },
    explicit_recording_choice: { mbid, title: 'Beer Drinkers & Hell Raisers', artist: 'ZZ Top', disambiguation: 'original stereo mix' },
    lookup: { status: 'ok', identity_validated: true, mbid, title: 'Beer Drinkers & Hell Raisers', artist: 'ZZ Top',
      disambiguation: 'original stereo mix', response_digest: digest('a'), release_year_candidates: [
        { value: 1973, source: 'MusicBrainz', response_digest: digest('a') }] },
    acousticbrainz: { status: 'ok', mbid, submission_count: 138, submission_enumeration: 'partial', sampled_offsets: [0, 68, 137],
      scalars: { original_key: [
        { value: { key_key: 'C', key_scale: 'minor' }, source: 'AcousticBrainz', response_digest: digest('b'), submission_id: `${mbid}:0`, key_strength: 0.71 },
        { value: { key_key: 'G', key_scale: 'major' }, source: 'AcousticBrainz', response_digest: digest('c'), submission_id: `${mbid}:68`, key_strength: 0.74 },
        { value: { key_key: 'C', key_scale: 'minor' }, source: 'AcousticBrainz', response_digest: digest('d'), submission_id: `${mbid}:137`, key_strength: 0.70 }],
      bpm: [134.480422974, 134.510665894, 134.438598633].map((value, index) => ({ value, source: 'AcousticBrainz',
        response_digest: digest(['b', 'c', 'd'][index]), submission_id: `${mbid}:${[0, 68, 137][index]}` })) } } };
}

function fakePool(song = baselineSong(), events = [], failAt = null) {
  const client = {
    async query(sql, params = []) {
      events.push(`query:${sql.trim().split(/\s+/).slice(0, 2).join(' ')}`);
      if (failAt === 'BEGIN' && sql === 'BEGIN READ ONLY' || failAt === 'COMMIT' && sql === 'COMMIT' ||
          failAt === 'SONG' && sql.includes('FROM public.songs s JOIN public.artists')) throw new Error('synthetic read failure');
      if (sql === 'BEGIN READ ONLY' || sql === 'COMMIT' || sql === 'ROLLBACK') return { rows: [] };
      if (sql.includes('FROM public.songs s JOIN public.artists')) return { rows: [{ ...song, artist_name: song.artist }] };
      if (sql.includes('FROM public.song_genres')) return { rows: song.genres.map(row => ({ song_id: song.id, ...row })) };
      if (sql.includes('FROM public.song_vibes')) return { rows: song.vibes.map(row => ({ song_id: song.id, ...row })) };
      if (sql === 'SELECT id,name FROM public.genres ORDER BY id') return { rows: [
        { id: 1, name: 'Rock' }, { id: 2, name: 'Hard Rock' }, { id: 3, name: 'Assigned Genre' },
      ] };
      if (sql === 'SELECT id,name FROM public.vibes ORDER BY id') return { rows: [
        { id: 4, name: 'Calm' }, { id: 5, name: 'Assigned Vibe' },
      ] };
      throw new Error('unexpected synthetic query');
    },
    release() { events.push('client:released'); },
  };
  return { connect: async () => client, end: async () => events.push('pool:ended') };
}

function geminiClient(events, response = { release_year: 1978, bpm: 104.2, original_key: { key_key: 'G', key_scale: 'major' },
  genres: ['Rock', 'Hard Rock'], vibes: ['Calm'] }) {
  const calls = [];
  const client = createSongEnrichmentProviderClient({ cache: null, config: {
    maxAttempts: 1, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
    geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024, musicBrainzUserAgent: UA,
  }, transport: async request => {
    calls.push(request);
    events.push('provider:dispatch');
    expect(events).toContain('client:released');
    const body = { status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify(response) }] }],
      usage: { total_input_tokens: 15, total_output_tokens: 8, total_tokens: 23 } };
    return { status: 200, headers: {}, body: Buffer.from(JSON.stringify(body)) };
  } });
  return { client, calls };
}

afterEach(async () => { await Promise.all(temporaries.splice(0).map(directory => fs.rm(directory, { recursive: true, force: true }))); });

describe('explicit Gemini fallback preview command', () => {
  it('sanitizes validation reason and usage into the standard CLI report and ledger', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-safe-validation-'));
    temporaries.push(parent);
    const outputDirectory = path.join(parent, 'run');
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', outputDirectory], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs, pool: fakePool(baselineSong()),
      transport: async () => ({ status: 200, body: JSON.stringify({ status: 'completed',
        steps: [{ type: 'model_output', content: [{ type: 'text', text: 'RAW_RESPONSE_SECRET {' }] }],
        usage: { total_input_tokens: 21, total_output_tokens: 9, total_tokens: 30 } }) }),
    });
    expect(result.report.gemini_suggestions).toMatchObject({ status: 'invalid_response',
      failure_details: { validation_reason: 'invalid_json' }, token_usage: { input_tokens: 21, output_tokens: 9, total_tokens: 30 } });
    expect(result.report.attempts.ledger).toContainEqual(expect.objectContaining({ status: 'invalid_response',
      failure_details: { failure_phase: 'response', validation_reason: 'invalid_json' },
      token_usage: { input_tokens: 21, output_tokens: 9, total_tokens: 30 } }));
    const saved = await fs.readFile(result.artifacts.jsonPath, 'utf8');
    expect(saved).not.toMatch(/RAW_RESPONSE_SECRET|synthetic-private-key/);
  });

  it('reports a null Interactions envelope as invalid_response through adapter, ledger and saved report', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-null-envelope-'));
    temporaries.push(parent);
    const outputDirectory = path.join(parent, 'run');
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', outputDirectory], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs, pool: fakePool(baselineSong()),
      transport: async () => ({ status: 200, headers: {}, body: 'null' }),
    });
    const expectedDetails = { failure_phase: 'response', validation_reason: 'interaction_envelope_invalid' };
    expect(result.report.gemini_suggestions).toMatchObject({ status: 'invalid_response', failure_details: expectedDetails });
    expect(result.report.attempts.ledger).toContainEqual(expect.objectContaining({ status: 'invalid_response',
      failure_details: expectedDetails }));
    const savedText = await fs.readFile(result.artifacts.jsonPath, 'utf8');
    const saved = JSON.parse(savedText);
    expect(saved.gemini_suggestions).toMatchObject({ status: 'invalid_response', failure_details: expectedDetails });
    expect(saved.attempts.ledger).toContainEqual(expect.objectContaining({ status: 'invalid_response',
      failure_details: expectedDetails }));
    expect(savedText).not.toMatch(/synthetic-private-key|postgresql:\/\/|RAW_RESPONSE/);
  });

  it('requires explicit one-song opt-in before reading config or causing effects', async () => {
    let reads = 0;
    await expect(fallback.main([], { configurationOptions: { filesystem: { readFile: async () => { reads += 1; } } },
      pool: fakePool(), providerClient: {} })).rejects.toMatchObject({ code: 'INVALID_ARGUMENTS' });
    expect(reads).toBe(0);
    expect(fallback.parseArguments(['--gemini-opt-in', '--song-id', '73', '--output-dir', 'backups/run']))
      .toMatchObject({ optIn: true, songId: 73, outputDirectory: 'backups/run' });
  });

  it('rejects missing or invalid credentials before filesystem, database or provider effects', async () => {
    const effects = { fs: 0, db: 0, provider: 0 };
    const args = ['--gemini-opt-in', '--song-id', '73', '--output-dir', 'backups/run'];
    const filesystem = { readFile: async () => { effects.fs += 1; return ''; } };
    await expect(fallback.main(args, { configurationOptions: { filesystem, processEnv: { GEMINI_API_KEY: 'bad\r\nInjected: x' }, envPath: 'synthetic' },
      outputOptions: { isIgnored: async () => { effects.fs += 1; return true; } },
      pool: { async connect() { effects.db += 1; } }, providerClient: { async requestGeminiTags() { effects.provider += 1; } } }))
      .rejects.toMatchObject({ code: 'GEMINI_CREDENTIAL_INVALID' });
    expect(effects).toEqual({ fs: 1, db: 0, provider: 0 });
    await expect(fallback.main(args, { configurationOptions: { filesystem: { readFile: async () => '' }, processEnv: {}, envPath: 'synthetic' },
      outputOptions: { isIgnored: async () => { effects.fs += 1; return true; } },
      pool: { async connect() { effects.db += 1; } } })).rejects.toMatchObject({ code: 'GEMINI_CREDENTIAL_REQUIRED' });
    expect(effects).toEqual({ fs: 1, db: 0, provider: 0 });
  });

  it('captures a read-only baseline, releases its client, then sends one private bounded request', async () => {
    const events = [];
    const pool = fakePool(baselineSong({ genres: [{ id: 3, name: 'Assigned Genre', is_primary: true }],
      vibes: [{ id: 5, name: 'Assigned Vibe' }] }), events);
    const { client, calls } = geminiClient(events);
    const result = await fallback.runPreview({ pool, providerClient: client, songId: 73, apiKey: 'synthetic-secret-key' });
    expect(calls).toHaveLength(1);
    expect(events.indexOf('client:released')).toBeLessThan(events.indexOf('provider:dispatch'));
    const sent = Buffer.from(calls[0].body).toString('utf8');
    expect(sent).not.toContain('synthetic-secret-key');
    expect(sent).not.toContain('mbid');
    expect(sent).not.toContain('chart');
    const requestInput = JSON.parse(sent).input;
    expect(requestInput).toContain('requested_missing_fields');
    expect(requestInput).not.toContain('factual_evidence');
    expect(result.report).toMatchObject({ database_writes: false, human_review_required: true,
      approved_apply_manifest: null, requested_missing_fields: ['release_year', 'original_key', 'bpm'],
      baseline: { genres: [{ id: 3, name: 'Assigned Genre', is_primary: true }], vibes: [{ id: 5, name: 'Assigned Vibe' }] } });
    expect(result.report.gemini_suggestions.scalar_states).toEqual({ release_year: 'unapproved_candidate',
      original_key: 'unapproved_candidate', bpm: 'unapproved_candidate' });
    expect(result.report.gemini_suggestions.genres.map(row => row.name)).toEqual(['Rock', 'Hard Rock']);
    expect(result.report.gemini_suggestions.vibes.map(row => row.name)).toEqual(['Calm']);
    expect(result.report.b1_validation_preview.has_changes).toBe(false);
    expect(result.attempts).toEqual({ total: 1, gemini: 1 });
  });

  it('converts saved-trial evidence and constructs the actual Gemini payload with resolved facts excluded', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-factual-context-'));
    temporaries.push(parent);
    const input = path.join(parent, 'trial.json');
    const context = convertSavedTrial(savedTrialFixture());
    await fs.writeFile(input, JSON.stringify(context));
    const song = baselineSong({ id: 727, title: 'Beer Drinkers and Hell Raisers', artist: 'ZZ Top' });
    const events = [];
    const requests = [];
    const outputDirectory = path.join(parent, 'run');
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '727', '--output-dir', outputDirectory,
      '--factual-report', input], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs,
      pool: fakePool(song, events), transport: async request => {
        requests.push(request);
        events.push('provider:dispatch');
        const body = { status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({
          release_year: null, bpm: 134, original_key: { key_key: 'C', key_scale: 'minor' }, genres: ['Rock'], vibes: ['Calm'],
        }) }] }], usage: { total_input_tokens: 21, total_output_tokens: 9, total_tokens: 30 } };
        return { status: 200, headers: {}, body: Buffer.from(JSON.stringify(body)) };
      },
    });
    expect(requests).toHaveLength(1);
    expect(events.indexOf('client:released')).toBeLessThan(events.indexOf('provider:dispatch'));
    const payload = JSON.parse(Buffer.from(requests[0].body).toString()).input;
    expect(payload).toContain('"requested_missing_fields":["original_key","bpm"]');
    const parsedPayload = JSON.parse(payload.slice(payload.indexOf('{')));
    expect(parsedPayload.factual_evidence).toMatchObject({ factual_context_version: 1,
      recording: { mbid: '62ec7207-aa84-43b6-949e-8066b6be2cad', validated: true },
      submission_coverage: { total_available: 138, sampled_offsets: [0, 68, 137], completeness: 'partial' } });
    expect(parsedPayload.factual_evidence.scalar_candidates.release_year.candidates[0]).toMatchObject({ value: 1973,
      source: 'MusicBrainz', response_digest: 'a'.repeat(64) });
    expect(parsedPayload.factual_evidence.scalar_candidates.original_key.candidates.map(item => item.response_digest))
      .toEqual(['b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64)]);
    expect(result.report.requested_missing_fields).toEqual(['original_key', 'bpm']);
    expect(result.report.gemini_suggestions.scalars.release_year).toEqual([]);
    expect(result.report.gemini_suggestions.scalar_states.release_year).toBe('factual_candidate_preserved');
    expect(result.report.factual_evidence.scalar_candidates.release_year.candidates[0].value).toBe(1973);
    expect(JSON.stringify(parsedPayload)).not.toMatch(/chart|lyrics|synthetic-private-key/i);
    expect(context.target.song_id).toBe(727);
    expect(context).not.toHaveProperty('attempts');
    expect(context).not.toHaveProperty('report_version');
  });

  it('classifies distinct sampled BPM decimals by the existing half-up integer rule', () => {
    const trial = savedTrialFixture();
    trial.acousticbrainz.scalars.bpm = [134.41, 134.44, 134.43].map((value, index) => ({ value,
      source: 'AcousticBrainz', response_digest: ['e', 'f', '0'][index].repeat(64),
      submission_id: `${trial.acousticbrainz.mbid}:${[0, 68, 137][index]}` }));
    const context = fallback.normalizeFactualContext(convertSavedTrial(trial), 727);
    expect(context.conflicts.bpm).toBe(false);
    expect(context.scalar_candidates.bpm.candidates.map(item => item.value)).toEqual([134.41, 134.44, 134.43]);
    expect(fallback.requestedMissingFields(baselineSong(), context)).toEqual(['original_key']);
  });

  it('rejects target, identity and malformed scalar provenance before output creation or dispatch', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-factual-reject-'));
    temporaries.push(parent);
    const base = convertSavedTrial(savedTrialFixture());
    const malformed = structuredClone(base);
    malformed.scalar_candidates.bpm.candidates[0].response_digest = 'not-a-digest';
    const badReference = structuredClone(base);
    badReference.scalar_candidates.original_key.candidates[0].submission_reference = '00000000-0000-4000-8000-000000000000:0';
    const variants = [
      { ...base, target: { ...base.target, song_id: 728 } },
      { ...base, target: { ...base.target, artist: 'Different Artist' } },
      malformed,
      badReference,
    ];
    for (let index = 0; index < variants.length; index += 1) {
      const input = path.join(parent, `bad-${index}.json`);
      const out = path.join(parent, `run-${index}`);
      await fs.writeFile(input, JSON.stringify(variants[index]));
      let dispatches = 0;
      await expect(fallback.main(['--gemini-opt-in', '--song-id', '727', '--output-dir', out, '--factual-report', input], {
        configuration: { apiKey: 'synthetic-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
        projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs,
        pool: fakePool(baselineSong({ id: 727, title: 'Beer Drinkers and Hell Raisers', artist: 'ZZ Top' })),
        transport: async () => { dispatches += 1; throw new Error('must not dispatch'); },
      })).rejects.toMatchObject({ code: index === 1 ? 'FACTUAL_CONTEXT_IDENTITY_MISMATCH' : 'FACTUAL_CONTEXT_INVALID' });
      expect(dispatches).toBe(0);
      await expect(fs.stat(out)).rejects.toMatchObject({ code: 'ENOENT' });
    }
  });

  it('rejects a model year when factual evidence excluded year from the request', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-unrequested-year-'));
    temporaries.push(parent);
    const input = path.join(parent, 'trial.json');
    await fs.writeFile(input, JSON.stringify(convertSavedTrial(savedTrialFixture())));
    let calls = 0;
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '727', '--output-dir', path.join(parent, 'run'),
      '--factual-report', input], {
      configuration: { apiKey: 'synthetic-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs,
      pool: fakePool(baselineSong({ id: 727, title: 'Beer Drinkers and Hell Raisers', artist: 'ZZ Top' })),
      transport: async () => { calls += 1; return { status: 200, headers: {}, body: Buffer.from(JSON.stringify({ status: 'completed',
        steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({ release_year: 1973, bpm: null,
          original_key: null, genres: [], vibes: [] }) }] }], usage: { total_input_tokens: 1, total_output_tokens: 1, total_tokens: 2 } })) }; },
    });
    expect(calls).toBe(1);
    expect(result.report.gemini_suggestions.status).toBe('invalid_response');
    expect(result.report.gemini_suggestions.scalars.release_year).toEqual([]);
  });

  it('requests only missing scalars while preserving populated fields and assignments', async () => {
    const events = [];
    const song = baselineSong({ release_year: 1978, original_key: 'D', bpm: 104,
      genres: [{ id: 3, name: 'Assigned Genre', is_primary: true }], vibes: [{ id: 5, name: 'Assigned Vibe' }] });
    const pool = fakePool(song, events);
    const { client, calls } = geminiClient(events, { release_year: null, bpm: null, original_key: null, genres: [], vibes: [] });
    const result = await fallback.runPreview({ pool, providerClient: client, songId: 73, apiKey: 'synthetic-key' });
    expect(calls).toHaveLength(1);
    const payload = JSON.parse(Buffer.from(calls[0].body).toString()).input;
    expect(payload).toContain('"requested_missing_fields":[]');
    expect(result.report.gemini_suggestions.scalar_states).toEqual({ release_year: 'populated_preserved',
      original_key: 'populated_preserved', bpm: 'populated_preserved' });
    expect(result.report.baseline).toMatchObject({ release_year: 1978, original_key: 'D', bpm: 104,
      genres: [{ id: 3, is_primary: true }], vibes: [{ id: 5 }] });
    expect(result.report.b1_validation_preview).toBeNull();
  });

  it('configures exactly one bounded Gemini attempt with no cache and respects pre-dispatch cancellation', async () => {
    let calls = 0;
    const client = fallback.createFallbackProviderClient({ musicBrainzUserAgent: UA, transport: async () => {
      calls += 1;
      return { status: 503, headers: {}, body: Buffer.from('{}') };
    } });
    const failed = await client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions: { genres: [{ name: 'Rock' }], vibes: [] }, apiKey: 'synthetic-key' });
    expect(failed).toMatchObject({ status: 'transient_error', failure_details: { failure_phase: 'http', http_status: 503,
      https_started: true } });
    expect(calls).toBe(1);
    expect(client.getAttemptCounts()).toEqual({ total: 1, gemini: 1 });
    const events = [];
    const controller = new AbortController();
    controller.abort();
    const cancelledClient = fallback.createFallbackProviderClient({ musicBrainzUserAgent: UA,
      transport: async () => { calls += 1; return { status: 200, body: '{}' }; } });
    const cancelled = await fallback.runPreview({ pool: fakePool(baselineSong(), events), providerClient: cancelledClient,
      songId: 73, apiKey: 'synthetic-key', signal: controller.signal });
    expect(cancelled.report.gemini_suggestions.status).toBe('cancelled');
    expect(cancelledClient.getAttemptCounts()).toEqual({ total: 0, gemini: 0 });
    expect(calls).toBe(1);
    expect(events).toContain('client:released');
  });

  it('exclusively writes unapproved JSON and readable output and never overwrites prior files', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-preview-'));
    temporaries.push(parent);
    const directory = path.join(parent, 'run');
    const events = [];
    const { client } = geminiClient(events, { release_year: null, bpm: null, original_key: null, genres: [], vibes: [] });
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', directory], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs,
      pool: fakePool(baselineSong(), events), providerClient: client,
    });
    const saved = await fs.readFile(result.artifacts.jsonPath, 'utf8');
    expect(events).toContain('pool:ended');
    expect(saved).not.toContain('synthetic-private-key');
    expect(saved).toContain('human_review_required');
    await expect(fs.readFile(result.artifacts.summaryPath, 'utf8')).resolves.toContain('unapproved');
    await expect(fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', directory], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs,
      pool: fakePool(), providerClient: client,
    })).rejects.toMatchObject({ code: 'OUTPUT_DIRECTORY_EXISTS' });
  });

  it('does not reintroduce legacy usageMetadata into the saved CLI report', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-usage-report-'));
    temporaries.push(parent);
    const outputDirectory = path.join(parent, 'run');
    const body = { status: 'completed', usageMetadata: { promptTokenCount: 700,
      candidatesTokenCount: 800, totalTokenCount: 1500 },
      steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({
        release_year: null, bpm: null, original_key: null, genres: [], vibes: [], unexpected: true,
      }) }] }] };
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', outputDirectory], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs,
      pool: fakePool(), transport: async () => ({ status: 200, headers: {}, body: Buffer.from(JSON.stringify(body)) }),
    });
    const report = JSON.parse(await fs.readFile(result.artifacts.jsonPath, 'utf8'));
    expect(report.gemini_suggestions.status).toBe('invalid_response');
    expect(report.gemini_suggestions).not.toHaveProperty('token_usage');
    expect(report.attempts.ledger).toEqual([{ provider: 'Gemini', operation: 'missing_metadata_fallback',
      attempt: 1, status: 'invalid_response', failure_details: { failure_phase: 'response',
        validation_reason: 'unexpected_field', validation_field: 'response' } }]);
    expect(await fs.readFile(result.artifacts.jsonPath, 'utf8')).not.toContain('700');
    expect(await fs.readFile(result.artifacts.jsonPath, 'utf8')).not.toContain('1500');
  });

  it('preserves allowlisted failure details through the saved CLI report and strips secret properties', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-safe-failure-'));
    temporaries.push(parent);
    const outputDirectory = path.join(parent, 'run');
    const body = { status: 'transient_error', failure_details: { failure_phase: 'http', http_status: 429,
      https_started: true, secret: 'DO_NOT_LEAK', url: 'https://user:pass@invalid.test' } };
    const result = await fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', outputDirectory], {
      configuration: { apiKey: 'synthetic-private-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs, pool: fakePool(),
      providerClient: { requestGeminiTags: async () => body, getAttemptCounts: () => ({ total: 1, gemini: 1 }),
        getRunLedger: () => [{ provider: 'Gemini', operation: 'missing_metadata_fallback', attempt: 1, status: 'transient_error',
          failure_details: { ...body.failure_details, transport_error_code: 'ECONNRESET', raw: 'SECRET_RAW' } }] },
    });
    const savedText = await fs.readFile(result.artifacts.jsonPath, 'utf8');
    const report = JSON.parse(savedText);
    expect(report.gemini_suggestions.failure_details).toEqual({ failure_phase: 'http', http_status: 429, https_started: true });
    expect(report.attempts.ledger[0].failure_details).toEqual({ failure_phase: 'http', http_status: 429,
      transport_error_code: 'ECONNRESET', https_started: true });
    expect(savedText).not.toMatch(/DO_NOT_LEAK|SECRET_RAW|user:pass/);
  });

  it.each(['BEGIN', 'SONG', 'COMMIT'])('rolls back/releases the read-only client and closes the pool after %s failure', async failAt => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-db-failure-'));
    temporaries.push(parent);
    const outputDirectory = path.join(parent, 'run');
    const events = [];
    let providerCalls = 0;
    const providerClient = { async requestGeminiTags() { providerCalls += 1; }, getAttemptCounts: () => ({ total: 0, gemini: 0 }), getRunLedger: () => [] };
    const pool = fakePool(baselineSong(), events, failAt);
    await expect(fallback.main(['--gemini-opt-in', '--song-id', '73', '--output-dir', outputDirectory], {
      configuration: { apiKey: 'synthetic-key', databaseUrl: 'postgresql://synthetic.invalid/db', musicBrainzUserAgent: UA },
      projectRoot: parent, outputOptions: { isIgnored: async () => true }, filesystem: fs, pool, providerClient,
    })).rejects.toMatchObject({ code: 'BASELINE_READ_FAILED' });
    expect(events).toContain('client:released');
    expect(events).toContain('pool:ended');
    expect(events).toContain('query:ROLLBACK');
    expect(providerCalls).toBe(0);
    await expect(fs.stat(outputDirectory)).rejects.toMatchObject({ code: 'ENOENT' });
  });

  it('cleans only its first exclusively created artifact when the second write fails', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-gemini-artifact-failure-'));
    temporaries.push(parent);
    const removed = [];
    const opened = [];
    const filesystem = {
      async open(file, flags, mode) {
        opened.push({ file: path.basename(file), flags, mode });
        if (opened.length === 2) throw new Error('synthetic second-file failure');
        const handle = await fs.open(file, flags, mode);
        return { writeFile: (...args) => handle.writeFile(...args), close: () => handle.close() };
      },
      async unlink(file) { removed.push(path.basename(file)); return fs.unlink(file); },
    };
    await expect(fallback.writeReportFiles(parent, { gemini_suggestions: { status: 'ok', scalars: {
      release_year: [], original_key: [], bpm: [] }, scalar_states: { release_year: 'abstained', original_key: 'abstained', bpm: 'abstained' },
      genres: [], vibes: [] }, baseline: baselineSong(), requested_missing_fields: [], attempts: { total: 0 } }, filesystem))
      .rejects.toMatchObject({ code: 'ARTIFACT_WRITE_FAILED' });
    expect(opened.map(item => item.flags)).toEqual(['wx', 'wx']);
    expect(opened[0].mode).toBe(0o600);
    expect(removed).toEqual(['report-v1.json']);
    expect(await fs.readdir(parent)).toEqual([]);
  });

  it('retains sanitized factual conflicts and unavailable status separately from Gemini', () => {
    const b4Report = { report_version: 1, songs: [{ song_id: 73,
      scalar_candidates: { release_year: { state: 'unapproved_candidates', evidence_status: 'ok', conflicting_evidence: true,
        candidates: [{ value: 1978, source: 'MusicBrainz', response_digest: 'a'.repeat(64) },
          { value: 1979, source: 'MusicBrainz', response_digest: 'b'.repeat(64) },
          { value: 'PRIVATE', source: 'Gemini', response_digest: 'c'.repeat(64) }] },
      original_key: { state: 'provider_unavailable', evidence_status: 'absent', conflicting_evidence: false, candidates: [] },
      bpm: { state: 'not_attempted', evidence_status: 'not_attempted', candidates: [] } },
      evidence: { genres: [{ name: 'Rock', source: 'MusicBrainz', response_digest: 'd'.repeat(64), chart: 'PRIVATE' }], vibes: [] },
      tag_candidates: { genres: { unknown_labels: [{ label: 'Unmatched', evidence: { source: 'MusicBrainz', response_digest: 'e'.repeat(64) } }],
        ambiguous_labels: [] }, vibes: { unknown_labels: [], ambiguous_labels: [] } },
      recording: { release_date_comparison: { lookup_year_discrepancy: true } },
    }] };
    const factual = fallback.safeFactualEvidence(b4Report, 73);
    expect(fallback.normalizeFactualContext(b4Report, 73)).toMatchObject({ context_version: 'b4-v1' });
    expect(factual.scalar_candidates.release_year).toMatchObject({ conflicting_evidence: true,
      candidates: [{ value: 1978, source: 'MusicBrainz' }, { value: 1979, source: 'MusicBrainz' }] });
    expect(factual.scalar_candidates.original_key.evidence_status).toBe('absent');
    expect(factual.genre_evidence).toEqual([{ name: 'Rock', source: 'MusicBrainz', response_digest: 'd'.repeat(64) }]);
    expect(factual.tag_diagnostics.genres.unknown_labels).toEqual([{ label: 'Unmatched', evidence: { source: 'MusicBrainz', response_digest: 'e'.repeat(64) } }]);
    expect(factual.search_lookup_year_discrepancy).toBe(true);
    expect(JSON.stringify(factual)).not.toContain('PRIVATE');
  });
});
