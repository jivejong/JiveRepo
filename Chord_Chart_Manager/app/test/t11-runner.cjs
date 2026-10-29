// T11's complete test command owns one disposable database and never points
// browser teardown at the live chords database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { Pool } = require('../server/node_modules/pg');
const { verifyHarnessIdentity } = require('./t11-guards.cjs');
const { readMainCounts, assertMainCountsUnchanged } = require('./t11-main-checks.cjs');

const appDir = path.resolve(__dirname, '..');
const scratchName = 'ccm_test';

function readLocalEnv() {
  const file = path.join(appDir, '.env');
  if (!fs.existsSync(file)) return {};
  return Object.fromEntries(fs.readFileSync(file, 'utf8').split(/\r?\n/)
    .filter(line => line && !line.trimStart().startsWith('#') && line.includes('='))
    .map(line => {
      const index = line.indexOf('=');
      let value = line.slice(index + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      return [line.slice(0, index).trim(), value];
    }));
}

function databaseUrl(env, database) {
  const url = new URL('postgresql://placeholder:placeholder@127.0.0.1:15432/chords');
  url.username = env.POSTGRES_USER || 'chords';
  url.password = env.POSTGRES_PASSWORD || '';
  url.port = env.DB_PORT || '15432';
  url.pathname = `/${database}`;
  return url.toString();
}

function runNode(label, script, args = [], env = process.env, timeoutMs = 600_000) {
  console.log(`\n[T11] ${label}`);
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], { cwd: appDir, env, stdio: 'inherit' });
    let timer;
    let killTimer;
    let timedOut = false;
    const settle = (error) => {
      clearTimeout(timer);
      clearTimeout(killTimer);
      error ? reject(error) : resolve();
    };
    timer = setTimeout(() => {
      timedOut = true;
      child.kill();
      killTimer = setTimeout(() => child.kill('SIGKILL'), 2_000);
    }, timeoutMs);
    child.once('error', error => settle(new Error(`${label} could not start: ${error.message}`)));
    child.once('exit', (code, signal) => {
      if (timedOut) return settle(new Error(`${label} exceeded its ${timeoutMs} ms subprocess budget.`));
      settle(code === 0 ? null : new Error(`${label} failed with ${signal || `exit code ${code}`}`));
    });
  });
}

async function seedSyntheticChartFixtures(pool) {
  const artist = await pool.query(
    `INSERT INTO public.artists(name) VALUES ('T11 synthetic sample artist') RETURNING id`);
  const artistId = artist.rows[0].id;
  const charts = [
    { title: 'Bye Bye Love', chart: { sections: [{ label: 'Invented Verse', lines: [
      { type: 'lyric', text: 'Invented opening line for alignment', confidence: 'high',
        chords: [{ sym: 'G', pos: 0 }, { sym: 'D', pos: 0 }, { sym: 'C', pos: 2 }] },
      { type: 'spacer' },
      { type: 'lyric', text: 'Invented middle line for scrolling', confidence: 'high', chords: [{ sym: 'C', pos: 4 }] },
      { type: 'spacer' },
      { type: 'lyric', text: 'Invented final line for transpose', confidence: 'high', chords: [{ sym: 'G', pos: 2 }] },
      ...Array.from({ length: 42 }, (_, index) => ({ type: 'lyric',
        text: `Invented auto-scroll filler line ${index + 1}`, confidence: 'high',
        chords: [{ sym: index % 2 ? 'C' : 'G', pos: 0 }] })),
    ] }] } },
    { title: 'Already Gone', chart: { sections: [{ label: 'Invented Verse', lines: [
      { type: 'lyric', text: 'Invented line used only as a test artist source', confidence: 'high', chords: [{ sym: 'C', pos: 4 }] },
    ] }] } },
  ];
  const inserted = [];
  for (const fixture of charts) {
    const result = await pool.query(`INSERT INTO public.songs
      (title,artist_id,performance_key,chart_content,chart_source)
      VALUES($1,$2,'C',$3::jsonb,NULL) RETURNING id`,
    [fixture.title, artistId, JSON.stringify(fixture.chart)]);
    inserted.push(result.rows[0].id);
  }
  return inserted;
}

async function main() {
  const local = readLocalEnv();
  assert.equal(local.POSTGRES_DB || 'chords', 'chords',
    'Refusing to run T11 unless app/.env identifies the protected main database as chords');
  const admin = new Pool({ connectionString: databaseUrl(local, 'postgres') });
  const failures = [];
  let ownsScratch = false;
  let testPool;
  let server;
  let countsBefore;
  try {
    const preflight = await admin.query(`SELECT
      EXISTS(SELECT 1 FROM pg_database WHERE datname=$1) AS exists,
      (SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user) AS can_create`, [scratchName]);
    assert.equal(preflight.rows[0].exists, false,
      'Refusing T11 because ccm_test already exists; inspect its state before cleanup');
    assert.equal(preflight.rows[0].can_create, true,
      'T11 database role cannot create ccm_test; grant the prerequisites before running');
    const initialStats = await fetch('http://127.0.0.1:3000/api/stats', { signal: AbortSignal.timeout(5_000) });
    assert.equal(initialStats.status, 200, 'Main app /api/stats must be available before T11 starts');
    countsBefore = readMainCounts(await initialStats.json());
    const runId = randomUUID();
    await admin.query(`CREATE DATABASE ${scratchName}`);
    ownsScratch = true;

    process.env.DATABASE_URL = databaseUrl(local, scratchName);
    const { runMigrations } = require('../server/migrate.js');
    const applied = await runMigrations({ database: scratchName });
    assert.deepEqual(applied, ['0001', '0002', '0003', '0004']);
    testPool = require('../server/db.js').pool;
    await seedSyntheticChartFixtures(testPool);
    const app = require('../server/server.js');
    server = http.createServer(async (req, res) => {
      if (req.url === '/__t11_identity' && req.method === 'GET') {
        try {
          const database = (await testPool.query('SELECT current_database() AS name')).rows[0].name;
          if (req.headers['x-ccm-t11-run'] !== runId || database !== scratchName) {
            res.writeHead(404).end();
            return;
          }
          res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
          res.end(JSON.stringify({ runId, database }));
        } catch {
          res.writeHead(503).end();
        }
        return;
      }
      app(req, res);
    });
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const testEnv = { ...process.env, E2E_BASE_URL: `http://127.0.0.1:${server.address().port}`,
      E2E_SYSTEM_CHROME: '1', CCM_T11_ISOLATED: '1', CCM_T11_RUN_ID: runId, CI: '1' };
    await verifyHarnessIdentity({ baseUrl: testEnv.E2E_BASE_URL, runId });
    if (process.env.CCM_T11_FOCUSED_E2E_ONLY !== '1') {
      await runNode('Unit tests (Vitest)', path.join(appDir, 'node_modules/vitest/vitest.mjs'), ['run'], testEnv);
      await runNode('Scratch-database API integration tests', path.join(appDir, 'test/integration/t11-api.test.cjs'), [], testEnv);
    } else {
      assert.ok(process.env.CCM_T11_E2E_GREP, 'Focused E2E requires an explicit test-name filter');
      console.log('[T11] Focused E2E only; unit and API suites skipped by request.');
    }
    const e2eArgs = ['test', '--workers=1'];
    if (process.env.CCM_T11_E2E_GREP) {
      e2eArgs.push('--grep', process.env.CCM_T11_E2E_GREP);
      console.log('[T11] Applying requested guarded E2E name filter.');
    }
    await runNode('Serial Playwright E2E (installed system Chrome)', path.join(appDir, 'node_modules/@playwright/test/cli.js'),
      e2eArgs, testEnv);
  } catch (error) {
    failures.push(error);
  }

  // Every cleanup step runs even when an earlier step or the tests failed.
  const step = async (label, action) => {
    try { await action(); } catch (error) { failures.push(new Error(`${label}: ${error.message}`, { cause: error })); }
  };
  if (server?.listening) {
    await step('HTTP server shutdown', () => new Promise((resolve, reject) => {
      server.close(error => (error ? reject(error) : resolve()));
      server.closeAllConnections();
    }));
  }
  if (testPool) await step('test pool shutdown', () => testPool.end());
  if (ownsScratch) {
    await step('ccm_test cleanup', async () => {
      await admin.query(`SELECT pg_terminate_backend(pid) FROM pg_stat_activity
        WHERE datname=$1 AND pid<>pg_backend_pid()`, [scratchName]);
      await admin.query(`DROP DATABASE ${scratchName}`);
      const remaining = await admin.query('SELECT 1 FROM pg_database WHERE datname=$1', [scratchName]);
      assert.equal(remaining.rowCount, 0, 'ccm_test remains after T11 cleanup');
      console.log('[T11] ccm_test dropped and absence verified.');
    });
  }
  if (countsBefore) {
    await step('main health and count check', async () => {
      const health = await fetch('http://localhost:3000/healthz', { signal: AbortSignal.timeout(5_000) });
      assert.equal(health.status, 200, 'Main app /healthz must remain healthy');
      assert.equal((await health.json()).status, 'ok');
      const statsResponse = await fetch('http://localhost:3000/api/stats', { signal: AbortSignal.timeout(5_000) });
      assert.equal(statsResponse.status, 200, 'Main app /api/stats must remain available');
      const statsAfter = readMainCounts(await statsResponse.json());
      assertMainCountsUnchanged(countsBefore, statsAfter);
      console.log(`[T11] Main /healthz healthy; /api/stats counts unchanged (${statsAfter.totalSongs} songs / ${statsAfter.totalTags} tags / ${statsAfter.totalSetlists} setlists).`);
    });
  }
  await step('admin pool shutdown', () => admin.end());
  if (failures.length) throw failures.length === 1 ? failures[0] : new AggregateError(failures, 'T11 failed');
}

main().catch(error => {
  console.error(`T11 test harness failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
