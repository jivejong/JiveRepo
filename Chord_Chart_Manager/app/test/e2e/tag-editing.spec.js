import { expect, test as base } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import path from 'node:path';

function removeUnreferencedFixtureTag(tag) {
  const match = /^([gv])(\d+)$/.exec(tag.id);
  if (!match) throw new Error(`Unexpected fixture tag id: ${tag.id}`);
  const table = match[1] === 'g' ? 'genres' : 'vibes';
  const junction = match[1] === 'g' ? 'song_genres' : 'song_vibes';
  const foreignKey = match[1] === 'g' ? 'genre_id' : 'vibe_id';
  const sql = `DELETE FROM public.${table} t WHERE t.id = ${Number(match[2])}
    AND NOT EXISTS (SELECT 1 FROM public.${junction} j WHERE j.${foreignKey} = t.id)`;
  execFileSync('docker', [
    'compose', '-f', path.resolve('docker-compose.yml'), 'exec', '-T', '-u', 'postgres',
    'db', 'sh', '-c', 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', 'sh', sql,
  ], { stdio: 'pipe', timeout: 20_000 });
}

const test = base.extend({
  t09Fixture: [async ({ request, context }, use) => {
    const initialResponse = await request.get('/api/tags');
    if (!initialResponse.ok()) throw new Error('Could not snapshot tag IDs before T09 fixture creation');
    const initialTagIds = new Set((await initialResponse.json()).map(tag => tag.id));
    const fixture = {
      initialTagIds,
      songIds: new Set(),
      tagIds: new Map(),
      tagNames: new Set(),
      recordSong(song) {
        for (const tag of [...(song.genres || []), ...(song.vibes || [])]) {
          if (tag.id && this.tagNames.has(tag.name) && !initialTagIds.has(tag.id)) {
            this.tagIds.set(tag.id, tag.name);
          }
        }
      },
      async recordDefinitions(request) {
        const response = await request.get('/api/tags');
        if (!response.ok()) throw new Error('Could not track T09 fixture tag IDs');
        for (const tag of await response.json()) {
          if (this.tagNames.has(tag.name) && !initialTagIds.has(tag.id)) {
            this.tagIds.set(tag.id, tag.name);
          }
        }
      },
    };
    await use(fixture);

    const errors = [];
    try { await context.setOffline(false); } catch (error) { errors.push(error); }
    for (const id of fixture.songIds) {
      try {
        const current = await request.get(`/api/songs/${id}`);
        if (current.status() === 404) continue;
        if (!current.ok()) throw new Error(`Could not inspect owned T09 song ${id}: HTTP ${current.status()}`);
        const song = await current.json();
        fixture.recordSong(song);
        const deleted = await request.delete(`/api/songs/${id}`, {
          data: { base_updated_at: song.updated_at },
        });
        if (!deleted.ok()) throw new Error(`Could not delete owned T09 song ${id}: HTTP ${deleted.status()}`);
        if ((await request.get(`/api/songs/${id}`)).status() !== 404) {
          throw new Error(`Owned T09 song ${id} still exists after cleanup`);
        }
      } catch (error) { errors.push(error); }
    }

    try {
      const tagsResponse = await request.get('/api/tags');
      if (!tagsResponse.ok()) throw new Error(`Could not inspect T09 tag definitions: HTTP ${tagsResponse.status()}`);
      const tags = await tagsResponse.json();
      for (const tag of tags) {
        if (fixture.tagNames.has(tag.name) && !initialTagIds.has(tag.id)) fixture.tagIds.set(tag.id, tag.name);
      }
      for (const [id] of fixture.tagIds) {
        const match = /^([gv])(\d+)$/.exec(id);
        if (!match || initialTagIds.has(id)) throw new Error(`Refusing to remove non-owned T09 tag ID ${id}`);
        removeUnreferencedFixtureTag({ id });
      }
      const remainingResponse = await request.get('/api/tags');
      if (!remainingResponse.ok()) throw new Error(`Could not verify T09 tag cleanup: HTTP ${remainingResponse.status()}`);
      const remainingIds = new Set((await remainingResponse.json()).map(tag => tag.id));
      for (const [id, name] of fixture.tagIds) {
        if (remainingIds.has(id)) throw new Error(`Owned T09 tag ${name} (${id}) remains or is still referenced`);
      }
    } catch (error) { errors.push(error); }
    if (errors.length) throw new AggregateError(errors, 'T09 timeout-safe fixture cleanup failed');
  }, { timeout: 60_000 }],
});

test('Genre and Vibe edits work offline and stale sync keeps the server version', async ({ page, request, context, t09Fixture }) => {
  const suffix = randomUUID();
  const title = `T09 synthetic tag check ${suffix}`;
  const onlineGenre = `T09 online genre ${suffix}`;
  const retainedGenre = `T09 retained genre ${suffix}`;
  const offlineExistingGenre = `T09 offline existing genre ${suffix}`;
  const offlineVibe = `T09 offline vibe ${suffix}`;
  const conflictVibe = `T09 discarded tablet vibe ${suffix}`;
  const serverVibe = `T09 server vibe ${suffix}`;
  const legacyVibe = `T09 rejected legacy vibe ${suffix}`;
  const fixtureNames = new Set([onlineGenre, retainedGenre, offlineExistingGenre, offlineVibe,
    conflictVibe, serverVibe, legacyVibe]);
  let songId;
  t09Fixture.tagNames = fixtureNames;

  const apiSong = async () => {
    const response = await request.get(`/api/songs/${songId}`);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const goHome = async () => {
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
  };
  const openSongEditor = async () => {
    await page.getByPlaceholder('Search songs, artists…').fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
  };
  const syncNow = async () => {
    const settings = page.getByRole('button', { name: 'Settings' });
    await expect(settings).toBeVisible();
    await settings.click();
    const button = page.getByRole('button', { name: 'Sync now' });
    await expect(button).toBeEnabled();
    const exportResponse = page.waitForResponse(response =>
      response.url().endsWith('/api/export') && response.status() === 200);
    await button.click();
    await exportResponse;
    await expect(button).toBeEnabled();
  };
  const saveAndWaitForChart = async () => {
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save changes' })).toHaveCount(0);
  };

  const created = await request.post('/api/songs', {
      data: {
        title, artist: 'T09 invented test artist', chart_written_key: 'C',
        chart_source: 'Verse\nInvented test line', release_year: null,
      },
    });
    expect(created.ok()).toBeTruthy();
  songId = (await created.json()).id;
  t09Fixture.songIds.add(songId);

    await page.goto('/');
    await syncNow();
    await page.getByRole('button', { name: 'Songs' }).click();
    await openSongEditor();

    await page.getByPlaceholder('Add a genre').fill(onlineGenre);
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByLabel('Release year').fill('1977');
    await expect(page.getByLabel('Era (derived)')).toHaveValue('1970s');
    await expect(page.getByLabel('Era (derived)')).toHaveAttribute('readonly', '');
    await saveAndWaitForChart();
    await expect.poll(async () => (await apiSong()).genres?.some(tag => tag.name === onlineGenre)).toBeTruthy();
    t09Fixture.recordSong(await apiSong());
    await t09Fixture.recordDefinitions(request);
    if (process.env.T13A_FORCE_TIMEOUT === '1') await page.waitForTimeout(60_000);
    if (process.env.T09_FORCE_ASSERTION_FAILURE === '1') {
      throw new Error('T09 expected fixture-cleanup probe failure');
    }

    await goHome();
    await syncNow();
    await page.getByRole('button', { name: 'Songs' }).click();
    await openSongEditor();
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: onlineGenre, exact: true }).click();
    await page.getByPlaceholder('Add a genre').fill(retainedGenre);
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByPlaceholder('Add a genre').fill(offlineExistingGenre);
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: 'Add', exact: true }).click();
    await saveAndWaitForChart();
    await expect.poll(async () => {
      const song = await apiSong();
      return !song.genres.some(tag => tag.name === onlineGenre)
        && song.genres.some(tag => tag.name === retainedGenre);
    }).toBeTruthy();
    t09Fixture.recordSong(await apiSong());
    await t09Fixture.recordDefinitions(request);

    await goHome();
    await syncNow();
    await page.getByRole('button', { name: 'Songs' }).click();
    await openSongEditor();
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: offlineExistingGenre, exact: true }).click();
    await saveAndWaitForChart();
    await goHome();
    await syncNow();
    await page.getByRole('button', { name: 'Songs' }).click();
    await context.setOffline(true);
    await openSongEditor();
    await expect(page.getByText(/changes saved locally/i)).toBeVisible();
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: retainedGenre, exact: true }).click();
    await page.locator('section[aria-label="Genre tags"]').getByRole('button', { name: offlineExistingGenre, exact: true }).click();
    await page.getByPlaceholder('Add a vibe').fill(offlineVibe);
    await page.locator('section[aria-label="Vibes tags"]').getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByLabel('Release year').fill('2003');
    await expect(page.getByLabel('Era (derived)')).toHaveValue('2000s');
    await saveAndWaitForChart();
    await goHome();
    await page.getByRole('button', { name: 'Filter by tags' }).click();
    await expect(page.locator('.tag-panel').getByRole('button', { name: offlineVibe })).toBeVisible();
    await page.locator('.tag-panel').getByRole('button', { name: offlineVibe }).click();
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    await context.setOffline(false);
    await expect.poll(async () => (await apiSong()).vibes?.includes(offlineVibe)).toBeTruthy();
    t09Fixture.recordSong(await apiSong());
    await t09Fixture.recordDefinitions(request);
    expect((await apiSong()).genres.some(tag => tag.name === retainedGenre)).toBeFalsy();
    expect((await apiSong()).genres.some(tag => tag.name === onlineGenre)).toBeFalsy();
    expect((await apiSong()).genres.some(tag => tag.name === offlineExistingGenre)).toBeTruthy();
    expect((await apiSong()).era).toBe('2000s');

    await syncNow();
    const beforeConflict = await apiSong();
    await page.getByRole('button', { name: 'Songs' }).click();
    await openSongEditor();
    const serverChanged = await request.post(`/api/songs/${songId}/tags`, {
      data: { name: serverVibe, category: 'Feel', base_updated_at: beforeConflict.updated_at },
    });
    expect(serverChanged.ok()).toBeTruthy();
    t09Fixture.recordSong(await serverChanged.json());
    await t09Fixture.recordDefinitions(request);

    await context.setOffline(true);
    await page.getByPlaceholder('Add a vibe').fill(conflictVibe);
    await page.locator('section[aria-label="Vibes tags"]').getByRole('button', { name: 'Add' }).click();
    await saveAndWaitForChart();
    await goHome();
    await context.setOffline(false);
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByText(/tablet change was replaced/i)).toBeVisible();
    await expect.poll(async () => (await apiSong()).vibes?.includes(serverVibe)).toBeTruthy();
    const finalSong = await apiSong();
    expect(finalSong.vibes).not.toContain(conflictVibe);
    expect(finalSong.vibes).toContain(offlineVibe);
    expect(finalSong.updated_at > beforeConflict.updated_at).toBeTruthy();

    await syncNow();
    const legacyToken = finalSong.updated_at.replace(/\.(\d{3})\d{3}Z$/, '.$1Z');
    await page.evaluate(async ({ id, token }) => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open('chart-manager', 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      await new Promise((resolve, reject) => {
        const transaction = db.transaction('songs', 'readwrite');
        const store = transaction.objectStore('songs');
        const get = store.get(id);
        get.onsuccess = () => {
          if (!get.result) { reject(new Error('Cached fixture song missing')); return; }
          store.put({ ...get.result, updated_at: token });
        };
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error);
      });
      db.close();
    }, { id: songId, token: legacyToken });
    await page.getByRole('button', { name: 'Songs' }).click();
    await context.setOffline(true);
    await openSongEditor();
    await page.getByPlaceholder('Add a vibe').fill(legacyVibe);
    await page.locator('section[aria-label="Vibes tags"]').getByRole('button', { name: 'Add' }).click();
    await saveAndWaitForChart();
    await goHome();
    const queued = await page.evaluate(async id => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open('chart-manager', 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const read = store => new Promise((resolve, reject) => {
        const request = db.transaction(store).objectStore(store).get(store === 'songs' ? id : 'dirty_songs');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const song = await read('songs');
      const dirty = await read('meta');
      db.close();
      return { base: song?._sync?.base_updated_at, dirty: dirty?.value?.includes(id) };
    }, songId);
    expect(queued).toEqual({ base: legacyToken, dirty: true });
    const legacyConflict = page.waitForResponse(response =>
      response.url().endsWith(`/api/songs/${songId}`)
        && response.request().method() === 'PUT' && response.status() === 409);
    await context.setOffline(false);
    await legacyConflict;
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByText(/tablet change was replaced/i)).toBeVisible();
    const authoritative = await apiSong();
    expect(authoritative.vibes).not.toContain(legacyVibe);
    await expect.poll(async () => page.evaluate(async id => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open('chart-manager', 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const song = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').get(id);
        get.onsuccess = () => resolve(get.result);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return { updated_at: song?.updated_at, vibes: song?.vibes, pending: !!song?._sync };
    }, songId)).toEqual({ updated_at: authoritative.updated_at, vibes: authoritative.vibes, pending: false });
});

test('a delayed editor save cannot override Back or a newer editor session', async ({ page, request, t09Fixture }) => {
  const title = `T09 navigation race ${randomUUID()}`;
  const sample = await request.get('/api/songs/29');
  expect(sample.ok()).toBeTruthy();
  const artist = (await sample.json()).artist;
  const created = await request.post('/api/songs', {
    data: { title, artist, chart_written_key: 'C', chart_source: 'Verse\nInvented race fixture', release_year: null },
  });
  expect(created.ok()).toBeTruthy();
  const songId = (await created.json()).id;
  t09Fixture.songIds.add(songId);

  await page.addInitScript(id => {
    const originalFetch = window.fetch.bind(window);
    const waiting = [];
    window.__t09SaveStarted = [false, false];
    window.__t09ReleaseSave = [null, null];
    window.__t09ReleaseSave.forEach((_, index) => {
      waiting[index] = new Promise(resolve => { window.__t09ReleaseSave[index] = resolve; });
    });
    let nextGate = 0;
    window.fetch = async (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      const method = init?.method || (typeof input === 'string' ? 'GET' : input.method);
      if (url.pathname === `/api/songs/${id}` && method === 'PUT' && nextGate < waiting.length) {
        const index = nextGate++;
        window.__t09SaveStarted[index] = true;
        await waiting[index];
      }
      return originalFetch(input, init);
    };
  }, songId);

  try {
    await page.goto('/');
    const openEditor = async () => {
      await page.locator('input[type="search"]').fill(title);
      await page.getByText(title, { exact: true }).click();
      await page.getByRole('button', { name: 'Edit' }).click();
      await expect(page.locator('.form-group input.form-input').first()).toHaveValue(title);
    };
    const waitForPersistedYear = year => expect.poll(async () => {
      const response = await request.get(`/api/songs/${songId}`);
      return response.ok() ? (await response.json()).release_year : null;
    }).toBe(year);

    await openEditor();
    await page.getByLabel('Release year').fill('1984');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await page.waitForFunction(() => window.__t09SaveStarted?.[0] === true);
    await page.getByRole('button', { name: 'Back' }).click();
    const settings = page.getByRole('button', { name: 'Settings' });
    await expect(settings).toBeVisible();
    await settings.click();
    const firstResponse = page.waitForResponse(response =>
      response.url().endsWith(`/api/songs/${songId}`) && response.request().method() === 'PUT');
    await page.evaluate(() => window.__t09ReleaseSave[0]());
    expect((await firstResponse).status()).toBe(200);
    await waitForPersistedYear(1984);
    await expect(page.getByRole('button', { name: 'Sync now' })).toBeVisible();

    await page.getByRole('button', { name: 'Songs' }).click();
    await openEditor();
    await page.getByLabel('Release year').fill('1985');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await page.waitForFunction(() => window.__t09SaveStarted?.[1] === true);
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(settings).toBeVisible();
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.locator('input[type="search"]').fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByRole('button', { name: 'Save changes' })).toBeVisible();
    await expect(page.locator('.form-group input.form-input').first()).toHaveValue(title);
    const secondResponse = page.waitForResponse(response =>
      response.url().endsWith(`/api/songs/${songId}`) && response.request().method() === 'PUT');
    await page.evaluate(() => window.__t09ReleaseSave[1]());
    expect((await secondResponse).status()).toBe(200);
    await waitForPersistedYear(1985);
    await expect(page.getByRole('button', { name: 'Save changes' })).toBeVisible();
    await expect(page.locator('.form-group input.form-input').first()).toHaveValue(title);

    await page.getByRole('button', { name: 'Back' }).click();
    await expect(settings).toBeVisible();
    await settings.click();
    await expect(page.getByRole('button', { name: 'Sync now' })).toBeVisible();
  } finally {
    if (!page.isClosed()) {
      await page.evaluate(() => window.__t09ReleaseSave?.forEach(release => release?.()));
    }
  }
});
