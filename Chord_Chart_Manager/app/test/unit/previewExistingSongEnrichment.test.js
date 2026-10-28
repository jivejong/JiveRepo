import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const preview = require('../../scripts/preview-existing-song-enrichment.cjs');
const { createSongEnrichmentProviderClient } = require('../../server/songEnrichmentProviders.js');
const { MAX_RESPONSE_BYTES } = require('../../server/songEnrichmentTransport.js');
const MBID = 'a1111111-1111-4111-8111-111111111111';
const OTHER_MBID = 'b2222222-2222-4222-8222-222222222222';
const THIRD_MBID = 'c3333333-3333-4333-8333-333333333333';
const UA = 'ChordChartManager/1.0.0 (contact@example.test)';
const temporaryDirectories = [];

function song(overrides = {}) {
  return { id: 42, title: 'Lights', artist: 'Journey', updated_at: '2026-10-15T14:22:33.123456Z',
    release_year: null, original_key: '', bpm: null, genres: [], vibes: [], ...overrides };
}

function fakePool(rows = [song()], events = [], failAt = null) {
  const queries = [];
  const client = {
    async query(sql, params = []) {
      queries.push({ sql, params });
      events.push(`db:${sql.trim().split(/\s+/).slice(0, 3).join(' ')}`);
      if (failAt === 'BEGIN' && sql === 'BEGIN READ ONLY' || failAt === 'COMMIT' && sql === 'COMMIT' ||
          failAt === 'SONGS' && sql.includes('FROM public.songs s JOIN public.artists')) throw new Error('synthetic database failure');
      if (sql === 'BEGIN READ ONLY' || sql === 'COMMIT' || sql === 'ROLLBACK') return { rows: [], rowCount: 0 };
      if (sql.includes('FROM public.songs s JOIN public.artists')) {
        const selected = rows.filter(row => params[0].includes(row.id)).sort((left, right) => left.id - right.id);
        return { rows: selected.map(row => ({ ...row, artist_name: row.artist })), rowCount: selected.length };
      }
      if (sql.includes('FROM public.song_genres')) {
        const assignments = rows.filter(row => params[0].includes(row.id)).flatMap(row => (row.genres || [])
          .map(assignment => ({ song_id: row.id, ...assignment })));
        return { rows: assignments, rowCount: assignments.length };
      }
      if (sql.includes('FROM public.song_vibes')) {
        const assignments = rows.filter(row => params[0].includes(row.id)).flatMap(row => (row.vibes || [])
          .map(assignment => ({ song_id: row.id, ...assignment })));
        return { rows: assignments, rowCount: assignments.length };
      }
      if (sql === 'SELECT id,name FROM public.genres ORDER BY id') return { rows: [{ id: 1, name: 'Country' }, { id: 2, name: 'Rock' }], rowCount: 2 };
      if (sql === 'SELECT id,name FROM public.vibes ORDER BY id') return { rows: [{ id: 3, name: 'Country' }, { id: 4, name: 'Calm' }], rowCount: 2 };
      throw new Error(`Unexpected fake database query: ${sql}`);
    },
    release() { events.push('db:release'); },
  };
  return { pool: { async connect() { events.push('db:connect'); return client; }, async end() { events.push('db:end'); } }, queries };
}

function mbSearch(candidates = [{ id: MBID, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }],
  'first-release-date': '1977-06-18', disambiguation: 'Studio recording', score: '99' }], overrides = {}) {
  return { count: candidates.length, offset: 0, recordings: candidates, apiKey: 'provider-secret-sentinel',
    chart_content: 'forbidden-chart-sentinel', ...overrides };
}

function evidenceBody({ bpm = 90, key = 'C', scale = 'major', keyStrength = 0.75 } = {}) {
  return { metadata: { tags: { musicbrainz_recordingid: [MBID] } }, rhythm: { bpm },
    tonal: { key_key: key, key_scale: scale, key_strength: keyStrength }, apiKey: 'provider-secret-sentinel', chart_source: 'forbidden-chart-sentinel' };
}

function mockProvider({ candidates, genres = [{ name: 'Country' }, { name: 'Uncatalogued' }],
  genresByMbid = {}, lookupDatesByMbid = {}, releaseEvidenceByMbid = {}, failedLookupMbids = [], authFailedLookupMbids = [], submissions = [evidenceBody(), evidenceBody({ bpm: 102, key: 'D', scale: 'minor' })],
  count = 2, events = [], maxAttempts = 12, failSubmissionOffset = null } = {}) {
  let now = 1_800_000_000_000;
  const calls = [];
  const body = request => {
    const url = new URL(request.url);
    if (url.hostname === 'musicbrainz.org' && url.pathname.endsWith('/recording/')) return mbSearch(candidates);
    if (url.hostname === 'musicbrainz.org') {
      const mbid = url.pathname.split('/').filter(Boolean).at(-1).toLowerCase();
      if (failedLookupMbids.map(value => value.toLowerCase()).includes(mbid)) {
        return { status: 503, headers: { 'Retry-After': '0' }, body: Buffer.from('synthetic lookup failure') };
      }
      if (authFailedLookupMbids.map(value => value.toLowerCase()).includes(mbid)) {
        return { status: 401, headers: {}, body: Buffer.from('synthetic authentication failure') };
      }
      const candidate = (candidates || [{ id: MBID, 'first-release-date': '1977-06-18' }]).find(item => item.id.toLowerCase() === mbid);
      const firstDate = candidate?.['first-release-date'] || lookupDatesByMbid[mbid] || '1977-06-18';
      return { id: mbid, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }],
        ...(lookupDatesByMbid[mbid] ? { 'first-release-date': lookupDatesByMbid[mbid] } : {}),
        'release-count': 1,
        releases: releaseEvidenceByMbid[mbid] || [{ id: OTHER_MBID, title: 'Studio Album', date: firstDate,
          status: 'Official', disambiguation: '', 'release-group': { id: THIRD_MBID, title: 'Studio Album',
            'primary-type': 'Album', 'secondary-types': [] } }],
        genres: genresByMbid[mbid] || genres, tags: [{ name: 'Rock' }], apiKey: 'provider-secret-sentinel' };
    }
    if (url.pathname.endsWith('/count')) return { mbid: MBID, count };
    const offset = Number(url.searchParams.get('n'));
    if (offset === failSubmissionOffset) return { status: 503, headers: { 'Retry-After': '0' }, body: Buffer.from('synthetic transient provider failure') };
    return { ...submissions[offset], offset };
  };
  const transport = async request => {
    calls.push(request);
    events.push(`provider:${new URL(request.url).pathname}`);
    const result = body(request);
    if (result && Object.hasOwn(result, 'status')) return result;
    return { status: 200, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify(result)) };
  };
  const delay = async (milliseconds, { kind, signal } = {}) => {
    if (kind === 'timeout') return new Promise(resolve => signal?.addEventListener('abort', resolve, { once: true }));
    now += milliseconds;
  };
  const client = createSongEnrichmentProviderClient({ transport, clock: () => now, delay, config: {
    maxAttempts, maxGeminiAttempts: 1, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
    geminiMaxInputBytes: 32_000, geminiMaxOutputTokens: 1_000, maxRetryAfterMs: 5_000,
    retryDelayMs: 1_000, musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: UA,
  }, cache: null });
  return { client, calls };
}

const cliArgs = (outputDirectory = 'backups/b4-run-unique', userAgent = UA) => ['--live-read-only', '--song-ids', '42', '--max-attempts', '12',
  '--musicbrainz-user-agent', userAgent, '--output-dir', outputDirectory];

async function ownedOutputDirectory() {
  const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-b4-main-'));
  temporaryDirectories.push(parent);
  return path.join(parent, 'run');
}

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map(directory => fs.rm(directory, { recursive: true, force: true })));
});

describe('existing-song read-only enrichment preview', () => {
  it('imports safely and default invocation rejects before database or provider access', async () => {
    let connects = 0;
    let requests = 0;
    const providerClient = { searchRecordings: async () => { requests += 1; }, lookupRecording: async () => {},
      getAcousticBrainzEvidence: async () => {}, getAttemptCounts: () => ({ total: 0 }), getRunLedger: () => [] };
    await expect(preview.main([], { databaseUrl: 'postgres://synthetic.invalid/db',
      pool: { async connect() { connects += 1; } }, providerClient })).rejects.toMatchObject({ code: 'INVALID_ARGUMENTS' });
    expect({ connects, requests }).toEqual({ connects: 0, requests: 0 });
  });

  it('rejects CRLF User-Agent before filesystem, database or provider effects', async () => {
    const effects = { filesystem: 0, db: 0, provider: 0 };
    const directory = await ownedOutputDirectory();
    const fakeFilesystem = Object.fromEntries(['realpath', 'mkdir', 'open', 'rmdir', 'unlink'].map(name => [name, async () => {
      effects.filesystem += 1;
      throw new Error('filesystem must not be reached');
    }]));
    await expect(preview.main(cliArgs(directory, `${UA}\r\n`), {
      databaseUrl: 'postgres://synthetic.invalid/db',
      filesystem: fakeFilesystem,
      outputOptions: { isIgnored: async () => { effects.filesystem += 1; return true; } },
      pool: { async connect() { effects.db += 1; throw new Error('DB must not be reached'); }, async end() {} },
      providerClient: { searchRecordings: async () => { effects.provider += 1; }, lookupRecording: async () => {},
        getAcousticBrainzEvidence: async () => {}, getAttemptCounts: () => ({ total: 0 }), getRunLedger: () => [] },
    })).rejects.toMatchObject({ code: 'INVALID_ARGUMENTS' });
    expect(effects).toEqual({ filesystem: 0, db: 0, provider: 0 });
  });

  it('rejects missing flags, malformed IDs, duplicate IDs, missing User-Agent and excessive bounds', () => {
    expect(() => preview.parseArguments([])).toThrow(/--live-read-only/);
    expect(() => preview.parseArguments(['--live-read-only'])).toThrow(/--song-ids/);
    const base = ['--live-read-only', '--song-ids', '42', '--max-attempts', '12', '--musicbrainz-user-agent', UA, '--output-dir', 'backups/new-run'];
    for (const ids of ['0', '-2', 'abc', '42,42', '1,2,3,4']) {
      const args = [...base]; args[2] = ids;
      expect(() => preview.parseArguments(args)).toThrow(/song-ids/);
    }
    for (const limit of ['0', '13', '1.5', '']) {
      const args = [...base]; args[4] = limit;
      expect(() => preview.parseArguments(args)).toThrow(/max-attempts/);
    }
    const noUserAgent = [...base]; noUserAgent.splice(5, 2);
    expect(() => preview.parseArguments(noUserAgent)).toThrow(/User-Agent/);
  });

  it('reads explicit baseline columns in one read-only transaction and releases before provider dispatch', async () => {
    const events = [];
    const { pool, queries } = fakePool([song()], events);
    const providers = mockProvider({ events });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12,
      clock: () => 1_800_000_000_000 });
    expect(events.indexOf('db:release')).toBeLessThan(events.findIndex(value => value.startsWith('provider:/')));
    expect(queries[0].sql).toBe('BEGIN READ ONLY');
    expect(queries.at(-1).sql).toBe('COMMIT');
    const songQuery = queries.find(item => item.sql.includes('FROM public.songs s JOIN public.artists'));
    expect(songQuery.sql).toContain('s.id,s.title,s.artist_id,a.name AS artist_name,s.release_year,s.original_key,s.bpm');
    expect(songQuery.sql).toContain('SS.US');
    expect(songQuery.sql).not.toMatch(/chart_source|chart_content|lyrics|SELECT\s+s\.\*/i);
    expect(queries.some(item => /\b(INSERT|UPDATE|DELETE|LOCK)\b/i.test(item.sql))).toBe(false);
    expect(result.songs[0].base_version).toBe('2026-10-15T14:22:33.123456Z');
    expect(result.database_writes).toBe(false);
  });

  it('runs baseline queries sequentially and releases the client after a query failure', async () => {
    const makePool = failOn => {
      const events = [];
      let active = 0;
      let overlap = false;
      const client = {
        async query(sql) {
          if (active !== 0) overlap = true;
          active += 1;
          events.push(sql.trim().split(/\s+/).slice(0, 3).join(' '));
          try {
            await Promise.resolve();
            if (failOn && sql.includes(failOn)) throw new Error('synthetic query failure');
            if (sql === 'BEGIN READ ONLY' || sql === 'COMMIT' || sql === 'ROLLBACK') return { rows: [] };
            if (sql.includes('FROM public.songs s JOIN public.artists')) return { rows: [{ id: 42, title: 'Lights',
              artist_name: 'Journey', updated_at: song().updated_at, release_year: null, original_key: null, bpm: null }] };
            if (sql.includes('FROM public.song_genres')) return { rows: [] };
            if (sql.includes('FROM public.song_vibes')) return { rows: [] };
            if (sql === 'SELECT id,name FROM public.genres ORDER BY id') return { rows: [{ id: 1, name: 'Rock' }] };
            if (sql === 'SELECT id,name FROM public.vibes ORDER BY id') return { rows: [{ id: 2, name: 'Calm' }] };
            throw new Error('unexpected query');
          } finally { active -= 1; }
        },
        release() { events.push('released'); },
      };
      return { pool: { async connect() { return client; } }, events, hasOverlap: () => overlap };
    };

    const success = makePool(null);
    const baseline = await preview.captureReadOnlyBaseline(success.pool, [42]);
    expect(baseline.songs[0]).toMatchObject({ id: 42, title: 'Lights', artist: 'Journey' });
    expect(success.hasOverlap()).toBe(false);
    expect(success.events.at(-1)).toBe('released');
    expect(success.events).toContain('COMMIT');

    const failure = makePool('FROM public.song_vibes');
    await expect(preview.captureReadOnlyBaseline(failure.pool, [42])).rejects.toMatchObject({ code: 'BASELINE_READ_FAILED' });
    expect(failure.hasOverlap()).toBe(false);
    expect(failure.events).toContain('ROLLBACK');
    expect(failure.events.at(-1)).toBe('released');
  });

  it('releases after a missing requested ID and makes no provider request', async () => {
    const events = [];
    const { pool } = fakePool([], events);
    const providers = mockProvider({ events });
    await expect(preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 }))
      .rejects.toMatchObject({ code: 'SONG_ID_SET_MISMATCH' });
    expect(events).toContain('db:ROLLBACK');
    expect(events).toContain('db:release');
    expect(providers.calls).toHaveLength(0);
  });

  it('matches through B1, reports unapproved missing candidates and resolves Genre only', async () => {
    const { pool } = fakePool([song()]);
    const providers = mockProvider();
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12,
      clock: () => 1_800_000_000_000 });
    const item = result.songs[0];
    expect(item.recording.match).toMatchObject({ matched: true, mbid: MBID });
    expect(item.recording.candidates[0]).not.toHaveProperty('score');
    expect(item.scalar_candidates.release_year.candidates[0]).toMatchObject({ value: 1977, source: 'MusicBrainz' });
    expect(item.scalar_candidates.original_key.conflicting_evidence).toBe(true);
    expect(item.scalar_candidates.bpm.conflicting_evidence).toBe(true);
    expect(item.tag_candidates.genres.unapproved_additions).toEqual([
      { id: 1, name: 'Country', evidence: [{ source: 'MusicBrainz', response_digest: expect.stringMatching(/^[a-f0-9]{64}$/) }] },
      { id: 2, name: 'Rock', evidence: [{ source: 'MusicBrainz', response_digest: expect.stringMatching(/^[a-f0-9]{64}$/) }] },
    ]);
    expect(item.tag_candidates.genres.unknown_labels.map(row => row.label)).toEqual(['Uncatalogued']);
    expect(item.tag_candidates.vibes.unapproved_additions).toEqual([]);
    expect(item.evidence).toMatchObject({ mbid: MBID, vibes: [] });
    expect(item).not.toHaveProperty('review_decision');
    expect(item.approved_preview).toBeNull();
    expect(item.human_review_required).toBe(true);
    expect(result.limits.gemini_calls).toBe(0);
    expect(result.attempts.gemini).toBe(0);
    expect(result.attempts.total).toBe(5);
    expect(result.database_writes).toBe(false);
    expect(providers.calls.some(call => new URL(call.url).hostname === 'generativelanguage.googleapis.com')).toBe(false);
  });

  it('selects the earliest-year eligible exact recording and excludes explicit alternate versions', async () => {
    const duplicate = [MBID, OTHER_MBID].map(id => ({ id, title: 'Lights',
      'artist-credit': [{ artist: { name: 'Journey' } }],
      'first-release-date': id === MBID ? '1977-06' : '1978',
      disambiguation: 'Studio recording',
      score: id === MBID ? '1' : '100' }));
    duplicate.push({ id: THIRD_MBID, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }],
      'first-release-date': '1970', disambiguation: 'Live' });
    const { pool } = fakePool([song({ release_year: 2001, original_key: 'D', bpm: 110 })]);
    const providers = mockProvider({ candidates: duplicate });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    expect(result.songs[0].recording.match).toMatchObject({ matched: true, mbid: MBID,
      selection_reason: 'earliest_release_year', earliest_year: 1977, eligible_candidate_count: 2,
      exclusion_counts: { live_version: 1 },
      release_date_evidence: { first_release_date: '1977-06', response_digest: expect.stringMatching(/^[a-f0-9]{64}$/) } });
    expect(result.songs[0].evidence).toMatchObject({ mbid: MBID });
    expect(providers.calls.map(call => new URL(call.url).pathname)).toEqual(['/ws/2/recording/', `/ws/2/recording/${MBID}`]);
    expect(result.songs[0].recording.candidates).toHaveLength(3);
    expect(result.songs[0].recording.candidates.find(row => row.mbid === MBID).first_release_date).toBe('1977-06');
    expect(preview.renderSummary(result)).toContain('Recording selection: earliest_release_year; eligible 2; earliest year 1977; missing dates 0; exclusions live_version=1.');
    expect(preview.renderSummary(result)).toMatch(/Selected release-date evidence: 1977-06; response digest [a-f0-9]{64}\./);
  });

  it('looks up every earliest-year tie once, ranks validated Genre matches, and reuses the selected lookup', async () => {
    const candidates = [
      { id: MBID, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }], 'first-release-date': '1978-01-01', disambiguation: 'Studio recording' },
      { id: OTHER_MBID, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }], 'first-release-date': '1978-12-31', disambiguation: 'Studio recording' },
    ];
    const { pool } = fakePool([song({ original_key: 'D', bpm: 120 })]);
    const providers = mockProvider({ candidates, genresByMbid: {
      [MBID]: [{ name: 'Rock' }], [OTHER_MBID]: [{ name: 'Country' }, { name: 'Rock' }],
    }, lookupDatesByMbid: { [OTHER_MBID]: '1979-02-11' } });
    const report = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const item = report.songs[0];
    expect(item.recording.match).toMatchObject({ matched: true, mbid: OTHER_MBID,
      selection_reason: 'genre_richness', earliest_year: 1978, tie_break_reason: 'matched_managed_genre_richness',
      same_year_candidate_mbids: [MBID, OTHER_MBID], matched_genre_counts: [
        { mbid: MBID, matched_genre_count: 1 }, { mbid: OTHER_MBID, matched_genre_count: 2 },
      ], richness_comparison_complete: true });
    expect(item.recording.richness_comparison).toMatchObject({ complete: true, selected_mbid: OTHER_MBID,
      candidates: [{ mbid: MBID, matched_genre_count: 1, lookup_status: 'ok' },
        { mbid: OTHER_MBID, matched_genre_count: 2, lookup_status: 'ok' }] });
    const lookupPaths = providers.calls.map(call => new URL(call.url).pathname)
      .filter(pathname => /^\/ws\/2\/recording\/[0-9a-f-]+$/.test(pathname));
    expect(lookupPaths.sort()).toEqual([`/ws/2/recording/${MBID}`, `/ws/2/recording/${OTHER_MBID}`].sort());
    expect(lookupPaths.filter(pathname => pathname === `/ws/2/recording/${OTHER_MBID}`)).toHaveLength(1);
    expect(item.recording.release_date_comparison).toMatchObject({ search_year: 1978,
      search_first_release_date: '1978-12-31', lookup_year_discrepancy: true,
      lookup_release_year_evidence: [{ value: 1979, source: 'MusicBrainz', origin: 'lookup_response' }] });
    expect(item.scalar_candidates.release_year.candidates).toMatchObject([{ value: 1979, source: 'MusicBrainz' }]);
    expect(item.current).toMatchObject({ release_year: null, original_key: 'D', bpm: 120 });
    expect(item.tag_candidates.genres.unapproved_additions.map(row => row.name)).toEqual(['Country', 'Rock']);
    expect(item.acousticbrainz.status).toBe('skipped');
    expect(report.attempts.total).toBe(3);
  });

  it('reuses the selected tied lookup when comparison consumes the final shared attempt', async () => {
    const candidates = [MBID, OTHER_MBID].map(id => ({ id, title: 'Lights',
      'artist-credit': [{ artist: { name: 'Journey' } }], 'first-release-date': '1978', disambiguation: 'Studio recording' }));
    const { pool } = fakePool([song({ original_key: '', bpm: null })]);
    const providers = mockProvider({ candidates, genresByMbid: {
      [MBID]: [{ name: 'Rock' }], [OTHER_MBID]: [{ name: 'Country' }, { name: 'Rock' }],
    } });
    const report = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 3 });
    const item = report.songs[0];
    expect(report.attempts.total).toBe(3);
    expect(item.recording.match).toMatchObject({ matched: true, mbid: OTHER_MBID, selection_reason: 'genre_richness' });
    expect(item.recording.lookup_status).toBe('ok');
    expect(item.scalar_candidates.release_year).toMatchObject({ candidates: [{ value: 1978, source: 'MusicBrainz' }] });
    expect(item.tag_candidates.genres.unapproved_additions.map(row => row.name)).toEqual(['Country', 'Rock']);
    expect(item.acousticbrainz.status).toBe('budget_exhausted');
    expect(providers.calls.filter(call => new URL(call.url).hostname === 'acousticbrainz.org')).toHaveLength(0);
    expect(providers.calls.map(call => new URL(call.url).pathname)
      .filter(pathname => pathname === `/ws/2/recording/${OTHER_MBID}`)).toHaveLength(1);
  });

  it.each([
    ['lookup failure', 12, [OTHER_MBID], 'transient_error'],
    ['shared budget exhaustion', 2, [], 'budget_exhausted'],
  ])('keeps tied-year selection provisional when a comparison lookup has %s', async (_label, maxAttempts, failed, expectedStatus) => {
    const candidates = [MBID, OTHER_MBID].map(id => ({ id, title: 'Lights',
      'artist-credit': [{ artist: { name: 'Journey' } }], 'first-release-date': '1978', disambiguation: 'Studio recording' }));
    const { pool } = fakePool([song({ original_key: 'D', bpm: 120 })]);
    const providers = mockProvider({ candidates, failedLookupMbids: failed });
    const report = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts });
    const item = report.songs[0];
    expect(item.recording.match).toMatchObject({ matched: false, reason: 'genre_richness_incomplete',
      selection_reason: 'richness_comparison_incomplete', earliest_year: 1978,
      same_year_candidate_mbids: [MBID, OTHER_MBID], provisional_mbid: MBID,
      richness_comparison_complete: false });
    expect(item.recording.richness_comparison).toMatchObject({ complete: false, selected_mbid: null,
      provisional_mbid: MBID, candidates: [
        { mbid: MBID, matched_genre_count: 1, lookup_status: 'ok' },
        { mbid: OTHER_MBID, matched_genre_count: null, lookup_status: expectedStatus },
      ] });
    expect(item.evidence).toBeNull();
    expect(item.scalar_candidates.release_year.candidates).toEqual([]);
    expect(item.acousticbrainz.status).toBe('skipped');
    expect(report.attempts.total).toBeLessThanOrEqual(maxAttempts);
  });

  it('preserves authentication failure for a tied recording lookup', async () => {
    const candidates = [MBID, OTHER_MBID].map(id => ({ id, title: 'Lights',
      'artist-credit': [{ artist: { name: 'Journey' } }], 'first-release-date': '1978', disambiguation: 'Studio recording' }));
    const { pool } = fakePool([song({ original_key: 'D', bpm: 120 })]);
    const providers = mockProvider({ candidates, authFailedLookupMbids: [OTHER_MBID] });
    const report = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const item = report.songs[0];
    expect(item.recording.match).toMatchObject({ matched: false, reason: 'genre_richness_incomplete',
      provisional_mbid: MBID, richness_comparison_complete: false });
    expect(item.recording.richness_comparison).toMatchObject({ complete: false,
      candidates: [{ mbid: MBID, lookup_status: 'ok' }, { mbid: OTHER_MBID, lookup_status: 'authentication_error' }] });
    expect(item.evidence).toBeNull();
    expect(item.acousticbrainz.status).toBe('skipped');
    expect(report.attempts.total).toBe(3);
  });

  it('preserves populated scalar and manual assignment baselines after earliest selection', async () => {
    const manualGenres = [{ id: 1, name: 'Country', is_primary: true }];
    const manualVibes = [{ id: 3, name: 'Country' }];
    const manual = song({ release_year: 2001, original_key: 'D', bpm: 123, genres: manualGenres, vibes: manualVibes });
    const { pool } = fakePool([manual]);
    const recordingCandidates = [
      { id: MBID, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }],
        'first-release-date': '1977', disambiguation: 'Studio' },
      { id: OTHER_MBID, title: 'Lights', 'artist-credit': [{ artist: { name: 'Journey' } }],
        'first-release-date': '1978', disambiguation: 'Studio recording' },
    ];
    const providers = mockProvider({ candidates: recordingCandidates, genres: [{ name: 'Country' }, { name: 'Rock' }] });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    expect(result.songs[0].current).toEqual({ release_year: 2001, original_key: 'D', bpm: 123,
      genres: manualGenres, vibes: manualVibes });
    for (const field of ['release_year', 'original_key', 'bpm']) {
      expect(result.songs[0].scalar_candidates[field]).toMatchObject({ state: 'populated_preserved', candidates: [] });
    }
    expect(result.songs[0].tag_candidates.genres.unapproved_additions.map(row => row.name)).toEqual(['Rock']);
    expect(result.songs[0].tag_candidates.vibes.unapproved_additions).toEqual([]);
  });

  it('retains safe single-response validation diagnostics in JSON and readable reports', async () => {
    const { pool } = fakePool([song()]);
    let dispatches = 0;
    const providerClient = createSongEnrichmentProviderClient({
      config: { maxAttempts: 12, maxGeminiAttempts: 2, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
        geminiMaxInputBytes: 32_000, geminiMaxOutputTokens: 1_000, musicBrainzUserAgent: UA },
      delay: async () => {},
      transport: async request => {
        dispatches += 1;
        expect(new URL(request.url).searchParams.get('limit')).toBe('100');
        expect(new URL(request.url).searchParams.get('offset')).toBe('0');
        return { status: 200, headers: {}, body: JSON.stringify({ count: 2, offset: 25,
          recordings: Array.from({ length: 2 }, (_, index) => ({
            id: `${String(index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`, title: `Other ${index}`,
            'artist-credit': [{ artist: { name: 'Journey' } }],
          })) }) };
      },
    });
    const report = await preview.runPreview({ pool, providerClient, songIds: [42], maxAttempts: 12,
      clock: () => 1_800_000_000_000 });
    expect(dispatches).toBe(1);
    expect(report.songs[0].recording).toMatchObject({ search_status: 'invalid_response', reason: 'incomplete_search_results',
      match: { matched: false, reason: 'incomplete_search_results' }, candidates: [],
      pagination: { expected_total: null, attempted_pages: 1, validated_pages: 0, collected_count: 0, complete: false },
      validation_diagnostic: { code: 'unexpected_offset', requested_offset: 0, returned_offset: 25,
        current_page_total: 2, returned_recording_count: 2,
        validated_response_count: 0, collected_record_count: 0 } });
    const json = JSON.stringify(report);
    const summary = preview.renderSummary(report);
    expect(json).toContain('"expected_total":null');
    expect(json).toContain('"requested_offset":0');
    expect(json).toContain('"code":"unexpected_offset"');
    expect(summary).toContain('Search responses: 0/1 validated/attempted; total unknown; collected 0; incomplete.');
    expect(summary).toContain('Search validation: unexpected_offset (requested_offset=0, returned_offset=25, current_page_total=2, returned_recording_count=2, validated_response_count=0, collected_record_count=0).');
    expect(report.songs[0].evidence).toBeNull();
    expect(report.songs[0].acousticbrainz.status).toBe('skipped');
  });

  it('reports complete single-response success and never invents malformed totals', async () => {
    const runWith = async transport => {
      const { pool } = fakePool([song()]);
      const providerClient = createSongEnrichmentProviderClient({
        config: { maxAttempts: 12, maxGeminiAttempts: 2, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
          geminiMaxInputBytes: 32_000, geminiMaxOutputTokens: 1_000, musicBrainzUserAgent: UA },
        delay: async () => {}, transport,
      });
      return preview.runPreview({ pool, providerClient, songIds: [42], maxAttempts: 12, clock: () => 1_800_000_000_000 });
    };
    let dispatches = 0;
    const successful = await runWith(async request => {
      dispatches += 1;
      expect(new URL(request.url).searchParams.get('limit')).toBe('100');
      expect(new URL(request.url).searchParams.get('offset')).toBe('0');
      const recordings = Array.from({ length: 75 }, (_, index) => ({
        id: `${String(index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`,
        title: `Other ${index}`, 'artist-credit': [{ artist: { name: 'Journey' } }],
      }));
      return { status: 200, headers: {}, body: JSON.stringify({ count: 75, offset: 0, recordings }) };
    });
    const successSong = successful.songs[0];
    expect(dispatches).toBe(1);
    expect(successSong.recording).toMatchObject({ search_status: 'ok',
      pagination: { expected_total: 75, attempted_pages: 1, validated_pages: 1, collected_count: 75, complete: true } });
    expect(preview.renderSummary(successful)).toContain('Search responses: 1/1 validated/attempted; total 75; collected 75; complete.');
    expect(JSON.stringify(successful)).toContain('"expected_total":75');

    const malformed = await runWith(async () => ({ status: 200, headers: {},
      body: JSON.stringify({ count: 'provider-secret-sentinel', offset: 0, recordings: [] }) }));
    expect(malformed.songs[0].recording).toMatchObject({ search_status: 'invalid_response',
      pagination: { expected_total: null, attempted_pages: 1, validated_pages: 0, collected_count: 0, complete: false },
      validation_diagnostic: { code: 'invalid_pagination_field', current_page_total: { type: 'string' } } });
    expect(JSON.stringify(malformed)).not.toContain('provider-secret-sentinel');
    expect(preview.renderSummary(malformed)).toContain('total unknown');
  });

  it('carries single-response duplicate MBID occurrences through actual B4 reporting', async () => {
    const { pool } = fakePool([song()]);
    const providerClient = createSongEnrichmentProviderClient({
      config: { maxAttempts: 12, maxGeminiAttempts: 2, timeoutMs: 10_000, maxResponseBytes: MAX_RESPONSE_BYTES,
        geminiMaxInputBytes: 32_000, geminiMaxOutputTokens: 1_000, musicBrainzUserAgent: UA },
      delay: async () => {},
      transport: async request => {
        const recordings = Array.from({ length: 2 }, (_, index) => ({
          id: `${String(index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`,
          title: `Other ${index}`, 'artist-credit': [{ artist: { name: 'Journey' } }],
        }));
        recordings[1].id = recordings[0].id;
        return { status: 200, headers: {}, body: JSON.stringify({ count: 2, offset: 0, recordings,
          apiKey: 'must-not-appear-in-report', raw_provider_payload: 'must-not-appear-in-report' }) };
      },
    });
    const report = await preview.runPreview({ pool, providerClient, songIds: [42], maxAttempts: 12,
      clock: () => 1_800_000_000_000 });
    const recording = report.songs[0].recording;
    expect(recording).toMatchObject({ search_status: 'invalid_response',
      match: { matched: false, reason: 'incomplete_search_results' }, candidates: [],
      reason: 'incomplete_search_results', validation_diagnostic: {
        code: 'duplicate_recording_identity', duplicate_mbid: '00000001-1111-4111-8111-111111111111',
        duplicate_scope: 'within_page', first_occurrence: { requested_offset: 0, index_in_page: 0 },
        repeated_occurrence: { requested_offset: 0, index_in_page: 1 },
      } });
    expect(recording.candidates).toEqual([]);
    const json = JSON.stringify(report);
    expect(json).toContain('"duplicate_mbid":"00000001-1111-4111-8111-111111111111"');
    expect(json).not.toContain('must-not-appear-in-report');
    const summary = preview.renderSummary(report);
    expect(summary).toContain('duplicate_mbid=00000001-1111-4111-8111-111111111111');
    expect(summary).toContain('duplicate_scope=within_page');
    expect(summary).toContain('first_occurrence={"requested_offset":0,"index_in_page":0}');
    expect(summary).toContain('repeated_occurrence={"requested_offset":0,"index_in_page":1}');
    expect(summary).toContain('abstained (incomplete_search_results)');
    expect(summary).not.toContain('must-not-appear-in-report');
  });

  it('stops dispatch when the shared budget is exhausted and marks later songs unattempted', async () => {
    const events = [];
    const second = song({ id: 43, title: 'Second light', release_year: 1991, original_key: 'D', bpm: 122,
      genres: [{ id: 2, name: 'Rock', is_primary: true }], vibes: [{ id: 4, name: 'Calm' }] });
    const { pool } = fakePool([song(), second], events);
    const providers = mockProvider({ events });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [43, 42], maxAttempts: 1 });
    expect(result.songs.map(row => row.song_id)).toEqual([42, 43]);
    expect(result.songs[0].recording.lookup_status).toBe('budget_exhausted');
    expect(result.songs[1]).toMatchObject({ state: 'not_dispatched', reason: 'shared_attempt_limit_reached' });
    expect(result.songs[1]).toMatchObject({
      base_version: second.updated_at,
      identity: { title: second.title, artist: second.artist },
      current: { release_year: 1991, original_key: 'D', bpm: 122, genres: second.genres, vibes: second.vibes },
      scalar_candidates: {
        release_year: { state: 'not_dispatched', current_value: 1991 },
        original_key: { state: 'not_dispatched', current_value: 'D' },
        bpm: { state: 'not_dispatched', current_value: 122 },
      },
    });
    expect(providers.calls).toHaveLength(1);
    expect(result.attempts.total).toBe(1);
  });

  it('preserves complete references for valid AcousticBrainz evidence before budget exhaustion', async () => {
    const { pool } = fakePool([song()]);
    const providers = mockProvider({ count: 3, maxAttempts: 5 });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 5 });
    const item = result.songs[0];
    expect(item.acousticbrainz).toMatchObject({ status: 'budget_exhausted', submission_enumeration: 'partial' });
    expect(item.scalar_candidates.bpm).toMatchObject({ state: 'partial_unapproved_candidates', evidence_status: 'budget_exhausted', conflicting_evidence: true });
    expect(item.scalar_candidates.bpm.candidates).toEqual([
      { value: 90, source: 'AcousticBrainz', response_digest: expect.stringMatching(/^[a-f0-9]{64}$/), submission_id: `${MBID}:0` },
      { value: 102, source: 'AcousticBrainz', response_digest: expect.stringMatching(/^[a-f0-9]{64}$/), submission_id: `${MBID}:1` },
    ]);
    expect(item.scalar_candidates.original_key.candidates.map(row => row.submission_id)).toEqual([`${MBID}:0`, `${MBID}:1`]);
    expect(item.evidence.scalars.bpm).toHaveLength(2);
    expect(item.approved_preview).toBeNull();
  });

  it('reports bounded sampled AcousticBrainz evidence and strength without claiming full consensus or widening B1 evidence', async () => {
    const { pool } = fakePool([song()]);
    const submissions = Array.from({ length: 226 }, () => evidenceBody({ bpm: 100, key: 'D', scale: 'minor', keyStrength: 0.83 }));
    const providers = mockProvider({ count: 226, submissions });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const item = result.songs[0];
    expect(item.acousticbrainz).toMatchObject({ status: 'ok', submission_count: 226,
      planned_sample_offsets: [0, 112, 225], attempted_offsets: [0, 112, 225], sampled_offsets: [0, 112, 225],
      submission_enumeration: 'partial', agreement_scope: 'sampled_submissions' });
    expect(item.scalar_candidates.original_key).toMatchObject({ state: 'partial_unapproved_candidates', conflicting_evidence: false });
    expect(item.scalar_candidates.original_key.candidates.map(candidate => ({ id: candidate.submission_id,
      strength: candidate.key_strength, digest: candidate.response_digest }))).toEqual([
      { id: `${MBID}:0`, strength: 0.83, digest: expect.stringMatching(/^[a-f0-9]{64}$/) },
      { id: `${MBID}:112`, strength: 0.83, digest: expect.stringMatching(/^[a-f0-9]{64}$/) },
      { id: `${MBID}:225`, strength: 0.83, digest: expect.stringMatching(/^[a-f0-9]{64}$/) },
    ]);
    expect(item.evidence.scalars.original_key).toHaveLength(3);
    expect(item.evidence.scalars.original_key.every(candidate => !Object.hasOwn(candidate, 'key_strength'))).toBe(true);
    expect(providers.calls.filter(call => new URL(call.url).pathname.endsWith('/low-level'))
      .map(call => new URL(call.url).searchParams.get('n'))).toEqual(['0', '112', '225']);
    const summary = preview.renderSummary(result);
    expect(summary).toContain('submissions 226; planned offsets 0, 112, 225; attempted offsets 0, 112, 225; sampled offsets 0, 112, 225; enumeration partial; agreement scope: sampled submissions only');
    expect(summary).toContain(`${MBID}:112=0.83`);
    expect(JSON.stringify(result)).not.toContain('provider-secret-sentinel');
    expect(JSON.stringify(result)).not.toContain('forbidden-chart-sentinel');
  });

  it('does not turn a candidate-free partial sample into a complete no-candidate claim', async () => {
    const { pool } = fakePool([song()]);
    const emptySubmission = { metadata: { tags: { musicbrainz_recordingid: [MBID] } } };
    const providers = mockProvider({ count: 226, submissions: Array.from({ length: 226 }, () => emptySubmission) });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const item = result.songs[0];
    expect(item.acousticbrainz).toMatchObject({ submission_enumeration: 'partial', submission_count: 226,
      attempted_offsets: [0, 112, 225], sampled_offsets: [0, 112, 225], agreement_scope: 'sampled_submissions' });
    expect(item.scalar_candidates.bpm).toMatchObject({ state: 'partial_no_candidate', evidence_status: 'ok', candidates: [] });
    expect(item.scalar_candidates.original_key).toMatchObject({ state: 'partial_no_candidate', evidence_status: 'ok', candidates: [] });
  });

  it('does not imply an agreement comparison when no AcousticBrainz sample succeeded', async () => {
    const { pool } = fakePool([song()]);
    const providers = mockProvider({ count: 226, maxAttempts: 3 });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 3 });
    expect(result.songs[0].acousticbrainz).toMatchObject({ status: 'budget_exhausted', submission_count: 226,
      sampled_offsets: [], submission_enumeration: 'partial' });
    const summary = preview.renderSummary(result);
    expect(summary).toContain('no successful samples; no agreement claim');
    expect(summary).not.toContain('agreement scope: sampled submissions only');
  });

  it('preserves earlier valid AcousticBrainz submissions when a later submission fails transiently', async () => {
    const { pool } = fakePool([song()]);
    const providers = mockProvider({ count: 3, failSubmissionOffset: 1 });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const item = result.songs[0];
    expect(item.acousticbrainz).toMatchObject({ status: 'transient_error', submission_enumeration: 'partial' });
    expect(item.scalar_candidates.bpm).toMatchObject({ state: 'partial_unapproved_candidates', evidence_status: 'transient_error', candidates: [
      { value: 90, source: 'AcousticBrainz', response_digest: expect.stringMatching(/^[a-f0-9]{64}$/), submission_id: `${MBID}:0` },
    ] });
    expect(item.evidence.scalars.original_key).toHaveLength(1);
    expect(item.evidence.scalars.original_key[0].submission_id).toBe(`${MBID}:0`);
    expect(item.approved_preview).toBeNull();
  });

  it('preserves populated scalar values and assignments for the synthetic Lights by Journey manual-data case', async () => {
    const manualSong = song({ release_year: 1977, original_key: 'G', bpm: 118,
      genres: [{ id: 2, name: 'Rock', is_primary: true }], vibes: [{ id: 4, name: 'Calm' }] });
    const { pool } = fakePool([manualSong]);
    const providers = mockProvider({ genres: [{ name: 'Rock' }, { name: 'Country' }] });
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const item = result.songs[0];
    expect(item.current).toEqual({ release_year: 1977, original_key: 'G', bpm: 118,
      genres: manualSong.genres, vibes: manualSong.vibes });
    for (const field of ['release_year', 'original_key', 'bpm']) {
      expect(item.scalar_candidates[field]).toMatchObject({ state: 'populated_preserved', candidates: [] });
    }
    expect(item.tag_candidates.genres.unapproved_additions.map(row => row.name)).toEqual(['Country']);
    expect(item.tag_candidates.vibes.unapproved_additions).toEqual([]);
    expect(providers.calls.filter(call => new URL(call.url).hostname === 'acousticbrainz.org')).toHaveLength(0);
  });

  it('keeps report fields allowlisted and excludes raw response secrets and chart fields', async () => {
    const { pool } = fakePool([song()]);
    const providers = mockProvider();
    const result = await preview.runPreview({ pool, providerClient: providers.client, songIds: [42], maxAttempts: 12 });
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain('provider-secret-sentinel');
    expect(serialized).not.toContain('forbidden-chart-sentinel');
    expect(serialized).not.toContain('chart_source');
    expect(serialized).not.toContain('chart_content');
    expect(serialized).not.toContain('apiKey');
    expect(result.songs[0].evidence.scalars.release_year[0].response_digest).toMatch(/^[a-f0-9]{64}$/);
  });

  it('reconstructs object-valued key evidence from allowlisted fields only', () => {
    const digest = 'a'.repeat(64);
    const report = preview.buildSongReport(song(), { status: 'ok', candidates: [] },
      { matched: true, mbid: MBID },
      { status: 'ok', recording: { scalars: { release_year: [], original_key: [] }, genres: [] } },
      { status: 'ok', scalars: { original_key: [{ source: 'AcousticBrainz', response_digest: digest,
        value: { key_key: 'C', key_scale: 'major', apiKey: 'nested-secret-sentinel' } }] } },
      { genres: [], vibes: [] });
    expect(report.scalar_candidates.original_key.candidates[0].value).toEqual({ key_key: 'C', key_scale: 'major' });
    expect(JSON.stringify(report)).not.toContain('nested-secret-sentinel');
  });

  it('requires ignored exclusive artifact directories and writes reports without overwriting', async () => {
    const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'ccm-b4-artifacts-'));
    temporaryDirectories.push(parent);
    const directory = path.join(parent, 'new-run');
    await expect(preview.createOutputDirectory(directory, { isIgnored: async () => false }))
      .rejects.toMatchObject({ code: 'OUTPUT_NOT_IGNORED' });
    await expect(fs.stat(directory)).rejects.toMatchObject({ code: 'ENOENT' });
    await preview.createOutputDirectory(directory, { isIgnored: async () => true });
    await expect(preview.createOutputDirectory(directory, { isIgnored: async () => true }))
      .rejects.toMatchObject({ code: 'OUTPUT_DIRECTORY_EXISTS' });
    const report = { report_version: 1, database_writes: false, selected_song_ids: [42],
      baseline: { captured_at: '2026-10-15T14:22:33.123Z', warning: 'synthetic' },
      attempts: { total: 0 }, limits: { total_outbound_attempts: 12 },
      songs: [{ song_id: 42, identity: { title: 'Lights', artist: 'Journey' }, base_version: '2026-10-15T14:22:33.123456Z',
        state: 'not_dispatched', reason: 'synthetic' }] };
    const files = await preview.writeReportFiles(directory, report);
    expect((await fs.readFile(files.jsonPath, 'utf8'))).toContain('"database_writes": false');
    await expect(preview.writeReportFiles(directory, report)).rejects.toMatchObject({ code: 'ARTIFACT_WRITE_FAILED' });
    expect(JSON.parse(await fs.readFile(files.jsonPath, 'utf8'))).toEqual(report);
  });

  it('runs main successfully with injected dependencies and always closes its pool', async () => {
    const directory = await ownedOutputDirectory();
    const events = [];
    const { pool } = fakePool([song()], events);
    const providers = mockProvider({ events });
    const result = await preview.main(cliArgs(directory), {
      databaseUrl: 'postgres://synthetic.invalid/db', pool, providerClient: providers.client,
      filesystem: fs, outputOptions: { isIgnored: async () => true }, clock: () => 1_800_000_000_000,
    });
    expect(events.indexOf('db:release')).toBeLessThan(events.findIndex(value => value.startsWith('provider:/')));
    expect(events.at(-1)).toBe('db:end');
    expect(await fs.readFile(result.artifacts.jsonPath, 'utf8')).toContain('"database_writes": false');
  });

  it.each(['BEGIN', 'SONGS', 'COMMIT'])('rolls back/releases and shuts down the pool after %s failure', async failAt => {
    const directory = await ownedOutputDirectory();
    const events = [];
    const { pool } = fakePool([song()], events, failAt);
    const providers = mockProvider({ events });
    await expect(preview.main(cliArgs(directory), {
      databaseUrl: 'postgres://synthetic.invalid/db', pool, providerClient: providers.client,
      filesystem: fs, outputOptions: { isIgnored: async () => true },
    })).rejects.toMatchObject({ code: 'BASELINE_READ_FAILED' });
    expect(events).toContain('db:ROLLBACK');
    expect(events).toContain('db:release');
    expect(events).toContain('db:end');
    expect(providers.calls).toHaveLength(0);
    await expect(fs.stat(directory)).rejects.toMatchObject({ code: 'ENOENT' });
  });

  it('closes the pool and removes only its empty output directory after provider failure', async () => {
    const directory = await ownedOutputDirectory();
    const events = [];
    const { pool } = fakePool([song()], events);
    const providerClient = { searchRecordings: async () => { throw new Error('synthetic provider failure'); },
      lookupRecording: async () => {}, getAcousticBrainzEvidence: async () => {},
      getAttemptCounts: () => ({ total: 0 }), getRunLedger: () => [] };
    await expect(preview.main(cliArgs(directory), {
      databaseUrl: 'postgres://synthetic.invalid/db', pool, providerClient, filesystem: fs,
      outputOptions: { isIgnored: async () => true },
    })).rejects.toMatchObject({ code: 'PREVIEW_FAILED' });
    expect(events).toContain('db:release');
    expect(events).toContain('db:end');
    await expect(fs.stat(directory)).rejects.toMatchObject({ code: 'ENOENT' });
  });

  it('removes only invocation-created partial reports and reports cleanup accurately', async () => {
    const directory = await ownedOutputDirectory();
    const sentinelPath = path.join(directory, 'unowned-sentinel.txt');
    const faultFilesystem = {
      ...fs,
      async mkdir(target, options) {
        await fs.mkdir(target, options);
        await fs.writeFile(sentinelPath, 'preserve this pre-existing sibling');
      },
      async open(target, ...args) {
        if (path.basename(target) === 'summary.txt') throw new Error('synthetic second artifact failure');
        return fs.open(target, ...args);
      },
    };
    const { pool } = fakePool([song()]);
    const providers = mockProvider();
    await expect(preview.main(cliArgs(directory), {
      databaseUrl: 'postgres://synthetic.invalid/db', pool, providerClient: providers.client,
      filesystem: faultFilesystem, outputOptions: { isIgnored: async () => true },
    })).rejects.toMatchObject({ code: 'ARTIFACT_WRITE_FAILED', safeMessage: expect.stringContaining('owned partial files were removed') });
    await expect(fs.stat(path.join(directory, 'report-v1.json'))).rejects.toMatchObject({ code: 'ENOENT' });
    expect(await fs.readFile(sentinelPath, 'utf8')).toBe('preserve this pre-existing sibling');
    expect(await fs.readdir(directory)).toEqual(['unowned-sentinel.txt']);
  });

  it('reports failed cleanup of an invocation-owned partial artifact', async () => {
    const directory = await ownedOutputDirectory();
    const faultFilesystem = {
      ...fs,
      async open(target, ...args) {
        if (path.basename(target) === 'summary.txt') throw new Error('synthetic second artifact failure');
        return fs.open(target, ...args);
      },
      async unlink() { throw new Error('synthetic cleanup failure'); },
    };
    const { pool } = fakePool([song()]);
    await expect(preview.main(cliArgs(directory), {
      databaseUrl: 'postgres://synthetic.invalid/db', pool, providerClient: mockProvider().client,
      filesystem: faultFilesystem, outputOptions: { isIgnored: async () => true },
    })).rejects.toMatchObject({ code: 'ARTIFACT_CLEANUP_FAILED', safeMessage: expect.stringContaining('owned partial file could not be removed') });
    expect(await fs.readFile(path.join(directory, 'report-v1.json'), 'utf8')).toContain('"database_writes": false');
  });

  it('uses the single exact 1 MiB bound in provider config and report', async () => {
    const args = preview.parseArguments(cliArgs());
    expect(preview.providerConfig(args).maxResponseBytes).toBe(MAX_RESPONSE_BYTES);
    const { pool } = fakePool([song()]);
    const result = await preview.runPreview({ pool, providerClient: mockProvider().client, songIds: [42], maxAttempts: 12 });
    expect(result.limits.maximum_response_bytes).toBe(MAX_RESPONSE_BYTES);
  });

  it('uses a valid repository ignore rule for a future private output path', () => {
    const ignored = path.resolve(__dirname, '../../../backups/b4-output-never-created');
    expect(preview.checkIgnoredDirectory(ignored)).toBe(true);
    expect(preview.checkIgnoredDirectory(path.resolve(__dirname, '../../../app/test/unit/not-ignored'))).toBe(false);
  });

  it('supplies every B2 bound explicitly while disabling Gemini at the orchestration layer', () => {
    const args = preview.parseArguments(cliArgs());
    const config = preview.providerConfig(args);
    expect(config).toMatchObject({ maxAttempts: 12, maxGeminiAttempts: 1, timeoutMs: 10_000,
      maxResponseBytes: MAX_RESPONSE_BYTES, geminiMaxInputBytes: 32_000, geminiMaxOutputTokens: 1_000,
      maxRetryAfterMs: 5_000, retryDelayMs: 1_000, musicBrainzMinimumIntervalMs: 1_000,
      model: 'gemini-3.1-flash-lite' });
  });
});
