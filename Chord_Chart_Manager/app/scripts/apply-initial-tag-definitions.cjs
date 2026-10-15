#!/usr/bin/env node
// One bounded initial vocabulary operation. The original T11 apply guard stays intact.
const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { Pool } = require('../server/node_modules/pg');
const { readNames, inspect, fingerprint } = require('./populate-tag-definitions.cjs');
const { categoryTable, lockTagDefinitionNames } = require('../server/tagDefinitionNames');

const TABLE_ORDERS = Object.freeze({
  artists: 'id', genres: 'id', vibes: 'id', songs: 'id',
  song_genres: 'song_id,genre_id', song_vibes: 'song_id,vibe_id',
  setlists: 'id', setlist_songs: 'setlist_id,position,song_id',
  mutation_receipts: 'operation_id', schema_migrations: 'version',
});
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');

function inputFingerprint(genresFile, vibesFile) {
  return sha256(JSON.stringify([sha256(fs.readFileSync(genresFile)), sha256(fs.readFileSync(vibesFile))]));
}

async function captureSnapshot(client) {
  // PostgreSQL emits timestamp strings with their exact microseconds; never pass them through Date.
  const pairs = Object.entries(TABLE_ORDERS).map(([table, order]) =>
    `'${table}', (SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY ${order.split(',').map(key => `r.${key}`).join(',')}), '[]'::jsonb) FROM public.${table} r)`);
  const { rows } = await client.query(`WITH full_snapshot AS (
      SELECT jsonb_build_object(${pairs.join(',')}) AS value
    )
    SELECT value::text AS snapshot,
      (SELECT jsonb_object_agg(key, elements::text)
       FROM jsonb_each(value) AS tables(key, elements)) AS table_texts
    FROM full_snapshot`);
  return { text: rows[0].snapshot, hash: sha256(rows[0].snapshot),
    tableHashes: Object.fromEntries(Object.entries(rows[0].table_texts).map(([table, text]) => [table, sha256(text)])),
    data: JSON.parse(rows[0].snapshot) };
}

function argumentsFrom(argv) {
  const result = {};
  const values = new Map([
    ['--genres', 'genres'], ['--vibes', 'vibes'], ['--expected-fingerprint', 'preview'],
    ['--expected-input-sha256', 'inputs'], ['--expected-snapshot-sha256', 'snapshot'],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--apply-live' || arg === '--rehearse') {
      if (result.mode) throw new Error('Choose exactly one operation mode.');
      result.mode = arg;
    } else if (values.has(arg)) {
      const key = values.get(arg);
      if (result[key] || !argv[index + 1] || argv[index + 1].startsWith('--')) throw new Error(`Missing or repeated ${arg}.`);
      result[key] = argv[++index];
    } else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!result.mode || !result.genres || !result.vibes
      || !['preview', 'inputs', 'snapshot'].every(key => /^[a-f0-9]{64}$/.test(result[key] || ''))) {
    throw new Error('Require --apply-live or --rehearse, both input paths, and the reviewed preview/input/snapshot SHA-256 values.');
  }
  return result;
}

async function applyInitialDefinitions(client, args) {
  const genres = readNames(args.genres, 'Genre', 45, 'genres');
  const vibes = readNames(args.vibes, 'Vibe', 40, 'vibes');
  assert.equal(inputFingerprint(args.genres, args.vibes), args.inputs, 'Supplied file bytes changed; no changes applied.');
  await client.query('BEGIN');
  try {
    await client.query("SET LOCAL lock_timeout='3s'; SET LOCAL statement_timeout='15s'; SET LOCAL idle_in_transaction_session_timeout='30s'");
    const { rows: [identity] } = await client.query(`SELECT current_database() AS database,
      d.datdba = role.oid AS owned, shobj_description(d.oid,'pg_database') AS marker
      FROM pg_database d JOIN pg_roles role ON role.rolname=current_user WHERE d.datname=current_database()`);
    if (!identity?.owned) throw new Error('Operation requires the database owner.');
    if (args.mode === '--apply-live') {
      if (identity.database !== 'chords') throw new Error('Live operation requires the explicit chords database.');
    } else {
      const run = process.env.CCM_VOCAB_RUN_ID;
      if (!/^[a-f0-9]{32}$/.test(run || '') || identity.database !== `ccm_vocab_${run}`
          || identity.marker !== `CCM initial vocabulary owned scratch ${run}`) {
        throw new Error('Rehearsal requires the exact owner-marked vocabulary scratch database.');
      }
    }
    // Follow management/assignment category/name order before table protection.
    // NOWAIT table locks refuse active writers and avoid table/row lock cycles.
    // Reads remain available. No retries can conceal a changed baseline.
    await lockTagDefinitionNames(client, 'Genre', genres);
    await lockTagDefinitionNames(client, 'Vibe', vibes);
    await client.query(`LOCK TABLE ${Object.keys(TABLE_ORDERS).map(table => `public.${table}`).join(',')}
      IN SHARE ROW EXCLUSIVE MODE NOWAIT`);
    const before = await captureSnapshot(client);
    assert.equal(before.hash, args.snapshot, 'Complete protected snapshot changed; no changes applied.');
    const report = [...await inspect(client, 'Genre', genres), ...await inspect(client, 'Vibe', vibes)];
    if (report.some(row => row.state === 'ambiguous')) throw new Error('Ambiguous normalized definitions; no changes applied.');
    assert.equal(fingerprint(report), args.preview, 'Reviewed preview changed; no changes applied.');
    const inserted = [];
    for (const item of report.filter(row => row.state === 'missing')) {
      const { rows: [row] } = await client.query(`INSERT INTO ${categoryTable(item.category)}(name) VALUES($1) RETURNING id,name`, [item.proposed]);
      assert.equal(row.name, item.proposed, 'Inserted spelling differs from the supplied name.');
      inserted.push({ category: item.category, ...row });
    }
    const after = await captureSnapshot(client);
    for (const table of Object.keys(before.tableHashes).filter(table => !['genres', 'vibes'].includes(table))) {
      assert.equal(after.tableHashes[table], before.tableHashes[table],
        `Unexpected protected row delta in ${table}; rolling back.`);
    }
    // Definition rows contain only integer IDs and names. Compare their intended delta separately.
    for (const [table, category] of [['genres', 'Genre'], ['vibes', 'Vibe']]) {
      const expected = [...before.data[table], ...inserted.filter(row => row.category === category)
        .map(({ id, name }) => ({ id, name }))].sort((a, b) => a.id - b.id);
      assert.deepEqual(after.data[table], expected, `Unexpected definition delta in ${table}; rolling back.`);
    }
    const finalReport = [...await inspect(client, 'Genre', genres), ...await inspect(client, 'Vibe', vibes)];
    if (finalReport.some(row => ['missing', 'ambiguous'].includes(row.state))) throw new Error('Post-insert lookup failed; rolling back.');
    await client.query('COMMIT');
    return { inserted, beforeSnapshot: before.hash, afterSnapshot: after.hash,
      fingerprint: fingerprint(finalReport), totals: { genres: after.data.genres.length, vibes: after.data.vibes.length } };
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  }
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL must be explicitly provided.');
  const pool = new Pool({ connectionString: process.env.DATABASE_URL, connectionTimeoutMillis: 5000 });
  try {
    const client = await pool.connect();
    try { process.stdout.write(`${JSON.stringify(await applyInitialDefinitions(client, args))}\n`); }
    finally { client.release(); }
  } finally { await pool.end(); }
}

module.exports = { applyInitialDefinitions, argumentsFrom, captureSnapshot, inputFingerprint };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
