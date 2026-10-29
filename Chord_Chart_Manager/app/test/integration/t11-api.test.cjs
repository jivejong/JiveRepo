const test = require('node:test');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { requireIsolatedTarget, verifyHarnessIdentity } = require('../t11-guards.cjs');
requireIsolatedTarget({ isolated: process.env.CCM_T11_ISOLATED,
  baseUrl: process.env.E2E_BASE_URL, runId: process.env.CCM_T11_RUN_ID });
const { pool } = require('../../server/db.js');
const dbExt = require('../../server/db_ext.js');

const base = process.env.E2E_BASE_URL;
assert.ok(base, 'T11 harness must provide the isolated E2E_BASE_URL');
const createdSongs = [];
const createdSetlists = [];
const createdGenres = [];
const createdGenreNames = new Map();
const createdVibes = [];
const createdVibeNames = new Map();
let initialArtistIds = new Set();
let initialReceiptIds = new Set();
test.before(async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  initialArtistIds = new Set((await pool.query('SELECT id FROM public.artists')).rows.map(row => row.id));
  initialReceiptIds = new Set((await pool.query('SELECT operation_id FROM public.mutation_receipts')).rows.map(row => row.operation_id));
});
test.after(async () => {
  try {
    const receiptRows = (await pool.query('SELECT operation_id FROM public.mutation_receipts ORDER BY operation_id')).rows;
    const newReceiptIds = receiptRows.map(row => row.operation_id).filter(id => !initialReceiptIds.has(id));
    if (newReceiptIds.length) {
      const removed = await pool.query('DELETE FROM public.mutation_receipts WHERE operation_id=ANY($1::uuid[]) RETURNING operation_id', [newReceiptIds]);
      assert.deepEqual(new Set(removed.rows.map(row => row.operation_id)), new Set(newReceiptIds),
        'all API-test-created mutation receipts are removed by exact operation ID');
    }
    const artists = (await pool.query('SELECT id,name FROM public.artists ORDER BY id')).rows
      .filter(row => !initialArtistIds.has(row.id) && /^T1[19] /.test(row.name));
    for (const artist of artists) {
      assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE artist_id=$1', [artist.id])).rowCount, 0,
        `test-created artist ${artist.id} must have no song references before cleanup`);
      const removed = await pool.query(`DELETE FROM public.artists WHERE id=$1 AND name=$2 AND NOT EXISTS
        (SELECT 1 FROM public.songs WHERE artist_id=$1) RETURNING id`, [artist.id, artist.name]);
      assert.equal(removed.rowCount, 1, `test-created artist ${artist.id} is removed by exact ID/name`);
    }
    const remainingReceipts = (await pool.query('SELECT operation_id FROM public.mutation_receipts')).rows
      .map(row => row.operation_id).filter(id => !initialReceiptIds.has(id));
    assert.deepEqual(remainingReceipts, [], 'API test leaves no task-created mutation receipts');
    const remainingArtists = (await pool.query('SELECT id FROM public.artists')).rows
      .filter(row => !initialArtistIds.has(row.id));
    assert.deepEqual(remainingArtists, [], 'API test leaves no task-created artists');
  } finally { await pool.end(); }
});
const json = async (method, path, body) => {
  const response = await fetch(new URL(path, base), {
    method,
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
  let value = null;
  try { value = await response.json(); } catch { /* keep status for assertion */ }
  return { response, value };
};

async function waitForObservedLockWait(queryFragment, label, timeoutMs = 8_000) {
  const deadline = Date.now() + timeoutMs;
  let observed;
  while (Date.now() < deadline) {
    const result = await pool.query(`SELECT pid,query,pg_blocking_pids(pid) AS blockers
      FROM pg_stat_activity
      WHERE datname=current_database() AND state='active' AND wait_event_type='Lock'
        AND query ILIKE $1 AND pid<>pg_backend_pid()`, [`%${queryFragment}%`]);
    observed = result.rows.find(row => row.blockers.length > 0);
    if (observed) return observed;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(`${label}: no blocked lock wait observed within ${timeoutMs}ms. Last candidate: ${JSON.stringify(observed)}`);
}

test('FR-021 FR-107 a legacy NULL-source chart is returned as null and stays NULL through no-op and metadata saves', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  const fixture = await pool.query(`SELECT id, chart_content FROM public.songs
    WHERE title='Already Gone' AND chart_source IS NULL ORDER BY id`);
  assert.equal(fixture.rowCount, 1, 'T13 requires the task-owned NULL-source synthetic chart');
  const id = fixture.rows[0].id;
  const before = await json('GET', `/api/songs/${id}`);
  assert.equal(before.response.status, 200);
  assert.deepEqual(before.value.chart_content, fixture.rows[0].chart_content);
  assert.equal(before.value.chart_source, null, 'no generated text is produced for a NULL source');

  const unchanged = await json('PUT', `/api/songs/${id}`, {
    chart_source: before.value.chart_source, base_updated_at: before.value.updated_at,
    operation_id: randomUUID(),
  });
  assert.equal(unchanged.response.status, 200, JSON.stringify(unchanged.value));
  assert.deepEqual(unchanged.value.chart_content, before.value.chart_content);
  assert.equal((await pool.query('SELECT chart_source FROM public.songs WHERE id=$1', [id])
    ).rows[0].chart_source, null);

  const metadata = await json('PUT', `/api/songs/${id}`, {
    chart_source: before.value.chart_source, bpm: 109,
    base_updated_at: unchanged.value.updated_at, operation_id: randomUUID(),
  });
  assert.equal(metadata.response.status, 200, JSON.stringify(metadata.value));
  assert.deepEqual(metadata.value.chart_content, before.value.chart_content);
  assert.equal(metadata.value.chart_source, before.value.chart_source);
  assert.equal((await pool.query('SELECT chart_source FROM public.songs WHERE id=$1', [id])
    ).rows[0].chart_source, null);
});

test('FR-021 FR-023 an ordinary chart is unchanged by opening it and by a metadata-only save', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  const suffix = randomUUID();
  const chartSource = ['[Verse]', 'C   G', 'Invented ordinary line  ', '', '{Am}Invented second line', ''].join('\n');
  let songId = null;
  try {
    const created = await json('POST', '/api/songs', { title: `T11 ordinary chart ${suffix}`,
      artist: `T11 ordinary chart artist ${suffix}`, chart_source: chartSource, operation_id: randomUUID() });
    assert.equal(created.response.status, 201, JSON.stringify(created.value));
    songId = created.value.id;
    const stored = async () => (await pool.query(
      'SELECT chart_source, chart_content, updated_at FROM public.songs WHERE id=$1', [songId])).rows[0];
    const before = await stored();
    assert.equal(before.chart_source, chartSource);

    const opened = await json('GET', `/api/songs/${songId}`);
    assert.equal(opened.value.chart_source, chartSource);
    assert.deepEqual(await stored(), before, 'opening a chart writes nothing');

    const metadata = await json('PUT', `/api/songs/${songId}`, { chart_source: chartSource, bpm: 88,
      base_updated_at: opened.value.updated_at, operation_id: randomUUID() });
    assert.equal(metadata.response.status, 200, JSON.stringify(metadata.value));
    const after = await stored();
    assert.equal(after.chart_source, before.chart_source);
    assert.deepEqual(after.chart_content, before.chart_content);
    assert.equal(metadata.value.bpm, 88);
  } finally {
    if (songId !== null) await pool.query('DELETE FROM public.songs WHERE id=$1', [songId]);
  }
});

test('FR-013 release year, FR-064 song-create replay, FR-106 repeated setlist song', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  const suffix = randomUUID();
  const artist = `T11 requirement artist ${suffix}`;
  let songId = null;
  let setlistId = null;
  try {
    // FR-013: a release year that is not a whole number from 1 to 32767 is rejected.
    for (const releaseYear of [0, 32768, 1979.5, 'abc']) {
      const title = `T11 bad year ${suffix} ${releaseYear}`;
      const rejected = await json('POST', '/api/songs', { title, artist, chart_source: 'Verse\nInvented year fixture',
        release_year: releaseYear, operation_id: randomUUID() });
      assert.equal(rejected.response.status, 400, `release_year ${releaseYear}: ${JSON.stringify(rejected.value)}`);
      assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE title=$1', [title])).rowCount, 0);
    }

    // FR-064: replaying a song create returns the original song; reusing the ID with a different body is 409.
    const createBody = { title: `T11 replay song ${suffix}`, artist, chart_source: 'Verse\nInvented replay fixture',
      release_year: 32767, operation_id: randomUUID() };
    const created = await json('POST', '/api/songs', createBody);
    assert.equal(created.response.status, 201, JSON.stringify(created.value));
    songId = created.value.id;
    const replay = await json('POST', '/api/songs', createBody);
    assert.equal(replay.response.status, 201, JSON.stringify(replay.value));
    assert.equal(replay.value.id, songId);
    assert.equal((await json('POST', '/api/songs', { ...createBody, title: `${createBody.title} altered` })).response.status, 409);
    assert.equal((await pool.query('SELECT count(*)::int AS n FROM public.songs WHERE artist_id=(SELECT id FROM public.artists WHERE name=$1)',
      [artist])).rows[0].n, 1, 'neither the replay nor the altered retry creates another song');

    for (const releaseYear of [0, 32768]) {
      const rejected = await json('PUT', `/api/songs/${songId}`, { release_year: releaseYear,
        base_updated_at: created.value.updated_at, operation_id: randomUUID() });
      assert.equal(rejected.response.status, 400, `update release_year ${releaseYear}: ${JSON.stringify(rejected.value)}`);
    }
    assert.equal((await json('GET', `/api/songs/${songId}`)).value.release_year, 32767);

    // FR-106: the same song can be added to one setlist more than once.
    const list = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(), type: 'create',
      setlist_id: null, base_updated_at: null, payload: { name: `T11 repeated song list ${suffix}` } });
    assert.equal(list.response.status, 200, JSON.stringify(list.value));
    setlistId = list.value.id;
    let token = list.value.updated_at;
    for (let i = 0; i < 2; i += 1) {
      const added = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(), type: 'entry_add',
        setlist_id: setlistId, base_updated_at: token, payload: { song_id: songId } });
      assert.equal(added.response.status, 200, JSON.stringify(added.value));
      token = added.value.updated_at;
    }
    const detail = await json('GET', `/api/setlists/${setlistId}`);
    assert.deepEqual(detail.value.songs.map(entry => entry.song_id), [songId, songId]);
    assert.equal(new Set(detail.value.songs.map(entry => entry.entry_id)).size, 2, 'each appearance is its own entry');
  } finally {
    if (setlistId !== null) await pool.query('DELETE FROM public.setlists WHERE id=$1', [setlistId]);
    if (songId !== null) await pool.query('DELETE FROM public.songs WHERE id=$1', [songId]);
  }
});

test('FR-111 FR-063 song update and delete without a valid six-digit base are rejected before any write', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  const suffix = randomUUID();
  let songId = null;
  try {
    const created = await json('POST', '/api/songs', { title: `T11 required base ${suffix}`,
      artist: `T11 required base artist ${suffix}`, chart_source: 'Verse\nInvented base fixture',
      operation_id: randomUUID() });
    assert.equal(created.response.status, 201, JSON.stringify(created.value));
    songId = created.value.id;
    const updateOperation = randomUUID();
    const update = await json('PUT', `/api/songs/${songId}`, { bpm: 99, operation_id: updateOperation });
    assert.equal(update.response.status, 400, JSON.stringify(update.value));
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [updateOperation])).rowCount, 0);
    const remove = await json('DELETE', `/api/songs/${songId}`, {});
    assert.equal(remove.response.status, 400, JSON.stringify(remove.value));
    // FR-063 (Q21-A): only six-digit tokens are valid; a legacy three-digit token is malformed.
    const threeDigit = created.value.updated_at.replace(/\.(\d{3})\d{3}Z$/, '.$1Z');
    const legacyUpdate = await json('PUT', `/api/songs/${songId}`, { bpm: 99, base_updated_at: threeDigit,
      operation_id: randomUUID() });
    assert.equal(legacyUpdate.response.status, 400, JSON.stringify(legacyUpdate.value));
    const legacyDelete = await json('DELETE', `/api/songs/${songId}`, { base_updated_at: threeDigit });
    assert.equal(legacyDelete.response.status, 400, JSON.stringify(legacyDelete.value));
    const after = await json('GET', `/api/songs/${songId}`);
    assert.equal(after.response.status, 200);
    assert.equal(after.value.bpm, created.value.bpm);
    assert.equal(after.value.updated_at, created.value.updated_at);
  } finally {
    if (songId !== null) await pool.query('DELETE FROM public.songs WHERE id=$1', [songId]);
  }
});

test('song, tag, setlist, and entry API contract on migrated ccm_test', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  assert.equal((await pool.query('SELECT current_database() AS name')).rows[0].name, 'ccm_test',
    'T11 API integration tests must target only ccm_test');
  let primarySong;
  let secondSong;
  let genreId;
  let setlistId;
  try {
    for (const [category, name] of [
      ['Genre', 'T11 API synthetic genre'], ['Genre', 'T11 API secondary'],
      ['Genre', 'T11 API shared category name'], ['Vibe', 'T11 API shared category name'],
      ['Vibe', 'T11 API calm'],
    ]) {
      const definition = await json('POST', '/api/tag-definitions', { category, name });
      assert.equal(definition.response.status, 201, JSON.stringify(definition.value));
      if (category === 'Genre') {
        createdGenres.push(definition.value.id);
        createdGenreNames.set(definition.value.id, name);
      } else {
        createdVibes.push(definition.value.id);
        createdVibeNames.set(definition.value.id, name);
      }
    }
    for (const [title, chart] of [
      ['T11 API song A', 'Invented API verse\nInvented lyric A'],
      ['T11 API song B', 'Invented API verse\nInvented lyric B'],
    ]) {
      const created = await json('POST', '/api/songs', { title, artist: 'T11 API synthetic artist',
        chart_written_key: 'C', chart_source: chart, operation_id: randomUUID() });
      assert.equal(created.response.status, 201, JSON.stringify(created.value));
      createdSongs.push(created.value.id);
      if (!primarySong) primarySong = created.value;
      else secondSong = created.value;
    }
    const loaded = await json('GET', `/api/songs/${primarySong.id}`);
    assert.equal(loaded.response.status, 200);
    assert.equal(loaded.value.title, primarySong.title);
    assert.ok(loaded.value.updated_at);

    const tagged = await json('PUT', `/api/songs/${primarySong.id}`, {
      genres: [{ name: 'T11 API synthetic genre', is_primary: true }],
      base_updated_at: loaded.value.updated_at, operation_id: randomUUID(),
    });
    assert.equal(tagged.response.status, 200, JSON.stringify(tagged.value));
    assert.ok(tagged.value.genres.some(tag => tag.name === 'T11 API synthetic genre'));
    assert.notEqual(tagged.value.updated_at, loaded.value.updated_at);
    genreId = (await pool.query('SELECT id FROM public.genres WHERE name=$1', ['T11 API synthetic genre'])).rows[0]?.id;
    assert.ok(genreId);
    createdGenres.push(genreId);
    createdGenreNames.set(genreId, 'T11 API synthetic genre');

    const update = await json('PUT', `/api/songs/${primarySong.id}`, {
      title: 'T11 API song A updated', artist: primarySong.artist,
      base_updated_at: tagged.value.updated_at, operation_id: randomUUID(),
    });
    assert.equal(update.response.status, 200, JSON.stringify(update.value));
    assert.equal(update.value.title, 'T11 API song A updated');

    const filterSong = await json('PUT', `/api/songs/${primarySong.id}`, {
      genres: [
        { name: 'T11 API synthetic genre', is_primary: true },
        { name: 'T11 API secondary', is_primary: false },
        { name: 'T11 API shared category name', is_primary: false },
      ],
      vibes: ['T11 API shared category name', 'T11 API calm'], release_year: 1979,
      base_updated_at: update.value.updated_at, operation_id: randomUUID(),
    });
    assert.equal(filterSong.response.status, 200, JSON.stringify(filterSong.value));
    const filterGenreIds = await pool.query(`SELECT id, name FROM public.genres
      WHERE name = ANY($1::text[]) ORDER BY id`, [[
        'T11 API synthetic genre', 'T11 API secondary', 'T11 API shared category name',
      ]]);
    for (const row of filterGenreIds.rows) {
      if (!createdGenres.includes(row.id)) createdGenres.push(row.id);
      createdGenreNames.set(row.id, row.name);
    }
    const filterVibeIds = await pool.query(`SELECT id,name FROM public.vibes
      WHERE name = ANY($1::text[]) ORDER BY id`, [['T11 API shared category name', 'T11 API calm']]);
    for (const row of filterVibeIds.rows) { createdVibes.push(row.id); createdVibeNames.set(row.id, row.name); }
    const filterSongVersion = filterSong.value.updated_at;

    const list = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'create', setlist_id: null, base_updated_at: null, payload: { name: 'T11 API synthetic list' } });
    assert.equal(list.response.status, 200, JSON.stringify(list.value));
    setlistId = list.value.id;
    createdSetlists.push(setlistId);
    let token = list.value.updated_at;

    const entryA = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'entry_add', setlist_id: setlistId, base_updated_at: token,
      payload: { song_id: primarySong.id, capo_fret: 0, notes: 'synthetic A' } });
    assert.equal(entryA.response.status, 200, JSON.stringify(entryA.value));
    token = entryA.value.updated_at;
    const entryB = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'entry_add', setlist_id: setlistId, base_updated_at: token,
      payload: { song_id: secondSong.id, capo_fret: null } });
    assert.equal(entryB.response.status, 200, JSON.stringify(entryB.value));
    token = entryB.value.updated_at;
    const entries = entryB.value.songs;
    assert.equal(entries.length, 2);
    assert.equal(new Set(entries.map(entry => entry.entry_id)).size, 2);

    const edited = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'entry_edit', setlist_id: setlistId, base_updated_at: token,
      payload: { entry_id: entries[0].entry_id, capo_fret: 0, transposed_key: 'D' } });
    assert.equal(edited.response.status, 200, JSON.stringify(edited.value));
    token = edited.value.updated_at;
    const reordered = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'reorder', setlist_id: setlistId, base_updated_at: token,
      payload: { entry_ids: entries.map(entry => entry.entry_id).reverse() } });
    assert.equal(reordered.response.status, 200, JSON.stringify(reordered.value));
    assert.deepEqual(reordered.value.songs.map(entry => entry.song_id), [secondSong.id, primarySong.id]);
    assert.equal(reordered.value.songs[1].capo_fret, 0);

    const detail = await json('GET', `/api/setlists/${setlistId}`);
    assert.equal(detail.response.status, 200);
    assert.deepEqual(detail.value.songs.map(entry => entry.song_id), [secondSong.id, primarySong.id]);
    const stale = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'metadata', setlist_id: setlistId, base_updated_at: list.value.updated_at,
      payload: { notes: 'must be rejected' } });
    assert.equal(stale.response.status, 409);
    const unchanged = await json('GET', `/api/setlists/${setlistId}`);
    assert.equal(unchanged.value.notes, null);

    const removedTag = await json('PUT', `/api/songs/${primarySong.id}`, {
      genres: [
        { name: 'T11 API secondary', is_primary: true },
        { name: 'T11 API shared category name', is_primary: false },
      ],
      base_updated_at: filterSongVersion, operation_id: randomUUID(),
    });
    // The intervening setlist operations do not update this song version.
    assert.equal(removedTag.response.status, 200, JSON.stringify(removedTag.value));
    assert.equal(removedTag.value.genres.some(tag => tag.name === 'T11 API synthetic genre'), false);

    // T19: shared definition operations are category-scoped and transactional.
    const mgmtName = `T19 API ${randomUUID()}`;
    const definition = async (method, route, body) => json(method, route, body);
    const createDefinition = async (category, name) => {
      const created = await definition('POST', '/api/tag-definitions', { category, name });
      assert.equal(created.response.status, 201, JSON.stringify(created.value));
      if (category === 'Genre') {
        createdGenres.push(created.value.id);
        createdGenreNames.set(created.value.id, name);
      } else { createdVibes.push(created.value.id); createdVibeNames.set(created.value.id, name); }
      return created.value;
    };
    const managedSource = await createDefinition('Genre', `${mgmtName} source`);
    const managedTarget = await createDefinition('Genre', `${mgmtName} target`);
    const managedVibe = await createDefinition('Vibe', `${mgmtName} source`);
    const replacementVibe = await createDefinition('Vibe', `${mgmtName} replacement`);
    const unused = await createDefinition('Genre', `${mgmtName} unused`);
    const managedSongUpdate = await json('PUT', `/api/songs/${primarySong.id}`, {
      genres: [{ name: managedSource.name, is_primary: true }, { name: 'T11 API secondary', is_primary: false }],
      vibes: [managedVibe.name, replacementVibe.name], base_updated_at: removedTag.value.updated_at, operation_id: randomUUID(),
    });
    assert.equal(managedSongUpdate.response.status, 200, JSON.stringify(managedSongUpdate.value));
    const secondManagedSong = await json('PUT', `/api/songs/${secondSong.id}`, {
      genres: [{ name: managedSource.name, is_primary: true }], vibes: [replacementVibe.name],
      base_updated_at: secondSong.updated_at, operation_id: randomUUID(),
    });
    assert.equal(secondManagedSong.response.status, 200, JSON.stringify(secondManagedSong.value));
    const sourceOnlySong = await json('POST', '/api/songs', { title: `${mgmtName} source only`, artist: 'T11 API synthetic artist',
      chart_written_key: 'C', chart_source: 'Verse\nInvented T19 source-only fixture', genres: [], vibes: [managedVibe.name], operation_id: randomUUID() });
    assert.equal(sourceOnlySong.response.status, 201, JSON.stringify(sourceOnlySong.value));
    createdSongs.push(sourceOnlySong.value.id);
    const sourceOnlyVersion = sourceOnlySong.value.updated_at;
    const renamedAssignedVibe = await definition('PUT', `/api/tag-definitions/Vibe/${managedVibe.id}`, {
      name: `${mgmtName} renamed vibe`,
    });
    assert.equal(renamedAssignedVibe.response.status, 200, JSON.stringify(renamedAssignedVibe.value));
    assert.equal(renamedAssignedVibe.value.affected_songs, 2);
    managedVibe.name = `${mgmtName} renamed vibe`;
    createdVibeNames.set(managedVibe.id, managedVibe.name);
    const afterAssignedRename = await json('GET', `/api/songs/${primarySong.id}`);
    assert.ok(afterAssignedRename.value.vibes.includes(managedVibe.name));
    assert.notEqual(afterAssignedRename.value.updated_at, managedSongUpdate.value.updated_at,
      'renaming a shared definition advances each affected song version');
    const beforeDefinitionChange = afterAssignedRename.value.updated_at;
    const definitions = await json('GET', '/api/tag-definitions');
    assert.equal(definitions.value.genres.find(item => item.id === managedSource.id).song_count, 2);
    assert.equal(definitions.value.vibes.find(item => item.id === managedVibe.id).song_count, 2);
    assert.equal(definitions.value.vibes.find(item => item.id === replacementVibe.id).song_count, 2);
    assert.equal(definitions.value.genres.some(item => item.name.startsWith('Era')), false,
      'derived Era is not exposed as a managed definition');
    const renamed = await definition('PUT', `/api/tag-definitions/Genre/${unused.id}`, { name: `${mgmtName} renamed` });
    assert.equal(renamed.response.status, 200, JSON.stringify(renamed.value));
    createdGenreNames.set(unused.id, `${mgmtName} renamed`);
    assert.equal((await definition('PUT', `/api/tag-definitions/Genre/${unused.id}`, { name: `${mgmtName} target` })).response.status, 409,
      'case-insensitive duplicate names in one category are rejected');
    assert.equal((await definition('DELETE', `/api/tag-definitions/Genre/${managedSource.id}`, {})).response.status, 400,
      'in-use definition cannot be deleted without an explicit assignment decision');
    const changedCount = await definition('DELETE', `/api/tag-definitions/Genre/${managedSource.id}`, {
      mode: 'remove', expected_song_count: 0,
    });
    assert.equal(changedCount.response.status, 409, 'stale usage confirmation is rejected');
    assert.equal((await json('GET', `/api/songs/${primarySong.id}`)).value.genres
      .some(tag => tag.name === managedSource.name), true, 'stale confirmation performs no assignment writes');
    const reassigned = await definition('DELETE', `/api/tag-definitions/Genre/${managedSource.id}`, {
      mode: 'reassign', replacement_id: managedTarget.id, expected_song_count: 2,
    });
    assert.equal(reassigned.response.status, 200, JSON.stringify(reassigned.value));
    assert.equal(reassigned.value.affected_songs, 2);
    const afterReassign = await json('GET', `/api/songs/${primarySong.id}`);
    assert.ok(afterReassign.value.genres.some(tag => tag.name === managedTarget.name && tag.is_primary));
    const afterSecondReassign = await json('GET', `/api/songs/${secondSong.id}`);
    assert.ok(afterSecondReassign.value.genres.some(tag => tag.name === managedTarget.name && tag.is_primary));
    assert.notEqual(afterReassign.value.updated_at, beforeDefinitionChange);
    assert.equal((await json('PUT', `/api/songs/${primarySong.id}`, {
      title: 'T19 stale write rejected', base_updated_at: beforeDefinitionChange, operation_id: randomUUID(),
    })).response.status, 409, 'affected song version advances so stale writes are rejected');
    const removedAssignments = await definition('DELETE', `/api/tag-definitions/Genre/${managedTarget.id}`, {
      mode: 'remove', expected_song_count: 2,
    });
    assert.equal(removedAssignments.response.status, 200, JSON.stringify(removedAssignments.value));
    const afterRemoval = await json('GET', `/api/songs/${primarySong.id}`);
    assert.ok(afterRemoval.value.genres.some(tag => tag.name === 'T11 API secondary' && tag.is_primary),
      'removing the prior primary promotes a remaining assignment');
    const replacementOnlyVersion = (await json('GET', `/api/songs/${secondSong.id}`)).value.updated_at;
    const removedVibe = await definition('DELETE', `/api/tag-definitions/Vibe/${managedVibe.id}`, {
      mode: 'reassign', replacement_id: replacementVibe.id, expected_song_count: 2,
    });
    assert.equal(removedVibe.response.status, 200, JSON.stringify(removedVibe.value));
    assert.equal(removedVibe.value.affected_songs, 2);
    const afterVibeMerge = await json('GET', `/api/songs/${primarySong.id}`);
    assert.deepEqual(afterVibeMerge.value.vibes.sort(), [replacementVibe.name]);
    assert.notEqual(afterVibeMerge.value.updated_at, beforeDefinitionChange,
      'the overlapping source assignment is merged and the song version advances');
    const afterSourceOnlyMerge = await json('GET', `/api/songs/${sourceOnlySong.value.id}`);
    assert.deepEqual(afterSourceOnlyMerge.value.vibes, [replacementVibe.name], 'source-only Vibe moves to replacement');
    assert.notEqual(afterSourceOnlyMerge.value.updated_at, sourceOnlyVersion);
    const afterReplacementOnly = await json('GET', `/api/songs/${secondSong.id}`);
    assert.deepEqual(afterReplacementOnly.value.vibes, [replacementVibe.name], 'replacement-only assignment remains exactly once');
    assert.equal(afterReplacementOnly.value.updated_at, replacementOnlyVersion,
      'replacement-only song is not an affected source assignment');
    const afterVibeDefinitions = await json('GET', '/api/tag-definitions');
    assert.equal(afterVibeDefinitions.value.vibes.some(item => item.id === managedVibe.id), false, 'source definition is removed');
    assert.equal(afterVibeDefinitions.value.vibes.find(item => item.id === replacementVibe.id).song_count, 3);
    const removedReplacement = await definition('DELETE', `/api/tag-definitions/Vibe/${replacementVibe.id}`, {
      mode: 'remove', expected_song_count: 3,
    });
    assert.equal(removedReplacement.response.status, 200, JSON.stringify(removedReplacement.value));
    assert.deepEqual((await json('GET', `/api/songs/${primarySong.id}`)).value.vibes, []);
    assert.deepEqual((await json('GET', `/api/songs/${sourceOnlySong.value.id}`)).value.vibes, []);
    assert.equal((await json('DELETE', `/api/tag-definitions/Genre/${unused.id}`, { mode: 'remove', expected_song_count: 0 })).response.status, 200);
    assert.equal((await definition('POST', '/api/tag-definitions', { category: 'Era', name: '1980s' })).response.status, 400);

    const deletedList = await json('POST', '/api/setlists/mutations', { operation_id: randomUUID(),
      type: 'delete', setlist_id: setlistId, base_updated_at: unchanged.value.updated_at, payload: {} });
    assert.equal(deletedList.response.status, 200, JSON.stringify(deletedList.value));
    assert.equal((await json('GET', `/api/setlists/${setlistId}`)).response.status, 404);
    createdSetlists.splice(createdSetlists.indexOf(setlistId), 1);

    for (const id of createdSongs) {
      const current = await json('GET', `/api/songs/${id}`);
      assert.equal(current.response.status, 200);
      const deleted = await json('DELETE', `/api/songs/${id}`, { base_updated_at: current.value.updated_at });
      assert.equal(deleted.response.status, 200, JSON.stringify(deleted.value));
      assert.equal((await json('GET', `/api/songs/${id}`)).response.status, 404);
    }
    createdSongs.length = 0;
  } finally {
    // SQL cleanup is limited to recorded T11 IDs/names and unreferenced definitions.
    for (const id of createdSetlists) {
      await pool.query('DELETE FROM public.setlists WHERE id=$1', [id]);
      assert.equal((await pool.query('SELECT 1 FROM public.setlists WHERE id=$1', [id])).rowCount, 0);
    }
    for (const id of createdSongs) {
      await pool.query('DELETE FROM public.songs WHERE id=$1', [id]);
      assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE id=$1', [id])).rowCount, 0);
    }
    for (const id of createdGenres) {
      await pool.query(`DELETE FROM public.genres g WHERE id=$1 AND name=$2
        AND NOT EXISTS (SELECT 1 FROM public.song_genres sg WHERE sg.genre_id=g.id)`, [id, createdGenreNames.get(id)]);
      assert.equal((await pool.query('SELECT 1 FROM public.genres WHERE id=$1', [id])).rowCount, 0);
    }
    for (const id of createdVibes) {
      await pool.query(`DELETE FROM public.vibes v WHERE id=$1 AND name=$2
        AND NOT EXISTS (SELECT 1 FROM public.song_vibes sv WHERE sv.vibe_id=v.id)`, [id, createdVibeNames.get(id)]);
      assert.equal((await pool.query('SELECT 1 FROM public.vibes WHERE id=$1', [id])).rowCount, 0,
        `owned T11 Vibe definition ${id} (${createdVibeNames.get(id)}) remains after cleanup`);
    }
  }
});

test('existing definitions reject unknown/ambiguous assignment atomically and lock assignment against deletion', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  assert.equal((await pool.query('SELECT current_database() AS name')).rows[0].name, 'ccm_test');
  const suffix = randomUUID();
  const title = `T11 validation song ${suffix}`;
  const artist = `T11 validation artist ${suffix}`;
  const unknownGenre = `T11 unknown Genre ${suffix}`;
  const ambiguousGenre = `T11 ambiguous Genre ${suffix}`;
  const ambiguousStored = ambiguousGenre.replace('T11 ambiguous', 'T11  ambiguous');
  const operation = randomUUID();
  const fixtureReceiptIds = [operation];
  const fixtureArtistNames = new Set([artist]);
  const beforeGenreIds = new Set((await pool.query('SELECT id FROM public.genres')).rows.map(row => row.id));
  const beforeVibeIds = new Set((await pool.query('SELECT id FROM public.vibes')).rows.map(row => row.id));
  let songId;
  const raceSongIds = [];
  const raceGenreIds = [];
  const definitionNamesById = new Map();
  let ambiguousIds = [];
  const blockers = [];
  const rowSnapshot = async id => {
    const result = await pool.query(`SELECT to_jsonb(s) AS song, to_jsonb(a) AS artist,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('join',to_jsonb(sg),'definition',to_jsonb(g))
        ORDER BY sg.genre_id) FROM public.song_genres sg JOIN public.genres g ON g.id=sg.genre_id WHERE sg.song_id=s.id),'[]'::jsonb) AS genres,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('join',to_jsonb(sv),'definition',to_jsonb(v))
        ORDER BY sv.vibe_id) FROM public.song_vibes sv JOIN public.vibes v ON v.id=sv.vibe_id WHERE sv.song_id=s.id),'[]'::jsonb) AS vibes
      FROM public.songs s JOIN public.artists a ON a.id=s.artist_id WHERE s.id=$1`, [id]);
    return result.rows[0] || null;
  };
  try {
    const created = await json('POST', '/api/songs', { title, artist, chart_written_key: 'C',
      chart_source: 'Verse\nInvented validation fixture', operation_id: operation });
    assert.equal(created.response.status, 201, JSON.stringify(created.value));
    songId = created.value.id;
    const before = await rowSnapshot(songId);

    const unknownCreateOperation = randomUUID();
    const unknownCreate = await json('POST', '/api/songs', { title: `${title} rejected create`,
      artist: `${artist} rejected create`, genres: [{ name: unknownGenre, is_primary: true }],
      chart_source: 'Invented rejected create', operation_id: unknownCreateOperation });
    assert.equal(unknownCreate.response.status, 422);
    assert.deepEqual(unknownCreate.value, { error: `Genre definition “${unknownGenre}” is no longer available. Choose an existing definition or remove the assignment.`,
      code: 'UNKNOWN_TAG_DEFINITION', category: 'Genre' });
    assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE title=$1', [`${title} rejected create`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.artists WHERE name=$1', [`${artist} rejected create`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [unknownCreateOperation])).rowCount, 0);
    assert.equal(JSON.stringify(await rowSnapshot(songId)), JSON.stringify(before));

    const completeState = async () => {
      const [songs, artists, genres, vibes, genreLinks, vibeLinks, receipts] = await Promise.all([
        pool.query('SELECT to_jsonb(s) AS row FROM public.songs s ORDER BY id'),
        pool.query('SELECT to_jsonb(a) AS row FROM public.artists a ORDER BY id'),
        pool.query('SELECT to_jsonb(g) AS row FROM public.genres g ORDER BY id'),
        pool.query('SELECT to_jsonb(v) AS row FROM public.vibes v ORDER BY id'),
        pool.query('SELECT to_jsonb(sg) AS row FROM public.song_genres sg ORDER BY song_id,genre_id'),
        pool.query('SELECT to_jsonb(sv) AS row FROM public.song_vibes sv ORDER BY song_id,vibe_id'),
        pool.query('SELECT to_jsonb(r) AS row FROM public.mutation_receipts r ORDER BY operation_id'),
      ]);
      return JSON.stringify({ songs: songs.rows, artists: artists.rows, genres: genres.rows,
        vibes: vibes.rows, genreLinks: genreLinks.rows, vibeLinks: vibeLinks.rows, receipts: receipts.rows });
    };
    const beforeMalformedInputs = await completeState();
    for (const name of ['   ', 42, { malformed: true }]) {
      const malformed = await json('PUT', `/api/songs/${songId}`, {
        genres: [{ name, is_primary: true }], base_updated_at: created.value.updated_at, operation_id: randomUUID(),
      });
      assert.equal(malformed.response.status, 400, JSON.stringify({ name, value: malformed.value }));
      assert.equal(malformed.value.code, 'INVALID_TAG_INPUT');
      assert.equal(malformed.value.category, 'Genre');
      assert.equal(await completeState(), beforeMalformedInputs,
        `malformed Genre name ${JSON.stringify(name)} rolls back every relevant row`);
    }

    const invalidUpdateOperation = randomUUID();
    const invalidUpdate = await json('PUT', `/api/songs/${songId}`, {
      title: `${title} must roll back`, artist: `${artist} invalid update artist`,
      genres: [{ name: unknownGenre, is_primary: true }], chart_source: 'Invented changed chart',
      base_updated_at: created.value.updated_at, operation_id: invalidUpdateOperation,
    });
    assert.equal(invalidUpdate.response.status, 422);
    assert.equal(invalidUpdate.value.code, 'UNKNOWN_TAG_DEFINITION');
    assert.equal(invalidUpdate.value.category, 'Genre');
    assert.equal(JSON.stringify(await rowSnapshot(songId)), JSON.stringify(before));
    assert.equal((await pool.query('SELECT 1 FROM public.artists WHERE name=$1', [`${artist} invalid update artist`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [invalidUpdateOperation])).rowCount, 0);

    const stale = await json('PUT', `/api/songs/${songId}`, {
      title: `${title} stale`, genres: [{ name: unknownGenre, is_primary: true }],
      base_updated_at: '2026-01-01T00:00:00.000001Z', operation_id: randomUUID(),
    });
    assert.equal(stale.response.status, 409);
    assert.equal(stale.value.code, 'SONG_VERSION_CONFLICT');
    assert.equal(JSON.stringify(await rowSnapshot(songId)), JSON.stringify(before));

    const ambiguousA = await pool.query('INSERT INTO public.genres(name) VALUES($1) RETURNING id', [ambiguousGenre]);
    const ambiguousB = await pool.query('INSERT INTO public.genres(name) VALUES($1) RETURNING id', [ambiguousStored]);
    ambiguousIds = [ambiguousA.rows[0].id, ambiguousB.rows[0].id];
    const ambiguous = await json('PUT', `/api/songs/${songId}`, {
      genres: [{ name: ambiguousGenre, is_primary: true }], base_updated_at: created.value.updated_at,
      operation_id: randomUUID(),
    });
    assert.equal(ambiguous.response.status, 422);
    assert.equal(ambiguous.value.code, 'AMBIGUOUS_TAG_DEFINITION');
    assert.equal(ambiguous.value.category, 'Genre');
    assert.equal(JSON.stringify(await rowSnapshot(songId)), JSON.stringify(before));

    const makeDefinition = async name => {
      const result = await json('POST', '/api/tag-definitions', { category: 'Genre', name });
      assert.equal(result.response.status, 201, JSON.stringify(result.value));
      raceGenreIds.push(result.value.id);
      definitionNamesById.set(result.value.id, name);
      return result.value;
    };
    const makeRaceSong = async (songTitle, assigned, receiptId, songArtist = artist) => {
      fixtureReceiptIds.push(receiptId);
      fixtureArtistNames.add(songArtist);
      const saved = await dbExt.createSong({ title: songTitle, artist: songArtist,
        chart_source: 'Invented deterministic lock fixture', genres: assigned }, receiptId);
      raceSongIds.push(saved.id);
      return saved;
    };

    // Create locks name keys alphabetically (AAA then ZZZ). The old management
    // path locked source ZZZ before replacement AAA, producing the reverse
    // order. Hold the affected song so both application calls can be observed
    // waiting on their real PostgreSQL lock before the barrier is released.
    const targetA = await makeDefinition(`AAA lock target ${suffix}`);
    const sourceB = await makeDefinition(`ZZZ lock source ${suffix}`);
    const firstSong = await makeRaceSong(`${title} reassign race`,
      [{ name: sourceB.name, is_primary: true }], randomUUID());
    const firstBlocker = await pool.connect();
    blockers.push(firstBlocker);
    await firstBlocker.query('BEGIN');
    await firstBlocker.query('SELECT id FROM public.songs WHERE id=$1 FOR UPDATE', [firstSong.id]);
    const firstManagement = dbExt.mutateTagDefinition('Genre', sourceB.id, {
      mode: 'reassign', replacement_id: targetA.id, expected_song_count: 1,
    });
    const managementWait = await waitForObservedLockWait('SELECT id FROM public.songs WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE',
      'B→A management song lock');
    const rejectedCreateOperation = randomUUID();
    fixtureReceiptIds.push(rejectedCreateOperation);
    fixtureArtistNames.add(`${artist} rejected overlapping create`);
    const overlappingCreate = dbExt.createSong({ title: `${title} overlapping create`,
      artist: `${artist} rejected overlapping create`, chart_source: 'Invented overlap chart',
      genres: [{ name: targetA.name, is_primary: true }, { name: sourceB.name, is_primary: false }] }, rejectedCreateOperation);
    const createWait = await waitForObservedLockWait('pg_advisory_xact_lock(hashtext($1), hashtext($2))',
      'overlapping A+B create name lock');
    assert.notEqual(managementWait.pid, createWait.pid, 'the blocked application operations use separate PostgreSQL sessions');
    await firstBlocker.query('COMMIT');
    firstBlocker.release();
    blockers.splice(blockers.indexOf(firstBlocker), 1);
    const [managementResult, createResult] = await Promise.allSettled([firstManagement, overlappingCreate]);
    assert.equal(managementResult.status, 'fulfilled', JSON.stringify(managementResult.reason));
    assert.deepEqual(managementResult.value, { id: sourceB.id, name: sourceB.name, category: 'Genre',
      mode: 'reassign', replacement_id: targetA.id, affected_songs: 1 });
    assert.equal(createResult.status, 'rejected');
    assert.equal(createResult.reason.status, 422);
    assert.equal(createResult.reason.code, 'UNKNOWN_TAG_DEFINITION');
    assert.equal(createResult.reason.category, 'Genre');
    assert.equal((await pool.query('SELECT 1 FROM public.genres WHERE id=$1', [sourceB.id])).rowCount, 0);
    assert.deepEqual((await pool.query('SELECT genre_id,is_primary FROM public.song_genres WHERE song_id=$1 ORDER BY genre_id',
      [firstSong.id])).rows, [{ genre_id: targetA.id, is_primary: true }]);
    assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE title=$1', [`${title} overlapping create`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.artists WHERE name=$1', [`${artist} rejected overlapping create`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [rejectedCreateOperation])).rowCount, 0);
    const versionAfterReassign = (await pool.query('SELECT updated_at FROM public.songs WHERE id=$1', [firstSong.id])).rows[0].updated_at;
    assert.notEqual(versionAfterReassign.toISOString(), firstSong.updated_at);

    // Deletion-first ordering uses the same barriers: management owns the
    // definition key and row, the create is observed blocked there, and after
    // deletion commits the waiting assignment rejects with a full rollback.
    const deleteSource = await makeDefinition(`T11 deletion source ${suffix}`);
    const remaining = await makeDefinition(`T11 deletion remaining ${suffix}`);
    const secondSong = await makeRaceSong(`${title} deletion race`, [
      { name: deleteSource.name, is_primary: true }, { name: remaining.name, is_primary: false },
    ], randomUUID());
    const secondBlocker = await pool.connect();
    blockers.push(secondBlocker);
    await secondBlocker.query('BEGIN');
    await secondBlocker.query('SELECT id FROM public.songs WHERE id=$1 FOR UPDATE', [secondSong.id]);
    const deletion = dbExt.mutateTagDefinition('Genre', deleteSource.id, {
      mode: 'remove', expected_song_count: 1,
    });
    const deletionWait = await waitForObservedLockWait('SELECT id FROM public.songs WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE',
      'definition deletion song lock');
    const rejectedDeleteCreateOperation = randomUUID();
    fixtureReceiptIds.push(rejectedDeleteCreateOperation);
    fixtureArtistNames.add(`${artist} rejected deletion create`);
    const deletionOrderedCreate = dbExt.createSong({ title: `${title} deletion-first create`,
      artist: `${artist} rejected deletion create`, chart_source: 'Invented deletion chart',
      genres: [{ name: deleteSource.name, is_primary: true }] }, rejectedDeleteCreateOperation);
    const deletionCreateWait = await waitForObservedLockWait('pg_advisory_xact_lock(hashtext($1), hashtext($2))',
      'assignment blocked behind definition deletion');
    assert.notEqual(deletionWait.pid, deletionCreateWait.pid);
    await secondBlocker.query('COMMIT');
    secondBlocker.release();
    blockers.splice(blockers.indexOf(secondBlocker), 1);
    const [deletionResult, deletionCreateResult] = await Promise.allSettled([deletion, deletionOrderedCreate]);
    assert.equal(deletionResult.status, 'fulfilled', JSON.stringify(deletionResult.reason));
    assert.equal(deletionResult.value.affected_songs, 1);
    assert.equal(deletionCreateResult.status, 'rejected');
    assert.equal(deletionCreateResult.reason.status, 422);
    assert.equal(deletionCreateResult.reason.code, 'UNKNOWN_TAG_DEFINITION');
    assert.equal((await pool.query('SELECT 1 FROM public.genres WHERE id=$1', [deleteSource.id])).rowCount, 0);
    assert.deepEqual((await pool.query('SELECT genre_id,is_primary FROM public.song_genres WHERE song_id=$1 ORDER BY genre_id',
      [secondSong.id])).rows, [{ genre_id: remaining.id, is_primary: true }]);
    assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE title=$1', [`${title} deletion-first create`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.artists WHERE name=$1', [`${artist} rejected deletion create`])).rowCount, 0);
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [rejectedDeleteCreateOperation])).rowCount, 0);
  } finally {
    for (const blocker of blockers) {
      await blocker.query('ROLLBACK').catch(() => {});
      blocker.release();
    }
    for (const id of [songId, ...raceSongIds].filter(Number.isInteger)) {
      await pool.query('DELETE FROM public.songs WHERE id=$1', [id]);
      assert.equal((await pool.query('SELECT 1 FROM public.songs WHERE id=$1', [id])).rowCount, 0);
    }
    for (const [id, name] of definitionNamesById) {
      const removed = await pool.query(`DELETE FROM public.genres WHERE id=$1 AND name=$2
        AND NOT EXISTS (SELECT 1 FROM public.song_genres WHERE genre_id=$1) RETURNING id`, [id, name]);
      assert.ok(removed.rowCount <= 1, `owned lock-test definition ${id} is removed at most once`);
      assert.equal((await pool.query('SELECT 1 FROM public.genres WHERE id=$1', [id])).rowCount, 0,
        `owned lock-test definition ${id} is absent after exact cleanup`);
    }
    for (const id of ambiguousIds) {
      await pool.query('DELETE FROM public.genres WHERE id=$1', [id]);
      assert.equal((await pool.query('SELECT 1 FROM public.genres WHERE id=$1', [id])).rowCount, 0);
    }
    for (const operationId of fixtureReceiptIds) {
      const removed = await pool.query('DELETE FROM public.mutation_receipts WHERE operation_id=$1 RETURNING operation_id', [operationId]);
      assert.ok(removed.rowCount <= 1, `task-created receipt ${operationId} is removed at most once`);
      assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [operationId])).rowCount, 0,
        `task-created receipt ${operationId} is absent after exact cleanup`);
    }
    for (const artistName of fixtureArtistNames) {
      const removed = await pool.query(`DELETE FROM public.artists a WHERE a.name=$1
        AND NOT EXISTS (SELECT 1 FROM public.songs s WHERE s.artist_id=a.id) RETURNING id`, [artistName]);
      assert.ok(removed.rowCount <= 1, `task-created artist ${artistName} is removed at most once`);
      assert.equal((await pool.query('SELECT 1 FROM public.artists WHERE name=$1', [artistName])).rowCount, 0,
        `task-created artist ${artistName} is absent after cleanup or rollback`);
    }
  }
});
