const test = require('node:test');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { requireIsolatedTarget, verifyHarnessIdentity } = require('../t11-guards.cjs');
requireIsolatedTarget({ isolated: process.env.CCM_T11_ISOLATED,
  baseUrl: process.env.E2E_BASE_URL, runId: process.env.CCM_T11_RUN_ID });
const { pool } = require('../../server/db.js');

const base = process.env.E2E_BASE_URL;
assert.ok(base, 'T11 harness must provide the isolated E2E_BASE_URL');
const createdSongs = [];
const createdSetlists = [];
const createdGenres = [];
test.after(async () => pool.end());
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

test('song, tag, setlist, and entry API contract on migrated ccm_test', async () => {
  await verifyHarnessIdentity({ baseUrl: base, runId: process.env.CCM_T11_RUN_ID });
  assert.equal((await pool.query('SELECT current_database() AS name')).rows[0].name, 'ccm_test',
    'T11 API integration tests must target only ccm_test');
  let primarySong;
  let secondSong;
  let genreId;
  let setlistId;
  try {
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

    let tagged = await json('POST', `/api/songs/${primarySong.id}/tags`, {
      name: 'T11 API synthetic genre', category: 'Genre', base_updated_at: loaded.value.updated_at,
    });
    assert.equal(tagged.response.status, 200, JSON.stringify(tagged.value));
    assert.ok(tagged.value.genres.some(tag => tag.name === 'T11 API synthetic genre'));
    assert.notEqual(tagged.value.updated_at, loaded.value.updated_at);
    genreId = (await pool.query('SELECT id FROM public.genres WHERE name=$1', ['T11 API synthetic genre'])).rows[0]?.id;
    assert.ok(genreId);
    createdGenres.push(genreId);

    const update = await json('PUT', `/api/songs/${primarySong.id}`, {
      title: 'T11 API song A updated', artist: primarySong.artist,
      base_updated_at: tagged.value.updated_at, operation_id: randomUUID(),
    });
    assert.equal(update.response.status, 200, JSON.stringify(update.value));
    assert.equal(update.value.title, 'T11 API song A updated');

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

    const removedTag = await json('DELETE', `/api/songs/${primarySong.id}/tags/T11%20API%20synthetic%20genre`, {
      category: 'Genre', base_updated_at: update.value.updated_at,
    });
    // The intervening setlist operations do not update this song version.
    assert.equal(removedTag.response.status, 200, JSON.stringify(removedTag.value));
    assert.equal(removedTag.value.genres.some(tag => tag.name === 'T11 API synthetic genre'), false);

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
      await pool.query(`DELETE FROM public.genres g WHERE id=$1 AND name='T11 API synthetic genre'
        AND NOT EXISTS (SELECT 1 FROM public.song_genres sg WHERE sg.genre_id=g.id)`, [id]);
      assert.equal((await pool.query('SELECT 1 FROM public.genres WHERE id=$1', [id])).rowCount, 0);
    }
  }
});
