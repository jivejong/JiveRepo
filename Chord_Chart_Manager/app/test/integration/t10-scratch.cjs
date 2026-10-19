// Destructive only to the task-owned ccm_setlisttest database. The caller
// must refuse to run if that database exists before this file is started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Pool } = require('pg');

const databaseName = 'ccm_setlisttest';
const freshDatabaseName = 'ccm_setlistfresh';
const scratchCreated = new Set();
const baseUrl = new URL(process.env.DATABASE_URL);
const adminUrl = new URL(baseUrl);
const adminPool = new Pool({ connectionString: adminUrl.toString() });
const databaseUrl = new URL(baseUrl);
databaseUrl.pathname = `/${databaseName}`;
process.env.DATABASE_URL = databaseUrl.toString();

const { runMigrations } = require('../../server/migrate');
const { pool } = require('../../server/db');
const app = require('../../server/server');

const uuid = () => crypto.randomUUID();
const tokenSql = `to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

async function main() {
  for (const name of [databaseName, freshDatabaseName]) {
    const exists = await adminPool.query('SELECT 1 FROM pg_database WHERE datname=$1', [name]);
    assert.equal(exists.rowCount, 0, `Refusing to use pre-existing scratch database ${name}`);
  }
  await adminPool.query(`CREATE DATABASE ${databaseName}`);
  scratchCreated.add(databaseName);
  await adminPool.query(`CREATE DATABASE ${freshDatabaseName}`);
  scratchCreated.add(freshDatabaseName);
  assert.deepEqual(await runMigrations({ database: freshDatabaseName }), ['0001', '0002', '0003', '0004']);
  assert.deepEqual(await runMigrations({ database: freshDatabaseName }), ['0001', '0002', '0003', '0004']);
  const root = path.resolve(__dirname, '../..');
  const migrations = path.join(root, 'db', 'migrations');
  const legacyDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ccm-t10-legacy-'));
  for (const file of ['0001_baseline.sql', '0002_monotonic_song_version.sql']) {
    fs.copyFileSync(path.join(migrations, file), path.join(legacyDir, file));
  }
  const appliedLegacy = await runMigrations({ migrationsDir: legacyDir });
  assert.deepEqual(appliedLegacy, ['0001', '0002']);

  const artist = await pool.query("INSERT INTO public.artists(name) VALUES('T10 legacy fixture artist') RETURNING id");
  const songIds = [];
  for (let i = 0; i < 2; i += 1) {
    const inserted = await pool.query(
      "INSERT INTO public.songs(title,artist_id,chart_source,chart_content) VALUES($1,$2,'','[]'::jsonb) RETURNING id",
      [`T10 legacy fixture song ${i + 1}`, artist.rows[0].id]);
    songIds.push(inserted.rows[0].id);
  }
  const legacyList = await pool.query(
    "INSERT INTO public.setlists(name) VALUES('T10 legacy upgrade fixture') RETURNING id");
  await pool.query(`INSERT INTO public.setlist_songs(setlist_id,song_id,position,capo_fret)
    VALUES($1,$2,1,0),($1,$3,2,NULL)`, [legacyList.rows[0].id, ...songIds]);
  const oldEntries = (await pool.query(
    'SELECT song_id,position,capo_fret FROM public.setlist_songs WHERE setlist_id=$1 ORDER BY position',
    [legacyList.rows[0].id])).rows;

  const applied = await runMigrations();
  assert.deepEqual(applied, ['0001', '0002', '0003', '0004']);
  const upgradedEntries = (await pool.query(
    'SELECT id,song_id,position,capo_fret FROM public.setlist_songs WHERE setlist_id=$1 ORDER BY position',
    [legacyList.rows[0].id])).rows;
  assert.equal(new Set(upgradedEntries.map(row => row.id)).size, 2);
  assert.deepEqual(upgradedEntries.map(({ song_id, position, capo_fret }) => ({ song_id, position, capo_fret })), oldEntries);

  const server = app.listen(0);
  const base = `http://127.0.0.1:${server.address().port}`;
  const api = async (method, route, body) => {
    const response = await fetch(base + route, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, body: await response.json(),
      replayed: response.headers.get('x-operation-replayed') === 'true' };
  };
  const mutate = (type, payload = {}, setlistId = null, baseUpdatedAt = null, operationId = uuid()) =>
    api('POST', '/api/setlists/mutations', {
      operation_id: operationId, type, setlist_id: setlistId,
      base_updated_at: baseUpdatedAt, payload,
    });
  const detail = id => api('GET', `/api/setlists/${id}`);

  try {
    // A lost song-create response can be replayed only with the frozen body;
    // identical replay returns the original server ID and changed replay is
    // rejected instead of creating a duplicate.
    const songCreateOperation = uuid();
    const songCreatePayload = { title: 'T10 create receipt fixture', artist: 'T10 receipt artist',
      chart_source: 'Invented receipt fixture words', operation_id: songCreateOperation };
    const songCreated = await api('POST', '/api/songs', songCreatePayload);
    assert.equal(songCreated.status, 201);
    const songReplay = await api('POST', '/api/songs', songCreatePayload);
    assert.equal(songReplay.status, 201);
    assert.equal(songReplay.body.id, songCreated.body.id);
    assert.equal((await api('POST', '/api/songs', { ...songCreatePayload, title: 'T10 altered retry' })).status, 409);

    // Song UPDATE receipts serialize duplicate attempts, reject UUID reuse
    // with a changed body, and remain historical after a later server edit.
    const updateBase = songCreated.body.updated_at;
    const updateOperation = uuid();
    const updatePayload = { bpm: 91, genres: [{ name: 'T10 receipt genre', is_primary: true }],
      base_updated_at: updateBase, operation_id: updateOperation };
    const updateUrl = `/api/songs/${songCreated.body.id}`;
    assert.equal((await api('PUT', updateUrl, { bpm: 91, base_updated_at: updateBase })).status, 400,
      'song updates require a replay-safe operation UUID');
    assert.equal((await api('PUT', updateUrl, { ...updatePayload, operation_id: 'not-a-uuid' })).status, 400);
    const duplicateUpdates = await Promise.all([
      api('PUT', updateUrl, updatePayload), api('PUT', updateUrl, updatePayload),
    ]);
    assert.deepEqual(duplicateUpdates.map(item => item.status), [200, 200]);
    assert.equal(duplicateUpdates.filter(item => item.replayed).length, 1);
    assert.equal(duplicateUpdates[0].body.updated_at, duplicateUpdates[1].body.updated_at);
    assert.equal(duplicateUpdates[0].body.bpm, 91);
    assert.equal((await pool.query('SELECT count(*)::int AS n FROM public.mutation_receipts WHERE operation_id=$1',
      [updateOperation])).rows[0].n, 1);
    const updateReceipt = duplicateUpdates[0].body;
    assert.ok(updateReceipt.updated_at > updateBase);
    const changedReuse = await api('PUT', updateUrl, { ...updatePayload, bpm: 92 });
    assert.equal(changedReuse.status, 409);
    assert.equal((await api('GET', updateUrl)).body.bpm, 91);
    assert.equal((await pool.query(`SELECT count(*)::int AS n FROM public.genres g
      JOIN public.song_genres sg ON sg.genre_id=g.id WHERE sg.song_id=$1 AND g.name='T10 receipt genre'`,
      [songCreated.body.id])).rows[0].n, 1, 'changed-payload rejection does not duplicate the tag assignment');

    const laterEdit = await api('PUT', updateUrl, {
      bpm: 103, base_updated_at: updateReceipt.updated_at, operation_id: uuid(),
    });
    assert.equal(laterEdit.status, 200);
    assert.notEqual(laterEdit.body.updated_at, updateReceipt.updated_at);
    const historicalReplay = await api('PUT', updateUrl, updatePayload);
    assert.equal(historicalReplay.status, 200);
    assert.equal(historicalReplay.replayed, true);
    assert.equal(historicalReplay.body.bpm, 91);
    assert.equal(historicalReplay.body.updated_at, updateReceipt.updated_at);
    const currentAfterReplay = await api('GET', updateUrl);
    assert.equal(currentAfterReplay.body.bpm, 103);
    assert.equal(currentAfterReplay.body.updated_at, laterEdit.body.updated_at);

    const staleOperation = uuid();
    const staleUpdate = await api('PUT', updateUrl, {
      bpm: 117, base_updated_at: updateBase, operation_id: staleOperation,
    });
    assert.equal(staleUpdate.status, 409, 'a stale first attempt remains a conflict');
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1',
      [staleOperation])).rowCount, 0, 'rejected stale attempt records no receipt');
    assert.equal((await api('GET', updateUrl)).body.bpm, 103);

    // Fresh create receipt replay is safe and maps one stable server identity.
    const createId = uuid();
    let response = await mutate('create', { name: 'T10 receipt fixture' }, null, null, createId);
    assert.equal(response.status, 200);
    const createdId = response.body.id;
    assert.ok(createdId);
    const createReplay = await mutate('create', { name: 'T10 receipt fixture' }, null, null, createId);
    assert.equal(createReplay.status, 200);
    assert.equal(createReplay.body.id, createdId);
    assert.equal((await mutate('create', { name: 'different' }, null, null, createId)).status, 409);

    let current = (await detail(createdId)).body;
    const addOne = await mutate('entry_add', {
      song_id: songIds[0], capo_fret: 0, transposed_key: null, notes: null,
    }, createdId, current.updated_at);
    assert.equal(addOne.status, 200);
    assert.equal(addOne.body.songs.length, 1);
    const entryOne = addOne.body.created_entry_id;
    assert.ok(entryOne);
    current = addOne.body;
    const addTwo = await mutate('entry_add', { song_id: songIds[0] }, createdId, current.updated_at);
    assert.equal(addTwo.status, 200);
    assert.notEqual(addTwo.body.created_entry_id, entryOne, 'duplicate songs have distinct entry identity');
    current = addTwo.body;

    const omittedEdit = await mutate('entry_edit', { entry_id: entryOne, notes: 'first note' }, createdId, current.updated_at);
    assert.equal(omittedEdit.status, 200);
    assert.equal(omittedEdit.body.songs[0].capo_fret, 0, 'omitted override stays unchanged');
    current = omittedEdit.body;
    const clearEdit = await mutate('entry_edit', { entry_id: entryOne, capo_fret: null }, createdId, current.updated_at);
    assert.equal(clearEdit.status, 200);
    assert.equal(clearEdit.body.songs[0].capo_fret, null, 'explicit null clears override');
    current = clearEdit.body;

    // A failed stale write leaves metadata, entries, and version unchanged.
    const beforeStale = (await detail(createdId)).body;
    response = await mutate('metadata', { name: 'must not write' }, createdId, addOne.body.updated_at);
    assert.equal(response.status, 409);
    assert.deepEqual((await detail(createdId)).body, beforeStale);
    assert.equal((await mutate('metadata', { name: 'bad date', gig_date: '2026-02-31' }, createdId, current.updated_at)).status, 400);
    assert.deepEqual((await detail(createdId)).body, beforeStale);

    const reordered = await mutate('reorder', { entry_ids: [addTwo.body.created_entry_id, entryOne] }, createdId, current.updated_at);
    assert.equal(reordered.status, 200);
    assert.deepEqual(reordered.body.songs.map(item => item.entry_id), [addTwo.body.created_entry_id, entryOne]);
    current = reordered.body;
    const badReorder = await mutate('reorder', { entry_ids: [entryOne] }, createdId, current.updated_at);
    assert.equal(badReorder.status, 400);
    assert.deepEqual((await detail(createdId)).body.songs.map(item => item.entry_id), [addTwo.body.created_entry_id, entryOne]);

    current = (await detail(createdId)).body;
    const addThree = await mutate('entry_add', { song_id: songIds[0] }, createdId, current.updated_at);
    assert.equal(addThree.status, 200);
    const removeMiddle = await mutate('entry_remove', { entry_id: entryOne }, createdId, addThree.body.updated_at);
    assert.equal(removeMiddle.status, 200);
    assert.deepEqual(removeMiddle.body.songs.map(item => item.position), [1, 2]);
    assert.deepEqual(removeMiddle.body.songs.map(item => item.entry_id), [addTwo.body.created_entry_id, addThree.body.created_entry_id]);
    current = removeMiddle.body;

    const emptySetlist = await mutate('create', { name: 'T10 empty reorder fixture' });
    const emptyReorder = await mutate('reorder', { entry_ids: [] }, emptySetlist.body.id, emptySetlist.body.updated_at);
    assert.equal(emptyReorder.status, 200);
    assert.ok(emptyReorder.body.updated_at > emptySetlist.body.updated_at);

    // Interleave a committed entry write immediately after the detail SELECT
    // executes. The returned header and entries must describe one snapshot.
    const snapshotList = await mutate('create', { name: 'T10 consistent detail fixture' });
    const originalPoolQuery = pool.query.bind(pool);
    let interleaveDetail = true;
    pool.query = async (sql, ...args) => {
      const result = await originalPoolQuery(sql, ...args);
      const statement = String(sql);
      if (interleaveDetail && (/FROM\s+public\.setlists\s+sl\s+WHERE\s+sl\.id\s*=\s*\$1/i.test(statement)
          || /FROM\s+setlists\s+WHERE\s+id\s*=\s*\$1/i.test(statement))) {
        interleaveDetail = false;
        const mutation = await mutate('entry_add', { song_id: songIds[0] }, snapshotList.body.id,
          snapshotList.body.updated_at);
        assert.equal(mutation.status, 200);
      }
      return result;
    };
    let snapshot;
    try { snapshot = await detail(snapshotList.body.id); }
    finally { pool.query = originalPoolQuery; }
    assert.equal(snapshot.status, 200);
    assert.equal(snapshot.body.songs.length, 0, 'a read begun before the write sees no newly committed entry');
    const snapshotAfter = await detail(snapshotList.body.id);
    assert.equal(snapshotAfter.body.songs.length, 1);
    assert.ok(snapshotAfter.body.updated_at > snapshot.body.updated_at);

    // Exactly one same-token concurrent writer succeeds.
    const commonToken = current.updated_at;
    const concurrent = await Promise.all([
      mutate('metadata', { notes: 'writer A' }, createdId, commonToken),
      mutate('metadata', { notes: 'writer B' }, createdId, commonToken),
    ]);
    assert.deepEqual(concurrent.map(item => item.status).sort(), [200, 409]);

    // A retry returns its durable historical receipt. A later queued mutation
    // using that historical version conflicts instead of overwriting newer data.
    current = (await detail(createdId)).body;
    const lostId = uuid();
    const committedBeforeLostResponse = await mutate('metadata', { name: 'receipt committed' }, createdId, current.updated_at, lostId);
    assert.equal(committedBeforeLostResponse.status, 200);
    const concurrentWriter = await mutate('metadata', { notes: 'later server edit' }, createdId, committedBeforeLostResponse.body.updated_at);
    assert.equal(concurrentWriter.status, 200);
    const replay = await mutate('metadata', { name: 'receipt committed' }, createdId, current.updated_at, lostId);
    assert.equal(replay.status, 200);
    assert.equal(replay.body.updated_at, committedBeforeLostResponse.body.updated_at);
    const staleFollowup = await mutate('metadata', { notes: 'queued local followup' }, createdId, replay.body.updated_at);
    assert.equal(staleFollowup.status, 409);
    const afterReplay = (await detail(createdId)).body;
    assert.equal(afterReplay.notes, 'later server edit');
    assert.equal(afterReplay.name, 'receipt committed');

    // Missing/malformed token rejection occurs before any writes.
    assert.equal((await mutate('metadata', { name: 'missing token' }, createdId, null)).status, 400);
    assert.equal((await mutate('entry_remove', { entry_id: entryOne }, createdId, 'invalid')).status, 400);

    // Entry-delete cascades advance every affected parent token.
    let cascadeList = (await mutate('create', { name: 'T10 cascade fixture' })).body;
    cascadeList = (await mutate('entry_add', { song_id: songIds[1] }, cascadeList.id, cascadeList.updated_at)).body;
    const beforeCascade = cascadeList.updated_at;
    await pool.query('DELETE FROM public.songs WHERE id=$1', [songIds[1]]);
    const afterCascade = (await detail(cascadeList.id)).body;
    assert.ok(afterCascade.updated_at > beforeCascade);
    assert.equal(afterCascade.songs.length, 0);
    const deletedSongOp = uuid();
    assert.equal((await mutate('entry_add', { song_id: songIds[1] }, cascadeList.id,
      afterCascade.updated_at, deletedSongOp)).status, 409);
    assert.deepEqual((await detail(cascadeList.id)).body, afterCascade);
    assert.equal((await pool.query('SELECT 1 FROM public.mutation_receipts WHERE operation_id=$1', [deletedSongOp])).rowCount, 0);

    // Delete receipts remain replayable after the setlist row is gone.
    const deleteToken = afterReplay.updated_at;
    const deleteId = uuid();
    const deleted = await mutate('delete', {}, createdId, deleteToken, deleteId);
    assert.equal(deleted.status, 200);
    assert.equal((await mutate('delete', {}, createdId, deleteToken, deleteId)).status, 200);
    assert.equal((await detail(createdId)).status, 404);
    await pool.query('DELETE FROM public.songs WHERE id=$1', [songCreated.body.id]);
    console.log('T10 scratch API checks passed: upgrade, identity, transactions, retries, conflicts, concurrency, cascades.');
  } finally {
    await new Promise(resolve => server.close(resolve));
    await pool.end();
    fs.rmSync(legacyDir, { recursive: true, force: true });
    for (const name of scratchCreated) {
      await adminPool.query(`DROP DATABASE ${name}`);
      scratchCreated.delete(name);
    }
    await adminPool.end();
  }
}

const crypto = require('node:crypto');
main().catch(async error => {
  console.error(`T10 scratch API check failed: ${error.message}`);
  try { await pool.end(); } catch {}
  try {
    for (const name of scratchCreated) {
      const exists = await adminPool.query('SELECT 1 FROM pg_database WHERE datname=$1', [name]);
      if (exists.rowCount) await adminPool.query(`DROP DATABASE ${name}`);
      scratchCreated.delete(name);
    }
    await adminPool.end();
  } catch {}
  process.exitCode = 1;
});
