import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

test('song sorting is consistent from cache offline and survives reload', async ({ page, context, request }) => {
  const suffix = randomUUID();
  const prefix = `T17 sort ${suffix}`;
  const fixtures = [
    { title: `${prefix} Cedar`, artist: `T17 Artist C ${suffix}` },
    { title: `${prefix} Amber`, artist: `T17 Artist A ${suffix}` },
    { title: `${prefix} Birch`, artist: `T17 Artist B ${suffix}` },
  ];
  const ids = [];
  const createdSongs = [];
  let primaryError = null;
  try {
    for (const fixture of fixtures) {
      const response = await request.post('/api/songs', { data: {
        ...fixture, chart_written_key: 'C', chart_source: 'Invented T17 verse\nInvented T17 line',
        genres: [], vibes: [],
      } });
      if (response.status() !== 201) throw new Error(`T17 song create failed with HTTP ${response.status()}.`);
      const created = await response.json();
      if (!Number.isInteger(created.id) || created.id < 1) throw new Error('T17 create returned no valid server ID.');
      ids.push(created.id);
      createdSongs.push(created);
    }

    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    const rows = page.locator('.song-row');
    await expect(rows).toHaveCount(fixtures.length);
    await expect.poll(async () => page.evaluate(async prefixValue => {
      const opening = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error); });
      const result = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').getAll();
        get.onsuccess = () => resolve(get.result.filter(song => song.title?.startsWith(prefixValue)).length);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return result;
    }, prefix)).toBe(fixtures.length);

    const sortBy = page.getByLabel('Sort songs by');
    await sortBy.selectOption('created_at');
    const dateOrder = [...createdSongs].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)
      || a.title.localeCompare(b.title));
    for (let index = 0; index < dateOrder.length; index += 1) {
      await expect(rows.nth(index)).toContainText(dateOrder[index].title);
    }

    await sortBy.selectOption('artist');
    await expect(rows.nth(0)).toContainText('Artist A');
    await expect(rows.nth(1)).toContainText('Artist B');
    await expect(rows.nth(2)).toContainText('Artist C');
    await page.getByRole('button', { name: 'Sort descending' }).click();
    await expect(rows.nth(0)).toContainText('Artist C');
    await expect(rows.nth(2)).toContainText('Artist A');
    await context.setOffline(true);
    await rows.nth(0).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(fixtures[0].title);
    await expect(page.locator('.swipe-hint')).toContainText(fixtures[2].title);
    await page.locator('.chart-scroll').evaluate(element => {
      const dispatchTouch = (type, point, points) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        Object.defineProperties(event, {
          touches: { value: points },
          changedTouches: { value: [point] },
        });
        element.dispatchEvent(event);
      };
      const start = { identifier: 1, clientX: 240, clientY: 320 };
      const end = { identifier: 1, clientX: 130, clientY: 322 };
      dispatchTouch('touchstart', start, [start]);
      dispatchTouch('touchend', end, []);
    });
    await expect(page.locator('.chart-topbar-title')).toHaveText(fixtures[2].title);
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(search).toBeVisible();
    await search.fill(prefix);
    await page.reload();
    await expect(search).toBeVisible();
    await search.fill(prefix);
    await expect(rows).toHaveCount(fixtures.length);
    await expect(rows.nth(0)).toContainText('Artist C');
    await expect(rows.nth(1)).toContainText('Artist B');
    await expect(rows.nth(2)).toContainText('Artist A');

    const localCreateStartedAt = Date.now();
    const localTitle = `${prefix} Offline`;
    const editedLocalTitle = `${prefix} Offline edited`;
    await page.getByRole('button', { name: 'Add a new song' }).click();
    await page.locator('.form-group').nth(0).locator('input').fill(localTitle);
    await page.locator('.form-group').nth(1).locator('input').fill(`T17 Artist D ${suffix}`);
    await page.locator('.form-textarea').fill('Invented offline chart line');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(localTitle);
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(search).toBeVisible();
    await search.fill(prefix);

    const readLocal = title => page.evaluate(async titleValue => {
      const opening = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error); });
      const result = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').getAll();
        get.onsuccess = () => resolve(get.result.find(song => song.title === titleValue)
          ? { id: get.result.find(song => song.title === titleValue).id,
            created_at: get.result.find(song => song.title === titleValue).created_at }
          : null);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return result;
    }, title);
    let localSong = await readLocal(localTitle);
    expect(localSong?.id).toMatch(/^local-/);
    expect(Date.parse(localSong.created_at)).toBeGreaterThanOrEqual(localCreateStartedAt);
    expect(Date.parse(localSong.created_at)).toBeLessThanOrEqual(Date.now());
    const initialLocalCreatedAt = localSong.created_at;
    await sortBy.selectOption('created_at');
    await expect(rows.nth(0)).toContainText(localTitle);

    await rows.nth(0).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(localTitle);
    await page.getByRole('button', { name: 'Edit' }).click();
    await page.locator('.form-group').nth(0).locator('input').fill(editedLocalTitle);
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(editedLocalTitle);
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(search).toBeVisible();
    await search.fill(prefix);
    localSong = await readLocal(editedLocalTitle);
    expect(localSong?.created_at).toBe(initialLocalCreatedAt);
    await page.reload();
    await search.fill(prefix);
    await expect(rows.nth(0)).toContainText(editedLocalTitle);
    expect((await readLocal(editedLocalTitle))?.created_at).toBe(localSong.created_at);

    const createResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/songs')
      && response.request().method() === 'POST');
    await context.setOffline(false);
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByText(/Online/)).toBeVisible();
    await page.getByRole('button', { name: 'Sync now' }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status()).toBe(201);
    const createdLocalOnServer = await createResponse.json();
    ids.push(createdLocalOnServer.id);
    expect(createdLocalOnServer.title).toBe(editedLocalTitle);
    expect(Date.parse(createdLocalOnServer.created_at)).toBeGreaterThanOrEqual(localCreateStartedAt);
    const acknowledgedLocal = await readLocal(editedLocalTitle);
    expect(acknowledgedLocal.id).toBe(createdLocalOnServer.id);
    expect(acknowledgedLocal.created_at).toBe(createdLocalOnServer.created_at);
    await expect(page.getByRole('button', { name: 'Sync now' })).toBeEnabled();

    await page.getByRole('button', { name: 'Songs' }).click();
    await search.fill(prefix);
    await expect(rows).toHaveCount(fixtures.length + 1);
    await expect(rows.nth(0)).toContainText(editedLocalTitle);
  } catch (error) {
    primaryError = error;
  } finally {
    await context.setOffline(false).catch(error => { primaryError ||= error; });
    const cleanupErrors = [];
    for (const id of ids) {
      try {
        const current = await request.get(`/api/songs/${id}`);
        if (current.status() === 404) continue;
        if (!current.ok()) throw new Error(`Could not inspect T17 fixture ${id}: HTTP ${current.status()}.`);
        const song = await current.json();
        const deleted = await request.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
        if (!deleted.ok()) throw new Error(`Could not delete T17 fixture ${id}: HTTP ${deleted.status()}.`);
        if ((await request.get(`/api/songs/${id}`)).status() !== 404) throw new Error(`T17 fixture ${id} remains after cleanup.`);
      } catch (error) { cleanupErrors.push(error); }
    }
    if (cleanupErrors.length) {
      const cleanupError = new AggregateError(cleanupErrors, 'T17 exact-ID fixture cleanup failed.');
      if (primaryError) throw new AggregateError([primaryError, cleanupError], 'T17 test and cleanup failed.');
      throw cleanupError;
    }
  }
  if (primaryError) throw primaryError;
});
