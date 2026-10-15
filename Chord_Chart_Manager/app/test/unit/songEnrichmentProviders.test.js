import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { resolveRecordingCandidate, buildEnrichmentPreview } = require('../../server/existingSongEnrichment.js');
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

function geminiResponse(json, usage = {}) {
  return response({ status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: JSON.stringify(json) }] }], usage });
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

  it('reports no MusicBrainz results as documented absence and never ranks a candidate as identity', async () => {
    const { client } = harness({ route: () => response({ count: 0, offset: 0, recordings: [] }) });
    expect(await client.searchRecordings({ title: 'Song', artist: 'Artist' })).toMatchObject({ status: 'absent', candidates: [] });
  });

  it('abstains on incomplete or malformed MusicBrainz paging and cannot establish a B1 match', async () => {
    const complete = harness({ route: () => response(searchBody()) });
    const found = await complete.client.searchRecordings({ title: 'Song', artist: 'Artist' });
    expect(resolveRecordingCandidate({ title: 'Song', artist: 'Artist' }, found.candidates).matched).toBe(true);

    const rows = Array.from({ length: 25 }, (_, index) => ({ id: `${String(index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`,
      title: index === 0 ? 'Song' : `Other ${index}`, 'artist-credit': [{ artist: { name: 'Artist' } }] }));
    const truncated = harness({ route: () => response({ count: 26, offset: 0, recordings: rows }) });
    const rejected = await truncated.client.searchRecordings({ title: 'Song', artist: 'Artist' });
    expect(rejected).toMatchObject({ status: 'invalid_response', reason: 'incomplete_search_results', candidates: [] });
    expect(resolveRecordingCandidate({ title: 'Song', artist: 'Artist' }, rejected.candidates))
      .toEqual({ matched: false, reason: 'no_exact_match' });
    for (const paging of [{ count: '1', offset: 0 }, { count: 1, offset: 1 }, { count: 2, offset: 0 }]) {
      const malformed = harness({ route: () => response(searchBody(paging)) });
      expect(await malformed.client.searchRecordings({ title: 'Song', artist: 'Artist' }))
        .toMatchObject({ status: 'invalid_response', reason: 'incomplete_search_results', candidates: [] });
    }
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
    const { client } = harness({ route: () => response({
      id: MBID, title: 'Song', 'artist-credit': [{ name: 'Artist', artist: { name: 'Artist' } }],
      genres: [{ name: 'Rock' }], tags: [{ name: 'Classic Rock' }],
    }) });
    const searchCandidate = { mbid: MBID, first_release_date: '1977-06-18', response_digest: 'a'.repeat(64) };
    const result = await client.lookupRecording({ mbid: MBID.toUpperCase(), searchCandidate });
    expect(result.status).toBe('ok');
    expect(result.recording.mbid).toBe(MBID);
    expect(result).toMatchObject({ mbid: MBID, vibes: [] });
    expect(result.recording.scalars.release_year[0]).toMatchObject({ value: 1977, source: 'MusicBrainz' });
    expect(result.recording.scalars.release_year[0].response_digest).toBe(searchCandidate.response_digest);
    expect(result.recording.genres.map(item => [item.name, item.association])).toEqual([['Rock', 'genre'], ['Classic Rock', 'tag']]);
    expect(result.recording.genres.every(item => item.response_digest === result.response_digest)).toBe(true);
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
    expect((await limited.client.searchRecordings({ title: 'One', artist: 'Artist' })).status).toBe('budget_exhausted');
    expect((await limited.client.searchRecordings({ title: 'Two', artist: 'Artist' })).status).toBe('budget_exhausted');
    expect(limited.calls).toHaveLength(1);
  });

  it('uses only validated MBIDs for AcousticBrainz and keeps each submission separately attributable', async () => {
    const h = harness({ route: request => {
      if (request.url.endsWith('/count')) return response({ mbid: MBID, count: 2 });
      const offset = Number(new URL(request.url).searchParams.get('n'));
      return response(offset === 0
        ? acousticDocument(MBID, offset, { rhythm: { bpm: 100.2 }, tonal: { key_key: 'C', key_scale: 'major' } })
        : acousticDocument(MBID, offset, { rhythm: { bpm: 101 }, tonal: { key_key: 'G', key_scale: 'minor' } }));
    } });
    expect((await h.client.getAcousticBrainzEvidence({ mbid: 'invalid' })).status).toBe('invalid_response');
    expect(h.calls).toHaveLength(0);
    const result = await h.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result.status).toBe('ok');
    expect(result.mbid).toBe(MBID);
    expect(result.submission_enumeration).toBe('complete');
    expect(result.scalars.bpm.map(item => item.value)).toEqual([100.2, 101]);
    expect(result.scalars.original_key.map(item => item.value.key_key)).toEqual(['C', 'G']);
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

  it('keeps partial AcousticBrainz submissions distinct and never claims enumeration completeness on budget exhaustion', async () => {
    const partial = harness({ config: { maxAttempts: 2 }, route: request => request.url.endsWith('/count')
      ? response({ mbid: MBID, count: 2 })
      : response({ rhythm: { bpm: 100 } }) });
    const result = await partial.client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result.status).toBe('budget_exhausted');
    expect(result.submission_enumeration).toBe('partial');
    expect(result.scalars.bpm).toHaveLength(1);
    const tooMany = harness({ route: () => response({ mbid: MBID, count: 101 }) });
    expect((await tooMany.client.getAcousticBrainzEvidence({ mbid: MBID })).status).toBe('budget_exhausted');
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
    expect(result.scalars).toEqual({});
    expect(result.genres.map(item => item.name)).toEqual(['  Hard   Rock  ', 'Country']);
    expect(result.vibes.map(item => item.name)).toEqual(['Upbeat']);
    expect(result.token_usage).toEqual({ input_tokens: 54, output_tokens: 8, total_tokens: 62 });
    const sent = Buffer.isBuffer(captured.body) ? captured.body.toString('utf8') : String(captured.body);
    expect(sent).not.toContain('PRIVATE-');
    expect(sent).not.toContain('lyrics');
    expect(captured.headers['x-goog-api-key']).toBe('synthetic-api-key');
    expect(captured.url).toBe('https://generativelanguage.googleapis.com/v1beta/interactions');
    expect(h.client.getRunLedger().some(item => JSON.stringify(item).includes('synthetic-api-key'))).toBe(false);
    expect(h.client.getRunLedger()).toContainEqual(expect.objectContaining({
      provider: 'Gemini', operation: 'genre_vibe_candidates', status: 'ok',
      token_usage: { input_tokens: 54, output_tokens: 8, total_tokens: 62 },
    }));
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

  it('accepts Gemini empty output arrays as abstention and keeps same-named categories separate', async () => {
    const abstain = harness({ route: () => geminiResponse({ genres: [], vibes: [] }) });
    expect(await abstain.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' }))
      .toMatchObject({ status: 'ok', genres: [], vibes: [] });
    const genreOnly = harness({ route: () => geminiResponse({ genres: ['Country'], vibes: [] }) });
    expect(await genreOnly.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions, apiKey: 'key' }))
      .toMatchObject({ status: 'ok', genres: [{ name: 'Country' }], vibes: [] });
  });

  it('enforces Gemini API input/output limits and does not dispatch for missing key or empty catalog', async () => {
    const missing = harness();
    expect((await missing.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions })).status).toBe('authentication_error');
    expect(missing.calls).toHaveLength(0);
    const empty = harness();
    expect(await empty.client.requestGeminiTags({ mbid: MBID, title: 'Song', artist: 'Artist', definitions: { genres: [], vibes: [] }, apiKey: 'key' }))
      .toMatchObject({ status: 'ok', skipped: 'empty_definition_catalog' });
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
