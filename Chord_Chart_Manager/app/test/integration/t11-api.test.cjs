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
const createdGenreNames = new Map();
const createdVibes = [];
const createdVibeNames = new Map();
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
    const filterContains = async selected => {
      const params = new URLSearchParams({ tag_filters: JSON.stringify(selected) });
      const result = await json('GET', `/api/songs?${params}`);
      assert.equal(result.response.status, 200, JSON.stringify(result.value));
      return result.value.songs.some(song => song.id === primarySong.id);
    };
    assert.equal(await filterContains([
      { category: 'Genre', name: 'T11 API missing' }, { category: 'Genre', name: 'T11 API secondary' },
      { category: 'Vibe', name: 'T11 API missing' }, { category: 'Vibe', name: 'T11 API calm' },
      { category: 'Era', name: '1960s' }, { category: 'Era', name: '1970s' },
    ]), true, 'values within Genre, Vibe, and Era are OR alternatives');
    assert.equal(await filterContains([
      { category: 'Genre', name: 'T11 API shared category name' },
      { category: 'Vibe', name: 'T11 API shared category name' },
    ]), true, 'equal names retain distinct Genre and Vibe identity');
    assert.equal(await filterContains([
      { category: 'Genre', name: 'T11 API secondary' }, { category: 'Vibe', name: 'T11 API missing' },
    ]), false, 'distinct categories combine with AND');
    assert.equal(await filterContains([{ category: 'Era', name: '1970s' }]), true);
    assert.equal(await filterContains([{ category: 'Era', name: '1980s' }]), false);
    const boundary = await json('PUT', `/api/songs/${primarySong.id}`, {
      release_year: 1980, base_updated_at: filterSong.value.updated_at, operation_id: randomUUID(),
    });
    assert.equal(boundary.response.status, 200, JSON.stringify(boundary.value));
    assert.equal(await filterContains([{ category: 'Era', name: '1970s' }]), false);
    assert.equal(await filterContains([{ category: 'Era', name: '1980s' }]), true);
    const filterSongVersion = boundary.value.updated_at;

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
      category: 'Genre', base_updated_at: filterSongVersion,
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
