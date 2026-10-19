import { EventEmitter } from 'node:events';
import https from 'node:https';
import { createRequire } from 'node:module';
import { Duplex } from 'node:stream';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { MAX_RESPONSE_BYTES, createSongEnrichmentTransport } = require('../../server/songEnrichmentTransport.js');
const { createSongEnrichmentProviderClient } = require('../../server/songEnrichmentProviders.js');
const MBID = 'a1111111-1111-4111-8111-111111111111';
const USER_AGENT = 'ChordChartManager/1.0.0 (contact@example.test)';

function request(url, overrides = {}) {
  const musicBrainz = url.hostname === 'musicbrainz.org';
  return {
    url: url.toString(), method: 'GET',
    headers: musicBrainz ? { Accept: 'application/json', 'User-Agent': USER_AGENT } : { Accept: 'application/json' },
    timeoutMs: 1000, maxResponseBytes: 1024, signal: new AbortController().signal,
    ...overrides,
  };
}

function fakeHttps({ status = 200, headers = { 'content-type': 'application/json' }, chunks = [Buffer.from('{"ok":true}')], autoResponse = true } = {}) {
  const captured = { calls: [], requests: [], responses: [] };
  captured.openResponse = (responseStatus = status, responseHeaders = headers) => {
    const incoming = new EventEmitter();
    incoming.statusCode = responseStatus;
    incoming.headers = responseHeaders;
    incoming.destroyed = false;
    incoming.destroy = () => {
      if (incoming.destroyed) return;
      incoming.destroyed = true;
      queueMicrotask(() => {
        incoming.emit('error', Object.assign(new Error('synthetic response reset'), { code: 'ECONNRESET' }));
        incoming.emit('close');
      });
    };
    captured.response = incoming;
    captured.responses.push(incoming);
    captured.pendingCallback(incoming);
    return incoming;
  };
  captured.respond = (bodyChunks = chunks, responseStatus = status, responseHeaders = headers) => {
    const incoming = captured.openResponse(responseStatus, responseHeaders);
    if (incoming.destroyed) return incoming;
    for (const chunk of bodyChunks) {
      incoming.emit('data', chunk);
      if (incoming.destroyed) return incoming;
    }
    incoming.emit('end');
    incoming.emit('close');
    return incoming;
  };
  const httpsRequest = (url, options, callback) => {
    captured.calls.push({ url: url.toString(), options });
    const outgoing = new EventEmitter();
    outgoing.destroyed = false;
    outgoing.end = () => {
      captured.pendingCallback = callback;
      if (autoResponse) captured.respond();
    };
    outgoing.destroy = () => {
      if (outgoing.destroyed) return outgoing;
      outgoing.destroyed = true;
      queueMicrotask(() => {
        outgoing.emit('error', Object.assign(new Error('synthetic socket hang up'), { code: 'ECONNRESET' }));
        outgoing.emit('close');
      });
      return outgoing;
    };
    captured.requests.push(outgoing);
    return outgoing;
  };
  return { httpsRequest, captured };
}

const searchUrl = () => new URL('https://musicbrainz.org/ws/2/recording/?query=x&fmt=json&limit=100&offset=0');

describe('bounded provider HTTPS transport', () => {
  it('accepts the actual provider lookup through the strict HTTPS transport with request-sensitive credits', async () => {
    const fake = fakeHttps({ autoResponse: false });
    const transport = createSongEnrichmentTransport({ request: (url, options, callback) => {
      const outgoing = fake.httpsRequest(url, options, callback);
      const end = outgoing.end;
      outgoing.end = () => {
        end();
        const body = { id: MBID, title: 'Song', 'first-release-date': '1978', genres: [{ name: 'Rock' }], tags: [] };
        if (url.searchParams.get('inc').split(' ').includes('artist-credits')) {
          body['artist-credit'] = [{ artist: { name: 'Artist' } }];
        }
        fake.captured.respond([Buffer.from(JSON.stringify(body))]);
      };
      return outgoing;
    } });
    const client = createSongEnrichmentProviderClient({ transport, config: {
      maxAttempts: 1, maxGeminiAttempts: 1, timeoutMs: 10000, maxResponseBytes: MAX_RESPONSE_BYTES,
      geminiMaxInputBytes: 32000, geminiMaxOutputTokens: 1000, musicBrainzUserAgent: USER_AGENT,
    } });
    const result = await client.lookupRecording({ mbid: MBID.toUpperCase() });
    expect(result).toMatchObject({ status: 'ok', mbid: MBID,
      recording: { title: 'Song', artist: 'Artist' },
      scalars: { release_year: [{ value: 1978, source: 'MusicBrainz' }] },
      genres: [{ name: 'Rock', association: 'genre' }] });
    expect(fake.captured.calls).toHaveLength(1);
    const actualUrl = new URL(fake.captured.calls[0].url);
    expect(actualUrl.pathname).toBe(`/ws/2/recording/${MBID}`);
    expect(actualUrl.searchParams.get('inc')).toBe('artist-credits genres tags');
    expect(actualUrl.searchParams.get('fmt')).toBe('json');
    expect(client.getAttemptCounts()).toEqual({ total: 1, gemini: 0 });
    expect(client.getRunLedger()).toEqual([{ provider: 'MusicBrainz', operation: 'recording_lookup', attempt: 1, status: 'ok' }]);
  });

  it('rejects old and unexpected recording lookup includes before HTTPS dispatch', async () => {
    const fake = fakeHttps();
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    for (const includes of ['genres+tags', 'artist-credits', 'artist-credits+genres', 'artist-credits+tags',
      'artist-credits+genres+tags+releases', 'artist-credits+genres+tags+artist-credits', 'tags+genres+artist-credits']) {
      await expect(transport(request(new URL(`https://musicbrainz.org/ws/2/recording/${MBID}?inc=${includes}&fmt=json`))))
        .rejects.toMatchObject({ code: 'INVALID_PROVIDER_REQUEST' });
    }
    expect(fake.captured.calls).toHaveLength(0);
  });

  it('converts allowed MusicBrainz and AcousticBrainz responses to the B2 contract', async () => {
    const fake = fakeHttps({ chunks: [Buffer.from('{"count":'), Buffer.from('0}')], headers: { 'x-test': 'ok' } });
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    const music = await transport(request(searchUrl()));
    expect(music).toEqual({ status: 200, headers: { 'x-test': 'ok' }, body: Buffer.from('{"count":0}') });
    expect(fake.captured.calls[0].options).toEqual({ method: 'GET', headers: { Accept: 'application/json', 'User-Agent': USER_AGENT } });

    const acoustic = fakeHttps({ chunks: [Buffer.from('{}')] });
    const acousticTransport = createSongEnrichmentTransport({ request: acoustic.httpsRequest });
    const result = await acousticTransport(request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=3`),
      { acousticbrainzSubmissionCount: 4 }));
    expect(result.body.toString()).toBe('{}');
    expect(acoustic.captured.calls[0].url).toContain(`/api/v1/${MBID}/low-level?n=3`);
  });

  it('rejects arbitrary hosts, credentials, unexpected paths, methods, Gemini and invalid headers before dispatch', async () => {
    const fake = fakeHttps();
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    const invalid = [
      request(new URL('https://example.com/')),
      request(new URL(`https://user:password@musicbrainz.org/ws/2/recording/${MBID}?inc=artist-credits+genres+tags&fmt=json`)),
      request(new URL('https://musicbrainz.org/ws/2/artist/?fmt=json')),
      request(new URL('https://acousticbrainz.org/api/v1/not-a-uuid/count')),
      request(new URL('https://generativelanguage.googleapis.com/v1beta/interactions'), { method: 'POST' }),
      request(searchUrl(), { method: 'POST' }),
      request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=1&n=2`), { acousticbrainzSubmissionCount: 3 }),
      request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=1.5`), { acousticbrainzSubmissionCount: 3 }),
      request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=225`)),
      request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=225`), { acousticbrainzSubmissionCount: 225 }),
      request(searchUrl(), { headers: { Accept: 'application/json', 'User-Agent': `${USER_AGENT}\r\nInjected: true` } }),
    ];
    for (const item of invalid) await expect(transport(item)).rejects.toMatchObject({ code: 'INVALID_PROVIDER_REQUEST' });
    expect(fake.captured.calls).toHaveLength(0);
  });

  it('allows only the exact limit-100 offset-zero MusicBrainz search URL', async () => {
    const fake = fakeHttps({ chunks: [Buffer.from('{}')] });
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    await transport(request(searchUrl()));
    expect(fake.captured.calls).toHaveLength(1);
    expect(new URL(fake.captured.calls[0].url).searchParams.get('limit')).toBe('100');
    expect(new URL(fake.captured.calls[0].url).searchParams.get('offset')).toBe('0');
    const invalid = [
      'https://musicbrainz.org/ws/2/recording/?query=x&fmt=json&limit=25&offset=0',
      'https://musicbrainz.org/ws/2/recording/?query=x&fmt=json&limit=100&offset=25',
      'https://musicbrainz.org/ws/2/recording/?query=x&fmt=json&limit=100',
      'https://musicbrainz.org/ws/2/recording/?query=x&fmt=json&limit=100&offset=-0',
      'https://musicbrainz.org/ws/2/recording/?query=x&fmt=json&limit=100&offset=0&extra=1',
    ];
    for (const url of invalid) {
      await expect(transport(request(new URL(url)))).rejects.toMatchObject({ code: 'INVALID_PROVIDER_REQUEST' });
    }
    expect(fake.captured.calls).toHaveLength(1);
  });

  it('allows validated AcousticBrainz offset 225 below its returned count', async () => {
    const fake = fakeHttps({ chunks: [Buffer.from('{}')] });
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    const result = await transport(request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=225`),
      { acousticbrainzSubmissionCount: 226 }));
    expect(result.status).toBe(200);
    expect(fake.captured.calls).toHaveLength(1);
    expect(new URL(fake.captured.calls[0].url).searchParams.get('n')).toBe('225');
    await expect(transport(request(new URL(`https://acousticbrainz.org/api/v1/${MBID}/low-level?n=226`),
      { acousticbrainzSubmissionCount: 226 }))).rejects.toMatchObject({ code: 'INVALID_PROVIDER_REQUEST' });
    expect(fake.captured.calls).toHaveLength(1);
  });

  it('runs 226-submission sampling through the provider and strict native transport boundary', async () => {
    const fake = fakeHttps({ autoResponse: false });
    const validatedCounts = [];
    const transport = createSongEnrichmentTransport({ request: (url, options, callback) => {
      const outgoing = fake.httpsRequest(url, options, callback);
      const originalEnd = outgoing.end;
      outgoing.end = () => {
        originalEnd();
        const requestUrl = new URL(url);
        let body;
        if (requestUrl.pathname.endsWith('/count')) body = { mbid: MBID, count: 226 };
        else {
          const offset = Number(requestUrl.searchParams.get('n'));
          body = { metadata: { tags: { musicbrainz_recordingid: [MBID] } }, offset,
            rhythm: { bpm: 90 + offset }, tonal: { key_key: 'C', key_scale: 'major', key_strength: 0.75 } };
        }
        fake.captured.respond([Buffer.from(JSON.stringify(body))]);
      };
      return outgoing;
    } });
    const observedTransport = requestObject => {
      if (new URL(requestObject.url).pathname.endsWith('/low-level')) {
        validatedCounts.push(requestObject.acousticbrainzSubmissionCount);
      }
      return transport(requestObject);
    };
    const client = createSongEnrichmentProviderClient({ transport: observedTransport, config: {
      maxAttempts: 4, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
      geminiMaxInputBytes: 32_000, geminiMaxOutputTokens: 1_000, musicBrainzUserAgent: USER_AGENT,
    } });
    const result = await client.getAcousticBrainzEvidence({ mbid: MBID });
    expect(result).toMatchObject({ status: 'ok', submission_count: 226, submission_enumeration: 'partial',
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0, 112, 225], sampled_offsets: [0, 112, 225],
      agreement_scope: 'sampled_submissions' });
    const offsets = fake.captured.calls.filter(call => new URL(call.url).pathname.endsWith('/low-level'))
      .map(call => new URL(call.url).searchParams.get('n'));
    expect(offsets).toEqual(['0', '112', '225']);
    expect(validatedCounts).toEqual([226, 226, 226]);
    expect(result.scalars.bpm.map(item => item.submission_id)).toEqual([`${MBID}:0`, `${MBID}:112`, `${MBID}:225`]);
    expect(result.scalars.original_key.map(item => item.key_strength)).toEqual([0.75, 0.75, 0.75]);
    expect(new Set(result.scalars.bpm.map(item => item.response_digest)).size).toBe(3);
    expect(client.getAttemptCounts()).toEqual({ total: 4, gemini: 0 });
  });

  it('returns redirects without following them or reading their response body', async () => {
    const fake = fakeHttps({ status: 302, headers: { location: 'https://attacker.invalid/secret' }, chunks: [Buffer.from('redirect body')] });
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    const result = await transport(request(searchUrl()));
    expect(result).toEqual({ status: 302, headers: { location: 'https://attacker.invalid/secret' }, body: Buffer.alloc(0) });
    await new Promise(resolve => setImmediate(resolve));
    expect(fake.captured.calls).toHaveLength(1);
    expect(fake.captured.response.destroyed).toBe(true);
    expect(fake.captured.response.listenerCount('data')).toBe(0);
  });

  it('accepts exactly 1 MiB and rejects one additional streamed byte', async () => {
    const exact = fakeHttps({ chunks: [Buffer.alloc(MAX_RESPONSE_BYTES - 9, 0x61), Buffer.alloc(9, 0x62)] });
    const exactTransport = createSongEnrichmentTransport({ request: exact.httpsRequest });
    const exactResult = await exactTransport(request(searchUrl(), { maxResponseBytes: MAX_RESPONSE_BYTES }));
    expect(exactResult.body.byteLength).toBe(MAX_RESPONSE_BYTES);

    const oversized = fakeHttps({ chunks: [Buffer.alloc(MAX_RESPONSE_BYTES, 0x61), Buffer.from('x')] });
    const transport = createSongEnrichmentTransport({ request: oversized.httpsRequest });
    await expect(transport(request(searchUrl(), { maxResponseBytes: MAX_RESPONSE_BYTES + 1 })))
      .rejects.toMatchObject({ code: 'RESPONSE_TOO_LARGE' });
    await new Promise(resolve => setImmediate(resolve));
    expect(oversized.captured.requests[0].destroyed).toBe(true);
    expect(oversized.captured.responses[0].destroyed).toBe(true);
    expect(oversized.captured.requests[0].listenerCount('error')).toBe(0);
    expect(oversized.captured.responses[0].listenerCount('error')).toBe(0);
  });

  it('handles response stream errors while reading and closes both resources', async () => {
    const fake = fakeHttps({ autoResponse: false });
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    const pending = transport(request(searchUrl()));
    const response = fake.captured.openResponse();
    response.emit('data', Buffer.from('partial'));
    response.emit('error', Object.assign(new Error('controlled stream failure'), { code: 'ECONNRESET' }));
    await expect(pending).rejects.toMatchObject({ message: 'Provider HTTPS response failed.' });
    await new Promise(resolve => setImmediate(resolve));
    expect(fake.captured.requests[0].destroyed).toBe(true);
    expect(response.destroyed).toBe(true);
    expect(fake.captured.requests[0].listenerCount('error')).toBe(0);
    expect(response.listenerCount('error')).toBe(0);
  });

  it.each(['timeout', 'cancellation'])('destroys a response delivered after %s without attaching body readers', async ending => {
    const fake = fakeHttps({ autoResponse: false });
    let timeout;
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest,
      setTimer: callback => { timeout = callback; return 1; }, clearTimer: () => {} });
    const controller = new AbortController();
    const pending = transport(request(searchUrl(), { signal: controller.signal }));
    if (ending === 'timeout') timeout(); else controller.abort();
    await expect(pending).rejects.toMatchObject(ending === 'timeout' ? { code: 'ETIMEDOUT' } : { name: 'AbortError' });
    const lateResponse = fake.captured.respond([Buffer.from('late body')]);
    await new Promise(resolve => setImmediate(resolve));
    expect(lateResponse.destroyed).toBe(true);
    expect(lateResponse.listenerCount('data')).toBe(0);
    expect(lateResponse.listenerCount('end')).toBe(0);
    expect(lateResponse.listenerCount('error')).toBe(0);
  });

  it('destroys a request acquired before a synchronous start failure', async () => {
    const fake = fakeHttps({ autoResponse: false });
    fake.httpsRequest = (url, options, callback) => {
      const outgoing = new EventEmitter();
      outgoing.destroyed = false;
      outgoing.end = () => { throw new Error('synthetic end failure'); };
      outgoing.destroy = () => {
        outgoing.destroyed = true;
        queueMicrotask(() => outgoing.emit('close'));
      };
      fake.captured.requests.push(outgoing);
      return outgoing;
    };
    const transport = createSongEnrichmentTransport({ request: fake.httpsRequest });
    await expect(transport(request(searchUrl()))).rejects.toMatchObject({ message: 'Provider HTTPS request could not be started.' });
    await new Promise(resolve => setImmediate(resolve));
    expect(fake.captured.requests[0].destroyed).toBe(true);
    expect(fake.captured.requests[0].listenerCount('error')).toBe(0);
  });

  it('retains native request error handling through abort and delayed ECONNRESET', async () => {
    const agent = new https.Agent();
    let nativeRequest;
    let socketCreations = 0;
    agent.createConnection = () => {
      socketCreations += 1;
      return new Duplex({ read() {}, write(_chunk, _encoding, callback) { callback(); } });
    };
    const transport = createSongEnrichmentTransport({ request: (url, options, callback) => {
      nativeRequest = https.request(url, { ...options, agent }, callback);
      return nativeRequest;
    } });
    const controller = new AbortController();
    const pending = transport(request(searchUrl(), { signal: controller.signal }));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await new Promise(resolve => setImmediate(resolve));
    expect(socketCreations).toBe(1);
    expect(nativeRequest.destroyed).toBe(true);
    expect(nativeRequest.listenerCount('error')).toBe(0);
    agent.destroy();
  });

  it('propagates cancellation and timeout without unhandled late socket errors', async () => {
    const pending = fakeHttps({ autoResponse: false });
    const transport = createSongEnrichmentTransport({ request: pending.httpsRequest });
    const controller = new AbortController();
    const cancelled = transport(request(searchUrl(), { signal: controller.signal }));
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
    await new Promise(resolve => setImmediate(resolve));
    expect(pending.captured.requests[0].destroyed).toBe(true);
    expect(pending.captured.requests[0].listenerCount('error')).toBe(0);

    const timed = fakeHttps({ autoResponse: false });
    const timeoutTransport = createSongEnrichmentTransport({ request: timed.httpsRequest,
      setTimer: callback => { queueMicrotask(callback); return 7; }, clearTimer: () => {} });
    await expect(timeoutTransport(request(searchUrl()))).rejects.toMatchObject({ code: 'ETIMEDOUT' });
    await new Promise(resolve => setImmediate(resolve));
    expect(timed.captured.requests[0].destroyed).toBe(true);
    expect(timed.captured.requests[0].listenerCount('error')).toBe(0);
  });
});
