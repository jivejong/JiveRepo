const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash, randomUUID } = require('node:crypto');
const { pool } = require('../../server/db.js');
const { buildEnrichmentPreview } = require('../../server/existingSongEnrichment.js');
const { applySongEnrichment, stableJson } = require('../../server/applySongEnrichment.js');
const { verifyHarnessIdentity } = require('../t11-guards.cjs');

const MBID = 'b3b3b3b3-1111-4111-8111-b3b3b3b3b3b3';
const DIGEST = 'b3fixture:';

function newFixture() {
  return { suffix: randomUUID(), artistIds: [], definitions: { genres: new Map(), vibes: new Map() }, ids: [], songs: {},
    extraDefinitionIds: { genres: [], vibes: [] }, operations: new Set(), triggers: new Map() };
}

async function createFixture(fixture) {
  const { suffix } = fixture;
  const artist = await pool.query('INSERT INTO public.artists(name) VALUES($1) RETURNING id', [`B3 synthetic artist ${suffix}`]);
  fixture.artistIds.push(artist.rows[0].id);
  const { artistIds, definitions, ids, songs } = fixture;
  for (const name of ['Rock', 'Country', 'Jazz', 'Pop']) {
    const row = await pool.query('INSERT INTO public.genres(name) VALUES($1) RETURNING id,name', [`${name} ${suffix}`]);
    definitions.genres.set(name, row.rows[0]);
  }
  for (const name of ['Calm', 'Mellow', 'Dreamy']) {
    const row = await pool.query('INSERT INTO public.vibes(name) VALUES($1) RETURNING id,name', [`${name} ${suffix}`]);
    definitions.vibes.set(name, row.rows[0]);
  }
  const addSong = async (name, scalars = {}) => {
    const result = scalars.updated_at
      ? await pool.query(`INSERT INTO public.songs
        (title,artist_id,release_year,original_key,bpm,updated_at,chart_content,chart_source,source_document)
        VALUES($1,$2,$3,$4,$5,$6,'{"fixture":"B3 chart sentinel"}'::jsonb,'B3 source sentinel','B3 synthetic') RETURNING id`,
      [`B3 ${name} ${suffix}`, artistIds[0], scalars.release_year ?? null, scalars.original_key ?? null, scalars.bpm ?? null, scalars.updated_at])
      : await pool.query(`INSERT INTO public.songs
        (title,artist_id,release_year,original_key,bpm,chart_content,chart_source,source_document)
        VALUES($1,$2,$3,$4,$5,'{"fixture":"B3 chart sentinel"}'::jsonb,'B3 source sentinel','B3 synthetic') RETURNING id`,
      [`B3 ${name} ${suffix}`, artistIds[0], scalars.release_year ?? null, scalars.original_key ?? null, scalars.bpm ?? null]);
    ids.push(result.rows[0].id);
    return result.rows[0].id;
  };
  songs.empty = await addSong('empty');
  songs.private = await addSong('private-input');
  songs.preserved = await addSong('preserved', { release_year: 1988, original_key: 'G', bpm: 120 });
  songs.primaryless = await addSong('primaryless');
  songs.staleFirst = await addSong('stale-first');
  songs.staleSecond = await addSong('stale-second', { updated_at: '2099-01-01T00:00:00.000000Z' });
  songs.rename = await addSong('rename');
  songs.ambiguous = await addSong('ambiguous');
  songs.forged = await addSong('forged');
  songs.replay = await addSong('replay');
  songs.concurrent = await addSong('concurrent');
  songs.title = await addSong('title-change');
  songs.artist = await addSong('artist-change');
  songs.missing = await addSong('missing');
  songs.noop = await addSong('noop');
  songs.sideEffect = await addSong('side-effect');
  songs.numeric = await addSong('numeric-boundary');
  await pool.query('INSERT INTO public.song_genres(song_id,genre_id,is_primary) VALUES($1,$2,true)', [songs.preserved, definitions.genres.get('Rock').id]);
  await pool.query('INSERT INTO public.song_vibes(song_id,vibe_id) VALUES($1,$2)', [songs.preserved, definitions.vibes.get('Calm').id]);
  await pool.query('INSERT INTO public.song_genres(song_id,genre_id,is_primary) VALUES($1,$2,false)', [songs.primaryless, definitions.genres.get('Jazz').id]);
  return fixture;
}

function newOperation(fixture) {
  const id = randomUUID();
  fixture.operations.add(id);
  return id;
}

async function makeManifest(fixture, songId, options = {}) {
  const { operationId = newOperation(fixture), year, key, bpm, genres = [], vibes = [], primaryGenre } = options;
  const songResult = await pool.query(`SELECT s.id,s.title,s.artist_id,a.name AS artist_name,s.release_year,s.original_key,s.bpm,
      to_char(s.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
    FROM public.songs s JOIN public.artists a ON a.id=s.artist_id WHERE s.id=$1`, [songId]);
  assert.equal(songResult.rowCount, 1);
  const [genreRows, vibeRows, genreAssignments, vibeAssignments] = await Promise.all([
    pool.query('SELECT id,name FROM public.genres ORDER BY id'),
    pool.query('SELECT id,name FROM public.vibes ORDER BY id'),
    pool.query('SELECT g.id,g.name,sg.is_primary FROM public.song_genres sg JOIN public.genres g ON g.id=sg.genre_id WHERE sg.song_id=$1 ORDER BY g.id', [songId]),
    pool.query('SELECT v.id,v.name FROM public.song_vibes sv JOIN public.vibes v ON v.id=sv.vibe_id WHERE sv.song_id=$1 ORDER BY v.id', [songId]),
  ]);
  const row = songResult.rows[0];
  const evidence = { mbid: MBID, scalars: {}, genres: [], vibes: [] };
  const review = { mbid: MBID, scalars: {}, genres: [...genres], vibes: [...vibes], ...(primaryGenre ? { primaryGenre } : {}) };
  for (const [field, value, source] of [['release_year', year, 'MusicBrainz'], ['original_key', key, 'AcousticBrainz'], ['bpm', bpm, 'AcousticBrainz']]) {
    if (value !== undefined) {
      const item = { value, source, response_digest: `${DIGEST}${field}_scalar` };
      evidence.scalars[field] = [item];
      review.scalars[field] = { ...item };
    }
  }
  for (const [field, labels] of [['genres', genres], ['vibes', vibes]]) {
    evidence[field] = labels.map((name, index) => ({ name, source: 'MusicBrainz', response_digest: `${DIGEST}${field}_${index}` }));
  }
  const snapshot = { id: row.id, updated_at: row.updated_at, release_year: row.release_year, original_key: row.original_key,
    bpm: row.bpm, genres: genreAssignments.rows, vibes: vibeAssignments.rows };
  const definitions = { genres: genreRows.rows, vibes: vibeRows.rows };
  const preview = buildEnrichmentPreview(snapshot, evidence, review, definitions);
  return { format_version: 1, operation_id: operationId, songs: [{ song_id: songId, base_version: row.updated_at,
    identity: { title: row.title, artist_id: row.artist_id, artist_name: row.artist_name },
    evidence, review_decision: review, preview }] };
}

async function version(songId) {
  const result = await pool.query(`SELECT to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS version
    FROM public.songs WHERE id=$1`, [songId]);
  return result.rows[0]?.version ?? null;
}

async function insertDigitBoundaryPair(category, fixture) {
  const table = category === 'genres' ? 'genres' : 'vibes';
  const previousResult = await pool.query(`SELECT id FROM public.${table} ORDER BY id DESC LIMIT 1`);
  let previous = previousResult.rows[0]?.id;
  let ordinal = 0;
  while (true) {
    const row = await pool.query(`INSERT INTO public.${table}(name) VALUES($1) RETURNING id,name`,
      [`B3 numeric ${category} ${fixture.suffix} ${ordinal++}`]);
    const current = row.rows[0];
    fixture.extraDefinitionIds[table].push(current.id);
    if (previous && String(previous).length !== String(current.id).length) return [previous, current.id];
    previous = current.id;
  }
}

const hasCode = code => error => error?.code === code;

async function cleanupFixture(fixture, targetPool) {
  const failures = [];
  const attempt = async action => {
    try { await action(); } catch (error) { failures.push(error); }
  };
  for (const [trigger, resource] of fixture.triggers) {
    await attempt(() => targetPool.query(`DROP TRIGGER IF EXISTS ${trigger} ON public.${resource.table}`));
    await attempt(() => targetPool.query(`DROP FUNCTION IF EXISTS public.${resource.fn}()`));
  }
  if (fixture.operations.size) await attempt(() => targetPool.query('DELETE FROM public.song_enrichment_runs WHERE operation_id=ANY($1::uuid[])', [[...fixture.operations]]));
  if (fixture.ids.length) await attempt(() => targetPool.query('DELETE FROM public.songs WHERE id=ANY($1::int[])', [fixture.ids]));
  const genreIds = [...[...fixture.definitions.genres.values()].map(row => row.id), ...fixture.extraDefinitionIds.genres];
  const vibeIds = [...[...fixture.definitions.vibes.values()].map(row => row.id), ...fixture.extraDefinitionIds.vibes];
  if (genreIds.length) {
    await attempt(() => targetPool.query('DELETE FROM public.genres WHERE id=ANY($1::int[])', [genreIds]));
    await attempt(async () => {
      const remaining = await targetPool.query('SELECT id FROM public.genres WHERE id=ANY($1::int[])', [genreIds]);
      if (remaining.rowCount) throw new Error(`Owned Genre definitions remain after cleanup: ${remaining.rows.map(row => row.id).join(', ')}.`);
    });
  }
  if (vibeIds.length) {
    await attempt(() => targetPool.query('DELETE FROM public.vibes WHERE id=ANY($1::int[])', [vibeIds]));
    await attempt(async () => {
      const remaining = await targetPool.query('SELECT id FROM public.vibes WHERE id=ANY($1::int[])', [vibeIds]);
      if (remaining.rowCount) throw new Error(`Owned Vibe definitions remain after cleanup: ${remaining.rows.map(row => row.id).join(', ')}.`);
    });
  }
  if (fixture.artistIds.length) await attempt(() => targetPool.query('DELETE FROM public.artists WHERE id=ANY($1::int[])', [fixture.artistIds]));
  await attempt(() => targetPool.end());
  return failures;
}

test('fixture cleanup attempts every owned resource and pool end after a cleanup failure', async () => {
  const fixture = newFixture();
  fixture.triggers.set('b3_owned_trigger', { fn: 'b3_owned_fn', table: 'songs' });
  fixture.operations.add('b3-operation');
  fixture.ids.push(42);
  fixture.definitions.genres.set('owned', { id: 43 });
  fixture.definitions.vibes.set('owned', { id: 44 });
  fixture.artistIds.push(45);
  const calls = [];
  let ended = false;
  const targetPool = {
    async query(sql) {
      calls.push(sql);
      if (sql.startsWith('DROP TRIGGER')) throw new Error('synthetic trigger drop failure');
      if (sql.startsWith('SELECT id FROM public.')) return { rowCount: 0, rows: [] };
      return { rowCount: 0, rows: [] };
    },
    async end() { ended = true; },
  };
  const failures = await cleanupFixture(fixture, targetPool);
  assert.equal(failures.length, 1);
  assert.match(failures[0].message, /synthetic trigger drop failure/);
  assert.equal(calls.length, 9);
  assert.equal(ended, true);
});

test('fixture cleanup deletes only category-owned definitions and verifies both categories', async () => {
  const fixture = newFixture();
  fixture.extraDefinitionIds.genres.push(9, 12);
  fixture.extraDefinitionIds.vibes.push(13);
  const genres = new Set([9, 12]);
  const vibes = new Set([9, 13, 14]); // Vibe 9 and Genre 13 are nonowned IDs colliding across categories.
  const nonownedGenreId = 13;
  genres.add(nonownedGenreId);
  const calls = [];
  let ended = false;
  const targetPool = {
    async query(sql, params = []) {
      calls.push({ sql, params });
      const table = sql.includes('public.genres') ? genres : vibes;
      if (sql.startsWith('DELETE FROM public.')) {
        for (const id of params[0]) table.delete(id);
        return { rowCount: params[0].length, rows: [] };
      }
      if (sql.startsWith('SELECT id FROM public.')) {
        const rows = params[0].filter(id => table.has(id)).map(id => ({ id }));
        return { rowCount: rows.length, rows };
      }
      throw new Error(`Unexpected cleanup query: ${sql}`);
    },
    async end() { ended = true; },
  };
  const failures = await cleanupFixture(fixture, targetPool);
  assert.deepEqual(failures, []);
  assert.deepEqual(calls.filter(call => call.sql.startsWith('DELETE FROM public.genres')).map(call => call.params[0]), [[9, 12]]);
  assert.deepEqual(calls.filter(call => call.sql.startsWith('DELETE FROM public.vibes')).map(call => call.params[0]), [[13]]);
  assert.equal(genres.has(nonownedGenreId), true);
  assert.equal(vibes.has(14), true);
  assert.deepEqual([...genres].sort((a, b) => a - b), [13]);
  assert.deepEqual([...vibes].sort((a, b) => a - b), [9, 14]);
  assert.equal(ended, true);

  const failedVerificationFixture = newFixture();
  failedVerificationFixture.extraDefinitionIds.genres.push(21);
  const remainingDefinition = await cleanupFixture(failedVerificationFixture, {
    async query(sql) {
      if (sql.startsWith('DELETE FROM public.genres')) return { rowCount: 0, rows: [] };
      if (sql.startsWith('SELECT id FROM public.genres')) return { rowCount: 1, rows: [{ id: 21 }] };
      throw new Error(`Unexpected verification query: ${sql}`);
    },
    async end() {},
  });
  assert.equal(remainingDefinition.length, 1);
  assert.match(remainingDefinition[0].message, /Owned Genre definitions remain.*21/);
});

test('transactional enrichment apply is scratch-bound, review-bound, preserving and replayable', async t => {
  const fixture = newFixture();
  let testFailure;
  try {
    await verifyHarnessIdentity({ baseUrl: process.env.E2E_BASE_URL, runId: process.env.CCM_T11_RUN_ID });
    assert.equal((await pool.query('SELECT current_database() AS name')).rows[0].name, 'ccm_test');
    await createFixture(fixture);
    await t.test('wrong database, owner, or run marker refuses before writes', async () => {
      const manifest = await makeManifest(fixture, fixture.songs.noop);
      const wrongIdentities = [
        { database_name: 'chords', current_user_name: 'role', owner_name: 'role', marker: `CCM T11 owned scratch run ${process.env.CCM_T11_RUN_ID}` },
        { database_name: 'ccm_test', current_user_name: 'role', owner_name: 'other', marker: `CCM T11 owned scratch run ${process.env.CCM_T11_RUN_ID}` },
        { database_name: 'ccm_test', current_user_name: 'role', owner_name: 'role', marker: 'CCM T11 owned scratch run 00000000-0000-4000-8000-000000000000' },
      ];
      for (const identity of wrongIdentities) {
        let writes = 0;
        const fakePool = { async connect() { return {
          async query(sql) {
            if (/^\s*SELECT current_database\(\)/i.test(sql)) return { rows: [identity], rowCount: 1 };
            writes += 1; return { rows: [], rowCount: 0 };
          }, release() {},
        }; } };
        await assert.rejects(applySongEnrichment(fakePool, manifest), hasCode('SCRATCH_OWNERSHIP_REQUIRED'));
        assert.equal(writes, 0);
      }
    });

    await t.test('mixed changed/no-op batch uses exact scalars, adds multiple category-scoped tags, and preserves no-op versions', async () => {
      const genres = [fixture.definitions.genres.get('Country').name, fixture.definitions.genres.get('Pop').name];
      const vibes = [fixture.definitions.vibes.get('Mellow').name, fixture.definitions.vibes.get('Dreamy').name];
      const first = await makeManifest(fixture, fixture.songs.empty, { year: 2001, key: { key_key: 'F#', key_scale: 'minor' }, bpm: 123.6, genres, vibes, primaryGenre: genres[0] });
      const second = await makeManifest(fixture, fixture.songs.noop);
      const noOpVersion = await version(fixture.songs.noop);
      const response = await applySongEnrichment(pool, { ...first, songs: [first.songs[0], second.songs[0]] });
      assert.deepEqual(response.songs.map(song => song.status), ['applied', 'no_op']);
      assert.equal(response.songs[0].delta.scalar_changes.length, 3);
      assert.equal(response.songs[0].delta.tag_additions.genres.length, 2);
      assert.equal(response.songs[0].delta.tag_additions.vibes.length, 2);
      assert.equal(response.songs[0].delta.primary_proposal.genre_id, fixture.definitions.genres.get('Country').id);
      assert.notEqual(response.songs[0].resulting_version, first.songs[0].base_version);
      assert.equal(response.songs[1].resulting_version, noOpVersion);
      assert.equal(await version(fixture.songs.noop), noOpVersion);
      assert.deepEqual((await pool.query('SELECT release_year,original_key,bpm FROM public.songs WHERE id=$1', [fixture.songs.empty])).rows[0],
        { release_year: 2001, original_key: 'F#m', bpm: 124 });
    });

    await t.test('caller mutations after entry cannot change the private reviewed operation', async () => {
      const manifest = await makeManifest(fixture, fixture.songs.private, {
        year: 2002, genres: [fixture.definitions.genres.get('Country').name],
      });
      assert.equal(manifest.songs[0].preview.tag_additions.genres.length, 1);
      const original = JSON.parse(JSON.stringify(manifest));
      let entered;
      let resume;
      const enteredPromise = new Promise(resolve => { entered = resolve; });
      const resumePromise = new Promise(resolve => { resume = resolve; });
      const pausingPool = {
        async connect() {
          const client = await pool.connect();
          return {
            async query(sql, values) {
              if (/SELECT current_database\(\)/i.test(sql)) {
                entered();
                await resumePromise;
              }
              return client.query(sql, values);
            },
            release(error) { return client.release(error); },
          };
        },
      };
      const applying = applySongEnrichment(pausingPool, manifest);
      await enteredPromise;
      manifest.songs[0].evidence.scalars.release_year[0].value = 2025;
      manifest.songs[0].review_decision.scalars.release_year.value = 2025;
      manifest.songs[0].evidence.genres[0].name = 'forged label';
      manifest.songs[0].preview.scalar_changes[0].field = 'chart_source';
      manifest.songs[0].preview.scalar_changes[0].after = 'forged source';
      manifest.songs[0].preview.tag_additions.genres[0].name = 'forged tag';
      manifest.songs[0].chart_content = 'forbidden';
      resume();
      const response = await applying;
      assert.equal(response.songs[0].delta.scalar_changes[0].after, 2002);
      assert.equal(response.songs[0].delta.tag_additions.genres[0].name, fixture.definitions.genres.get('Country').name);
      const song = await pool.query('SELECT release_year,chart_content,chart_source FROM public.songs WHERE id=$1', [fixture.songs.private]);
      assert.equal(song.rows[0].release_year, 2002);
      assert.deepEqual(song.rows[0].chart_content, { fixture: 'B3 chart sentinel' });
      assert.equal(song.rows[0].chart_source, 'B3 source sentinel');
      const receipt = await pool.query('SELECT request_hash,manifest FROM public.song_enrichment_runs WHERE operation_id=$1', [original.operation_id]);
      assert.deepEqual(receipt.rows[0].manifest, original);
      assert.equal(receipt.rows[0].request_hash, createHash('sha256').update(stableJson(original)).digest('hex'));
      fixture.operations.add(original.operation_id);
    });

    await t.test('assignment preservation uses numeric ID ordering across digit boundaries in both categories', async () => {
      for (const category of ['genres', 'vibes']) {
        const [lower, upper] = await insertDigitBoundaryPair(category, fixture);
        if (category === 'genres') {
          await pool.query('INSERT INTO public.song_genres(song_id,genre_id,is_primary) VALUES($1,$2,false),($1,$3,false)',
            [fixture.songs.numeric, lower, upper]);
        } else {
          await pool.query('INSERT INTO public.song_vibes(song_id,vibe_id) VALUES($1,$2),($1,$3)',
            [fixture.songs.numeric, lower, upper]);
        }
      }
      const manifest = await makeManifest(fixture, fixture.songs.numeric);
      assert.equal(manifest.songs[0].preview.has_changes, false);
      const response = await applySongEnrichment(pool, manifest);
      assert.equal(response.songs[0].status, 'no_op');
      assert.equal((await pool.query('SELECT count(*)::int AS n FROM public.song_genres WHERE song_id=$1', [fixture.songs.numeric])).rows[0].n, 2);
      assert.equal((await pool.query('SELECT count(*)::int AS n FROM public.song_vibes WHERE song_id=$1', [fixture.songs.numeric])).rows[0].n, 2);
    });

    await t.test('populated values and current primary survive; an explicit valid primary applies', async () => {
      const additive = await makeManifest(fixture, fixture.songs.preserved, {
        year: 2020, key: 'C', bpm: 90, genres: [fixture.definitions.genres.get('Country').name], vibes: [fixture.definitions.vibes.get('Mellow').name],
      });
      const versionBefore = await version(fixture.songs.preserved);
      const response = await applySongEnrichment(pool, additive);
      assert.deepEqual(response.songs[0].delta.scalar_changes, []);
      assert.equal(response.songs[0].delta.primary_proposal, null);
      assert.notEqual(response.songs[0].resulting_version, versionBefore);
      const existing = await pool.query(`SELECT s.release_year,s.original_key,s.bpm,sg.is_primary,g.name
        FROM public.songs s JOIN public.song_genres sg ON sg.song_id=s.id JOIN public.genres g ON g.id=sg.genre_id
        WHERE s.id=$1 AND g.id=$2`, [fixture.songs.preserved, fixture.definitions.genres.get('Rock').id]);
      assert.deepEqual(existing.rows[0], { release_year: 1988, original_key: 'G', bpm: 120, is_primary: true, name: fixture.definitions.genres.get('Rock').name });
      const primary = await makeManifest(fixture, fixture.songs.primaryless, { primaryGenre: fixture.definitions.genres.get('Jazz').name });
      const primaryResponse = await applySongEnrichment(pool, primary);
      assert.deepEqual(primaryResponse.songs[0].delta.primary_proposal,
        { genre_id: fixture.definitions.genres.get('Jazz').id, name: fixture.definitions.genres.get('Jazz').name });
      assert.equal((await pool.query('SELECT is_primary FROM public.song_genres WHERE song_id=$1 AND genre_id=$2',
        [fixture.songs.primaryless, fixture.definitions.genres.get('Jazz').id])).rows[0].is_primary, true);
    });

    await t.test('one stale exact version rolls back every target and the receipt', async () => {
      const operationId = newOperation(fixture);
      const first = await makeManifest(fixture, fixture.songs.staleFirst, { operationId, year: 1997 });
      const second = await makeManifest(fixture, fixture.songs.staleSecond, { operationId });
      const batch = { ...first, songs: [first.songs[0], second.songs[0]] };
      const firstVersion = await version(fixture.songs.staleFirst);
      assert.equal(second.songs[0].base_version, '2099-01-01T00:00:00.000000Z');
      await pool.query('UPDATE public.songs SET updated_at=updated_at WHERE id=$1', [fixture.songs.staleSecond]);
      assert.equal(await version(fixture.songs.staleSecond), '2099-01-01T00:00:00.000001Z');
      assert.equal((await version(fixture.songs.staleSecond)).slice(0, 23), second.songs[0].base_version.slice(0, 23));
      await assert.rejects(applySongEnrichment(pool, batch), hasCode('STALE_VERSION'));
      assert.equal(await version(fixture.songs.staleFirst), firstVersion);
      assert.equal((await pool.query('SELECT release_year FROM public.songs WHERE id=$1', [fixture.songs.staleFirst])).rows[0].release_year, null);
      assert.equal((await pool.query('SELECT 1 FROM public.song_enrichment_runs WHERE operation_id=$1', [operationId])).rowCount, 0);
    });

    await t.test('changed identity, missing songs, rename and ambiguous definitions invalidate reviewed previews', async () => {
      const title = await makeManifest(fixture, fixture.songs.title, { year: 1990 });
      await pool.query('UPDATE public.songs SET title=title || $2 WHERE id=$1', [fixture.songs.title, ' edited']);
      await assert.rejects(applySongEnrichment(pool, title), hasCode('SONG_IDENTITY_CHANGED'));
      const artist = await makeManifest(fixture, fixture.songs.artist, { year: 1991 });
      const other = await pool.query('INSERT INTO public.artists(name) VALUES($1) RETURNING id', [`B3 changed artist ${fixture.suffix}`]);
      fixture.artistIds.push(other.rows[0].id);
      await pool.query('UPDATE public.songs SET artist_id=$2 WHERE id=$1', [fixture.songs.artist, other.rows[0].id]);
      await assert.rejects(applySongEnrichment(pool, artist), hasCode('SONG_IDENTITY_CHANGED'));
      const missing = await makeManifest(fixture, fixture.songs.missing, { year: 1992 });
      await pool.query('DELETE FROM public.songs WHERE id=$1', [fixture.songs.missing]);
      await assert.rejects(applySongEnrichment(pool, missing), hasCode('SONG_NOT_FOUND'));
      const renamed = await makeManifest(fixture, fixture.songs.rename, { genres: [fixture.definitions.genres.get('Country').name] });
      await pool.query('UPDATE public.genres SET name=$1 WHERE id=$2', [`Renamed ${fixture.suffix}`, fixture.definitions.genres.get('Country').id]);
      await assert.rejects(applySongEnrichment(pool, renamed), hasCode('PREVIEW_MISMATCH'));
      const popName = fixture.definitions.genres.get('Pop').name;
      const ambiguous = await makeManifest(fixture, fixture.songs.ambiguous, { genres: [popName] });
      const duplicate = await pool.query('INSERT INTO public.genres(name) VALUES($1) RETURNING id', [` ${popName} `]);
      fixture.extraDefinitionIds.genres.push(duplicate.rows[0].id);
      await assert.rejects(applySongEnrichment(pool, ambiguous), hasCode('PREVIEW_MISMATCH'));
    });

    await t.test('forged preview fields, evidence references and unknown keys cannot create changes', async () => {
      const base = await makeManifest(fixture, fixture.songs.forged, { year: 2004, genres: [fixture.definitions.genres.get('Rock').name] });
      assert.equal(base.songs[0].preview.scalar_changes.length, 1);
      assert.equal(base.songs[0].preview.tag_additions.genres.length, 1);
      assert.equal(base.songs[0].preview.tag_additions.genres[0].id, fixture.definitions.genres.get('Rock').id);
      const duplicateTarget = { ...base, operation_id: newOperation(fixture), songs: [base.songs[0], base.songs[0]] };
      await assert.rejects(applySongEnrichment(pool, duplicateTarget), hasCode('INVALID_MANIFEST'));
      const mutations = [
        [copy => { copy.songs[0].preview.scalar_changes[0].after = 2005; }, 'PREVIEW_MISMATCH'],
        [copy => { copy.songs[0].preview.tag_additions.genres[0].id += 100000; }, 'PREVIEW_MISMATCH'],
        [copy => { copy.songs[0].preview.scalar_changes[0].evidence.response_digest = 'forged'; }, 'PREVIEW_MISMATCH'],
        [copy => { copy.songs[0].evidence.scalars.release_year[0].value = 2005; }, 'PREVIEW_MISMATCH'],
        [copy => { copy.songs[0].chart_content = { forbidden: true }; }, 'INVALID_MANIFEST'],
      ];
      for (const [mutate, expectedCode] of mutations) {
        const forged = JSON.parse(JSON.stringify(base));
        forged.operation_id = newOperation(fixture);
        mutate(forged);
        await assert.rejects(applySongEnrichment(pool, forged), hasCode(expectedCode));
      }
      assert.equal((await pool.query('SELECT release_year FROM public.songs WHERE id=$1', [fixture.songs.forged])).rows[0].release_year, null);
    });

    await t.test('replay returns the saved result after updates and deletion; changed request reuse fails', async () => {
      const manifest = await makeManifest(fixture, fixture.songs.replay, { year: 1981 });
      const first = await applySongEnrichment(pool, manifest);
      await pool.query('UPDATE public.songs SET title=title || $2 WHERE id=$1', [fixture.songs.replay, ' changed']);
      assert.deepEqual(await applySongEnrichment(pool, manifest), first);
      await pool.query('DELETE FROM public.songs WHERE id=$1', [fixture.songs.replay]);
      assert.deepEqual(await applySongEnrichment(pool, manifest), first);
      const mismatch = JSON.parse(JSON.stringify(manifest));
      mismatch.songs[0].identity.title += ' other';
      await assert.rejects(applySongEnrichment(pool, mismatch), hasCode('OPERATION_ID_REUSED'));
      const receipt = await pool.query('SELECT request_hash,response FROM public.song_enrichment_runs WHERE operation_id=$1', [manifest.operation_id]);
      assert.equal(receipt.rowCount, 1);
      assert.equal(receipt.rows[0].request_hash, first.request_hash);
      assert.deepEqual(receipt.rows[0].response, first);
    });

    await t.test('diagnostics survive a valid apply and an all-no-op operation is recorded and replayed exactly', async () => {
      const unknownName = `B3 unmatched reviewed label ${fixture.suffix}`;
      const diagnosticRun = await makeManifest(fixture, fixture.songs.noop, { year: 2008, genres: [unknownName] });
      const diagnosticResult = await applySongEnrichment(pool, diagnosticRun);
      assert.equal(diagnosticResult.songs[0].status, 'applied');
      assert.ok(diagnosticRun.songs[0].preview.diagnostics.some(item => item.code === 'unknown_tag_definition' && item.label === unknownName));
      const noOpVersion = await version(fixture.songs.noop);
      const noOp = await makeManifest(fixture, fixture.songs.noop);
      const result = await applySongEnrichment(pool, noOp);
      assert.equal(result.songs[0].status, 'no_op');
      assert.equal(result.songs[0].resulting_version, noOpVersion);
      assert.deepEqual(await applySongEnrichment(pool, noOp), result);
      assert.equal((await pool.query('SELECT count(*)::int AS n FROM public.song_enrichment_runs WHERE operation_id=$1', [noOp.operation_id])).rows[0].n, 1);
      const saved = await pool.query('SELECT manifest::text AS manifest_text,response::text AS response_text FROM public.song_enrichment_runs WHERE operation_id=$1', [diagnosticRun.operation_id]);
      assert.match(saved.rows[0].manifest_text, /unknown_tag_definition/);
      assert.doesNotMatch(saved.rows[0].manifest_text, /B3 chart sentinel|B3 source sentinel|chart_content|chart_source/);
      assert.doesNotMatch(saved.rows[0].response_text, /B3 chart sentinel|B3 source sentinel|chart_content|chart_source/);
    });

    await t.test('concurrent identical calls produce one receipt and one applied delta', async () => {
      const manifest = await makeManifest(fixture, fixture.songs.concurrent, { year: 2007 });
      const [left, right] = await Promise.all([applySongEnrichment(pool, manifest), applySongEnrichment(pool, manifest)]);
      assert.deepEqual(left, right);
      assert.equal(left.songs[0].status, 'applied');
      assert.equal((await pool.query('SELECT count(*)::int AS n FROM public.song_enrichment_runs WHERE operation_id=$1', [manifest.operation_id])).rows[0].n, 1);
      assert.equal((await pool.query('SELECT release_year FROM public.songs WHERE id=$1', [fixture.songs.concurrent])).rows[0].release_year, 2007);
    });

    await t.test('receipt-trigger changes to an approved scalar or no-op version roll back with the run', async () => {
      for (const mode of ['scalar', 'noop-version']) {
        const songId = mode === 'scalar' ? fixture.songs.sideEffect : fixture.songs.noop;
        const manifest = await makeManifest(fixture, songId, mode === 'scalar' ? { year: 2005 } : {});
        const operationId = manifest.operation_id;
        const suffix = randomUUID().replaceAll('-', '');
        const trigger = `b3_receipt_${suffix}`;
        const fn = `${trigger}_fn`;
        fixture.triggers.set(trigger, { fn, table: 'song_enrichment_runs' });
        const body = mode === 'scalar'
          ? `UPDATE public.songs SET release_year=COALESCE(release_year,0)+1 WHERE id=${songId};`
          : `UPDATE public.songs SET updated_at=updated_at WHERE id=${songId};`;
        await pool.query(`CREATE FUNCTION public.${fn}() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF NEW.operation_id='${operationId}'::uuid THEN ${body} END IF; RETURN NEW; END; $$`);
        await pool.query(`CREATE TRIGGER ${trigger} AFTER INSERT ON public.song_enrichment_runs
          FOR EACH ROW WHEN (NEW.operation_id='${operationId}'::uuid) EXECUTE FUNCTION public.${fn}()`);
        const oldVersion = await version(songId);
        await assert.rejects(applySongEnrichment(pool, manifest), hasCode('PRESERVATION_FAILED'));
        assert.equal(await version(songId), oldVersion);
        if (mode === 'scalar') {
          assert.equal((await pool.query('SELECT release_year FROM public.songs WHERE id=$1', [songId])).rows[0].release_year, null);
        }
        assert.equal((await pool.query('SELECT 1 FROM public.song_enrichment_runs WHERE operation_id=$1', [operationId])).rowCount, 0);
      }
    });

    await t.test('unexpected row side effects abort the transaction and receipt', async () => {
      const manifest = await makeManifest(fixture, fixture.songs.sideEffect, { year: 2005 });
      const suffix = randomUUID().replaceAll('-', '');
      const trigger = `b3_side_effect_${suffix}`;
      const fn = `${trigger}_fn`;
      fixture.triggers.set(trigger, { fn, table: 'songs' });
      await pool.query(`CREATE FUNCTION public.${fn}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN INSERT INTO public.artists(name) VALUES ('B3 trigger side effect sentinel'); RETURN NEW; END; $$`);
      await pool.query(`CREATE TRIGGER ${trigger} AFTER UPDATE ON public.songs FOR EACH ROW
        WHEN (NEW.id=${fixture.songs.sideEffect}) EXECUTE FUNCTION public.${fn}()`);
      const before = await version(fixture.songs.sideEffect);
      await assert.rejects(applySongEnrichment(pool, manifest), hasCode('PRESERVATION_FAILED'));
      assert.equal(await version(fixture.songs.sideEffect), before);
      assert.equal((await pool.query("SELECT count(*)::int AS n FROM public.artists WHERE name='B3 trigger side effect sentinel'")).rows[0].n, 0);
      assert.equal((await pool.query('SELECT 1 FROM public.song_enrichment_runs WHERE operation_id=$1', [manifest.operation_id])).rowCount, 0);
    });

    await t.test('receipt contains no chart/source body and exact chart fields survive', async () => {
      const row = await pool.query('SELECT manifest::text AS manifest_text,response::text AS response_text FROM public.song_enrichment_runs ORDER BY created_at DESC LIMIT 1');
      assert.equal(row.rowCount, 1);
      for (const sentinel of ['B3 chart sentinel', 'B3 source sentinel', 'chart_content', 'chart_source', 'apiKey', 'secret']) {
        assert.equal(row.rows[0].manifest_text.includes(sentinel), false);
        assert.equal(row.rows[0].response_text.includes(sentinel), false);
      }
      const chart = await pool.query('SELECT chart_content::text,chart_source FROM public.songs WHERE id=$1', [fixture.songs.empty]);
      assert.deepEqual(chart.rows[0], { chart_content: '{"fixture": "B3 chart sentinel"}', chart_source: 'B3 source sentinel' });
    });
  } catch (error) {
    testFailure = error;
  } finally {
    const cleanupFailures = await cleanupFixture(fixture, pool);
    if (testFailure && cleanupFailures.length) throw new AggregateError([testFailure, ...cleanupFailures], 'B3 integration test and cleanup both failed.');
    if (testFailure) throw testFailure;
    if (cleanupFailures.length) throw new AggregateError(cleanupFailures, 'B3 integration test cleanup failed.');
  }
});
