import { randomUUID } from 'node:crypto';
import { expect, request as playwrightRequest, test } from '@playwright/test';

test('combined category filters work from cache offline and chart swipe follows filtered title order', async ({ page, context, request }) => {
  const suffix = randomUUID();
  const prefix = `T18 filter ${suffix}`;
  const shared = `T18 secondary ${suffix}`;
  const primary = `T18 primary ${suffix}`;
  const vibeA = `T18 calm ${suffix}`;
  const vibeB = `T18 bright ${suffix}`;
  const fixtures = [
    { title: `${prefix} Alpha`, genres: [{ name: primary, is_primary: true }, { name: shared }], vibes: [vibeA], release_year: 1979 },
    { title: `${prefix} Bravo`, genres: [{ name: shared, is_primary: true }], vibes: [vibeB], release_year: 1975 },
    { title: `${prefix} Charlie`, genres: [{ name: shared, is_primary: true }], vibes: [vibeA], release_year: 1980 },
    { title: `${prefix} Delta`, genres: [{ name: `T18 excluded ${suffix}`, is_primary: true }], vibes: [vibeA], release_year: 1970 },
  ];
  const ids = [];
  const cleanupRequest = await playwrightRequest.newContext({ baseURL: process.env.E2E_BASE_URL });
  let primaryError = null;
  try {
    for (const fixture of fixtures) {
      const response = await request.post('/api/songs', { data: {
        ...fixture, artist: `T18 test artist ${suffix}`, chart_written_key: 'C', original_key: 'C',
        chart_source: 'Verse\nInvented filter fixture line',
      } });
      if (response.status() !== 201) throw new Error(`T18 fixture create failed with HTTP ${response.status()}.`);
      const song = await response.json();
      if (!Number.isInteger(song.id)) throw new Error('T18 fixture create returned no server ID.');
      ids.push(song.id);
    }

    await page.goto('/');
    const syncButton = page.getByRole('button', { name: 'Settings' });
    await syncButton.click();
    const sync = page.getByRole('button', { name: 'Sync now' });
    const exportResponse = page.waitForResponse(response => response.url().endsWith('/api/export')
      && response.request().method() === 'GET' && response.status() === 200);
    await sync.click();
    await exportResponse;
    await expect.poll(async () => page.evaluate(async fixtureIds => {
      const opening = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error); });
      const songs = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').getAll();
        get.onsuccess = () => resolve(get.result); get.onerror = () => reject(get.error);
      });
      db.close();
      const cached = new Set(songs.map(song => Number(song.id)));
      return fixtureIds.every(id => cached.has(id));
    }, ids)).toBe(true);
    await page.getByRole('button', { name: 'Songs' }).click();

    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row')).toHaveCount(4);
    await page.getByRole('button', { name: 'Filter by tags' }).click();
    const panel = page.locator('.tag-panel');
    for (const name of [primary, shared]) await panel.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    for (const name of [vibeA, vibeB]) await panel.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    // TagPanel's fixed leading order is Genre, Vibe, Era, independent of counts.
    const eraCategory = panel.locator('.tag-category').nth(2);
    await eraCategory.locator('.tag-chip').filter({ hasText: /^1970s/ }).click();
    await eraCategory.locator('.tag-chip').filter({ hasText: /^1980s/ }).click();
    const rows = page.locator('.song-row');
    await expect(rows).toHaveCount(3);
    await expect(page.getByText(`${prefix} Delta`, { exact: true })).toHaveCount(0);

    // The category's All action clears Genre only; Vibe and Era still constrain results.
    await panel.getByRole('button', { name: 'All Genre' }).click();
    await expect(rows).toHaveCount(4);
    await expect(page.getByText(`${prefix} Delta`, { exact: true })).toBeVisible();
    for (const name of [primary, shared]) await panel.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    await expect(rows).toHaveCount(3);
    await panel.getByRole('button', { name: 'Clear all' }).click();
    await expect(rows).toHaveCount(4);
    await expect(panel.locator('.active-tags-bar')).toHaveCount(0);
    for (const name of [primary, shared]) await panel.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    for (const name of [vibeA, vibeB]) await panel.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    await eraCategory.locator('.tag-chip').filter({ hasText: /^1970s/ }).click();
    await eraCategory.locator('.tag-chip').filter({ hasText: /^1980s/ }).click();
    await expect(rows).toHaveCount(3);

    await page.getByLabel('Sort songs by').selectOption('title');
    await page.getByRole('button', { name: 'Sort descending' }).click();
    await expect(rows.nth(0)).toContainText('Charlie');
    await expect(rows.nth(1)).toContainText('Bravo');
    await expect(rows.nth(2)).toContainText('Alpha');

    await context.setOffline(true);
    await expect.poll(async () => page.locator('.list-section-head').innerText()).toMatch(/3 songs/i);
    await expect(rows.nth(0)).toContainText('Charlie');
    await rows.nth(0).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(`${prefix} Charlie`);
    await page.locator('.chart-scroll').evaluate(element => {
      const dispatchTouch = (type, point, points) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        Object.defineProperties(event, { touches: { value: points }, changedTouches: { value: [point] } });
        element.dispatchEvent(event);
      };
      const start = { identifier: 1, clientX: 240, clientY: 320 };
      const end = { identifier: 1, clientX: 130, clientY: 322 };
      dispatchTouch('touchstart', start, [start]);
      dispatchTouch('touchend', end, []);
    });
    await expect(page.locator('.chart-topbar-title')).toHaveText(`${prefix} Bravo`);
  } catch (error) {
    primaryError = error;
  } finally {
    await context.setOffline(false).catch(error => { primaryError ||= error; });
    const cleanupErrors = [];
    for (const id of ids) {
      try {
        const current = await cleanupRequest.get(`/api/songs/${id}`);
        if (current.status() === 404) continue;
        if (!current.ok()) throw new Error(`Could not inspect owned T18 song ${id}: HTTP ${current.status()}.`);
        const song = await current.json();
        const deleted = await cleanupRequest.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
        if (!deleted.ok()) throw new Error(`Could not delete owned T18 song ${id}: HTTP ${deleted.status()}.`);
        if ((await cleanupRequest.get(`/api/songs/${id}`)).status() !== 404) throw new Error(`Owned T18 song ${id} remains after cleanup.`);
      } catch (error) { cleanupErrors.push(error); }
    }
    await cleanupRequest.dispose().catch(error => { cleanupErrors.push(error); });
    if (cleanupErrors.length) {
      const cleanup = new AggregateError(cleanupErrors, 'T18 exact-ID fixture cleanup failed.');
      if (primaryError) throw new AggregateError([primaryError, cleanup], 'T18 browser test and cleanup failed.');
      throw cleanup;
    }
  }
  if (primaryError) throw primaryError;
});

test('online filters overlay pending create, edit, and delete projections until acknowledgement', async ({ page, context, request }) => {
  const suffix = randomUUID();
  const prefix = `T18 pending ${suffix}`;
  const target = `T18 pending target ${suffix}`;
  const old = `T18 pending old ${suffix}`;
  const fixtures = [
    { title: `${prefix} enters`, genres: [{ name: old, is_primary: true }], vibes: ['Calm'], release_year: 1978 },
    { title: `${prefix} leaves`, genres: [{ name: target, is_primary: true }], vibes: ['Calm'], release_year: 1978 },
    { title: `${prefix} deleted`, genres: [{ name: target, is_primary: true }], vibes: ['Calm'], release_year: 1978 },
  ];
  const ids = [];
  const cleanupRequest = await playwrightRequest.newContext({ baseURL: process.env.E2E_BASE_URL });
  let primaryError = null;
  try {
    for (const fixture of fixtures) {
      const response = await request.post('/api/songs', { data: { ...fixture, artist: `${prefix} artist`,
        chart_written_key: 'C', original_key: 'C', chart_source: 'Verse\nInvented pending filter fixture' } });
      expect(response.status()).toBe(201);
      ids.push((await response.json()).id);
    }
    await page.goto('/');
    await page.getByRole('button', { name: 'Settings' }).click();
    const sync = page.getByRole('button', { name: 'Sync now' });
    const initialExport = page.waitForResponse(response => response.url().endsWith('/api/export') && response.status() === 200);
    await sync.click();
    await initialExport;
    await expect.poll(() => page.evaluate(async fixtureIds => {
      const opening = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error); });
      const songs = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').getAll();
        get.onsuccess = () => resolve(get.result); get.onerror = () => reject(get.error);
      });
      db.close(); const ids = new Set(songs.map(song => Number(song.id)));
      return fixtureIds.every(id => ids.has(id));
    }, ids)).toBe(true);
    await page.getByRole('button', { name: 'Songs' }).click();
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row')).toHaveCount(3);

    await context.setOffline(true);
    await page.locator('.song-row').filter({ hasText: `${prefix} enters` }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    const genres = page.locator('[aria-label="Genre tags"]');
    await genres.locator('input[placeholder="Add a genre"]').fill(target);
    await genres.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    await page.getByRole('button', { name: 'Back' }).click();

    await search.fill(`${prefix} leaves`);
    await page.locator('.song-row').click();
    await page.getByRole('button', { name: 'Edit' }).click();
    const leaveGenres = page.locator('[aria-label="Genre tags"]');
    await leaveGenres.getByRole('button', { name: target, exact: true }).click();
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    await page.getByRole('button', { name: 'Back' }).click();

    await search.fill(`${prefix} deleted`);
    await page.locator('.song-row').click();
    await page.getByRole('button', { name: 'Edit' }).click();
    page.once('dialog', dialog => dialog.accept());
    await page.locator('.btn-danger').click();
    await expect(search).toBeVisible();

    await page.getByRole('button', { name: 'Add a new song' }).click();
    await page.locator('.form-group').filter({ hasText: 'Title' }).locator('input').fill(`${prefix} local create`);
    await page.locator('.form-group').filter({ hasText: 'Artist' }).locator('input').fill(`${prefix} local artist`);
    const createGenres = page.locator('[aria-label="Genre tags"]');
    await createGenres.getByRole('button', { name: target, exact: true }).click();
    await page.locator('.form-textarea').fill('Invented local pending filter fixture');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(`${prefix} local create`);
    await page.getByRole('button', { name: 'Back' }).click();

    // Let online reads work while every pending write fails, isolating the
    // connected-but-unacknowledged projection behavior.
    await page.route('**/api/songs**', async route => {
      if (route.request().method() === 'GET') return route.continue();
      return route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"controlled pending-push failure"}' });
    });
    await context.setOffline(false);
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByText(/Sync failed:/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sync now' })).toBeEnabled();
    await page.getByRole('button', { name: 'Songs' }).click();
    await expect(search).toBeVisible();
    await search.fill(prefix);
    await page.getByRole('button', { name: 'Filter by tags' }).click();
    const panel = page.locator('.tag-panel');
    await panel.getByRole('button', { name: new RegExp(`^${target}`) }).click();
    const rows = page.locator('.song-row');
    await expect(rows).toHaveCount(2);
    await expect(rows).toContainText([`${prefix} enters`, `${prefix} local create`]);
    await expect(page.getByText(`${prefix} leaves`, { exact: true })).toHaveCount(0);
    await expect(page.getByText(`${prefix} deleted`, { exact: true })).toHaveCount(0);

    await page.unroute('**/api/songs**');
    await page.getByRole('button', { name: 'Settings' }).click();
    const syncButton = page.getByRole('button', { name: 'Sync now' });
    const acknowledgedExport = page.waitForResponse(response => response.url().endsWith('/api/export') && response.status() === 200);
    await syncButton.click();
    await acknowledgedExport;
    await expect.poll(async () => page.evaluate(async () => {
      const opening = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error); });
      const queue = await new Promise((resolve, reject) => {
        const get = db.transaction('meta').objectStore('meta').get('pending_mutations');
        get.onsuccess = () => resolve(get.result?.value || []); get.onerror = () => reject(get.error);
      });
      db.close(); return queue.length;
    }), { timeout: 15000 }).toBe(0);
    await page.getByRole('button', { name: 'Songs' }).click();
    await search.fill(prefix);
    await page.getByRole('button', { name: 'Filter by tags' }).click();
    await page.locator('.tag-panel').getByRole('button', { name: new RegExp(`^${target}`) }).click();
    await expect(page.locator('.song-row')).toHaveCount(2);
    await expect(page.locator('.song-row')).toContainText([`${prefix} enters`, `${prefix} local create`]);
    await expect(page.getByText(`${prefix} leaves`, { exact: true })).toHaveCount(0);
    await expect(page.getByText(`${prefix} deleted`, { exact: true })).toHaveCount(0);
  } catch (error) { primaryError = error; }
  finally {
    await context.setOffline(false).catch(error => { primaryError ||= error; });
    await page.unroute('**/api/songs**').catch(error => { primaryError ||= error; });
    const cleanupErrors = [];
    try {
      const query = await cleanupRequest.get(`/api/songs?q=${encodeURIComponent(prefix)}`);
      if (!query.ok()) throw new Error(`Could not locate T18 pending fixtures during cleanup: HTTP ${query.status()}.`);
      for (const song of (await query.json()).songs) if (song.title.startsWith(prefix)) ids.push(song.id);
    } catch (error) { cleanupErrors.push(error); }
    for (const id of new Set(ids)) {
      try {
        const current = await cleanupRequest.get(`/api/songs/${id}`);
        if (current.status() === 404) continue;
        if (!current.ok()) throw new Error(`Could not inspect T18 pending song ${id}: HTTP ${current.status()}.`);
        const song = await current.json();
        const deleted = await cleanupRequest.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
        if (!deleted.ok()) throw new Error(`Could not delete T18 pending song ${id}: HTTP ${deleted.status()}.`);
        if ((await cleanupRequest.get(`/api/songs/${id}`)).status() !== 404) throw new Error(`T18 pending song ${id} remains after cleanup.`);
      } catch (error) { cleanupErrors.push(error); }
    }
    await cleanupRequest.dispose().catch(error => cleanupErrors.push(error));
    if (cleanupErrors.length) throw new AggregateError(primaryError ? [primaryError, ...cleanupErrors] : cleanupErrors,
      'T18 pending online-filter fixture cleanup failed.');
  }
  if (primaryError) throw primaryError;
});
