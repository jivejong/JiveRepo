#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Pool } = require('../server/node_modules/pg');
const { categoryTable, definitionKey, normalizeDefinitionName } = require('../server/tagDefinitionNames');

function argumentsFrom(argv) {
  const result = { apply: false, genres: null, vibes: null, fingerprint: null };
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === '--apply') result.apply = true;
    else if (item === '--genres') result.genres = argv[++index];
    else if (item === '--vibes') result.vibes = argv[++index];
    else if (item === '--expected-fingerprint') result.fingerprint = argv[++index];
    else throw new Error(`Unknown argument: ${item}`);
  }
  return result;
}

function readNames(file, category, expected, option) {
  if (!file) throw new Error(`Pass --${option} <file>.`);
  const names = fs.readFileSync(path.resolve(file), 'utf8').split(/\r?\n/)
    .map(value => value.trim()).filter(Boolean)
    .map(value => normalizeDefinitionName(value, category));
  if (names.length !== expected) throw new Error(`${category} list must contain ${expected} names; found ${names.length}.`);
  const seen = new Set();
  for (const name of names) {
    const key = definitionKey(name);
    if (seen.has(key)) throw new Error(`${category} input contains a normalized duplicate: ${name}`);
    seen.add(key);
  }
  return names;
}

async function inspect(client, category, names) {
  const table = categoryTable(category);
  const existing = (await client.query(`SELECT id,name FROM ${table} ORDER BY id`)).rows;
  return names.map(name => {
    const matches = existing.filter(row => definitionKey(row.name) === definitionKey(name));
    if (matches.length > 1) return { category, proposed: name, state: 'ambiguous', matches };
    if (!matches.length) return { category, proposed: name, state: 'missing', matches: [] };
    return { category, proposed: name, state: matches[0].name === name ? 'existing' : 'spelling_collision', matches };
  });
}

function fingerprint(report) {
  return crypto.createHash('sha256').update(JSON.stringify(report)).digest('hex');
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const genres = readNames(args.genres, 'Genre', 45, 'genres');
  const vibes = readNames(args.vibes, 'Vibe', 40, 'vibes');
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  const client = await pool.connect();
  try {
    if (args.apply) {
      if (!args.fingerprint) throw new Error('--apply requires --expected-fingerprint from the reviewed preview.');
      if (process.env.CCM_T11_ISOLATED !== '1' || !process.env.CCM_T11_RUN_ID) {
        throw new Error('Apply is restricted to the owner-guarded T11 scratch database.');
      }
      const identity = await client.query(`SELECT current_database() AS database,
        current_user AS owner, d.datdba, owner.oid AS current_user_id,
        shobj_description(d.oid,'pg_database') AS marker
        FROM pg_database d JOIN pg_roles owner ON owner.rolname=current_user
        WHERE d.datname=current_database()`);
      const marker = `CCM T11 owned scratch run ${process.env.CCM_T11_RUN_ID}`;
      if (identity.rows[0]?.database !== 'ccm_test' || identity.rows[0]?.datdba !== identity.rows[0]?.current_user_id
          || identity.rows[0]?.marker !== marker) {
        throw new Error('Apply target does not match the owner-marked ccm_test scratch database.');
      }
      await client.query('BEGIN ISOLATION LEVEL SERIALIZABLE');
      await client.query("SELECT pg_advisory_xact_lock(hashtext('ccm-populate-tag-definitions'))");
      const report = [...await inspect(client, 'Genre', genres), ...await inspect(client, 'Vibe', vibes)];
      if (report.some(row => row.state === 'ambiguous')) throw new Error('Ambiguous normalized definitions found; no changes applied.');
      if (fingerprint(report) !== args.fingerprint) throw new Error('Preview fingerprint changed; no changes applied.');
      for (const item of report.filter(row => row.state === 'missing')) {
        await client.query(`INSERT INTO ${categoryTable(item.category)}(name) VALUES($1)`, [item.proposed]);
      }
      const after = [...await inspect(client, 'Genre', genres), ...await inspect(client, 'Vibe', vibes)];
      if (after.some(row => ['missing', 'ambiguous'].includes(row.state))) throw new Error('Post-insert verification failed.');
      await client.query('COMMIT');
      process.stdout.write(`${JSON.stringify({ applied: report.filter(row => row.state === 'missing').length,
        fingerprint: fingerprint(after), report: after })}\n`);
      return;
    }

    const report = [...await inspect(client, 'Genre', genres), ...await inspect(client, 'Vibe', vibes)];
    const result = { fingerprint: fingerprint(report), report,
      counts: Object.fromEntries(['existing', 'missing', 'spelling_collision', 'ambiguous']
        .map(state => [state, report.filter(item => item.state === state).length])) };
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (report.some(row => row.state === 'ambiguous')) process.exitCode = 2;
  } catch (error) {
    try { await client.query('ROLLBACK'); } catch { /* no open transaction */ }
    throw error;
  } finally {
    client.release();
    await pool.end();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
