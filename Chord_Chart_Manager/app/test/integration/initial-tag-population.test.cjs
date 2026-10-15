const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { Pool } = require('../../server/node_modules/pg');
const { applyInitialDefinitions, captureSnapshot, inputFingerprint } = require('../../scripts/apply-initial-tag-definitions.cjs');
const { inspect, fingerprint, readNames } = require('../../scripts/populate-tag-definitions.cjs');

test('initial vocabulary operation on an owned, verified full backup restore', async () => {
  const run = process.env.CCM_VOCAB_RUN_ID;
  assert.match(run || '', /^[a-f0-9]{32}$/, 'Requires its dedicated scratch owner run ID');
  assert.equal(new URL(process.env.DATABASE_URL).pathname, `/ccm_vocab_${run}`);
  const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 3, connectionTimeoutMillis: 5000 });
  const client = await pool.connect();
  try {
    const identity = (await client.query(`SELECT current_database() AS database,
      d.datdba=role.oid AS owned, shobj_description(d.oid,'pg_database') AS marker
      FROM pg_database d JOIN pg_roles role ON role.rolname=current_user WHERE d.datname=current_database()`)).rows[0];
    assert.equal(identity.database, `ccm_vocab_${run}`);
    assert.equal(identity.owned, true);
    assert.equal(identity.marker, `CCM initial vocabulary owned scratch ${run}`);
    const initial = await captureSnapshot(client);
    assert.equal(initial.hash, process.env.CCM_VOCAB_RESTORE_SHA256, 'Restored complete row snapshot must equal the fresh live snapshot');
    const files = { genres: path.resolve(__dirname, '../../../docs/genres.txt'), vibes: path.resolve(__dirname, '../../../docs/vibes.txt') };
    const genres = readNames(files.genres, 'Genre', 45, 'genres');
    const vibes = readNames(files.vibes, 'Vibe', 40, 'vibes');
    const report = () => Promise.all([inspect(client, 'Genre', genres), inspect(client, 'Vibe', vibes)]).then(rows => rows.flat());
    const options = async () => ({ ...files, mode: '--rehearse', inputs: inputFingerprint(files.genres, files.vibes),
      snapshot: (await captureSnapshot(client)).hash, preview: fingerprint(await report()) });
    const initialReport = await report();
    assert.equal(fingerprint(initialReport), process.env.CCM_VOCAB_PREVIEW_SHA256, 'Backup restores the reviewed preview too');

    for (const [change, pattern] of [
      [{ mode: '--apply-live' }, /explicit chords database/],
      [{ inputs: '0'.repeat(64) }, /file bytes changed/],
      [{ snapshot: '0'.repeat(64) }, /snapshot changed/],
      [{ preview: '0'.repeat(64) }, /preview changed/],
    ]) {
      await assert.rejects(applyInitialDefinitions(client, { ...await options(), ...change }), pattern);
      assert.equal((await captureSnapshot(client)).hash, initial.hash, 'Refusal must leave every protected row unchanged');
    }
    const originalGuard = spawnSync(process.execPath, [path.resolve(__dirname, '../../scripts/populate-tag-definitions.cjs'),
      '--genres', files.genres, '--vibes', files.vibes, '--apply', '--expected-fingerprint', fingerprint(initialReport)],
    { env: { ...process.env, CCM_T11_ISOLATED: '1', CCM_T11_RUN_ID: run }, encoding: 'utf8', timeout: 10000 });
    assert.equal(originalGuard.status, 1);
    assert.match(originalGuard.stderr, /owner-marked ccm_test scratch database/);
    assert.equal((await captureSnapshot(client)).hash, initial.hash);

    const duplicate = (await client.query("INSERT INTO public.genres(name) VALUES('Classic  Rock') RETURNING id")).rows[0];
    const ambiguousBefore = await captureSnapshot(client);
    await assert.rejects(applyInitialDefinitions(client, await options()), /Ambiguous normalized/);
    assert.equal((await captureSnapshot(client)).hash, ambiguousBefore.hash);
    assert.equal((await client.query("DELETE FROM public.genres WHERE id=$1 AND name='Classic  Rock' RETURNING id", [duplicate.id])).rowCount, 1);

    const writer = await pool.connect();
    try {
      await writer.query('BEGIN; LOCK TABLE public.songs IN ROW EXCLUSIVE MODE');
      await assert.rejects(applyInitialDefinitions(client, await options()), error => error.code === '55P03');
    } finally { await writer.query('ROLLBACK'); writer.release(); }
    assert.equal((await captureSnapshot(client)).hash, initial.hash, 'Active writer refusal must be atomic');

    const fixtureArtist = (await client.query('INSERT INTO public.artists(name) VALUES($1) RETURNING id', [`CCM vocabulary rollback ${run}`])).rows[0];
    const fixtureOperation = '00000000-0000-4000-8000-' + run.slice(0, 12);
    try {
      await client.query(`CREATE FUNCTION public.ccm_vocab_side_effect() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN UPDATE public.artists SET name=name || ' changed' WHERE id=${fixtureArtist.id}; RETURN NEW; END $$`);
      await client.query('CREATE TRIGGER ccm_vocab_side_effect AFTER INSERT ON public.genres FOR EACH ROW EXECUTE FUNCTION public.ccm_vocab_side_effect()');
      const before = await captureSnapshot(client);
      await assert.rejects(applyInitialDefinitions(client, await options()), /Unexpected protected row delta in artists/);
      const rolledBack = await captureSnapshot(client);
      assert.equal(rolledBack.hash, before.hash, 'Artist side effect and definition insertion roll back');
    } finally {
      await client.query('DROP TRIGGER IF EXISTS ccm_vocab_side_effect ON public.genres');
      await client.query('DROP FUNCTION IF EXISTS public.ccm_vocab_side_effect()');
      assert.equal((await client.query('DELETE FROM public.artists WHERE id=$1 AND name=$2 RETURNING id', [fixtureArtist.id, `CCM vocabulary rollback ${run}`])).rowCount, 1);
    }
    assert.equal((await captureSnapshot(client)).hash, initial.hash, 'Synthetic fault fixture cleanup restores every row');

    const receiptBeforeNumber = '9007199254740992';
    const receiptAfterNumber = '9007199254740993';
    assert.equal(Number(receiptBeforeNumber), Number(receiptAfterNumber), 'The JSONB number pair collapses under JavaScript Number parsing');
    await client.query(`INSERT INTO public.mutation_receipts(operation_id,request_hash,entity_type,resource_id,response)
      VALUES($1,'ccm-precision-fixture','song','ccm-precision-fixture',$2::jsonb)`,
    [fixtureOperation, `{"receipt_number":${receiptBeforeNumber}}`]);
    try {
      await client.query(`CREATE FUNCTION public.ccm_vocab_precision_side_effect() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN UPDATE public.mutation_receipts SET response='{"receipt_number":${receiptAfterNumber}}'::jsonb
          WHERE operation_id='${fixtureOperation}'; RETURN NEW; END $$`);
      await client.query('CREATE TRIGGER ccm_vocab_precision_side_effect AFTER INSERT ON public.genres FOR EACH ROW EXECUTE FUNCTION public.ccm_vocab_precision_side_effect()');
      const before = await captureSnapshot(client);
      await assert.rejects(applyInitialDefinitions(client, await options()), /Unexpected protected row delta in mutation_receipts/);
      const rolledBack = await captureSnapshot(client);
      assert.equal(rolledBack.hash, before.hash, 'Receipt precision mismatch and definition insertion roll back exactly');
      assert.equal(rolledBack.tableHashes.mutation_receipts, before.tableHashes.mutation_receipts,
        'Receipt JSONB large-number side effect must be detected and rolled back losslessly');
    } finally {
      await client.query('DROP TRIGGER IF EXISTS ccm_vocab_precision_side_effect ON public.genres');
      await client.query('DROP FUNCTION IF EXISTS public.ccm_vocab_precision_side_effect()');
      assert.equal((await client.query('DELETE FROM public.mutation_receipts WHERE operation_id=$1 AND request_hash=$2 RETURNING operation_id', [fixtureOperation, 'ccm-precision-fixture'])).rowCount, 1);
    }
    assert.equal((await captureSnapshot(client)).hash, initial.hash, 'Precision fixture cleanup restores every row');

    const applied = await applyInitialDefinitions(client, await options());
    const missing = initialReport.filter(row => row.state === 'missing');
    assert.equal(applied.inserted.length, missing.length);
    assert.deepEqual(applied.inserted.map(({ category, name }) => ({ category, name })), missing.map(({ category, proposed }) => ({ category, name: proposed })));
    const after = await captureSnapshot(client);
    for (const table of Object.keys(initial.tableHashes).filter(key => !['genres','vibes'].includes(key))) {
      assert.equal(after.tableHashes[table], initial.tableHashes[table], `Full ${table} rows remain exact`);
    }
    for (const [category, table] of [['Genre','genres'],['Vibe','vibes']]) {
      const insertedIds = applied.inserted.filter(row => row.category === category).map(row => row.id);
      assert.deepEqual(after.data[table].filter(row => !insertedIds.includes(row.id)), initial.data[table]);
      assert.equal(after.data[table].length, initial.data[table].length + insertedIds.length);
    }
    for (const name of ['Country','Americana','Indie']) {
      assert.equal(after.data.genres.filter(row => row.name === name).length, 1);
      assert.equal(after.data.vibes.filter(row => row.name === name).length, 1);
    }
    assert.equal((await report()).filter(row => row.state === 'missing').length, 0);
    const repeated = await applyInitialDefinitions(client, await options());
    assert.deepEqual(repeated.inserted, []);
    assert.equal((await captureSnapshot(client)).hash, after.hash);

    // Only task-inserted, unused rows are changed in this scratch-only spelling probe.
    const ownedGenre = applied.inserted.find(row => row.category === 'Genre');
    assert.ok(ownedGenre);
    const spelling = `  ${ownedGenre.name.toUpperCase().replace(/ /g, '   ')}  `;
    assert.equal((await client.query('UPDATE public.genres SET name=$1 WHERE id=$2 AND name=$3 RETURNING id', [spelling, ownedGenre.id, ownedGenre.name])).rowCount, 1);
    const collisionBefore = await captureSnapshot(client);
    assert.equal((await report()).filter(row => row.state === 'spelling_collision').length, 1);
    assert.deepEqual((await applyInitialDefinitions(client, await options())).inserted, []);
    assert.equal((await captureSnapshot(client)).hash, collisionBefore.hash, 'Equivalent spelling must reuse the existing ID/name without renaming');
    console.log(JSON.stringify({ restoredSnapshot: initial.hash, rehearsedInserted: applied.inserted.length,
      repeatedInserted: repeated.inserted.length, totals: applied.totals, safeguards: 'passed' }));
  } finally { client.release(); await pool.end(); }
});
