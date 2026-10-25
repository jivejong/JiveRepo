import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { ABSENCE_TTL_MS, SUCCESS_TTL_MS, cacheKey, createSongEnrichmentCache, normalizeCacheRequest } =
  require('../../server/songEnrichmentCache.js');
const { GEMINI_PROMPT_VERSION, GEMINI_SCHEMA_VERSION, createSongEnrichmentProviderClient } =
  require('../../server/songEnrichmentProviders.js');
const { resolveRecordingCandidate } = require('../../server/existingSongEnrichment.js');

const MBID = 'a1111111-1111-4111-8111-111111111111';
const REQUIRED_BOUNDS = { maxAttempts: 12, maxGeminiAttempts: 2, timeoutMs: 10000, maxResponseBytes: 1000000,
  geminiMaxInputBytes: 32000, geminiMaxOutputTokens: 1000 };
const baseRequest = { provider: 'MusicBrainz', operation: 'recording_search', parameters: { title: 'Song  Title', artist: 'The Artist' } };
let root;

beforeEach(async () => {
  root = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-song-enrichment-cache-'));
});

afterEach(async () => {
  vi.restoreAllMocks();
  if (root) await fs.rm(root, { recursive: true, force: true });
});

describe('song enrichment response cache', () => {
  it('requires an explicit directory and positive entry bound', async () => {
    await expect(createSongEnrichmentCache()).rejects.toThrow(/explicit cache directory/);
    await expect(createSongEnrichmentCache({ directory: root, maxEntryBytes: 0 })).rejects.toThrow(/positive integer/);
  });

  it('returns a deterministic integrity hash and rejects corrupted private entries', async () => {
    const cache = await createSongEnrichmentCache({ directory: path.join(root, 'integrity-cache') });
    const empty = await cache.verifyIntegrity();
    await cache.set(baseRequest, { status: 'ok', response: Buffer.from('{"recordings":[]}') });
    const first = await cache.verifyIntegrity();
    expect(first.entries).toBe(1);
    expect(first.sha256).not.toBe(empty.sha256);
    expect((await cache.verifyIntegrity()).sha256).toBe(first.sha256);
    const file = path.join(root, 'integrity-cache', `${cacheKey(baseRequest)}.json`);
    await fs.writeFile(file, 'corrupted cache entry');
    await expect(cache.verifyIntegrity()).rejects.toThrow(/invalid|hash/i);
  });

  it('normalizes search identity and MBID cache keys without including credentials', () => {
    expect(cacheKey(baseRequest)).toBe(cacheKey({
      ...baseRequest, parameters: { title: ' song title ', artist: 'the\tartist' },
    }));
    const lookup = { provider: 'MusicBrainz', operation: 'recording_lookup', parameters: { mbid: MBID } };
    expect(cacheKey(lookup)).toBe(cacheKey({ ...lookup, parameters: { mbid: MBID.toUpperCase() } }));
    expect(() => cacheKey({ ...lookup, parameters: { mbid: 'bad' } })).toThrow(/MBID/);
    const gemini = {
      provider: 'Gemini', operation: 'missing_metadata_fallback', parameters: { title: 'Song', artist: 'Artist', requestedFields: ['bpm'], context: {} },
      parserVersion: '2', model: 'gemini-3.1-flash-lite', promptVersion: 'p1', schemaVersion: 's1',
      definitionCatalog: { genres: ['Rock', 'Pop'], vibes: ['Upbeat'] },
      validationCatalog: { genres: ['Rock', 'Pop', 'Assigned'], vibes: ['Upbeat'] },
    };
    expect(cacheKey(gemini)).toBe(cacheKey({ ...gemini, definitionCatalog: { genres: ['Pop', 'Rock'], vibes: ['Upbeat'] } }));
    expect(cacheKey(gemini)).not.toBe(cacheKey({ ...gemini, model: 'other-model' }));
    expect(cacheKey(gemini)).not.toBe(cacheKey({ ...gemini, promptVersion: 'p2' }));
    expect(cacheKey(gemini)).not.toBe(cacheKey({ ...gemini, schemaVersion: 'schema-v2' }));
    expect(cacheKey(gemini)).not.toBe(cacheKey({ ...gemini,
      parameters: { ...gemini.parameters, requestedFields: ['original_key', 'bpm'] } }));
    expect(cacheKey(gemini)).not.toBe(cacheKey({ ...gemini, definitionCatalog: { genres: ['Rock'], vibes: ['Upbeat'] } }));
    expect(cacheKey(gemini)).not.toBe(cacheKey({ ...gemini, validationCatalog: { genres: ['Rock', 'Pop', 'Assigned', 'Duplicate'], vibes: ['Upbeat'] } }));
    expect(JSON.stringify(gemini)).not.toContain('credential');
  });

  it('versions single-response MusicBrainz search cache identity and fixes limit and offset', () => {
    const search = { ...baseRequest, parserVersion: 'musicbrainz-search-pages-v2', parameters: { ...baseRequest.parameters,
      query: 'recording:"Song Title" AND artist:"The Artist"', limit: 100, offset: 0 } };
    const changedQuery = { ...search, parameters: { ...search.parameters, query: 'recording:"Other" AND artist:"The Artist"' } };
    expect(cacheKey(search)).not.toBe(cacheKey(changedQuery));
    expect(normalizeCacheRequest(search).parserVersion).toBe('musicbrainz-search-single-response-v1');
    expect(normalizeCacheRequest(search).parameters).toMatchObject({ limit: 100, offset: 0 });
    expect(() => cacheKey({ ...search, parameters: { ...search.parameters, limit: 25 } })).toThrow(/limit must be 100/);
    expect(() => cacheKey({ ...search, parameters: { ...search.parameters, offset: 25 } })).toThrow(/offset must be zero/);
  });

  it('writes and reads raw successful responses with digest, fetch time, version and seven-day expiry', async () => {
    let now = 1_800_000_000_000;
    const cache = await createSongEnrichmentCache({ directory: path.join(root, 'private-cache'), clock: () => now });
    const raw = Buffer.from('{"recordings":[]}');
    expect(await cache.set(baseRequest, { status: 'ok', response: raw })).toBe(true);
    const hit = await cache.get(baseRequest);
    expect(hit.status).toBe('ok');
    expect(hit.response).toEqual(raw);
    expect(hit.response_digest).toMatch(/^[a-f0-9]{64}$/);
    expect(hit.fetched_at).toBe(now);
    now += SUCCESS_TTL_MS - 1;
    expect(await cache.get(baseRequest)).not.toBeNull();
    now += 2;
    expect(await cache.get(baseRequest)).toBeNull();
  });

  it('gives confirmed absence a one-hour expiry and rejects caching errors', async () => {
    let now = 1_800_000_000_000;
    const cache = await createSongEnrichmentCache({ directory: root, clock: () => now });
    expect(await cache.set(baseRequest, { status: 'absent', response: '{"recordings":[]}' })).toBe(true);
    now += ABSENCE_TTL_MS - 1;
    expect((await cache.get(baseRequest)).status).toBe('absent');
    now += 2;
    expect(await cache.get(baseRequest)).toBeNull();
    await expect(cache.set(baseRequest, { status: 'transient_error', response: '{}' })).rejects.toThrow(/Only successful/);
  });

  it('treats digest corruption, malformed entries and oversized cache files as misses', async () => {
    const cache = await createSongEnrichmentCache({ directory: root, maxEntryBytes: 2048 });
    await cache.set(baseRequest, { status: 'ok', response: '{}' });
    const file = path.join(root, `${cache.cacheKey(baseRequest)}.json`);
    const original = JSON.parse(await fs.readFile(file, 'utf8'));
    await fs.writeFile(file, JSON.stringify({ ...original, response: Buffer.from('{"bad":true}').toString('base64') }));
    expect(await cache.get(baseRequest)).toBeNull();
    await fs.writeFile(file, 'not-json');
    expect(await cache.get(baseRequest)).toBeNull();
    await fs.writeFile(file, 'x'.repeat(2049));
    expect(await cache.get(baseRequest)).toBeNull();
  });

  it('rejects unsupported cache versions and mismatched keys', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    await cache.set(baseRequest, { status: 'ok', response: '{}' });
    const file = path.join(root, `${cache.cacheKey(baseRequest)}.json`);
    const original = JSON.parse(await fs.readFile(file, 'utf8'));
    await fs.writeFile(file, JSON.stringify({ ...original, cache_version: 99 }));
    expect(await cache.get(baseRequest)).toBeNull();
    await fs.writeFile(file, JSON.stringify({ ...original, cache_key: '0'.repeat(64) }));
    expect(await cache.get(baseRequest)).toBeNull();
  });

  it('uses distinct AcousticBrainz submission offsets and makes cache hits provider-independent', async () => {
    const count = { provider: 'AcousticBrainz', operation: 'recording_count', parameters: { mbid: MBID } };
    const first = { provider: 'AcousticBrainz', operation: 'recording_submission', parameters: { mbid: MBID, offset: 0 } };
    const second = { ...first, parameters: { mbid: MBID, offset: 1 } };
    expect(cacheKey(first)).not.toBe(cacheKey(second));
    const cache = await createSongEnrichmentCache({ directory: root });
    await cache.set(count, { status: 'ok', response: '{"count":2}' });
    await cache.set(first, { status: 'ok', response: '{"0":{}}' });
    expect((await cache.get(first)).response.toString()).toBe('{"0":{}}');
    expect(await cache.get(second)).toBeNull();
  });

  it('atomically tolerates concurrent writes for the same key', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    await Promise.all([
      cache.set(baseRequest, { status: 'ok', response: '{"value":1}' }),
      cache.set(baseRequest, { status: 'ok', response: '{"value":2}' }),
    ]);
    const hit = await cache.get(baseRequest);
    expect(['{"value":1}', '{"value":2}']).toContain(hit.response.toString());
    expect(await fs.readdir(root)).toEqual([`${cache.cacheKey(baseRequest)}.json`]);
  });

  it('replaces sequentially and preserves old content with honest failure and owned-temp cleanup', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    expect(await cache.set(baseRequest, { status: 'ok', response: '{"value":"old"}' })).toBe(true);
    expect(await cache.set(baseRequest, { status: 'ok', response: '{"value":"new"}' })).toBe(true);
    expect((await cache.get(baseRequest)).response.toString()).toBe('{"value":"new"}');

    const rename = vi.spyOn(fs, 'rename').mockRejectedValueOnce(Object.assign(new Error('rename denied'), { code: 'EPERM' }));
    const lstat = vi.spyOn(fs, 'lstat').mockRejectedValueOnce(Object.assign(new Error('read failed'), { code: 'EIO' }));
    expect(await cache.set(baseRequest, { status: 'ok', response: '{"value":"rejected"}' })).toBe(false);
    expect(lstat).not.toHaveBeenCalled();
    lstat.mockRestore();
    expect((await cache.get(baseRequest)).response.toString()).toBe('{"value":"new"}');
    expect((await fs.readdir(root)).sort()).toEqual([`${cache.cacheKey(baseRequest)}.json`]);
    expect(rename).toHaveBeenCalledTimes(1);
  });

  it('serves provider cache hits without spending outbound attempt budget', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    let dispatches = 0;
    const config = { ...REQUIRED_BOUNDS, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (contact@example.test)' };
    const makeClient = () => createSongEnrichmentProviderClient({
      cache,
      config,
      transport: async () => {
        dispatches += 1;
        return { status: 200, headers: {}, body: JSON.stringify({ count: 1, offset: 0, recordings: [{
          id: MBID, title: 'Song Title', 'artist-credit': [{ artist: { name: 'The Artist' } }],
        }] }) };
      },
      delay: async () => new Promise(() => {}),
    });
    const first = makeClient();
    expect((await first.searchRecordings({ title: 'Song Title', artist: 'The Artist' })).cache_hit).toBe(false);
    expect(first.getAttemptCounts().total).toBe(1);
    const second = makeClient();
    const cached = await second.searchRecordings({ title: ' song   title ', artist: 'the artist' });
    expect(cached).toMatchObject({ status: 'ok', cache_hit: true });
    expect(second.getAttemptCounts()).toEqual({ total: 0, gemini: 0 });
    expect(dispatches).toBe(1);
  });

  it('rejects a truncated cached single response without exposing matchable candidates', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    const truncated = { count: 75, offset: 0, recordings: Array.from({ length: 74 }, (_, index) => ({
      id: `${String(index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`,
      title: index === 0 ? 'Song Title' : `Other ${index}`,
      'artist-credit': [{ artist: { name: 'The Artist' } }],
    })) };
    await cache.set({ provider: 'MusicBrainz', operation: 'recording_search', parserVersion: 'musicbrainz-search-single-response-v1',
      parameters: { title: 'Song Title', artist: 'The Artist', query: 'recording:"Song Title" AND artist:"The Artist"', limit: 100, offset: 0 } },
    { status: 'ok', response: JSON.stringify(truncated) });
    let dispatches = 0;
    const client = createSongEnrichmentProviderClient({ cache,
      config: { ...REQUIRED_BOUNDS, maxAttempts: 1, maxGeminiAttempts: 1, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (contact@example.test)' },
      transport: async () => {
        dispatches += 1;
        return { status: 200, headers: {}, body: JSON.stringify({ count: 1, offset: 0, recordings: [{
          id: MBID, title: 'Priming', 'artist-credit': [{ artist: { name: 'The Artist' } }],
        }] }) };
      },
      delay: async () => {},
    });
    const result = await client.searchRecordings({ title: 'Song Title', artist: 'The Artist' });
    expect(result).toMatchObject({ status: 'invalid_response', reason: 'incomplete_search_results', candidates: [],
      pagination: { expected_total: null, attempted_pages: 1, validated_pages: 0, collected_count: 0, complete: false },
      validation_diagnostic: { code: 'unexpected_page_length', current_page_total: 75, returned_recording_count: 74 } });
    expect(resolveRecordingCandidate({ title: 'Song Title', artist: 'The Artist' }, result.candidates))
      .toEqual({ matched: false, reason: 'no_exact_match', selection_reason: 'no_eligible_recording', eligible_candidate_count: 0,
        exclusion_counts: {}, missing_release_date_count: 0, earliest_year: null, same_year_candidate_mbids: [],
        matched_genre_counts: [], richness_comparison_complete: false, provisional_mbid: null, tie_break_reason: null });
    expect(dispatches).toBe(0);
    expect(client.getAttemptCounts().total).toBe(0);
  });

  it('fails closed when cancellation races a cached page or its cache write', async () => {
    const pageBytes = Buffer.from(JSON.stringify({ count: 1, offset: 0, recordings: [{
      id: MBID, title: 'Song Title', 'artist-credit': [{ artist: { name: 'The Artist' } }],
    }] }));
    const config = { ...REQUIRED_BOUNDS, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (contact@example.test)' };
    const preAbortedController = new AbortController();
    preAbortedController.abort();
    let preAbortedCacheReads = 0;
    const preAborted = createSongEnrichmentProviderClient({ config, cache: {
      get: async () => { preAbortedCacheReads += 1; return { status: 'ok', response: pageBytes, response_digest: 'a'.repeat(64) }; },
    }, transport: async () => { throw new Error('must not dispatch'); }, delay: async () => {} });
    expect(await preAborted.searchRecordings({ title: 'Song Title', artist: 'The Artist', signal: preAbortedController.signal }))
      .toMatchObject({ status: 'cancelled', candidates: [], pagination: { validated_pages: 0, collected_count: 0, complete: false } });
    expect(preAbortedCacheReads).toBe(0);

    const duringReadController = new AbortController();
    const duringRead = createSongEnrichmentProviderClient({ config, cache: {
      get: async () => { duringReadController.abort(); return { status: 'ok', response: pageBytes, response_digest: 'b'.repeat(64) }; },
    }, transport: async () => { throw new Error('cached hit must not dispatch'); }, delay: async () => {} });
    expect(await duringRead.searchRecordings({ title: 'Song Title', artist: 'The Artist', signal: duringReadController.signal }))
      .toMatchObject({ status: 'cancelled', candidates: [], pagination: { validated_pages: 0, collected_count: 0, complete: false } });

    const duringWriteController = new AbortController();
    const duringWrite = createSongEnrichmentProviderClient({ config, cache: {
      get: async () => null,
      set: async () => { duringWriteController.abort(); return true; },
    }, transport: async () => ({ status: 200, headers: {}, body: pageBytes }), delay: async () => {} });
    expect(await duringWrite.searchRecordings({ title: 'Song Title', artist: 'The Artist', signal: duringWriteController.signal }))
      .toMatchObject({ status: 'cancelled', candidates: [],
        pagination: { expected_total: 1, validated_pages: 1, collected_count: 1, complete: false } });
  });

  it('serves Gemini structured candidates from cache without spending provider attempt budget', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    const definitions = { genres: [{ name: 'Rock' }], vibes: [{ name: 'Upbeat' }] };
    const request = {
      provider: 'Gemini', operation: 'missing_metadata_fallback',
      parameters: { title: 'Song', artist: 'Artist', context: { factual_evidence: null }, requestedFields: [] },
      parserVersion: '2', model: 'gemini-3.1-flash-lite',
      promptVersion: GEMINI_PROMPT_VERSION, schemaVersion: GEMINI_SCHEMA_VERSION,
      definitionCatalog: { genres: ['Rock'], vibes: ['Upbeat'] },
      validationCatalog: { genres: ['Rock'], vibes: ['Upbeat'] },
    };
    await cache.set(request, { status: 'ok', response: JSON.stringify({
      status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: '{"release_year":null,"bpm":null,"original_key":null,"genres":["Rock"],"vibes":["Upbeat"]}' }] }],
    }) });
    let dispatches = 0;
    const client = createSongEnrichmentProviderClient({
      cache,
      config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (contact@example.test)' },
      transport: async () => { dispatches += 1; return { status: 500, body: '{}' }; },
    });
    const result = await client.requestGeminiTags({ mbid: null, title: 'Song', artist: 'Artist', definitions,
      apiKey: 'synthetic-key' });
    expect(result).toMatchObject({ status: 'ok', cache_hit: true, genres: [{ name: 'Rock' }], vibes: [{ name: 'Upbeat' }] });
    expect(client.getAttemptCounts()).toEqual({ total: 0, gemini: 0 });
    expect(dispatches).toBe(0);
  });

  it('keeps credentials and chart content out of actual provider cache files', async () => {
    const cache = await createSongEnrichmentCache({ directory: root });
    const client = createSongEnrichmentProviderClient({ cache,
      config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: 'ChordChartManager/1.0.0 (contact@example.test)' },
      transport: async () => ({ status: 200, headers: {}, body: JSON.stringify({
        status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: '{"release_year":null,"bpm":null,"original_key":null,"genres":["Rock"],"vibes":[]}' }] }],
      }) }) });
    await client.requestGeminiTags({ mbid: MBID, title: 'Synthetic Song', artist: 'Synthetic Artist',
      context: { chart_content: 'PRIVATE_CHART_CONTENT', lyrics: 'PRIVATE_LYRICS' },
      definitions: { genres: ['Rock'], vibes: [] }, apiKey: 'PRIVATE_API_CREDENTIAL' });
    const files = await fs.readdir(root);
    expect(files.some(file => file.endsWith('.json'))).toBe(true);
    const entries = await Promise.all(files.filter(file => file.endsWith('.json'))
      .map(async file => JSON.parse(await fs.readFile(path.join(root, file), 'utf8'))));
    expect(entries).toHaveLength(1);
    const persisted = entries.map(entry => JSON.stringify({ ...entry, response: undefined })).join('\n');
    const decodedResponses = entries.map(entry => Buffer.from(entry.response, 'base64').toString('utf8')).join('\n');
    for (const content of [persisted, decodedResponses]) {
      expect(content).not.toContain('PRIVATE_CHART_CONTENT');
      expect(content).not.toContain('PRIVATE_LYRICS');
      expect(content).not.toContain('PRIVATE_API_CREDENTIAL');
      expect(content).not.toContain('x-goog-api-key');
    }
  });
});
