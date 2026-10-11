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
