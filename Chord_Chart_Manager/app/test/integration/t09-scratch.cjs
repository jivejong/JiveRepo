// Run in the app container with this file on stdin after initializing
// ccm_tagfix through the migration runner. It never connects to chords.
const assert = require('node:assert/strict');
const { setTimeout: delay } = require('node:timers/promises');

const url = new URL(process.env.DATABASE_URL);
url.pathname = '/ccm_tagfix';
process.env.DATABASE_URL = url.toString();
const app = require('/app/server/server');
const { pool } = require('/app/server/db');
const { runMigrations } = require('/app/server/migrate');
const tokenSql = `to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

async function main() {
  assert.deepEqual(await runMigrations(), ['0001', '0002']); // Later migration skips baseline adoption validation.
  const server = app.listen(0);
  const base = `http://127.0.0.1:${server.address().port}`;
  const api = async (method, path, body) => {
    const response = await fetch(base + path, {
      method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  };
  try {
    const created = await api('POST', '/api/songs', {
      title: 'T09 invented scratch song', artist: 'T09 invented scratch artist',
      chart_source: 'Verse\nInvented test line',
    });
    assert.equal(created.status, 201);
    const id = created.body.id;
    const read = async () => (await api('GET', `/api/songs/${id}`)).body;
    const snapshot = async name => {
      const result = await pool.query(`SELECT
        (SELECT to_jsonb(s) FROM public.songs s WHERE id=$1) AS song,
        (SELECT jsonb_agg(to_jsonb(sg) ORDER BY sg.genre_id) FROM public.song_genres sg WHERE song_id=$1) AS genres,
        (SELECT jsonb_agg(to_jsonb(sv) ORDER BY sv.vibe_id) FROM public.song_vibes sv WHERE song_id=$1) AS vibes,
        (SELECT count(*)::int FROM public.genres WHERE name=$2) AS genre_definitions,
        (SELECT count(*)::int FROM public.vibes WHERE name=$2) AS vibe_definitions`, [id, name]);
      return result.rows[0];
    };
    let current = await read();
    assert.match(current.updated_at, /\.\d{6}Z$/);

    for (let i = 0; i < 20; i += 1) {
      const updated = await api('PUT', `/api/songs/${id}`, {
        title: `T09 invented scratch song ${i}`, base_updated_at: current.updated_at,
      });
      assert.equal(updated.status, 200);
      assert.ok(updated.body.updated_at > current.updated_at, 'rapid version must advance');
      current = updated.body;
    }
    const rejectedName = 'T09 rejected definition';
    const beforeRejected = await snapshot(rejectedName);
    const legacy = current.updated_at.replace(/\.(\d{3})\d{3}Z$/, '.$1Z');
    assert.equal((await api('PUT', `/api/songs/${id}`, {
      title: 'Rejected legacy edit', genres: [{ name: rejectedName }], base_updated_at: legacy,
    })).status, 409);
    assert.equal((await api('PUT', `/api/songs/${id}`, {
      title: 'Rejected stale edit', genres: [{ name: rejectedName }],
      base_updated_at: created.body.updated_at,
    })).status, 409);
    assert.equal((await api('DELETE', `/api/songs/${id}`, { base_updated_at: legacy })).status, 409);
    assert.equal((await api('DELETE', `/api/songs/${id}`, { base_updated_at: '' })).status, 400);
    assert.deepEqual(await snapshot(rejectedName), beforeRejected);
    assert.equal((await api('POST', `/api/songs/${id}/tags`, {
      name: rejectedName, category: 'Genre',
    })).status, 400);
    assert.equal((await api('POST', `/api/songs/${id}/tags`, {
      name: rejectedName, category: 'Genre', base_updated_at: 'invalid',
    })).status, 400);
    assert.equal((await api('POST', `/api/songs/${id}/tags`, {
      name: rejectedName, category: 'Genre', base_updated_at: legacy,
    })).status, 409);
    assert.deepEqual(await snapshot(rejectedName), beforeRejected);

    const shared = 'T09 scratch shared';
    let changed = await api('POST', `/api/songs/${id}/tags`, {
      name: shared, category: 'Genre', base_updated_at: current.updated_at,
    });
    assert.equal(changed.status, 200);
    assert.ok(changed.body.updated_at > current.updated_at);
    current = changed.body;
    changed = await api('POST', `/api/songs/${id}/tags`, {
      name: shared, category: 'Feel', base_updated_at: current.updated_at,
    });
    assert.equal(changed.status, 200);
    assert.ok(changed.body.updated_at > current.updated_at);
    current = changed.body;
    const beforeStale = await snapshot(rejectedName);
    assert.equal((await api('POST', `/api/songs/${id}/tags`, {
      name: rejectedName, category: 'Genre', base_updated_at: created.body.updated_at,
    })).status, 409);
    assert.equal((await api('DELETE', `/api/songs/${id}/tags/${encodeURIComponent(shared)}`, {
      category: 'Genre', base_updated_at: legacy,
    })).status, 409);
    assert.equal((await api('DELETE', `/api/songs/${id}/tags/${encodeURIComponent(shared)}`, {
      base_updated_at: current.updated_at,
    })).status, 400);
    assert.equal((await api('DELETE', `/api/songs/${id}/tags/${encodeURIComponent(shared)}`, {
      category: 'Genre', base_updated_at: 'bad-token',
    })).status, 400);
    assert.deepEqual(await snapshot(rejectedName), beforeStale);
    changed = await api('DELETE', `/api/songs/${id}/tags/${encodeURIComponent(shared)}`, {
      category: 'Genre', base_updated_at: current.updated_at,
    });
    assert.equal(changed.status, 200);
    assert.equal(changed.body.genres.some(tag => tag.name === shared), false);
    assert.equal(changed.body.vibes.includes(shared), true);
    assert.ok(changed.body.updated_at > current.updated_at);
    current = changed.body;
    const definitions = (await api('GET', '/api/tags')).body;
    assert.equal(definitions.filter(tag => tag.name === shared).length, 2);

    const full = await api('PUT', `/api/songs/${id}`, {
      genres: [{ name: 'T09 scratch primary', is_primary: true },
        { name: shared, is_primary: false }], vibes: [], release_year: 2003,
      base_updated_at: current.updated_at,
    });
    assert.equal(full.status, 200);
    current = full.body;
    const filter = async selected => {
      const params = new URLSearchParams({ tag_filters: JSON.stringify(selected) });
      const response = await api('GET', `/api/songs?${params}`);
      assert.equal(response.status, 200);
      return response.body.songs.some(song => song.id === id);
    };
    assert.equal(await filter([{ category: 'Genre', name: shared }]), true);
    assert.equal(await filter([{ category: 'Feel', name: shared }]), false);
    assert.equal(await filter([{ category: 'Genre', name: shared }, { category: 'Era', name: '2000s' }]), true);
    assert.equal((await api('GET', `/api/songs?tags=${encodeURIComponent(shared)}`)).body.songs.some(song => song.id === id), true);

    const first = await pool.connect();
    const second = await pool.connect();
    try {
      await first.query('BEGIN');
      const a = (await first.query(`UPDATE public.songs SET title=title WHERE id=$1 RETURNING ${tokenSql} AS version`, [id])).rows[0].version;
      await second.query('BEGIN');
      let finished = false;
      const waiting = second.query(`UPDATE public.songs SET title=title WHERE id=$1 RETURNING ${tokenSql} AS version`, [id])
        .then(result => { finished = true; return result.rows[0].version; });
      await delay(100);
      assert.equal(finished, false, 'second writer must wait for first row lock');
      await first.query('COMMIT');
      const b = await waiting;
      await second.query('COMMIT');
      assert.ok(b > a, 'lock-wait writer must advance exact version');
    } finally {
      if (!first.released) { await first.query('ROLLBACK').catch(() => {}); first.release(); }
      if (!second.released) { await second.query('ROLLBACK').catch(() => {}); second.release(); }
    }
    console.log('scratch: later migration rerun, 20 rapid writes, two lock-wait writers, stale/legacy/malformed tokens, category removal, definitions, and online filters passed');
  } finally {
    await new Promise(resolve => server.close(resolve));
    await pool.end();
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
