import { expect, test as base } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { pool: testDatabase } = require('../../server/db.js');

async function removeUnreferencedFixtureTag(tag) {
  const match = /^([gv])(\d+)$/.exec(tag.id);
  if (!match) throw new Error(`Unexpected fixture tag id: ${tag.id}`);
  const table = match[1] === 'g' ? 'genres' : 'vibes';
  const junction = match[1] === 'g' ? 'song_genres' : 'song_vibes';
  const foreignKey = match[1] === 'g' ? 'genre_id' : 'vibe_id';
  const id = Number(match[2]);
  const deleted = await testDatabase.query(`DELETE FROM public.${table} t
    WHERE t.id=$1 AND t.name=$2
      AND NOT EXISTS (SELECT 1 FROM public.${junction} j WHERE j.${foreignKey}=t.id)
    RETURNING t.id`, [id, tag.name]);
  if (deleted.rowCount > 1) throw new Error(`Unexpected multi-row T09 tag cleanup for ${tag.id}`);
  const remaining = await testDatabase.query(`SELECT 1 FROM public.${table} WHERE id=$1`, [id]);
  if (remaining.rowCount) throw new Error(`T09 tag ${tag.id} remains or is still referenced`);
}

async function cachedSongAndQueue(page, songId) {
  return page.evaluate(async id => {
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('chart-manager', 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta'], 'readonly');
      const songRequest = transaction.objectStore('songs').get(id);
      const queueRequest = transaction.objectStore('meta').get('pending_mutations');
      transaction.oncomplete = () => resolve({ song: songRequest.result, queue: queueRequest.result?.value || [] });
      transaction.onerror = () => reject(transaction.error);
    });
  }, songId);
}

async function expectOfflineSaveCommitted(page, songId, title) {
  const chart = page.locator('.chart-view');
  await expect(chart).toBeVisible();
  await expect(chart.locator('.chart-topbar-title')).toHaveText(title);
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();

  const state = await cachedSongAndQueue(page, songId);
  expect(state.song).toBeTruthy();
  expect(state.song._sync?.operation).toBe('update');
  expect(state.song._sync?.genres_unchanged).toBe(false);
  expect(state.queue).toHaveLength(1);
  expect(state.queue[0]).toMatchObject({ entity: 'song', id: songId });
  expect(state.queue[0].local_revision).toBeGreaterThan(0);
  return state;
}

const test = base.extend({
  t09Fixture: [async ({ request, context }, use) => {
    const initialArtistIds = new Set((await testDatabase.query('SELECT id FROM public.artists')).rows.map(row => row.id));
    const initialReceiptIds = new Set((await testDatabase.query('SELECT operation_id FROM public.mutation_receipts')).rows.map(row => row.operation_id));
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
      const newReceipts = (await testDatabase.query('SELECT operation_id,resource_id FROM public.mutation_receipts'))
        .rows.filter(row => !initialReceiptIds.has(row.operation_id) && fixture.songIds.has(Number(row.resource_id)));
      for (const receipt of newReceipts) {
        const removed = await testDatabase.query('DELETE FROM public.mutation_receipts WHERE operation_id=$1 AND resource_id=$2 RETURNING operation_id',
          [receipt.operation_id, receipt.resource_id]);
        if (removed.rowCount !== 1) throw new Error(`T09 receipt ${receipt.operation_id} was not removed exactly`);
      }
      const remainingReceipts = (await testDatabase.query('SELECT operation_id,resource_id FROM public.mutation_receipts'))
        .rows.filter(row => !initialReceiptIds.has(row.operation_id) && fixture.songIds.has(Number(row.resource_id)));
      if (remainingReceipts.length) throw new Error('T09 task-created song receipts remain after cleanup');
      const fixtureArtists = (await testDatabase.query('SELECT id,name FROM public.artists ORDER BY id')).rows
        .filter(row => !initialArtistIds.has(row.id) && row.name.startsWith('T09 '));
      for (const artist of fixtureArtists) {
        const removed = await testDatabase.query(`DELETE FROM public.artists a WHERE a.id=$1 AND a.name=$2
          AND NOT EXISTS (SELECT 1 FROM public.songs s WHERE s.artist_id=a.id) RETURNING id`, [artist.id, artist.name]);
        if (removed.rowCount !== 1) throw new Error(`T09 artist ${artist.id} still has song references or changed`);
      }
    } catch (error) { errors.push(error); }

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
        await removeUnreferencedFixtureTag({ id, name: fixture.tagIds.get(id) });
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

test('FR-015 FR-057 FR-062 Genre and Vibe edits work offline and stale sync keeps the server version', async ({ page, request, context, t09Fixture }) => {
  const suffix = randomUUID();
  const title = `T09 synthetic tag check ${suffix}`;
  const onlineGenre = `T09 online genre ${suffix}`;
  const retainedGenre = `T09 retained genre ${suffix}`;
  const offlineExistingGenre = `T09 offline existing genre ${suffix}`;
  const offlineVibe = `T09 offline vibe ${suffix}`;
  const conflictVibe = `T09 discarded tablet vibe ${suffix}`;
  const serverVibe = `T09 server vibe ${suffix}`;
  const fixtureNames = new Set([onlineGenre, retainedGenre, offlineExistingGenre, offlineVibe,
    conflictVibe, serverVibe]);
  let songId;
  t09Fixture.tagNames = fixtureNames;
  for (const name of [onlineGenre, retainedGenre, offlineExistingGenre]) {
    const definition = await request.post('/api/tag-definitions', { data: { category: 'Genre', name } });
    expect(definition.status()).toBe(201);
  }
  for (const name of [offlineVibe, conflictVibe, serverVibe]) {
    const definition = await request.post('/api/tag-definitions', { data: { category: 'Vibe', name } });
    expect(definition.status()).toBe(201);
  }
  await t09Fixture.recordDefinitions(request);

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

    await page.getByRole('combobox', { name: 'Add Genre' }).fill(onlineGenre);
    await page.getByRole('option', { name: onlineGenre, exact: true }).click();
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
    await page.getByRole('button', { name: `Remove ${onlineGenre} from Genre` }).click();
    await page.getByRole('combobox', { name: 'Add Genre' }).fill(retainedGenre);
    await page.getByRole('option', { name: retainedGenre, exact: true }).click();
    await page.getByRole('combobox', { name: 'Add Genre' }).fill(offlineExistingGenre);
    await page.getByRole('option', { name: offlineExistingGenre, exact: true }).click();
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
    await page.getByRole('button', { name: `Remove ${offlineExistingGenre} from Genre` }).click();
    await saveAndWaitForChart();
    await goHome();
    await syncNow();
    await page.getByRole('button', { name: 'Songs' }).click();
    await context.setOffline(true);
    await openSongEditor();
    await expect(page.getByText(/changes saved locally/i)).toBeVisible();
    await page.getByRole('button', { name: `Remove ${retainedGenre} from Genre` }).click();
    await page.getByRole('combobox', { name: 'Add Genre' }).fill(offlineExistingGenre);
    await page.getByRole('option', { name: offlineExistingGenre, exact: true }).click();
    await page.getByRole('combobox', { name: 'Add Vibe' }).fill(offlineVibe);
    await page.getByRole('option', { name: offlineVibe, exact: true }).click();
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
    const serverChanged = await request.put(`/api/songs/${songId}`, {
      data: { vibes: [...beforeConflict.vibes, serverVibe], base_updated_at: beforeConflict.updated_at,
        operation_id: randomUUID() },
    });
    expect(serverChanged.ok()).toBeTruthy();
    t09Fixture.recordSong(await serverChanged.json());
    await t09Fixture.recordDefinitions(request);

    await context.setOffline(true);
    await page.getByRole('combobox', { name: 'Add Vibe' }).fill(conflictVibe);
    await page.getByRole('option', { name: conflictVibe, exact: true }).click();
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
});

test('FR-015 FR-017 EditView uses assigned chips and existing definitions with stable primary promotion and Cancel',
  async ({ page, request, t09Fixture }) => {
    const suffix = randomUUID();
    const primary = `T09 primary ${suffix}`;
    const secondary = `T09 secondary ${suffix}`;
    const shared = `T09 shared ${suffix}`;
    const vibeOnly = `T09 short Vibe ${suffix}`;
    const keyboardNames = Array.from({ length: 32 }, (_, index) => `T09 keyboard ${suffix} ${String(index).padStart(2, '0')}`);
    const title = `T09 assigned editor ${suffix}`;
    const fixtureNames = new Set([primary, secondary, shared, vibeOnly, ...keyboardNames]);
    t09Fixture.tagNames = fixtureNames;
    const createdDefinitions = [];
    for (const [category, name] of [
      ['Genre', primary], ['Genre', secondary], ['Genre', shared], ['Vibe', shared], ['Vibe', vibeOnly],
      ...keyboardNames.map(name => ['Genre', name]),
    ]) {
      const response = await request.post('/api/tag-definitions', { data: { category, name } });
      expect(response.status()).toBe(201);
      createdDefinitions.push({ category, name });
    }
    const created = await request.post('/api/songs', { data: { title, artist: `T09 artist ${suffix}`,
      genres: [{ name: primary, is_primary: true }, { name: secondary, is_primary: false }],
      vibes: [shared], chart_written_key: 'C', chart_source: 'Verse\nInvented assigned editor fixture' } });
    expect(created.status()).toBe(201);
    const song = await created.json();
    t09Fixture.songIds.add(song.id);
    t09Fixture.recordSong(song);
    await t09Fixture.recordDefinitions(request);

    await page.goto('/');
    await page.getByPlaceholder('Search songs, artists…').fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    const genreSection = page.locator('[aria-label="Genre tags"]');
    const vibeSection = page.locator('[aria-label="Vibe tags"]');
    await expect(genreSection.getByRole('button', { name: `Remove ${primary} from Genre` })).toBeVisible();
    await expect(genreSection.getByRole('button', { name: `Remove ${secondary} from Genre` })).toBeVisible();
    await expect(vibeSection.getByRole('button', { name: `Remove ${shared} from Vibe` })).toBeVisible();
    await expect(genreSection.getByRole('button', { name: `Remove ${shared} from Genre` })).toHaveCount(0);

    const writes = [];
    const onRequest = req => {
      if (['POST', 'PUT', 'DELETE'].includes(req.method()) && /\/api\/(songs|tag-definitions)/.test(req.url())) writes.push(req.url());
    };
    page.on('request', onRequest);
    const genreSearch = page.getByRole('combobox', { name: 'Add Genre' });
    const vibeSearch = page.getByRole('combobox', { name: 'Add Vibe' });
    const cacheBeforeCancel = await cachedSongAndQueue(page, song.id);
    await vibeSearch.fill(vibeOnly);
    await expect(page.getByRole('option', { name: vibeOnly, exact: true })).toBeVisible();
    await genreSearch.fill(shared.slice(0, 8));
    await expect(page.getByRole('option', { name: shared, exact: true })).toBeVisible();
    await genreSearch.press('ArrowDown');
    await expect(genreSearch).toHaveAttribute('aria-activedescendant', /song-genre-options-option-/);
    await genreSearch.fill(`T09 keyboard ${suffix}`);
    for (let index = 0; index < 24; index += 1) await genreSearch.press('ArrowDown');
    const listbox = page.getByRole('listbox', { name: 'Genres definitions' });
    const keyboardVisibility = await page.evaluate(() => {
      const input = document.querySelector('#song-add-genre');
      const activeId = input?.getAttribute('aria-activedescendant');
      const active = activeId ? document.getElementById(activeId) : null;
      const list = active?.parentElement;
      if (!active || !list) return null;
      const itemRect = active.getBoundingClientRect();
      const listRect = list.getBoundingClientRect();
      return { visible: itemRect.top >= listRect.top && itemRect.bottom <= listRect.bottom,
        scrollTop: list.scrollTop, pageY: window.scrollY };
    });
    expect(keyboardVisibility?.visible).toBe(true);
    expect(keyboardVisibility?.scrollTop).toBeGreaterThan(0);
    await vibeSearch.focus();
    await expect(vibeSearch).toHaveValue(vibeOnly);
    const activeDescendant = await vibeSearch.getAttribute('aria-activedescendant');
    if (activeDescendant) {
      const activeOption = page.locator(`#${activeDescendant}`);
      await expect(activeOption).toHaveAttribute('role', 'option');
      await expect(activeOption).toBeVisible();
    }
    else expect(activeDescendant).toBeNull();
    await expect(page.getByRole('option', { name: vibeOnly, exact: true })).toBeVisible();

    await genreSearch.focus();
    for (let index = 0; index < 24; index += 1) await genreSearch.press('ArrowDown');
    const pageYBefore = await page.evaluate(() => window.scrollY);
    const genreScrollBeforeUp = await page.locator('[aria-label="Genres definitions"]')
      .evaluate(list => list.scrollTop);
    for (let index = 0; index < 10; index += 1) await genreSearch.press('ArrowUp');
    const upwardState = await page.evaluate(() => {
      const input = document.querySelector('#song-add-genre');
      const active = document.getElementById(input?.getAttribute('aria-activedescendant'));
      const rect = active?.getBoundingClientRect();
      const listRect = active?.parentElement.getBoundingClientRect();
      return { focused: document.activeElement === input,
        visible: Boolean(rect && listRect && rect.top >= listRect.top && rect.bottom <= listRect.bottom),
        scrollTop: active?.parentElement.scrollTop, pageY: window.scrollY };
    });
    expect(upwardState.focused).toBe(true);
    expect(upwardState.visible).toBe(true);
    expect(upwardState.scrollTop).toBeLessThan(genreScrollBeforeUp);
    expect(upwardState.pageY).toBe(pageYBefore);

    await genreSearch.focus();
    await genreSearch.press('Escape');
    await expect(page.getByRole('listbox', { name: 'Genres definitions' })).toHaveCount(0);
    await genreSearch.fill(`unmatched ${suffix}`);
    await expect(page.getByText('No matching unassigned definitions.')).toBeVisible();
    await page.getByRole('button', { name: 'Back' }).click();
    page.off('request', onRequest);
    expect(writes).toEqual([]);
    const afterCancel = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(afterCancel.genres).toEqual(song.genres);
    expect(afterCancel.vibes).toEqual(song.vibes);
    expect(await cachedSongAndQueue(page, song.id)).toEqual(cacheBeforeCancel);

    await page.getByPlaceholder('Search songs, artists…').fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByRole('combobox', { name: 'Add Genre' })).toBeEnabled();
    const removePrimary = page.getByRole('button', { name: `Remove ${primary} from Genre` });
    await removePrimary.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Add Genre' })).toBeFocused();
    const genreInput = page.getByRole('combobox', { name: 'Add Genre' });
    await genreInput.fill(shared.slice(0, 8));
    await genreInput.press('ArrowDown');
    await genreInput.press('Enter');
    await expect(genreSection.getByRole('button', { name: `Remove ${shared} from Genre` })).toBeVisible();
    await genreInput.fill(primary.slice(0, 8));
    await page.getByRole('option', { name: primary, exact: true }).click();
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    let saved = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(saved.genres.find(tag => tag.is_primary)?.name).toBe(secondary);
    expect(saved.genres.map(tag => tag.name).sort()).toEqual([primary, secondary, shared].sort());
    expect(saved.vibes).toEqual([shared]);
    t09Fixture.recordSong(saved);

    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.locator('[aria-label="Genre tags"]')
      .getByRole('button', { name: new RegExp(`Remove ${primary} from Genre`) })).toBeVisible();
    saved = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(saved.genres.find(tag => tag.is_primary)?.name).toBe(secondary);

    for (const name of [secondary, primary, shared]) {
      await page.getByRole('button', { name: `Remove ${name} from Genre` }).click();
    }
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    saved = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(saved.genres).toEqual([]);
    expect(saved.primary_genre ?? saved.genre).toBeFalsy();

    await page.getByRole('button', { name: 'Edit' }).click();
    await page.getByRole('combobox', { name: 'Add Genre' }).fill(primary.slice(0, 8));
    await page.getByRole('option', { name: primary, exact: true }).click();
    const finalSave = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
      && response.request().method() === 'PUT');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await finalSave).status()).toBe(200);
    saved = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(saved.genres).toEqual([{ name: primary, is_primary: true }]);
  });

test('FR-017 offline export ordering promotes the first name-ordered secondary and re-add leaves it primary',
  async ({ page, request, context, t09Fixture }) => {
    const suffix = randomUUID();
    const primary = `T09 order primary ${suffix}`;
    const zeta = `T09 order Zeta ${suffix}`;
    const alpha = `T09 order Alpha ${suffix}`;
    const title = `T09 offline order ${suffix}`;
    t09Fixture.tagNames = new Set([primary, zeta, alpha]);
    for (const name of [primary, zeta, alpha]) {
      const response = await request.post('/api/tag-definitions', { data: { category: 'Genre', name } });
      expect(response.status()).toBe(201);
    }
    const created = await request.post('/api/songs', { data: { title, artist: `T09 order artist ${suffix}`,
      genres: [{ name: primary, is_primary: true }, { name: zeta }, { name: alpha }],
      chart_written_key: 'C', chart_source: 'Verse\nInvented offline ordering fixture' } });
    expect(created.status()).toBe(201);
    const song = await created.json();
    t09Fixture.songIds.add(song.id);
    t09Fixture.recordSong(song);
    await t09Fixture.recordDefinitions(request);
    const exportResponse = await request.get('/api/export');
    expect(exportResponse.status()).toBe(200);
    const exportedSong = (await exportResponse.json()).songs.find(item => item.id === song.id);
    expect(exportedSong).toBeTruthy();
    // Definitions were created primary, Zeta, Alpha: their ID and name orders differ.
    // This direct export assertion is independent of the browser's online song-detail GET.
    expect(exportedSong.genres.map(tag => tag.name)).toEqual([primary, alpha, zeta]);
    expect(exportedSong.genres.map(tag => tag.is_primary)).toEqual([true, false, false]);
    await page.goto('/');
    await page.getByRole('searchbox').fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByRole('button', { name: `Remove ${primary} from Genre` })).toBeVisible();
    await context.setOffline(true);
    await page.getByRole('button', { name: `Remove ${primary} from Genre` }).click();
    await page.getByRole('button', { name: 'Save changes' }).click();
    const promoted = await expectOfflineSaveCommitted(page, song.id, title);
    expect(promoted.song.genres.map(tag => tag.name)).toEqual([alpha, zeta]);
    expect(promoted.song.genres.find(tag => tag.is_primary)?.name).toBe(alpha);
    await page.getByRole('button', { name: 'Edit' }).click();
    const input = page.getByRole('combobox', { name: 'Add Genre' });
    await input.fill(primary);
    await page.getByRole('option', { name: primary, exact: true }).click();
    await page.getByRole('button', { name: 'Save changes' }).click();
    const afterReadd = await expectOfflineSaveCommitted(page, song.id, title);
    expect(afterReadd.song.genres.find(tag => tag.is_primary)?.name).toBe(alpha);
    expect(afterReadd.song.genres.map(tag => tag.name)).toEqual([alpha, zeta, primary]);
    expect(afterReadd.queue[0].local_revision).toBeGreaterThan(promoted.queue[0].local_revision);
    expect(afterReadd.song._sync.genres_unchanged).toBe(false);
    await context.setOffline(false);
  });

test('FR-015 FR-057 an offline metadata-only save queues no Genre or Vibe change and the push omits them', async ({ page, request, context, t09Fixture }) => {
  const suffix = randomUUID();
  const title = `T09 offline metadata ${suffix}`;
  const genre = `T09 offline kept genre ${suffix}`;
  const vibe = `T09 offline kept vibe ${suffix}`;
  t09Fixture.tagNames = new Set([genre, vibe]);
  for (const [category, name] of [['Genre', genre], ['Vibe', vibe]]) {
    expect((await request.post('/api/tag-definitions', { data: { category, name } })).status()).toBe(201);
  }
  const created = await request.post('/api/songs', { data: { title, artist: `T09 artist ${suffix}`,
    genres: [{ name: genre, is_primary: true }], vibes: [vibe], chart_source: 'Invented offline metadata chart',
    operation_id: randomUUID() } });
  expect(created.status()).toBe(201);
  const song = await created.json();
  t09Fixture.songIds.add(song.id);
  t09Fixture.recordSong(song);
  await t09Fixture.recordDefinitions(request);

  await page.goto('/');
  await page.getByPlaceholder('Search songs, artists…').fill(title);
  await page.getByText(title, { exact: true }).click();
  await page.getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('button', { name: `Remove ${genre} from Genre` })).toBeVisible();
  await context.setOffline(true);
  await page.getByLabel('Release year').fill('1991');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
  const queued = await cachedSongAndQueue(page, song.id);
  expect(queued.song._sync).toMatchObject({ operation: 'update', genres_unchanged: true, vibes_unchanged: true });

  const responsePromise = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
    && response.request().method() === 'PUT');
  await context.setOffline(false);
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON()).not.toHaveProperty('genres');
  expect(response.request().postDataJSON()).not.toHaveProperty('vibes');
  const saved = await (await request.get(`/api/songs/${song.id}`)).json();
  expect(saved.release_year).toBe(1991);
  expect(saved.genres).toEqual([{ name: genre, is_primary: true }]);
  expect(saved.vibes).toEqual([vibe]);
});

test('FR-015 definition load failure keeps assigned chips and permits metadata-only Save', async ({ page, request, t09Fixture }) => {
  const suffix = randomUUID();
  const title = `T09 failed definitions ${suffix}`;
  const genre = `T09 retained ${suffix}`;
  t09Fixture.tagNames = new Set([genre]);
  const definition = await request.post('/api/tag-definitions', { data: { category: 'Genre', name: genre } });
  expect(definition.status()).toBe(201);
  const created = await request.post('/api/songs', { data: { title, artist: `T09 artist ${suffix}`,
    genres: [{ name: genre, is_primary: true }], chart_source: 'Invented cache fallback chart' } });
  expect(created.status()).toBe(201);
  const song = await created.json();
  t09Fixture.songIds.add(song.id);
  t09Fixture.recordSong(song);
  await t09Fixture.recordDefinitions(request);
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      const method = init?.method || (typeof input === 'string' ? 'GET' : input.method);
      if (url.pathname === '/api/tag-definitions' && method === 'GET') {
        return Promise.resolve(new Response('{"error":"controlled definitions failure"}', {
          status: 503, headers: { 'content-type': 'application/json' },
        }));
      }
      return originalFetch(input, init);
    };
  });
  await page.goto('/');
  await page.getByPlaceholder('Search songs, artists…').fill(title);
  await page.getByText(title, { exact: true }).click();
  await page.getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('button', { name: `Remove ${genre} from Genre` })).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: /no saved definitions are available/i }).first()).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Add Genre' })).toBeDisabled();
  await page.getByLabel('Release year').fill('1987');
  const responsePromise = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
    && response.request().method() === 'PUT');
  await page.getByRole('button', { name: 'Save changes' }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON()).not.toHaveProperty('genres');
  expect(response.request().postDataJSON()).not.toHaveProperty('vibes');
  const saved = await (await request.get(`/api/songs/${song.id}`)).json();
  expect(saved.genres).toEqual([{ name: genre, is_primary: true }]);
  expect(saved.release_year).toBe(1987);
});

test('FR-015 managed definition options can be selected by touch', async ({ browser, request, t09Fixture }) => {
  const suffix = randomUUID();
  const genre = `T09 touch Genre ${suffix}`;
  const title = `T09 touch picker ${suffix}`;
  t09Fixture.tagNames = new Set([genre]);
  const definition = await request.post('/api/tag-definitions', { data: { category: 'Genre', name: genre } });
  expect(definition.status()).toBe(201);
  const created = await request.post('/api/songs', { data: { title, artist: `T09 artist ${suffix}`,
    chart_source: 'Invented touch picker fixture' } });
  expect(created.status()).toBe(201);
  const song = await created.json();
  t09Fixture.songIds.add(song.id);
  t09Fixture.recordSong(song);
  await t09Fixture.recordDefinitions(request);
  const touchContext = await browser.newContext({ baseURL: process.env.E2E_BASE_URL, hasTouch: true });
  try {
    const touchPage = await touchContext.newPage();
    await touchPage.goto('/');
    await touchPage.getByPlaceholder('Search songs, artists…').fill(title);
    await touchPage.getByText(title, { exact: true }).click();
    await touchPage.getByRole('button', { name: 'Edit' }).click();
    const input = touchPage.getByRole('combobox', { name: 'Add Genre' });
    await expect(input).toBeEnabled();
    await input.fill(genre.slice(0, 8));
    await touchPage.getByRole('option', { name: genre, exact: true }).tap();
    await expect(touchPage.getByRole('button', { name: `Remove ${genre} from Genre` })).toBeVisible();
    const responsePromise = touchPage.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
      && response.request().method() === 'PUT');
    await touchPage.getByRole('button', { name: 'Save changes' }).click();
    expect((await responsePromise).status()).toBe(200);
    const saved = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(saved.genres).toEqual([{ name: genre, is_primary: true }]);
  } finally {
    await touchContext.close();
  }
});

test('FR-016 FR-059 a rejected offline create is corrected in EditView and creates exactly one server song',
  async ({ page, request, context, t09Fixture }) => {
    const suffix = randomUUID();
    const staleGenre = `T09 removed Genre ${suffix}`;
    const validGenre = `T09 valid Genre ${suffix}`;
    const retainedVibe = `T09 retained Vibe ${suffix}`;
    const title = `T09 corrected create ${suffix}`;
    t09Fixture.tagNames = new Set([staleGenre, validGenre, retainedVibe]);
    const definitions = [];
    for (const [category, name] of [['Genre', staleGenre], ['Genre', validGenre], ['Vibe', retainedVibe]]) {
      const response = await request.post('/api/tag-definitions', { data: { category, name } });
      expect(response.status()).toBe(201);
      definitions.push({ category, id: (await response.json()).id, name });
    }
    await t09Fixture.recordDefinitions(request);

    let localId;
    try {
      await page.goto('/');
      await page.getByRole('button', { name: 'Add a new song' }).click();
      await expect(page.getByRole('combobox', { name: 'Add Genre' })).toBeEnabled();
      await context.setOffline(true);
      await page.locator('.form-group').filter({ hasText: 'Title' }).locator('input').fill(title);
      await page.locator('.form-group').filter({ hasText: 'Artist' }).locator('input').fill(`T09 artist ${suffix}`);
      await page.getByRole('combobox', { name: 'Add Genre' }).fill(staleGenre.slice(0, 8));
      await page.getByRole('option', { name: staleGenre, exact: true }).click();
      await page.getByRole('combobox', { name: 'Add Vibe' }).fill(retainedVibe.slice(0, 8));
      await page.getByRole('option', { name: retainedVibe, exact: true }).click();
      const chart = `Invented corrected create chart ${suffix}`;
      await page.locator('.form-textarea').fill(chart);
      await page.getByRole('button', { name: 'Save changes' }).click();
      await expect(page.locator('.chart-topbar-title')).toHaveText(title);
      const queuedCreate = await page.evaluate(async songTitle => {
        const db = await new Promise((resolve, reject) => {
          const opening = indexedDB.open('chart-manager', 1);
          opening.onsuccess = () => resolve(opening.result);
          opening.onerror = () => reject(opening.error);
        });
        const result = await new Promise((resolve, reject) => {
          const transaction = db.transaction(['songs', 'meta']);
          const songs = transaction.objectStore('songs').getAll();
          const queue = transaction.objectStore('meta').get('pending_mutations');
          transaction.oncomplete = () => {
            const song = songs.result.find(value => value.title === songTitle);
            resolve({ id: song?.id, song, operation: queue.result?.value?.find(item => String(item.id) === String(song?.id)) });
          };
          transaction.onerror = () => reject(transaction.error);
        });
        db.close();
        return result;
      }, title);
      localId = queuedCreate.id;
      expect(String(localId)).toMatch(/^local-/);
      expect(queuedCreate.operation).toMatchObject({ entity: 'song', id: localId });
      const originalOperationId = queuedCreate.operation.op_id;
      expect(queuedCreate.song).toMatchObject({ title, artist: `T09 artist ${suffix}`,
        genres: [{ name: staleGenre, is_primary: true }], vibes: [retainedVibe], chart_source: chart });

      const removed = definitions.find(item => item.name === staleGenre);
      const deletion = await request.delete(`/api/tag-definitions/Genre/${removed.id}`, {
        data: { mode: 'remove', expected_song_count: 0 },
      });
      expect(deletion.status()).toBe(200);
      const rejectedResponse = page.waitForResponse(response => response.url().endsWith('/api/songs')
        && response.request().method() === 'POST' && response.status() === 422);
      await context.setOffline(false);
      const rejection = await rejectedResponse;
      expect(await rejection.json()).toMatchObject({ code: 'UNKNOWN_TAG_DEFINITION', category: 'Genre' });
      const rejectedQueueState = await page.evaluate(async id => {
        const db = await new Promise((resolve, reject) => {
          const opening = indexedDB.open('chart-manager', 1);
          opening.onsuccess = () => resolve(opening.result);
          opening.onerror = () => reject(opening.error);
        });
        const result = await new Promise((resolve, reject) => {
          const transaction = db.transaction(['songs', 'meta']);
          const song = transaction.objectStore('songs').get(id);
          const queue = transaction.objectStore('meta').get('pending_mutations');
          transaction.oncomplete = () => resolve({ song: song.result,
            operation: queue.result?.value?.find(item => String(item.id) === String(id)) });
          transaction.onerror = () => reject(transaction.error);
        });
        db.close();
        return result;
      }, localId);
      expect(rejectedQueueState.operation).toMatchObject({ op_id: originalOperationId,
        rejected_code: 'UNKNOWN_TAG_DEFINITION', rejected_category: 'Genre',
        sent_payload: expect.objectContaining({ chart_source: chart, vibes: [retainedVibe] }) });
      expect(rejectedQueueState.song).toMatchObject({ id: localId, title, chart_source: chart,
        vibes: [retainedVibe] });

      await page.getByRole('button', { name: 'Edit' }).click();
      await page.getByRole('button', { name: `Remove ${staleGenre} from Genre` }).click();
      await page.getByRole('combobox', { name: 'Add Genre' }).fill(validGenre.slice(0, 8));
      await page.getByRole('option', { name: validGenre, exact: true }).click();
      const successfulPost = page.waitForRequest(requestValue => requestValue.url().endsWith('/api/songs')
        && requestValue.method() === 'POST');
      const successResponse = page.waitForResponse(response => response.url().endsWith('/api/songs')
        && response.request().method() === 'POST' && response.status() === 201);
      await page.getByRole('button', { name: 'Save changes' }).click();
      const sentRequest = await successfulPost;
      const sentOperationId = sentRequest.postDataJSON().operation_id;
      expect(sentOperationId).not.toBe(originalOperationId);
      const createdResponse = await successResponse;
      const serverSong = await createdResponse.json();
      expect(serverSong).toMatchObject({ title, artist: `T09 artist ${suffix}`,
        chart_source: chart, genres: [{ name: validGenre, is_primary: true }], vibes: [retainedVibe] });
      const matching = await request.get('/api/songs');
      expect(matching.ok()).toBeTruthy();
      const exactSongs = (await matching.json()).songs.filter(song => song.title === title);
      expect(exactSongs).toHaveLength(1);
      expect(exactSongs[0].id).toBe(serverSong.id);
      t09Fixture.songIds.add(serverSong.id);
      t09Fixture.recordSong(serverSong);
      await expect(page.locator('.chart-topbar-title')).toHaveText(title);
    } finally {
      await context.setOffline(false).catch(() => {});
      const matching = await request.get('/api/songs');
      if (matching.ok()) {
        for (const song of (await matching.json()).songs.filter(item => item.title === title)) {
          t09Fixture.songIds.add(song.id);
          t09Fixture.recordSong(song);
        }
      }
    }
  });

test('FR-011 a delayed editor save cannot override Back or a newer editor session', async ({ page, request, t09Fixture }) => {
  const title = `T09 navigation race ${randomUUID()}`;
  const sample = await request.get('/api/songs');
  expect(sample.ok()).toBeTruthy();
  const artist = (await sample.json()).songs.find(song => song.title === 'Already Gone')?.artist;
  expect(artist).toBeTruthy();
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
