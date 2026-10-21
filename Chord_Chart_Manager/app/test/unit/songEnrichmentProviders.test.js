import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';
import { EventEmitter } from 'node:events';

const require = createRequire(import.meta.url);
const { resolveRecordingCandidate, buildEnrichmentPreview } = require('../../server/existingSongEnrichment.js');
const { createSongEnrichmentTransport } = require('../../server/songEnrichmentTransport.js');
const {
  createSongEnrichmentProviderClient,
  normalizeMusicBrainzLookup,
  normalizeMusicBrainzSearch,
  queryEscape,
} = require('../../server/songEnrichmentProviders.js');

const MBID = 'a1111111-1111-4111-8111-111111111111';
const OTHER_MBID = 'b2222222-2222-4222-8222-222222222222';
const USER_AGENT = 'ChordChartManager/1.0.0 (contact@example.test)';
const REQUIRED_BOUNDS = { maxAttempts: 12, maxGeminiAttempts: 2, timeoutMs: 10000, maxResponseBytes: 1000000,
  geminiMaxInputBytes: 32000, geminiMaxOutputTokens: 1000 };
const definitions = {
  genres: [{ id: 1, name: '  Hard   Rock  ' }, { id: 2, name: 'Country' }],
  vibes: [{ id: 3, name: 'Upbeat' }, { id: 4, name: 'Country' }],
};

function response(body, status = 200, headers = {}) {
  return { status, headers, body: typeof body === 'string' ? body : JSON.stringify(body) };
}

function harness({ route, config = {}, cache = null, timeout = false, initialTime = 1_800_000_000_000, delayImpl = null } = {}) {
  let now = initialTime;
  const calls = [];
  const transport = async request => {
    calls.push(request);
    return route ? route(request, calls.length) : response({});
  };
  const delay = async (milliseconds, { kind, signal } = {}) => {
    if (delayImpl) return delayImpl(milliseconds, { kind, signal });
    if (kind === 'timeout') {
      if (timeout) return;
      return new Promise((resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
    }
    now += milliseconds;
  };
  const client = createSongEnrichmentProviderClient({ transport, clock: () => now, delay, cache,
    config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: USER_AGENT, ...config } });
  return { client, calls, now: () => now };
}

function searchBody(overrides = {}) {
  const recordings = [{ id: MBID, title: 'Song', 'artist-credit': [{ artist: { name: 'Artist' } }], score: '100', 'first-release-date': '1977-06-18' }];
  return { count: recordings.length, offset: 0, recordings, ...overrides };
}

function recordingRows(start, length, { matchFirst = false, ids = null } = {}) {
  return Array.from({ length }, (_, index) => ({
    id: ids?.[index] || `${String(start + index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`,
    title: matchFirst && index === 0 ? 'Song' : `Other ${start + index}`,
    'artist-credit': [{ artist: { name: 'Artist' } }],
    'first-release-date': '1977',
  }));
}

function geminiResponse(json, usage = {}, usageMetadata = undefined) {
  return response({ status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify({
    release_year: null, bpm: null, original_key: null, genres: [], vibes: [], ...json,
  }) }] }], usage, ...(usageMetadata ? { usageMetadata } : {}) });
}

function acousticDocument(mbid, offset, fields = {}) {
  return { metadata: { tags: { musicbrainz_recordingid: [mbid] } }, offset, ...fields };
}

describe('song enrichment provider adapters', () => {
  it('requires positive integer bounds and identifying MusicBrainz contact', () => {
    expect(() => createSongEnrichmentProviderClient()).toThrow(/explicitly configured/);
    expect(() => createSongEnrichmentProviderClient({ config: { musicBrainzUserAgent: USER_AGENT } })).toThrow(/explicitly configured/);
    for (const field of Object.keys(REQUIRED_BOUNDS)) {
      const config = { ...REQUIRED_BOUNDS, musicBrainzUserAgent: USER_AGENT };
      delete config[field];
      expect(() => createSongEnrichmentProviderClient({ config })).toThrow(new RegExp(`${field}.*explicitly configured`));
    }
    for (const value of [0, -1, 1.5, Infinity, NaN, '3']) {
      expect(() => createSongEnrichmentProviderClient({ config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: USER_AGENT, maxAttempts: value } })).toThrow(/positive integer/);
    }
    const valid = { ...REQUIRED_BOUNDS, musicBrainzUserAgent: USER_AGENT };
    expect(() => createSongEnrichmentProviderClient({ config: { ...valid, maxGeminiAttempts: 13 } })).toThrow(/cannot exceed/);
    expect(() => createSongEnrichmentProviderClient({ config: { ...valid, musicBrainzMinimumIntervalMs: 999 } })).toThrow(/1000ms/);
    expect(() => createSongEnrichmentProviderClient({ config: { ...valid, geminiMaxOutputTokens: 65_537 } })).toThrow(/output-token limit/);
    expect(() => createSongEnrichmentProviderClient({ config: { ...valid, model: 'other-model' } })).toThrow(/Only gemini-3.1-flash-lite/);
    expect(() => createSongEnrichmentProviderClient({ config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: 'App' } })).toThrow(/User-Agent/);
  });

  it('retains the fixed Gemini model when the caller explicitly supplies undefined', async () => {
    let captured;
    let cacheIdentity;
    const client = createSongEnrichmentProviderClient({
      config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: USER_AGENT, model: undefined },
      transport: async request => { captured = request; return geminiResponse({ genres: [], vibes: [] }); },
      cache: { get: async request => { cacheIdentity = request.model; return null; }, set: async () => true },
      delay: async () => {},
    });
    const result = await client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'synthetic-key' });
    expect(result.status).toBe('ok');
    expect(JSON.parse(captured.body.toString('utf8')).model).toBe('gemini-3.1-flash-lite');
    expect(cacheIdentity).toBe('gemini-3.1-flash-lite');
  });

  it('defaults to a network-disabled transport without consuming outbound budget', async () => {
    const client = createSongEnrichmentProviderClient({ config: { ...REQUIRED_BOUNDS, musicBrainzUserAgent: USER_AGENT } });
    const result = await client.searchRecordings({ title: 'Song', artist: 'Artist' });
    expect(result.status).toBe('network_disabled');
    expect(client.getAttemptCounts()).toEqual({ total: 0, gemini: 0 });
    expect(client.getRunLedger()).toEqual([{ provider: 'MusicBrainz', operation: 'recording_search', attempt: 0, status: 'network_disabled' }]);
  });

  it('escapes MusicBrainz Lucene syntax and URL encoding while returning identity candidates only', async () => {
    const { client, calls } = harness({ route: () => response(searchBody()) });
    const found = await client.searchRecordings({ title: 'Song + (Live)', artist: 'Artist/Name' });
    expect(found.status).toBe('ok');
    expect(found.candidates).toEqual([expect.objectContaining({
      mbid: MBID, title: 'Song', artist: 'Artist', disambiguation: '', first_release_date: '1977-06-18',
      response_digest: found.response_digest,
    })]);
    expect(calls[0].url).toContain('query=');
    expect(decodeURIComponent(calls[0].url)).toContain('Song \\+ \\(Live\\)');
    expect(calls[0].headers['User-Agent']).toBe(USER_AGENT);
    expect(JSON.stringify(found)).not.toContain('score');
    expect(queryEscape('a:b')).toBe('a\\:b');
  });

  it('preserves only valid MusicBrainz first-release-date evidence on search candidates', () => {
    for (const value of ['1977', '1977-06', '1977-06-18']) {
      const parsed = normalizeMusicBrainzSearch(searchBody({ recordings: [{ ...searchBody().recordings[0],
        'first-release-date': value }] }), 'c'.repeat(64));
      expect(parsed.candidates[0]).toMatchObject({ mbid: MBID, title: 'Song', artist: 'Artist',
        disambiguation: '', first_release_date: value, response_digest: 'c'.repeat(64) });
      expect(parsed.candidates[0]).not.toHaveProperty('score');
    }
    for (const value of [undefined, '1977-02-29', '1977-13', '1977-06-32', 1977]) {
      const row = { ...searchBody().recordings[0] };
      if (value === undefined) delete row['first-release-date'];
      else row['first-release-date'] = value;
      const parsed = normalizeMusicBrainzSearch(searchBody({ recordings: [row] }), 'd'.repeat(64));
      expect(parsed.status).toBe('ok');
      expect(parsed.candidates[0]).not.toHaveProperty('first_release_date');
      expect(parsed.candidates[0].response_digest).toBe('d'.repeat(64));
    }
  });

  it('reports no MusicBrainz results as documented absence and never ranks a candidate as identity', async () => {
    const { client } = harness({ route: () => response({ count: 0, offset: 0, recordings: [] }) });
    expect(await client.searchRecordings({ title: 'Song', artist: 'Artist' })).toMatchObject({ status: 'absent', candidates: [] });
  });

  it('accepts complete 0, 75 and 100 result searches from one limit-100 request', async () => {
    for (const count of [0, 75, 100]) {
      const h = harness({ route: () => response({ count, offset: 0,
        recordings: recordingRows(0, count, { matchFirst: true }) }) });
      const found = await h.client.searchRecordings({ title: 'Song', artist: 'Artist' });
      expect(h.calls).toHaveLength(1);
      const url = new URL(h.calls[0].url);
      expect(url.searchParams.get('limit')).toBe('100');
      expect(url.searchParams.get('offset')).toBe('0');
      expect(found).toMatchObject({ status: count === 0 ? 'absent' : 'ok',
        pagination: { expected_total: count, attempted_pages: 1, validated_pages: 1,
          collected_count: count, complete: true } });
      expect(found.candidates).toHaveLength(count);
      if (count > 0) {
        expect(resolveRecordingCandidate({ title: 'Song', artist: 'Artist' }, found.candidates).matched).toBe(true);
        expect(new Set(found.candidates.map(candidate => candidate.response_digest))).toEqual(new Set([found.response_digest]));
      }
    }
  });

  it('rejects oversized, truncated, malformed, wrong-offset and duplicate results without exposing candidates', async () => {
    const cases = [
      { name: 'oversized count', body: { count: 101, offset: 0, recordings: recordingRows(0, 100) },
        code: 'result_cap_exceeded', expected: { current_page_total: 101, returned_offset: 0, returned_recording_count: 100 } },
      { name: 'truncated response', body: { count: 75, offset: 0, recordings: recordingRows(0, 74) },
        code: 'unexpected_page_length', expected: { current_page_total: 75, returned_offset: 0, returned_recording_count: 74 } },
      { name: 'malformed count', body: { count: 'provider-secret-sentinel', offset: 0, recordings: [] },
        code: 'invalid_pagination_field', expected: { current_page_total: { type: 'string' }, returned_offset: 0, returned_recording_count: 0 } },
      { name: 'malformed recordings', body: { count: 1, offset: 0, recordings: {} },
        code: 'invalid_pagination_field', expected: { current_page_total: 1, returned_offset: 0, returned_recording_count: { type: 'object' } } },
      { name: 'wrong offset', body: { count: 1, offset: 25, recordings: [searchBody().recordings[0]] },
        code: 'unexpected_offset', expected: { current_page_total: 1, returned_offset: 25, returned_recording_count: 1 } },
      { name: 'duplicate identity', body: { count: 2, offset: 0, recordings: [
        searchBody().recordings[0], { ...searchBody().recordings[0], 'first-release-date': '1978' },
      ] }, code: 'duplicate_recording_identity', expected: { current_page_total: 2, returned_offset: 0,
        returned_recording_count: 2, duplicate_mbid: MBID, duplicate_scope: 'within_page',
        first_occurrence: { requested_offset: 0, index_in_page: 0 },
        repeated_occurrence: { requested_offset: 0, index_in_page: 1 } } },
      { name: 'invalid candidate identity', body: { count: 1, offset: 0, recordings: [
        { id: 'bad', title: 'Song', 'artist-credit': [{ artist: { name: 'Artist' } }] },
      ] }, code: 'invalid_candidate', expected: { current_page_total: 1, returned_offset: 0, returned_recording_count: 1 } },
    ];
    for (const scenario of cases) {
      const h = harness({ route: () => response(scenario.body) });
      const rejected = await h.client.searchRecordings({ title: 'Song', artist: 'Artist' });
      expect(h.calls).toHaveLength(1);
      expect(rejected, scenario.name).toMatchObject({ status: 'invalid_response', reason: 'incomplete_search_results', candidates: [],
        pagination: { expected_total: null,
          attempted_pages: 1, validated_pages: 0, collected_count: 0, complete: false },
        validation_diagnostic: { code: scenario.code, requested_offset: 0, ...scenario.expected,
          validated_response_count: 0, collected_record_count: 0 } });
      expect(JSON.stringify(rejected)).not.toContain('provider-secret-sentinel');
      expect(resolveRecordingCandidate({ title: 'Song', artist: 'Artist' }, rejected.candidates).matched).toBe(false);
    }
  });

  it('keeps the single request within shared retry, budget and cancellation safeguards', async () => {
    const exhausted = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1 }, route: () => response({}, 503) });
    expect(await exhausted.client.searchRecordings({ title: 'Song', artist: 'Artist' }))
      .toMatchObject({ status: 'transient_error', candidates: [],
        pagination: { expected_total: null, attempted_pages: 1, validated_pages: 0, collected_count: 0, complete: false } });
    expect(exhausted.calls).toHaveLength(1);

    const retried = harness({ route: (_request, count) => count === 1 ? response({}, 503) : response(searchBody()) });
    expect(await retried.client.searchRecordings({ title: 'Song', artist: 'Artist' })).toMatchObject({ status: 'ok', candidates: [expect.any(Object)] });
    expect(retried.calls).toHaveLength(2);
    expect(retried.calls.map(call => new URL(call.url).searchParams.get('offset'))).toEqual(['0', '0']);
    expect(retried.client.getAttemptCounts().total).toBe(2);

    const controller = new AbortController();
    const cancelled = harness({ route: () => response(searchBody()), delayImpl: (_milliseconds, { kind }) => {
      if (kind === 'musicbrainz_spacing') { controller.abort(); throw new Error('synthetic cancel during spacing'); }
    } });
    expect((await cancelled.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('ok');
    expect(await cancelled.client.searchRecordings({ title: 'Other', artist: 'Artist', signal: controller.signal }))
      .toMatchObject({ status: 'cancelled', candidates: [], pagination: { complete: false } });
    expect(cancelled.calls).toHaveLength(1);
  });

  it('passes actual MusicBrainz evidence through both accepted B1 public functions', async () => {
    const h = harness({ route: request => request.url.includes('/recording/?') ? response(searchBody()) : response({
      id: MBID, title: 'Song', 'artist-credit': [{ artist: { name: 'Artist' } }],
      'first-release-date': '1977-06-18', genres: [{ name: 'Rock' }],
    }) });
    const search = await h.client.searchRecordings({ title: 'Song', artist: 'Artist' });
    const match = resolveRecordingCandidate({ title: 'Song', artist: 'Artist' }, search.candidates);
    expect(match.matched).toBe(true);
    const detail = await h.client.lookupRecording({ mbid: match.mbid, searchCandidate: search.candidates[0] });
    const year = detail.scalars.release_year[0];
    const preview = buildEnrichmentPreview({ id: 42, updated_at: '2026-10-14T18:30:00.123456Z',
      release_year: null, original_key: null, bpm: null, genres: [], vibes: [] }, detail,
    { mbid: match.mbid, scalars: { release_year: year }, genres: [], vibes: [] },
    { genres: [{ id: 1, name: 'Rock' }], vibes: [] });
    expect(preview.scalar_changes).toEqual([{ field: 'release_year', before: null, after: 1977,
      evidence: { source: 'MusicBrainz', response_digest: year.response_digest } }]);
    expect(preview.has_changes).toBe(true);
  });

  it('rejects malformed MusicBrainz search records instead of returning partial matches', async () => {
    const { client } = harness({ route: () => response({ count: 1, offset: 0, recordings: [{ id: 'bad', title: 'Song', 'artist-credit': [] }] }) });
    expect(await client.searchRecordings({ title: 'Song', artist: 'Artist' })).toMatchObject({ status: 'invalid_response', candidates: [] });
  });

  it('parses exact MusicBrainz MBID, earliest release year, genres and tags with digest attribution', async () => {
    const { client, calls } = harness({ route: request => response({
      id: MBID, title: 'Song',
      ...(new URL(request.url).searchParams.get('inc').split(' ').includes('artist-credits')
        ? { 'artist-credit': [{ name: 'Artist', artist: { name: 'Artist' } }] } : {}),
      genres: [{ name: 'Rock' }], tags: [{ name: 'Classic Rock' }],
    }) });
    const searchCandidate = { mbid: MBID, first_release_date: '1977-06-18', response_digest: 'a'.repeat(64) };
    const result = await client.lookupRecording({ mbid: MBID.toUpperCase(), searchCandidate });
    expect(calls).toHaveLength(1);
    expect(new URL(calls[0].url).pathname).toBe(`/ws/2/recording/${MBID}`);
    expect(new URL(calls[0].url).searchParams.get('inc')).toBe('artist-credits genres tags');
    expect(result.status).toBe('ok');
    expect(result.recording.mbid).toBe(MBID);
    expect(result).toMatchObject({ mbid: MBID, vibes: [] });
    expect(result.recording.scalars.release_year[0]).toMatchObject({ value: 1977, source: 'MusicBrainz' });
    expect(result.recording.scalars.release_year[0].response_digest).toBe(searchCandidate.response_digest);
    expect(result.recording.genres.map(item => [item.name, item.association])).toEqual([['Rock', 'genre'], ['Classic Rock', 'tag']]);
    expect(result.recording.genres.every(item => item.response_digest === result.response_digest)).toBe(true);
  });

  it('rejects missing or malformed lookup artist credits without accepting scalar or Genre evidence', async () => {
    for (const credits of [undefined, null, [], 'Artist', [{ name: 'Artist' }], [{ artist: {} }], [{ artist: { name: '' } }]]) {
      const { client, calls } = harness({ route: () => response({
        id: MBID, title: 'Song', 'first-release-date': '1978', genres: [{ name: 'Rock' }], tags: [],
        ...(credits === undefined ? {} : { 'artist-credit': credits }),
      }) });
      expect(await client.lookupRecording({ mbid: MBID })).toEqual({
        status: 'invalid_response', mbid: MBID, scalars: {}, genres: [], vibes: [],
      });
      expect(calls).toHaveLength(1);
      expect(client.getRunLedger()[0].status).toBe('invalid_response');
    }
  });

  it('accepts requested lookup credits with optional date, Genres and tags absent', async () => {
    const { client, calls } = harness({ route: request => response({ id: MBID, title: 'Song',
      ...(new URL(request.url).searchParams.get('inc').split(' ').includes('artist-credits')
        ? { 'artist-credit': [{ artist: { name: 'Artist' } }] } : {}),
    }) });
    const result = await client.lookupRecording({ mbid: MBID });
    expect(calls).toHaveLength(1);
    expect(result).toMatchObject({ status: 'ok', mbid: MBID, scalars: { release_year: [] }, genres: [], vibes: [],
      recording: { mbid: MBID, title: 'Song', artist: 'Artist', scalars: { release_year: [] }, genres: [] } });
  });

  it('omits an unavailable or invalid first release year and rejects a mismatched lookup identity', async () => {
    const validIdentity = { id: MBID, title: 'Song', 'artist-credit': [{ artist: { name: 'Artist' } }], 'first-release-date': 'unknown' };
    expect(normalizeMusicBrainzLookup(validIdentity, MBID, 'digest').recording.scalars.release_year).toEqual([]);
    expect(normalizeMusicBrainzLookup({ ...validIdentity, 'first-release-date': '32768-01-01' }, MBID, 'digest').recording.scalars.release_year).toEqual([]);
    expect(normalizeMusicBrainzLookup({ ...validIdentity, 'first-release-date': '1977-99-99' }, MBID, 'digest').recording.scalars.release_year).toEqual([]);
    expect(normalizeMusicBrainzLookup({ ...validIdentity, id: OTHER_MBID }, MBID, 'digest').status).toBe('invalid_response');
    const noLookupDate = { ...validIdentity };
    delete noLookupDate['first-release-date'];
    expect(normalizeMusicBrainzLookup(noLookupDate, MBID, 'digest', {
      mbid: OTHER_MBID, first_release_date: '1977', response_digest: 'a'.repeat(64),
    }).recording.scalars.release_year).toEqual([]);
  });

  it('respects MusicBrainz minimum start spacing across concurrent calls and retries', async () => {
    const starts = [];
    const h = harness({ route: async (_request, count) => {
      starts.push(h.now());
      return count === 1 ? response({}, 503) : response(searchBody());
    } });
    const results = await Promise.all([
      h.client.searchRecordings({ title: 'One', artist: 'Artist' }),
      h.client.searchRecordings({ title: 'Two', artist: 'Artist' }),
    ]);
    expect(results.map(item => item.status)).toEqual(['ok', 'ok']);
    expect(starts).toHaveLength(3);
    expect(starts[1] - starts[0]).toBeGreaterThanOrEqual(1000);
    expect(starts[2] - starts[1]).toBeGreaterThanOrEqual(1000);
    expect(h.client.getAttemptCounts().total).toBe(3);
    expect(h.client.getRunLedger().map(item => item.status)).toEqual(['transient_error', 'ok', 'ok']);
  });

  it('honors bounded Retry-After and stops when its value exceeds the configured cap', async () => {
    const accepted = harness({ config: { maxRetryAfterMs: 1500 }, route: (_request, count) =>
      count === 1 ? response({}, 429, { 'Retry-After': '1' }) : response(searchBody()) });
    expect((await accepted.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('ok');
    expect(accepted.calls).toHaveLength(2);
    const rejected = harness({ config: { maxRetryAfterMs: 1500 }, route: () => response({}, 429, { 'Retry-After': '2' }) });
    expect((await rejected.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('transient_error');
    expect(rejected.calls).toHaveLength(1);
  });

  it('accepts strict HTTP dates and stops on malformed Retry-After headers with stable reasons', async () => {
    const initialTime = Date.parse('Sun, 06 Nov 1994 08:49:37 GMT');
    const accepted = harness({ initialTime, route: (_request, count) => count === 1
      ? response({}, 429, { 'Retry-After': 'Sun, 06 Nov 1994 08:49:38 GMT' }) : response(searchBody()) });
    expect((await accepted.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('ok');
    expect(accepted.calls).toHaveLength(2);
    for (const value of ['-1', '1.5', 'January 1, 2000']) {
      const invalid = harness({ route: () => response({}, 429, { 'Retry-After': value }) });
      expect(await invalid.client.searchRecordings({ title: 'Song', artist: 'Artist' }))
        .toMatchObject({ status: 'transient_error', reason: 'invalid_retry_after' });
      expect(invalid.calls).toHaveLength(1);
    }
    const overCap = harness({ config: { maxRetryAfterMs: 1000 }, route: () => response({}, 429, { 'Retry-After': '2' }) });
    expect(await overCap.client.searchRecordings({ title: 'Song', artist: 'Artist' }))
      .toMatchObject({ status: 'transient_error', reason: 'retry_after_exceeds_cap' });
  });

  it('does not dispatch a retry when cancelled during a valid Retry-After wait', async () => {
    const controller = new AbortController();
    const cancelled = harness({ route: () => response({}, 429, { 'Retry-After': '1' }),
      delayImpl: (_milliseconds, { kind }) => {
        if (kind === 'retry_after') { controller.abort(); throw new Error('cancelled wait'); }
      } });
    expect(await cancelled.client.searchRecordings({ title: 'Song', artist: 'Artist', signal: controller.signal }))
      .toMatchObject({ status: 'cancelled' });
    expect(cancelled.calls).toHaveLength(1);
    expect(cancelled.client.getRunLedger().map(item => item.status)).toEqual(['transient_error']);
  });

  it('reserves the shared budget after waits and prevents dispatch after spacing cancellation', async () => {
    let releaseSpacing;
    let announceSpacing;
    const spacingStarted = new Promise(resolve => { announceSpacing = resolve; });
    const h = harness({ config: { maxAttempts: 2 },
      route: request => request.url.endsWith('/count') ? response({ mbid: MBID, count: 0 }) : response(searchBody()),
      delayImpl: (_milliseconds, { kind }) => kind === 'musicbrainz_spacing'
        ? new Promise(resolve => { releaseSpacing = resolve; announceSpacing(); }) : Promise.resolve() });
    await h.client.searchRecordings({ title: 'First', artist: 'Artist' });
    const waiting = h.client.searchRecordings({ title: 'Second', artist: 'Artist' });
    await spacingStarted;
    expect((await h.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('absent');
    releaseSpacing();
    expect((await waiting).status).toBe('budget_exhausted');
    expect(h.calls).toHaveLength(2);
    expect(h.client.getAttemptCounts().total).toBe(2);
    expect(h.client.getRunLedger().map(row => row.status)).toEqual(['ok', 'absent']);

    let release;
    let announce;
    const started = new Promise(resolve => { announce = resolve; });
    const cancelH = harness({ route: () => response(searchBody()), delayImpl: (_ms, { kind }) => kind === 'musicbrainz_spacing'
      ? new Promise(resolve => { release = resolve; announce(); }) : Promise.resolve() });
    await cancelH.client.searchRecordings({ title: 'First', artist: 'Artist' });
    const controller = new AbortController();
    const pending = cancelH.client.searchRecordings({ title: 'Second', artist: 'Artist', signal: controller.signal });
    await started;
    controller.abort();
    release();
    expect((await pending).status).toBe('cancelled');
    expect(cancelH.calls).toHaveLength(1);
    expect(cancelH.client.getRunLedger()).toHaveLength(1);
  });

  it('uses AcousticBrainz X-RateLimit-Reset-In when Retry-After is absent', async () => {
    const startTimes = [];
    const h = harness({ route: (_request, count) => {
      startTimes.push(h.now());
      return count === 1
        ? response({}, 429, { 'X-RateLimit-Reset-In': '1' })
        : response({ mbid: MBID, count: 0 });
    } });
    expect((await h.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('absent');
    expect(h.calls).toHaveLength(2);
    expect(startTimes[1] - startTimes[0]).toBe(1000);
    const pacedStarts = [];
    const paced = harness({ route: request => {
      pacedStarts.push(paced.now());
      return request.url.endsWith('/count')
        ? response({ mbid: MBID, count: 1 }, 200, { 'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset-In': '1' })
        : response({ rhythm: { bpm: 100 } });
    } });
    expect((await paced.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('ok');
    expect(pacedStarts[1] - pacedStarts[0]).toBe(1000);
  });

  it('retries transient failures once, never retries authentication or ordinary client errors', async () => {
    const transient = harness({ route: (_request, count) => count === 1 ? response({}, 500) : response(searchBody()) });
    expect((await transient.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('ok');
    expect(transient.calls).toHaveLength(2);
    for (const status of [400, 401, 403]) {
      const authOrClient = harness({ route: () => response({}, status) });
      const result = await authOrClient.client.lookupRecording({ mbid: MBID });
      expect(result.status).toBe(status === 401 || status === 403 ? 'authentication_error' : 'invalid_response');
      expect(authOrClient.calls).toHaveLength(1);
    }
  });

  it.each([429, 503])('preserves one-attempt Gemini HTTP %i without retry delay', async status => {
    const delays = [];
    const h = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1 }, delayImpl: async (_ms, opts) => { delays.push(opts.kind); },
      route: () => response({}, status) });
    const result = await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' });
    expect(result).toMatchObject({ status: 'transient_error', failure_details: { failure_phase: 'http', http_status: status, https_started: true } });
    expect(h.calls).toHaveLength(1);
    expect(delays).not.toContain('retry_after');
    expect(h.client.getRunLedger()[0]).toMatchObject({ failure_details: { http_status: status } });
  });

  it('retries Gemini only when total and provider budgets permit another attempt', async () => {
    const h = harness({ config: { maxAttempts: 2, maxGeminiAttempts: 2 }, route: (_request, count) =>
      count === 1 ? response({}, 503) : geminiResponse({ genres: [], vibes: [] }) });
    const result = await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' });
    expect(result.status).toBe('ok');
    expect(h.calls).toHaveLength(2);
    expect(h.client.getAttemptCounts()).toEqual({ total: 2, gemini: 2 });
    const totalExhausted = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1 }, route: () => response({}, 503) });
    expect(await totalExhausted.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' }))
      .toMatchObject({ status: 'transient_error', failure_details: { http_status: 503 } });
    const providerExhausted = harness({ config: { maxAttempts: 2, maxGeminiAttempts: 1 }, route: () => response({}, 429) });
    expect(await providerExhausted.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' }))
      .toMatchObject({ status: 'transient_error', failure_details: { http_status: 429 } });
    expect(providerExhausted.calls).toHaveLength(1);
  });

  it('keeps the first terminal failure if another call spends the retry slot during delay', async () => {
    let releaseRetry;
    let retryWaiting;
    const waiting = new Promise(resolve => { retryWaiting = resolve; });
    const h = harness({ config: { maxAttempts: 2, maxGeminiAttempts: 2 }, route: (_request, count) =>
      count === 1 ? response({}, 503) : response(searchBody()),
    delayImpl: (_ms, { kind }) => kind === 'retry_after'
      ? new Promise(resolve => { releaseRetry = resolve; retryWaiting(); }) : Promise.resolve() });
    const first = h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' });
    await waiting;
    await h.client.searchRecordings({ title: 'Other', artist: 'Artist' });
    releaseRetry();
    expect(await first).toMatchObject({ status: 'transient_error' });
    expect(h.client.getRunLedger()[0]).toMatchObject({ status: 'transient_error',
      failure_details: { failure_phase: 'http', http_status: 503, https_started: true } });
    expect(h.calls).toHaveLength(2);
  });

  it('preserves terminal HTTP failure if a provider spacing wait consumes the retry budget', async () => {
    let releaseSpacing;
    let spacingWaiting;
    const waiting = new Promise(resolve => { spacingWaiting = resolve; });
    const h = harness({ config: { maxAttempts: 2, maxGeminiAttempts: 2 }, route: request => {
      if (request.url.includes('/count')) return response({ mbid: MBID, count: 0 });
      return response({}, 503, { 'Retry-After': '0' });
    }, delayImpl: (_ms, { kind }) => kind === 'musicbrainz_spacing'
      ? new Promise(resolve => { releaseSpacing = resolve; spacingWaiting(); }) : Promise.resolve() });
    const first = h.client.searchRecordings({ title: 'Song', artist: 'Artist' });
    await waiting;
    await h.client.getAcousticBrainzEvidence({ mbid: MBID });
    releaseSpacing();
    expect(await first).toMatchObject({ status: 'transient_error' });
    expect(h.client.getRunLedger()[0]).toMatchObject({ status: 'transient_error',
      failure_details: { failure_phase: 'http', http_status: 503, https_started: true } });
    expect(h.client.getAttemptCounts().total).toBe(2);
  });

  it('keeps safe transport codes, validation phase and timeout source without leaking error properties', async () => {
    const thrown = Object.assign(new Error('secret=DO_NOT_LEAK https://user:pass@example.invalid'), { code: 'ECONNRESET', token: 'PRIVATE',
      failure_details: { failure_phase: 'request', https_started: true, transport_error_code: 'ECONNRESET' } });
    const h = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1 }, route: () => { throw thrown; } });
    const result = await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' });
    expect(result).toMatchObject({ status: 'transient_error', failure_details: { transport_error_code: 'ECONNRESET' } });
    expect(JSON.stringify({ result, ledger: h.client.getRunLedger() })).not.toMatch(/DO_NOT_LEAK|PRIVATE|user:pass/);
    const validationTransport = createSongEnrichmentTransport({ request: () => { throw new Error('must not reach https'); } });
    let actualCalls = 0;
    const invalid = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1 }, route: request => {
      actualCalls += 1;
      return validationTransport({ ...request, url: 'http://invalid.example/', timeoutMs: 10000,
        maxResponseBytes: 1000000, signal: new AbortController().signal });
    } });
    const invalidResult = await invalid.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'fake-secret' });
    expect(invalidResult.failure_details).toMatchObject({ failure_phase: 'validation', https_started: false,
      transport_error_code: 'INVALID_PROVIDER_REQUEST' });
    expect(actualCalls).toBe(1);
  });

  it('preserves native HTTPS error codes and classifies the transport deadline', async () => {
    let httpsStarts = 0;
    const transport = createSongEnrichmentTransport({ request: () => {
      httpsStarts += 1;
      const req = new EventEmitter();
      req.end = () => queueMicrotask(() => req.emit('error', Object.assign(new Error('private'), { code: 'ECONNRESET' })));
      req.destroy = () => {};
      return req;
    }, setTimer: () => 1, clearTimer: () => {} });
    const client = createSongEnrichmentProviderClient({ transport, delay: async (_ms, { kind }) => {
      if (kind === 'timeout') return new Promise(() => {});
    }, config: { ...REQUIRED_BOUNDS, maxAttempts: 1, maxGeminiAttempts: 1, musicBrainzUserAgent: USER_AGENT } });
    const failed = await client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'synthetic' });
    expect(httpsStarts).toBe(1);
    expect(failed).toMatchObject({ status: 'transient_error', failure_details: { failure_phase: 'request',
      transport_error_code: 'ECONNRESET', https_started: true } });
    expect(JSON.stringify(client.getRunLedger())).not.toContain('private');

    let timeoutStarted = false;
    const timedTransport = createSongEnrichmentTransport({ request: () => {
      timeoutStarted = true;
      const req = new EventEmitter(); req.end = () => {}; req.destroy = () => {}; return req;
    }, setTimer: callback => { queueMicrotask(callback); return 2; }, clearTimer: () => {} });
    const timeoutClient = createSongEnrichmentProviderClient({ transport: timedTransport, delay: async (_ms, { kind }) => {
      if (kind === 'timeout') return new Promise(() => {});
    }, config: { ...REQUIRED_BOUNDS, maxAttempts: 1, maxGeminiAttempts: 1, musicBrainzUserAgent: USER_AGENT } });
    const timed = await timeoutClient.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'synthetic' });
    expect(timeoutStarted).toBe(true);
    expect(timed).toMatchObject({ status: 'transient_error', failure_details: { failure_phase: 'timeout',
      timeout: true, timeout_source: 'transport', transport_error_code: 'ETIMEDOUT', https_started: true } });

    const oversizedTransport = createSongEnrichmentTransport({ request: (_url, _options, callback) => {
      const req = new EventEmitter();
      req.end = () => {
        const res = new EventEmitter(); res.statusCode = 200; res.headers = {};
        callback(res);
        queueMicrotask(() => res.emit('data', Buffer.from('too large')));
      };
      req.destroy = () => {};
      return req;
    }, setTimer: () => 3, clearTimer: () => {} });
    const oversizedClient = createSongEnrichmentProviderClient({ transport: oversizedTransport,
      delay: async (_ms, { kind }) => kind === 'timeout' ? new Promise(() => {}) : Promise.resolve(),
      config: { ...REQUIRED_BOUNDS, maxAttempts: 1, maxGeminiAttempts: 1, maxResponseBytes: 5,
        musicBrainzUserAgent: USER_AGENT } });
    expect(await oversizedClient.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions, apiKey: 'synthetic' }))
      .toMatchObject({ status: 'transient_error', failure_details: { failure_phase: 'response',
        transport_error_code: 'RESPONSE_TOO_LARGE', https_started: true } });
  });

  it('bounds response bytes and classifies malformed JSON as invalid response', async () => {
    const large = harness({ config: { maxResponseBytes: 12 }, route: () => response(searchBody()) });
    expect((await large.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('invalid_response');
    const malformed = harness({ route: () => response('{not json') });
    expect((await malformed.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('invalid_response');
  });

  it('aborts timed out transports and never retries an unsettled attempt or accepts a late response', async () => {
    let observedAbort = false;
    const held = harness({ timeout: true, route: request => new Promise((_resolve, reject) => {
      request.signal.addEventListener('abort', () => { observedAbort = true; reject(Object.assign(new Error('aborted'), { name: 'AbortError' })); }, { once: true });
    }) });
    expect(await held.client.searchRecordings({ title: 'Song', artist: 'Artist' }))
      .toMatchObject({ status: 'transient_error', reason: 'timeout' });
    expect(observedAbort).toBe(true);
    expect(held.calls).toHaveLength(1);
    expect(held.client.getRunLedger()).toHaveLength(1);

    let settleLate;
    let cacheWrites = 0;
    const late = harness({ timeout: true, cache: { get: async () => null, set: async () => { cacheWrites += 1; } },
      route: () => new Promise(resolve => { settleLate = resolve; }) });
    expect((await late.client.searchRecordings({ title: 'Song', artist: 'Artist' })).reason).toBe('timeout');
    settleLate(response(searchBody()));
    await Promise.resolve();
    await Promise.resolve();
    expect(late.calls).toHaveLength(1);
    expect(cacheWrites).toBe(0);
    expect(late.client.getRunLedger().map(row => row.status)).toEqual(['transient_error']);
  });

  it('does not dispatch after cancellation and stops at the shared request budget', async () => {
    const cancelled = harness({ route: () => response(searchBody()) });
    const controller = new AbortController();
    controller.abort();
    expect((await cancelled.client.searchRecordings({ title: 'Song', artist: 'Artist', signal: controller.signal })).status).toBe('cancelled');
    expect(cancelled.calls).toHaveLength(0);
    const limited = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1 }, route: () => response({}, 503) });
    expect((await limited.client.searchRecordings({ title: 'One', artist: 'Artist' })).status).toBe('transient_error');
    expect((await limited.client.searchRecordings({ title: 'Two', artist: 'Artist' })).status).toBe('budget_exhausted');
    expect(limited.calls).toHaveLength(1);
  });

  it('uses only validated MBIDs for AcousticBrainz and keeps each submission separately attributable', async () => {
    const h = harness({ route: request => {
      if (request.url.endsWith('/count')) return response({ mbid: MBID, count: 2 });
      const offset = Number(new URL(request.url).searchParams.get('n'));
      return response(offset === 0
        ? acousticDocument(MBID, offset, { rhythm: { bpm: 100.2 }, tonal: { key_key: 'C', key_scale: 'major', key_strength: 0.72 } })
        : acousticDocument(MBID, offset, { rhythm: { bpm: 101 }, tonal: { key_key: 'G', key_scale: 'minor', key_strength: 0.91 } }));
    } });
    expect((await h.client.getAcousticBrainzEvidence({ mbid: 'invalid' })).status).toBe('invalid_response');
    expect(h.calls).toHaveLength(0);
    const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result.status).toBe('ok');
    expect(result.mbid).toBe(MBID);
    expect(result.submission_enumeration).toBe('complete');
    expect(result.submission_count).toBe(2);
    expect(result.planned_sample_offsets).toEqual([0, 1]);
    expect(result.attempted_offsets).toEqual([0, 1]);
    expect(result.sampled_offsets).toEqual([0, 1]);
    expect(result.agreement_scope).toBe('sampled_submissions');
    expect(result.scalars.bpm.map(item => item.value)).toEqual([100.2, 101]);
    expect(result.scalars.original_key.map(item => item.value.key_key)).toEqual(['C', 'G']);
    expect(result.scalars.original_key.map(item => item.key_strength)).toEqual([0.72, 0.91]);
    expect(result.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`, `${MBID}:1`]);
    expect(h.calls).toHaveLength(3);
    expect(h.calls[1].url).toContain('/low-level?n=0');
    expect(h.calls[1].url).not.toContain('features=');
    const wrongIdentity = harness({ route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 1 }) : response({ mbid: OTHER_MBID, offset: 0, rhythm: { bpm: 100 } }) });
    expect((await wrongIdentity.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('invalid_response');
    const nestedWrongIdentity = harness({ route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 1 })
      : response(acousticDocument(OTHER_MBID, 0, { rhythm: { bpm: 100 } })) });
    expect((await nestedWrongIdentity.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('invalid_response');
    expect(nestedWrongIdentity.client.getRunLedger().at(-1).status).toBe('invalid_response');
    const malformedNestedIdentity = harness({ route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 1 })
      : response({ metadata: { tags: { musicbrainz_recordingid: [MBID, 'not-an-mbid'] } }, rhythm: { bpm: 100 } }) });
    expect((await malformedNestedIdentity.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('invalid_response');
    expect(malformedNestedIdentity.client.getRunLedger().at(-1).status).toBe('invalid_response');
  });

  it('treats only a validated zero AcousticBrainz count as absence', async () => {
    const absent = harness({ route: () => response({ mbid: MBID, count: 0 }) });
    expect(await absent.client.getAcousticBrainzEvidence({ mbid: MBID })).toMatchObject({ status: 'absent', submission_enumeration: 'complete' });
    const malformed = harness({ route: () => response({}) });
    expect((await malformed.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('invalid_response');
    const malformedSubmission = harness({ route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 1 }) : response({ rhythm: { bpm: 'fast' } }) });
    expect((await malformedSubmission.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('invalid_response');
    const invalidKey = harness({ route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 1 })
      : response({ tonal: { key_key: 'C', key_scale: 'dorian' } }) });
    expect((await invalidKey.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('invalid_response');
    const noKey = harness({ route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 1 })
      : response({ tonal: { key_key: 'C', key_scale: 'none' } }) });
    expect((await noKey.client.getAcousticBrainzEvidence({ mbid: MBID })).scalars.original_key).toEqual([]);
  });

  it('samples deterministic distinct offsets and marks completeness only when every submission was retrieved', async () => {
    for (const [count, expectedOffsets, expectedEnumeration] of [
      [0, [], 'complete'], [1, [0], 'complete'], [2, [0, 1], 'complete'], [3, [0, 1, 2], 'complete'],
      [226, [0, 112, 225], 'partial'],
    ]) {
      const h = harness({ route: request => {
        if (request.url.endsWith('/count')) return response({ mbid: MBID, count });
        const offset = Number(new URL(request.url).searchParams.get('n'));
        return response(acousticDocument(MBID, offset, {
          rhythm: { bpm: 90 + offset }, tonal: { key_key: 'C', key_scale: 'major', key_strength: 0.8 },
        }));
      } });
      const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
      expect(result.status).toBe(count === 0 ? 'absent' : 'ok');
      expect(result.submission_count).toBe(count);
      expect(result.planned_sample_offsets).toEqual(expectedOffsets);
      expect(result.attempted_offsets).toEqual(expectedOffsets);
      expect(result.sampled_offsets).toEqual(expectedOffsets);
      expect(result.submission_enumeration).toBe(expectedEnumeration);
      expect(result.agreement_scope).toBe(count === 0 ? 'none' : 'sampled_submissions');
      expect(h.calls.filter(call => !call.url.endsWith('/count')).map(call => Number(new URL(call.url).searchParams.get('n'))))
        .toEqual(expectedOffsets);
      expect(expectedOffsets.every(offset => offset < count)).toBe(true);
    }
  });

  it('rejects malformed AcousticBrainz submission counts before sampling', async () => {
    for (const count of [-1, 1.5, '226', Number.MAX_SAFE_INTEGER + 1, null, undefined]) {
      const h = harness({ route: () => response({ mbid: MBID, ...(count === undefined ? {} : { count }) }) });
      const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
      expect(result.status).toBe('invalid_response');
      expect(result.scalars).toEqual({ original_key: [], bpm: [] });
      expect(h.calls).toHaveLength(1);
    }
  });

  it('keeps successful samples when shared request budget is exhausted before later offsets', async () => {
    const h = harness({ config: { maxAttempts: 2 }, route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 226 })
      : response(acousticDocument(MBID, 0, { rhythm: { bpm: 92 }, tonal: { key_key: 'D', key_scale: 'minor' } })) });
    const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result).toMatchObject({ status: 'budget_exhausted', submission_enumeration: 'partial', submission_count: 226,
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0], sampled_offsets: [0], agreement_scope: 'sampled_submissions' });
    expect(result.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`]);
    expect(h.calls.map(call => new URL(call.url).pathname.endsWith('/count') ? 'count' : Number(new URL(call.url).searchParams.get('n'))))
      .toEqual(['count', 0]);
    expect(h.client.getAttemptCounts().total).toBe(2);
  });

  it('charges a retry within the shared budget before suppressing the final sample', async () => {
    let transientUsed = false;
    const h = harness({ config: { maxAttempts: 4 }, route: request => {
      if (request.url.endsWith('/count')) return response({ mbid: MBID, count: 226 });
      const offset = Number(new URL(request.url).searchParams.get('n'));
      if (offset === 112 && !transientUsed) { transientUsed = true; return response({}, 503, { 'Retry-After': '0' }); }
      return response(acousticDocument(MBID, offset, { rhythm: { bpm: 91 + offset } }));
    } });
    const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result).toMatchObject({ status: 'budget_exhausted', submission_count: 226,
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0, 112], sampled_offsets: [0, 112], submission_enumeration: 'partial' });
    expect(result.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`, `${MBID}:112`]);
    expect(h.calls.map(call => new URL(call.url).pathname.endsWith('/count') ? 'count' : new URL(call.url).searchParams.get('n')))
      .toEqual(['count', '0', '112', '112']);
    expect(h.client.getAttemptCounts().total).toBe(4);
  });

  it('retains validated samples on cancellation and transient failure at a later offset', async () => {
    const controller = new AbortController();
    const cancelled = harness({ route: request => {
      if (request.url.endsWith('/count')) return response({ mbid: MBID, count: 226 });
      const offset = Number(new URL(request.url).searchParams.get('n'));
      return response(acousticDocument(MBID, offset, { rhythm: { bpm: 90 + offset } }), 200,
        offset === 0 ? { 'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset-In': '1' } : {});
    }, delayImpl: (_ms, { kind, signal }) => {
      if (kind === 'provider_rate_limit') { controller.abort(); throw new Error('cancelled'); }
      if (kind === 'timeout') return new Promise(resolve => signal?.addEventListener('abort', resolve, { once: true }));
      return Promise.resolve();
    } });
    const cancelledResult = await cancelled.client.getAcousticBrainzEvidence({ mbid: MBID, signal: controller.signal });
    expect(cancelledResult).toMatchObject({ status: 'cancelled', submission_enumeration: 'partial', submission_count: 226,
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0], sampled_offsets: [0] });
    expect(cancelledResult.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`]);
    expect(cancelled.calls).toHaveLength(2);

    const failed = harness({ route: request => {
      if (request.url.endsWith('/count')) return response({ mbid: MBID, count: 226 });
      const offset = Number(new URL(request.url).searchParams.get('n'));
      if (offset === 225) return response({}, 503, { 'Retry-After': '0' });
      return response(acousticDocument(MBID, offset, { rhythm: { bpm: offset === 0 ? 90 : 101 },
        tonal: { key_key: offset === 0 ? 'C' : 'G', key_scale: offset === 0 ? 'major' : 'minor', key_strength: 0.7 + offset / 1000 } }));
    } });
    const failedResult = await failed.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(failedResult).toMatchObject({ status: 'transient_error', submission_enumeration: 'partial', submission_count: 226,
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0, 112, 225], sampled_offsets: [0, 112], agreement_scope: 'sampled_submissions' });
    expect(failedResult.scalars.bpm.map(item => item.value)).toEqual([90, 101]);
    expect(failedResult.scalars.original_key.map(item => item.value)).toEqual([
      { key_key: 'C', key_scale: 'major' }, { key_key: 'G', key_scale: 'minor' },
    ]);
    expect(failedResult.scalars.original_key.every(item => /^[a-f0-9]{64}$/.test(item.response_digest) && Number.isFinite(item.key_strength))).toBe(true);
    expect(failed.client.getAttemptCounts().total).toBe(5);
    expect(failed.calls.filter(call => !call.url.endsWith('/count')).map(call => Number(new URL(call.url).searchParams.get('n'))))
      .toEqual([0, 112, 225, 225]);
  });

  it('cancellation during cache reads or writes never returns successful AcousticBrainz evidence', async () => {
    const readController = new AbortController();
    const read = harness({ cache: { get: async () => { readController.abort(); return null; }, set: async () => true } });
    const readResult = await read.client.getAcousticBrainzEvidence({ mbid: MBID, signal: readController.signal });
    expect(readResult).toMatchObject({ status: 'cancelled', submission_enumeration: 'unknown', scalars: { bpm: [], original_key: [] } });
    expect(read.calls).toHaveLength(0);
    expect(read.client.getAttemptCounts().total).toBe(0);

    const sampleReadController = new AbortController();
    const sampleRead = harness({ cache: { get: async request => {
      if (request.operation === 'recording_submission') {
        sampleReadController.abort();
        return { status: 'ok', response: Buffer.from(JSON.stringify(acousticDocument(MBID, 0, { rhythm: { bpm: 97 } }))),
          response_digest: 'e'.repeat(64) };
      }
      return null;
    }, set: async () => true }, route: () => response({ mbid: MBID, count: 1 }) });
    const sampleReadResult = await sampleRead.client.getAcousticBrainzEvidence({ mbid: MBID, signal: sampleReadController.signal });
    expect(sampleReadResult).toMatchObject({ status: 'cancelled', submission_count: 1,
      attempted_offsets: [], sampled_offsets: [], scalars: { bpm: [], original_key: [] } });
    expect(sampleRead.calls).toHaveLength(1);
    expect(sampleRead.client.getAttemptCounts().total).toBe(1);

    const writeController = new AbortController();
    const write = harness({ cache: {
      get: async () => null,
      set: async request => {
        if (request.operation === 'recording_submission' && request.parameters.offset === 225) writeController.abort();
        return true;
      },
    }, route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 226 })
      : response(acousticDocument(MBID, Number(new URL(request.url).searchParams.get('n')),
        { rhythm: { bpm: 96 }, tonal: { key_key: 'D', key_scale: 'major', key_strength: 0.8 } })) });
    const writeResult = await write.client.getAcousticBrainzEvidence({ mbid: MBID, signal: writeController.signal });
    expect(writeResult).toMatchObject({ status: 'cancelled', submission_count: 226,
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0, 112, 225], sampled_offsets: [0, 112, 225],
      submission_enumeration: 'partial', agreement_scope: 'sampled_submissions' });
    expect(writeResult.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`, `${MBID}:112`, `${MBID}:225`]);
    expect(writeResult.scalars.original_key.map(item => item.key_strength)).toEqual([0.8, 0.8, 0.8]);
    expect(write.calls).toHaveLength(4);
    expect(write.client.getAttemptCounts().total).toBe(4);
  });

  it('preserves conflicting sampled BPM and key candidates without choosing, averaging, or tempo-adjusting', async () => {
    const h = harness({ route: request => {
      if (request.url.endsWith('/count')) return response({ mbid: MBID, count: 226 });
      const offset = Number(new URL(request.url).searchParams.get('n'));
      return response(acousticDocument(MBID, offset, { rhythm: { bpm: offset === 0 ? 90 : offset === 112 ? 92 : 180 },
        tonal: { key_key: offset === 0 ? 'C' : offset === 112 ? 'D' : 'G', key_scale: 'major', key_strength: 0.5 } }));
    } });
    const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result).toMatchObject({ status: 'ok', submission_enumeration: 'partial', submission_count: 226,
      attempted_offsets: [0, 112, 225], sampled_offsets: [0, 112, 225], agreement_scope: 'sampled_submissions' });
    expect(result.scalars.bpm.map(item => item.value)).toEqual([90, 92, 180]);
    expect(result.scalars.original_key.map(item => item.value.key_key)).toEqual(['C', 'D', 'G']);
    expect(result.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`, `${MBID}:112`, `${MBID}:225`]);
  });

  it('keeps partial AcousticBrainz submissions distinct and never claims enumeration completeness on budget exhaustion', async () => {
    const partial = harness({ config: { maxAttempts: 2 }, route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 2 })
      : response({ rhythm: { bpm: 100 } }) });
    const result = await partial.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result.status).toBe('budget_exhausted');
    expect(result.submission_enumeration).toBe('partial');
    expect(result.scalars.bpm).toHaveLength(1);
    const largeCount = harness({ route: () => response({ mbid: MBID, count: 101 }) });
    expect(await largeCount.client.getAcousticBrainzEvidence({ mbid: MBID })).toMatchObject({
      status: 'ok', submission_enumeration: 'partial', submission_count: 101,
      planned_sample_offsets: [0, 50, 100], sampled_offsets: [0, 50, 100],
    });
  });

  it('sends Gemini only allowlisted identity/context/catalog data and resolves canonical tag names', async () => {
    let captured;
    const h = harness({ route: request => {
      captured = request;
      return geminiResponse({ genres: ['Hard Rock', 'Country'], vibes: ['Upbeat'] }, { total_input_tokens: 54, total_output_tokens: 8, total_tokens: 62 });
    } });
    const result = await h.client.requestGeminiTags({
      mbid: MBID, title: 'Song', artist: 'Artist',
      context: { release_year: 1977, recording_type: 'studio', chart_content: 'PRIVATE-CHART', lyrics: 'PRIVATE-LYRICS' },
      definitions, apiKey: 'synthetic-api-key', chart_source: 'PRIVATE-SOURCE',
    });
    expect(result.status).toBe('ok');
    expect(result.mbid).toBe(MBID);
    expect(result.scalars).toEqual({ release_year: [], bpm: [], original_key: [] });
    expect(result.genres.map(item => item.name)).toEqual(['  Hard   Rock  ', 'Country']);
    expect(result.vibes.map(item => item.name)).toEqual(['Upbeat']);
    expect(result.token_usage).toEqual({ input_tokens: 54, output_tokens: 8, total_tokens: 62 });
    const sent = Buffer.isBuffer(captured.body) ? captured.body.toString('utf8') : String(captured.body);
    expect(sent).not.toContain('PRIVATE-');
    expect(sent).not.toContain('lyrics');
    expect(sent).not.toContain(MBID);
    expect(captured.headers['x-goog-api-key']).toBe('synthetic-api-key');
    expect(captured.url).toBe('https://generativelanguage.googleapis.com/v1beta/interactions');
    expect(h.client.getRunLedger().some(item => JSON.stringify(item).includes('synthetic-api-key'))).toBe(false);
    expect(h.client.getRunLedger()).toContainEqual(expect.objectContaining({
      provider: 'Gemini', operation: 'missing_metadata_fallback', status: 'ok',
      token_usage: { input_tokens: 54, output_tokens: 8, total_tokens: 62 },
    }));
  });

  it('keys cached fallback responses by their normalized factual evidence', async () => {
    const entries = new Map();
    const cache = { async get(request) { return entries.get(JSON.stringify(request)) || null; },
      async set(request, entry) { entries.set(JSON.stringify(request), entry); } };
    const h = harness({ cache, route: () => geminiResponse({}) });
    const factual = year => ({ context_version: 1, target: { song_id: 727, title: 'Song', artist: 'Artist' },
      recording: { mbid: MBID, title: 'Song', artist: 'Artist', disambiguation: '', validated: true, response_digest: 'a'.repeat(64) },
      scalar_candidates: { release_year: { state: 'unapproved_candidates', conflicting_evidence: false, candidates: [
        { source: 'MusicBrainz', value: year, response_digest: 'a'.repeat(64), submission_reference: `recording:${MBID}` }] },
      original_key: { state: 'partial_unapproved_candidates', conflicting_evidence: true, candidates: [] },
      bpm: { state: 'partial_unapproved_candidates', conflicting_evidence: true, candidates: [] } },
      submission_coverage: { source: 'AcousticBrainz', total_available: 0, sampled_offsets: [], completeness: 'complete' },
      conflicts: { release_year: false, original_key: true, bpm: true } });
    for (const year of [1973, 1974]) await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions,
      factualEvidence: factual(year), requestedFields: ['original_key', 'bpm'], apiKey: 'key' });
    expect(h.calls).toHaveLength(2);
    expect(entries.size).toBe(2);
  });

  it('rejects Gemini unknown, ambiguous, malformed, scalar, or extra-property outputs', async () => {
    const cases = [
      { genres: ['Unknown'], vibes: [] },
      { output: { genres: ['Country'], vibes: [] }, catalog: { genres: [{ name: 'Country' }, { name: ' country ' }], vibes: [] } },
      { genres: ['Hard Rock'], vibes: 'Upbeat' },
      { genres: [], vibes: [], bpm: 120 },
      { genres: [], vibes: [], mbid: MBID },
    ];
    for (const item of cases) {
      const h = harness({ route: () => geminiResponse(output) });
      const output = item.output || item;
      const result = await h.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions: item.catalog || definitions, apiKey: 'key' });
      expect(result).toMatchObject({ status: 'invalid_response', genres: [], vibes: [] });
    }
  });

  it('rejects uniquely catalogued Genre and Vibe labels omitted from their supplied allowed subsets', async () => {
    const h = harness({ route: () => geminiResponse({ genres: ['Country'], vibes: ['Country'] }) });
    const result = await h.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions,
      allowedNames: { genres: [definitions.genres[0].name], vibes: ['Upbeat'] }, apiKey: 'key' });
    expect(result).toMatchObject({ status: 'invalid_response', genres: [], vibes: [] });
    expect(h.client.getRunLedger()).toContainEqual(expect.objectContaining({
      provider: 'Gemini', operation: 'missing_metadata_fallback', status: 'invalid_response',
    }));

    const categoryOverlap = harness({ route: () => geminiResponse({ genres: ['Country'], vibes: ['Country'] }) });
    const overlap = await categoryOverlap.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions,
      allowedNames: { genres: ['Country'], vibes: ['Upbeat'] }, apiKey: 'key' });
    expect(overlap.status).toBe('invalid_response');

    const ambiguousAllowed = harness();
    const ambiguous = await ambiguousAllowed.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist',
      definitions: { genres: [], vibes: [{ name: 'Country' }, { name: ' country ' }] },
      allowedNames: { genres: [], vibes: ['Country'] }, apiKey: 'key' });
    expect(ambiguous.status).toBe('invalid_response');
    expect(ambiguousAllowed.calls).toHaveLength(0);
  });

  it('uses only Interactions usage in results and ledgers, even when legacy usageMetadata conflicts', async () => {
    const rejected = harness({ route: () => geminiResponse({ genres: ['Unknown'] },
      { total_input_tokens: 21, total_output_tokens: 9, total_tokens: 30 },
      { promptTokenCount: 700, candidatesTokenCount: 800, totalTokenCount: 1500 }) });
    const invalid = await rejected.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' });
    expect(invalid).toMatchObject({ status: 'invalid_response', token_usage: { input_tokens: 21, output_tokens: 9, total_tokens: 30 } });
    expect(invalid.genres).toEqual([]);
    expect(rejected.client.getRunLedger()).toContainEqual(expect.objectContaining({ status: 'invalid_response',
      token_usage: { input_tokens: 21, output_tokens: 9, total_tokens: 30 } }));

    const absent = harness({ route: () => geminiResponse({ genres: ['Unknown'] }, {},
      { promptTokenCount: 700, candidatesTokenCount: 800, totalTokenCount: 1500 }) });
    const absentResult = await absent.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' });
    expect(absentResult).not.toHaveProperty('token_usage');
    expect(absent.client.getRunLedger()[0]).not.toHaveProperty('token_usage');

    const malformed = harness({ route: () => geminiResponse({ genres: ['Unknown'] },
      { total_input_tokens: 21, total_output_tokens: -9, total_tokens: 12 },
      { promptTokenCount: 700, candidatesTokenCount: 800, totalTokenCount: 1500 }) });
    const malformedResult = await malformed.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' });
    expect(malformedResult).not.toHaveProperty('token_usage');
    expect(malformed.client.getRunLedger()[0]).not.toHaveProperty('token_usage');
  });

  it('keeps legacy usageMetadata accounting for non-Interactions providers', async () => {
    const h = harness({ route: () => response({ ...searchBody(), usageMetadata: {
      promptTokenCount: 7, candidatesTokenCount: 3, totalTokenCount: 10,
    } }) });
    expect((await h.client.searchRecordings({ title: 'Song', artist: 'Artist' })).status).toBe('ok');
    expect(h.client.getRunLedger()).toContainEqual(expect.objectContaining({ provider: 'MusicBrainz',
      operation: 'recording_search', status: 'ok',
      token_usage: { input_tokens: 7, output_tokens: 3, total_tokens: 10 } }));
  });

  it('accepts Gemini empty output arrays as abstention and keeps same-named categories separate', async () => {
    const abstain = harness({ route: () => geminiResponse({ genres: [], vibes: [] }) });
    expect(await abstain.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' }))
      .toMatchObject({ status: 'ok', genres: [], vibes: [] });
    const genreOnly = harness({ route: () => geminiResponse({ genres: ['Country'], vibes: [] }) });
    expect(await genreOnly.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' }))
      .toMatchObject({ status: 'ok', genres: [{ name: 'Country' }], vibes: [] });
  });

  it('constructs subjective Vibe-only requests and rejects every suggestion outside the supplied Vibe subset', async () => {
    const vibeDefinitions = { genres: [{ id: 1, name: 'Rock' }], vibes: [{ id: 2, name: 'Warm' }, { id: 3, name: 'Energetic' }] };
    const allowedNames = { genres: [], vibes: ['Warm'] };
    let captured;
    const accepted = harness({ config: { maxAttempts: 1, maxGeminiAttempts: 1, geminiMaxInputBytes: 16_384,
      geminiMaxOutputTokens: 1_024 }, route: request => {
      captured = request;
      return geminiResponse({ vibes: ['Warm'] });
    } });
    const result = await accepted.client.requestGeminiTags({ title: 'Beer Drinkers and Hell Raisers', artist: 'ZZ Top',
      requestedFields: [], definitions: vibeDefinitions, allowedNames, apiKey: 'private-test-key' });
    expect(accepted.calls).toHaveLength(1);
    expect(accepted.client.getAttemptCounts()).toEqual({ total: 1, gemini: 1 });
    expect(result).toMatchObject({ status: 'ok', scalars: { release_year: [], bpm: [], original_key: [] }, genres: [],
      vibes: [{ name: 'Warm' }] });
    const body = JSON.parse(captured.body.toString('utf8'));
    expect(body.system_instruction).toContain('Vibes describe subjective musical, listening, or performance character.');
    expect(body.system_instruction).toContain('Choose only appropriate supplied labels; return an empty array when none fit.');
    expect(body.input).toContain(body.system_instruction);
    expect(body.store).toBe(false);
    expect(body.generation_config.max_output_tokens).toBe(1_024);
    const schema = body.response_format.schema;
    for (const field of ['release_year', 'bpm', 'original_key']) expect(schema.properties[field]).toEqual({ type: 'null' });
    expect(schema.required).toEqual(['release_year', 'bpm', 'original_key', 'genres', 'vibes']);
    expect(schema.properties.genres.maxItems).toBe(0);
    expect(schema.properties.vibes.maxItems).toBe(3);
    const payload = JSON.parse(body.input.slice(body.input.indexOf('\n') + 1));
    expect(payload).toEqual({ recording: { title: 'Beer Drinkers and Hell Raisers', artist: 'ZZ Top', context: {} },
      requested_missing_fields: [], allowed_names: { genres: [], vibes: ['Warm'] } });
    expect(captured.body.toString('utf8')).not.toContain('private-test-key');

    for (const invalid of [
      { bpm: 134, vibes: [] },
      { genres: ['Rock'], vibes: [] },
      { vibes: ['Energetic'] },
      { vibes: ['Unknown'] },
    ]) {
      const rejected = harness({ route: () => geminiResponse(invalid) });
      expect((await rejected.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: [],
        definitions: vibeDefinitions, allowedNames, apiKey: 'test-key' })).status).toBe('invalid_response');
    }

    const ambiguous = harness();
    expect((await ambiguous.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: [],
      definitions: { genres: [], vibes: [{ name: 'Warm' }, { name: ' warm ' }] },
      allowedNames: { genres: [], vibes: ['Warm'] }, apiKey: 'test-key' })).status).toBe('invalid_response');
    expect(ambiguous.calls).toHaveLength(0);
  });

  it('builds scalar response constraints from the requested field set', async () => {
    let captured;
    const h = harness({ route: request => { captured = request; return geminiResponse({ original_key: { key_key: 'C', key_scale: 'minor' },
      bpm: 128, vibes: ['Upbeat'] }); } });
    const result = await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: ['bpm', 'original_key'],
      definitions: { genres: [], vibes: [{ name: 'Upbeat' }] }, allowedNames: { genres: [], vibes: ['Upbeat'] }, apiKey: 'test-key' });
    expect(result.status).toBe('ok');
    const schema = JSON.parse(captured.body.toString('utf8')).response_format.schema;
    expect(schema.properties.release_year).toEqual({ type: 'null' });
    expect(schema.properties.bpm).toEqual({ type: ['number', 'null'], minimum: 0 });
    expect(schema.properties.original_key).toMatchObject({ type: ['object', 'null'], required: ['key_key', 'key_scale'],
      additionalProperties: false });
    expect(schema.properties.genres).toEqual({ type: 'array', items: { type: 'string' }, maxItems: 0 });
    expect(schema.properties.vibes).toEqual({ type: 'array', items: { type: 'string' }, maxItems: 3 });

    const rejected = harness({ route: () => geminiResponse({ release_year: 1978, bpm: null, original_key: null }) });
    expect(await rejected.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: ['bpm'],
      definitions: { genres: [], vibes: [] }, allowedNames: { genres: [], vibes: [] }, apiKey: 'test-key' }))
      .toMatchObject({ status: 'invalid_response', failure_details: { validation_reason: 'unrequested_scalar', validation_field: 'release_year' } });
  });

  it('returns stable safe validation reasons through the Gemini result and ledger', async () => {
    const validOutput = { release_year: null, bpm: null, original_key: null, genres: [], vibes: ['Upbeat'] };
    const usage = { total_input_tokens: 21, total_output_tokens: 9, total_tokens: 30 };
    const envelope = text => response({ status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text }] }], usage });
    const cases = [
      { reason: 'interaction_envelope_invalid', response: () => response({ status: 'in_progress', steps: [], usage }) },
      { reason: 'model_text_missing', response: () => response({ status: 'completed', steps: [{ type: 'model_output', content: [] }], usage }) },
      { reason: 'invalid_json', response: () => envelope('RAW_RESPONSE_SECRET invalid json') },
      { reason: 'required_field_missing', response: () => envelope(JSON.stringify({ release_year: null, bpm: null, genres: [], vibes: ['Upbeat'] })) },
      { reason: 'unexpected_field', response: () => envelope(JSON.stringify({ ...validOutput, explanation: 'RAW_RESPONSE_SECRET' })) },
      { reason: 'invalid_field_type_value', response: () => envelope(JSON.stringify({ ...validOutput, bpm: null, vibes: 'Upbeat' })) },
      { reason: 'unrequested_scalar', response: () => geminiResponse({ ...validOutput, bpm: 120 }, usage) },
      { reason: 'disallowed_genre_output', response: () => geminiResponse({ ...validOutput, genres: ['Country'] }, usage), allowedNames: { genres: [], vibes: ['Upbeat'] }, category: 'Genre' },
      { reason: 'unknown_label', response: () => geminiResponse({ ...validOutput, vibes: ['Unknown'] }, usage), category: 'Vibe' },
      { reason: 'ambiguous_label', response: () => geminiResponse({ ...validOutput, vibes: ['Warm'] }, usage),
        definitions: { genres: [], vibes: [{ name: 'Warm' }, { name: ' warm ' }, { name: 'Upbeat' }] },
        allowedNames: { genres: [], vibes: ['Upbeat'] }, category: 'Vibe' },
      { reason: 'label_outside_allowed_subset', response: () => geminiResponse({ ...validOutput, vibes: ['Country'] }, usage),
        allowedNames: { genres: [], vibes: ['Upbeat'] }, category: 'Vibe' },
    ];
    for (const scenario of cases) {
      const h = harness({ route: scenario.response });
      const result = await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: [],
        definitions: scenario.definitions || definitions, allowedNames: scenario.allowedNames || { genres: [], vibes: ['Upbeat'] }, apiKey: 'synthetic-key' });
      expect(result.status, scenario.reason).toBe('invalid_response');
      expect(result.failure_details.validation_reason, scenario.reason).toBe(scenario.reason);
      if (scenario.category) expect(result.failure_details.validation_category).toBe(scenario.category);
      else if (scenario.reason === 'required_field_missing' || scenario.reason === 'unrequested_scalar') {
        expect(result.failure_details.validation_field).toBe(scenario.reason === 'required_field_missing' ? 'original_key' : 'bpm');
      }
      expect(result.token_usage).toEqual({ input_tokens: 21, output_tokens: 9, total_tokens: 30 });
      expect(h.client.getRunLedger()[0]).toMatchObject({ status: 'invalid_response', token_usage: result.token_usage,
        failure_details: result.failure_details });
      expect(JSON.stringify({ result, ledger: h.client.getRunLedger() })).not.toMatch(/RAW_RESPONSE_SECRET|synthetic-key/);
    }

    const invalidFactual = harness();
    const result = await invalidFactual.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: [], definitions,
      factualEvidence: { unexpected_secret: 'PRIVATE' }, apiKey: 'synthetic-key' });
    expect(result).toMatchObject({ status: 'invalid_response', failure_details: { validation_reason: 'invalid_provenance' } });
    expect(invalidFactual.calls).toHaveLength(0);

    const accepted = harness({ route: () => geminiResponse({ ...validOutput, vibes: ['Upbeat'] }, usage) });
    expect(await accepted.client.requestGeminiTags({ title: 'Song', artist: 'Artist', requestedFields: [], definitions,
      allowedNames: { genres: [], vibes: ['Upbeat'] }, apiKey: 'synthetic-key' })).toMatchObject({ status: 'ok', vibes: [{ name: 'Upbeat' }] });
  });

  it('validates requested missing scalars and tags with model/schema/digest provenance', async () => {
    const h = harness({ route: () => geminiResponse({ release_year: 1978, bpm: 104.2,
      original_key: { key_key: 'G', key_scale: 'major' }, genres: ['Hard Rock'], vibes: ['Upbeat'] }) });
    const result = await h.client.requestGeminiTags({ mbid: null, title: 'Song', artist: 'Artist', definitions,
      requestedFields: ['release_year', 'bpm', 'original_key'], apiKey: 'synthetic-api-key' });
    expect(result.status).toBe('ok');
    expect(result.scalars.release_year[0]).toMatchObject({ value: 1978, source: 'Gemini', model_id: 'gemini-3.1-flash-lite' });
    expect(result.scalars.bpm[0]).toMatchObject({ value: 104.2, source: 'Gemini', prompt_version: 'missing-metadata-fallback-v2' });
    expect(result.scalars.original_key[0]).toMatchObject({ value: { key_key: 'G', key_scale: 'major' }, schema_version: 'missing-metadata-schema-v2' });
    expect(result.genres[0].name).toBe('  Hard   Rock  ');
    expect(result.genres[0].response_digest).toMatch(/^[a-f0-9]{64}$/);
    expect(result.vibes[0]).toMatchObject({ name: 'Upbeat', source: 'Gemini',
      model_id: 'gemini-3.1-flash-lite', prompt_version: 'missing-metadata-fallback-v2', schema_version: 'missing-metadata-schema-v2' });
    const body = JSON.parse(h.calls[0].body.toString());
    expect(body.input).not.toContain('mbid');
    expect(body.input).toContain('requested_missing_fields');
    expect(body.generation_config.max_output_tokens).toBe(1000);
    expect(body.response_format.schema.properties.release_year).toEqual({ type: ['integer', 'null'] });
    expect(body.response_format.schema.properties.bpm).toEqual({ type: ['number', 'null'], minimum: 0 });
    expect(body.response_format.schema.properties.original_key).toMatchObject({ type: ['object', 'null'],
      required: ['key_key', 'key_scale'], additionalProperties: false });
  });

  it('rejects scalar suggestions for populated or unrequested fields and malformed key/value output', async () => {
    for (const output of [
      { release_year: 1978 },
      { release_year: null, bpm: -1 },
      { release_year: null, original_key: { key_key: 'H', key_scale: 'major' } },
      { release_year: 1978, bpm: null, original_key: null },
    ]) {
      const h = harness({ route: () => geminiResponse(output) });
      const result = await h.client.requestGeminiTags({ title: 'Song', artist: 'Artist', definitions,
        requestedFields: ['bpm', 'original_key'], apiKey: 'key' });
      expect(result).toMatchObject({ status: 'invalid_response', scalars: {} });
    }
  });

  it('enforces Gemini API input/output limits and does not dispatch for missing key or empty catalog', async () => {
    const missing = harness();
    expect((await missing.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions })).status).toBe('authentication_error');
    expect(missing.calls).toHaveLength(0);
    const empty = harness();
    expect(await empty.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions: { genres: [], vibes: [] }, apiKey: 'key' }))
      .toMatchObject({ status: 'ok', skipped: 'no_missing_fields_or_unassigned_definitions' });
    expect(empty.calls).toHaveLength(0);
    const tooLarge = harness({ config: { geminiMaxInputBytes: 50 } });
    expect((await tooLarge.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' })).status).toBe('invalid_response');
    expect(tooLarge.calls).toHaveLength(0);
    const lowOutput = harness({ config: { geminiMaxOutputTokens: 1 } });
    await lowOutput.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' });
    expect(JSON.parse(lowOutput.calls[0].body.toString()).generation_config.max_output_tokens).toBe(1);
  });

  it('retries Gemini transient failures once within its separate and shared budgets', async () => {
    const retry = harness({ config: { maxAttempts: 5, maxGeminiAttempts: 2 }, route: () => response({}, 503) });
    expect((await retry.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' })).status)
      .toBe('transient_error');
    expect(retry.calls).toHaveLength(2);
    expect(retry.client.getAttemptCounts()).toEqual({ total: 2, gemini: 2 });
    const auth = harness({ route: () => response({}, 401) });
    expect((await auth.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' })).status)
      .toBe('authentication_error');
    expect(auth.calls).toHaveLength(1);
  });
});
