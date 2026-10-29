import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('FR-019 artist suggestions use cached and local songs offline and preserve free text through sync', async ({ page, context, request, browser }) => {
  const suffix = randomUUID();
  const prefix = `T20 ${suffix}`;
  const candidateArtist = `The T20 Artist ${suffix}`;
  const baseTitle = `${prefix} edit fixture`;
  const localTitle = `${prefix} local suggestion source`;
  const selectedTitle = `${prefix} selected local artist`;
  const freeTextTitle = `${prefix} free text`;
  const touchTitle = `${prefix} touch selection`;
  const freeTextArtist = `A New T20 Artist ${suffix}`;
  const seededIds = [];
  const exactTitles = [baseTitle, localTitle, selectedTitle, freeTextTitle, touchTitle];
  let touchContext = null;
  let primaryError = null;
  try {
    for (const [title, artist] of [[`${prefix} artist candidate`, candidateArtist], [baseTitle, `Original T20 Artist ${suffix}`]]) {
      const response = await request.post('/api/songs', { data: {
        title, artist, chart_written_key: 'C', chart_source: 'Verse\nInvented T20 lyric', genres: [], vibes: [],
      } });
      expect(response.status()).toBe(201);
      seededIds.push((await response.json()).id);
    }

    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row')).toHaveCount(2);
    // A navigation reload can only be served offline after this page is under
    // the installed PWA worker. Wait for that concrete state before disabling
    // the network so the assertion covers reload behavior, not first-install
    // timing.
    await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker?.controller)),
      { message: 'PWA service worker did not control the app before offline testing' }).toBe(true);
    await context.setOffline(true);

    await page.getByRole('button', { name: 'Add a new song' }).click();
    await page.locator('.form-group').nth(0).locator('input').fill(localTitle);
    const artist = page.getByRole('combobox', { name: 'Artist' });
    await artist.fill(`Local T20 Artist ${suffix}`);
    await page.locator('.form-textarea').fill('Invented local-source chart');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(localTitle);
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(search).toBeVisible();

    await page.getByRole('button', { name: 'Add a new song' }).click();
    await page.locator('.form-group').nth(0).locator('input').fill(selectedTitle);
    await artist.fill(`local t20 artist ${suffix}`);
    const listbox = page.getByRole('listbox', { name: 'Existing artists' });
    await expect(listbox.getByRole('option', { name: `Local T20 Artist ${suffix}`, exact: true })).toBeVisible();
    await artist.press('ArrowDown');
    await artist.press('Enter');
    await expect(artist).toHaveValue(`Local T20 Artist ${suffix}`);
    await page.locator('.form-textarea').fill('Invented selected-artist chart');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(selectedTitle);
    await page.getByRole('button', { name: 'Back' }).click();

    await page.getByRole('button', { name: 'Add a new song' }).click();
    await page.locator('.form-group').nth(0).locator('input').fill(freeTextTitle);
    await artist.fill(freeTextArtist);
    await expect(page.getByRole('listbox', { name: 'Existing artists' })).toHaveCount(0);
    await expect(artist).toHaveValue(freeTextArtist);
    await page.locator('.form-textarea').fill('Invented free-text chart');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(freeTextTitle);
    await page.getByRole('button', { name: 'Back' }).click();

    await search.fill(baseTitle);
    await expect(page.locator('.song-row')).toHaveCount(1);
    await page.locator('.song-row').click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await artist.fill(candidateArtist.slice(0, 10).toLocaleLowerCase());
    await page.getByRole('option', { name: candidateArtist, exact: true }).click();
    await expect(artist).toHaveValue(candidateArtist);
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chart-topbar-title')).toHaveText(baseTitle);

    await page.reload();
    await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
    for (const title of [localTitle, selectedTitle, freeTextTitle, baseTitle]) {
      await page.getByPlaceholder('Search songs, artists…').fill(title);
      await expect(page.locator('.song-row')).toHaveCount(1);
    }

    const queueEmpty = async () => page.evaluate(async () => {
      const open = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { open.onsuccess = () => resolve(open.result); open.onerror = () => reject(open.error); });
      const result = await new Promise((resolve, reject) => {
        const get = db.transaction('meta').objectStore('meta').get('pending_mutations');
        get.onsuccess = () => resolve(get.result?.value || []); get.onerror = () => reject(get.error);
      });
      db.close(); return result.length === 0;
    });
    await context.setOffline(false);
    await expect.poll(queueEmpty, { timeout: 30_000 }).toBe(true);
    const titles = new Set(exactTitles);
    const query = await request.get('/api/songs');
    expect(query.ok()).toBeTruthy();
    const synced = (await query.json()).songs.filter(song => titles.has(song.title));
    expect(synced.filter(song => song.title !== touchTitle)).toHaveLength(4);
    expect(synced.find(song => song.title === localTitle).artist).toBe(`Local T20 Artist ${suffix}`);
    expect(synced.find(song => song.title === selectedTitle).artist).toBe(`Local T20 Artist ${suffix}`);
    expect(synced.find(song => song.title === freeTextTitle).artist).toBe(freeTextArtist);
    expect(synced.find(song => song.title === baseTitle).artist).toBe(candidateArtist);

    touchContext = await browser.newContext({ baseURL: new URL(page.url()).origin,
      viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const touchPage = await touchContext.newPage();
    await touchPage.goto('/');
    await expect(touchPage.getByRole('button', { name: 'Add a new song' })).toBeVisible();
    await expect.poll(() => touchPage.evaluate(async artistName => {
      const open = indexedDB.open('chart-manager', 1);
      const db = await new Promise((resolve, reject) => { open.onsuccess = () => resolve(open.result); open.onerror = () => reject(open.error); });
      const songs = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').getAll();
        get.onsuccess = () => resolve(get.result); get.onerror = () => reject(get.error);
      });
      db.close(); return songs.some(song => song.artist === artistName);
    }, candidateArtist)).toBe(true);
    await touchContext.setOffline(true);
    await touchPage.getByRole('button', { name: 'Add a new song' }).click();
    await touchPage.locator('.form-group').nth(0).locator('input').fill(touchTitle);
    const touchArtist = touchPage.getByRole('combobox', { name: 'Artist' });
    await touchArtist.fill(candidateArtist.slice(0, 10).toLocaleLowerCase());
    await touchPage.getByRole('option', { name: candidateArtist, exact: true }).tap();
    await expect(touchArtist).toHaveValue(candidateArtist);
    await touchPage.locator('.form-textarea').fill('Invented touch-selected chart');
    await touchPage.getByRole('button', { name: 'Save changes' }).tap();
    await expect(touchPage.locator('.chart-topbar-title')).toHaveText(touchTitle);
    await touchContext.setOffline(false);
    await expect.poll(async () => {
      const response = await request.get('/api/songs');
      return response.ok() && (await response.json()).songs.some(song => song.title === touchTitle && song.artist === candidateArtist);
    }, { timeout: 30_000 }).toBe(true);
  } catch (error) {
    primaryError = error;
  } finally {
    const cleanupErrors = [];
    try { await context.setOffline(false); } catch (error) { cleanupErrors.push(error); }
    if (touchContext) {
      try { await touchContext.setOffline(false); await touchContext.close(); }
      catch (error) { cleanupErrors.push(error); }
    }
    const ids = new Set(seededIds);
    try {
      const response = await request.get('/api/songs');
      if (!response.ok()) throw new Error(`Could not locate task-owned T20 songs: HTTP ${response.status()}`);
      for (const song of (await response.json()).songs) if (exactTitles.includes(song.title)) ids.add(song.id);
    } catch (error) { cleanupErrors.push(error); }
    for (const id of ids) {
      try {
        const current = await request.get(`/api/songs/${id}`);
        if (current.status() === 404) continue;
        if (!current.ok()) throw new Error(`Could not inspect T20 fixture ${id}: HTTP ${current.status()}`);
        const song = await current.json();
        const deleted = await request.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
        if (!deleted.ok()) throw new Error(`Could not delete T20 fixture ${id}: HTTP ${deleted.status()}`);
        if ((await request.get(`/api/songs/${id}`)).status() !== 404) throw new Error(`T20 fixture ${id} remains after cleanup`);
      } catch (error) { cleanupErrors.push(error); }
    }
    if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'T20 test and cleanup failed');
    if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'T20 exact-ID cleanup failed');
  }
  if (primaryError) throw primaryError;
});
