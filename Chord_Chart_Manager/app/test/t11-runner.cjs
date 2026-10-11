// T11's complete test command owns one disposable database and never points
// browser teardown at the live chords database.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { Pool } = require('../server/node_modules/pg');
const { bounded, runCleanupStages, startServerBounded, runWithCleanup } = require('./t11-lifecycle.cjs');
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

function fingerprintRows(rows) {
  return rows.map(row => {
    const data = { song: row.song, artist: row.artist, genres: row.genres,
      vibes: row.vibes, entries: row.entries };
    return { id: row.id, version: row.version,
      sha256: crypto.createHash('sha256').update(JSON.stringify(data)).digest('hex') };
  });
}

async function importedSnapshot(pool) {
  const { rows } = await pool.query(`
    SELECT s.id, to_char(s.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS version,
      s.source_document, s.chart_source, to_jsonb(s) AS song, to_jsonb(a) AS artist,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('join',to_jsonb(sg),'definition',to_jsonb(g)) ORDER BY sg.genre_id)
        FROM public.song_genres sg JOIN public.genres g ON g.id=sg.genre_id WHERE sg.song_id=s.id),'[]'::jsonb) AS genres,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('join',to_jsonb(sv),'definition',to_jsonb(v)) ORDER BY sv.vibe_id)
        FROM public.song_vibes sv JOIN public.vibes v ON v.id=sv.vibe_id WHERE sv.song_id=s.id),'[]'::jsonb) AS vibes,
      COALESCE((SELECT jsonb_agg(to_jsonb(ss) ORDER BY ss.setlist_id,ss.position)
        FROM public.setlist_songs ss WHERE ss.song_id=s.id),'[]'::jsonb) AS entries
    FROM public.songs s JOIN public.artists a ON a.id=s.artist_id
    WHERE s.id BETWEEN 29 AND 36 ORDER BY s.id`);
  assert.deepEqual(rows.map(row => row.id), [29,30,31,32,33,34,35,36],
    'T11 requires the eight imported sample records to exist before testing');
  for (const row of rows) {
    assert.equal(row.source_document, 'Sample.docx');
    assert.equal(row.chart_source, null);
  }
  return fingerprintRows(rows);
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
  const baseUrl = databaseUrl(local, 'chords');
  const adminUrl = new URL(baseUrl);
  adminUrl.pathname = '/postgres';
  const admin = new Pool({ connectionString: adminUrl.toString() });
  const main = new Pool({ connectionString: baseUrl });
  let ownsScratch = false;
  let testPool;
  let appServer;
  let testServer;
  let acceptingRequests = false;
  let runId;
  let importedBefore;
  let mainCountsBefore;
  await runWithCleanup(async () => {
    const preflight = await admin.query(`SELECT
      EXISTS(SELECT 1 FROM pg_database WHERE datname=$1) AS exists,
      (SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user) AS can_create`, [scratchName]);
    assert.equal(preflight.rows[0].exists, false,
      'Refusing T11 because task-owned ccm_test already exists; inspect its state before cleanup');
    assert.equal(preflight.rows[0].can_create, true,
      'T11 database role cannot create ccm_test; grant task authorization/prerequisites before running');
    const initialStats = await fetch('http://127.0.0.1:3000/api/stats', { signal: AbortSignal.timeout(5_000) });
    assert.equal(initialStats.status, 200, 'Main app /api/stats must be available before T11 starts');
    mainCountsBefore = readMainCounts(await initialStats.json());
    importedBefore = await importedSnapshot(main);
    await admin.query(`CREATE DATABASE ${scratchName}`);
    ownsScratch = true;

    process.env.DATABASE_URL = databaseUrl(local, scratchName);
    const { runMigrations } = require('../server/migrate.js');
    const applied = await runMigrations({ database: scratchName });
    assert.deepEqual(applied, ['0001', '0002', '0003']);
    testPool = require('../server/db.js').pool;
    await seedSyntheticChartFixtures(testPool);
    const app = require('../server/server.js');
    runId = randomUUID();
    testServer = http.createServer(async (req, res) => {
      if (req.url === '/__t11_identity' && req.method === 'GET') {
        try {
          const requestedRun = req.headers['x-ccm-t11-run'];
          const database = (await testPool.query('SELECT current_database() AS name')).rows[0].name;
          if (requestedRun !== runId || database !== scratchName) {
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
      if (!acceptingRequests) {
        res.writeHead(503).end();
        return;
      }
      app(req, res);
    });
    appServer = testServer;
    await startServerBounded(appServer, () => appServer.listen(0, '127.0.0.1'));
    acceptingRequests = true;
    const testEnv = { ...process.env, E2E_BASE_URL: `http://127.0.0.1:${appServer.address().port}`,
      E2E_SYSTEM_CHROME: '1', CCM_T11_ISOLATED: '1', CCM_T11_RUN_ID: runId, CI: '1' };
    await verifyHarnessIdentity({ baseUrl: testEnv.E2E_BASE_URL, runId });
    process.env.E2E_BASE_URL = testEnv.E2E_BASE_URL;
    process.env.E2E_SYSTEM_CHROME = '1';
    process.env.CCM_T11_ISOLATED = '1';
    await runNode('Unit tests (Vitest)', path.join(appDir, 'node_modules/vitest/vitest.mjs'), ['run'], testEnv);
    await runNode('Scratch-database API integration tests', path.join(appDir, 'test/integration/t11-api.test.cjs'), [], testEnv);
    const e2eArgs = ['test', '--workers=1'];
    if (process.env.CCM_T11_E2E_GREP) {
      e2eArgs.push('--grep', process.env.CCM_T11_E2E_GREP);
      console.log('[T11] Applying requested guarded E2E name filter.');
    }
    await runNode('Serial Playwright E2E (installed system Chrome)', path.join(appDir, 'node_modules/@playwright/test/cli.js'),
      e2eArgs, testEnv);
  }, async () => {
    const cleanup = [];
    const stages = [];
    if (appServer) stages.push(['HTTP server shutdown', async () => {
      acceptingRequests = false;
      const closed = new Promise((resolve, reject) => {
        if (!appServer.listening) { resolve(); return; }
        appServer.close(error => error ? reject(error) : resolve());
      });
      await bounded(closed, 'HTTP server shutdown', 5_000, () => appServer.closeAllConnections?.());
    }]);
    if (testPool) stages.push(['test pool shutdown', () => bounded(() => testPool.end(), 'test pool shutdown', 5_000)]);
    if (ownsScratch) stages.push(['owned scratch cleanup', async () => {
        await admin.query(`SELECT pg_terminate_backend(pid) FROM pg_stat_activity
          WHERE datname=$1 AND pid<>pg_backend_pid()`, [scratchName]);
        const connected = await admin.query('SELECT count(*)::int AS n FROM pg_stat_activity WHERE datname=$1', [scratchName]);
        assert.equal(connected.rows[0].n, 0, 'Refusing to drop ccm_test while database sessions remain.');
        await admin.query(`DROP DATABASE ${scratchName}`);
        const remaining = await admin.query('SELECT 1 FROM pg_database WHERE datname=$1', [scratchName]);
        assert.equal(remaining.rowCount, 0, 'Task-owned ccm_test remains after T11 cleanup');
        console.log('[T11] ccm_test dropped and absence verified.');
    }]);
    if (importedBefore) {
      stages.push(['imported-row preservation check', async () => {
        const after = await importedSnapshot(main);
        assert.deepEqual(after, importedBefore,
          'T11 must preserve every imported song row, exact version, and association');
        console.log('[T11] Imported rows 29–36, exact versions, and associations match.');
      }]);
      stages.push(['main health and baseline check', async () => {
      const health = await fetch('http://localhost:3000/healthz', { signal: AbortSignal.timeout(5_000) });
      assert.equal(health.status, 200, 'Main app /healthz must remain healthy');
      assert.equal((await health.json()).status, 'ok');
      const statsResponse = await fetch('http://localhost:3000/api/stats', { signal: AbortSignal.timeout(5_000) });
      assert.equal(statsResponse.status, 200, 'Main app /api/stats must remain available');
      const statsAfter = readMainCounts(await statsResponse.json());
      assertMainCountsUnchanged(mainCountsBefore, statsAfter);
      console.log(`[T11] Main /healthz healthy; /api/stats counts unchanged (${statsAfter.totalSongs} songs / ${statsAfter.totalTags} tags / ${statsAfter.totalSetlists} setlists).`);
      }]);
    }
    stages.push(['main fingerprint pool shutdown', () => bounded(() => main.end(), 'main pool shutdown', 5_000)]);
    stages.push(['admin pool shutdown', () => bounded(() => admin.end(), 'admin pool shutdown', 5_000)]);
    try { await runCleanupStages(stages); } catch (error) { cleanup.push(error); }
    if (cleanup.length) throw cleanup.length === 1 ? cleanup[0] : new AggregateError(cleanup, 'T11 cleanup failed');
  });
}

main().catch(error => {
  console.error(`T11 test harness failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});
