import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('late category load cannot update a different management view', async ({ page, context }) => {
  const requestId = randomUUID();
  const leakedName = `T19 stale Genre ${randomUUID()}`;
  let releaseHeld;
  let markHeld;
  const held = new Promise(resolve => { releaseHeld = resolve; });
  const requestHeld = new Promise(resolve => { markHeld = resolve; });
  let holdFirst = true;
  const routeHandler = async route => {
    if (holdFirst && route.request().method() === 'GET') {
      holdFirst = false;
      markHeld();
      await held;
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: {
        ...body,
        __ccmTestLoadId: requestId,
        genres: [...body.genres, { id: -1, name: leakedName, song_count: 0 }],
      } });
      return;
    }
    await route.continue();
  };
  await page.addInitScript(() => {
    const events = [];
    const waiters = new Map();
    window.__CCM_TEST_OBSERVE_TAG_LOAD__ = true;
    window.__CCM_TEST_TAG_LOAD_EVENTS__ = events;
    window.__CCM_TEST_WAIT_FOR_TAG_LOAD__ = id => {
      const existing = events.find(detail => detail.requestId === id);
      if (existing) return Promise.resolve(existing);
      return new Promise(resolve => waiters.set(id, resolve));
    };
    window.__CCM_TEST_TAG_LOAD_WAITING__ = id => waiters.has(id);
    window.addEventListener('ccm:test-tag-definition-load-settled', event => {
      events.push(event.detail);
      const resolve = waiters.get(event.detail.requestId);
      if (resolve) {
        waiters.delete(event.detail.requestId);
        resolve(event.detail);
      }
    });
  });
  await context.route('**/api/tag-definitions', routeHandler);
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('button', { name: 'Manage Genres' }).click();
    await requestHeld;

    await page.getByRole('button', { name: 'Back to Settings' }).click();
    await page.getByRole('button', { name: 'Manage Vibes' }).click();
    await expect(page.locator('.topbar-title')).toHaveText('Manage Vibes');

    const completion = page.evaluate(id => window.__CCM_TEST_WAIT_FOR_TAG_LOAD__(id), requestId);
    expect(await page.evaluate(id => window.__CCM_TEST_TAG_LOAD_WAITING__(id), requestId),
      'the exact held Genre load completion must still be pending before release').toBe(true);
    releaseHeld();
    await expect(completion).resolves.toMatchObject({ requestId, category: 'Genre', published: false });
    await expect(page.locator('.topbar-title')).toHaveText('Manage Vibes');
    await expect(page.getByText(leakedName, { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Back to Settings' }).click();
    await expect(page.getByRole('region', { name: 'Shared definitions' })).toBeVisible();
  } finally {
    releaseHeld();
    await context.unroute('**/api/tag-definitions', routeHandler);
  }
});

test('Settings manages Genre and Vibe definitions with explicit assignment decisions', async ({ page, request, context }) => {
  const suffix = randomUUID();
  const sourceName = `T19 source ${suffix}`;
  const targetName = `T19 target ${suffix}`;
  const stableName = `T19 stable ${suffix}`;
  const vibeName = `T19 shared ${suffix}`;
  const unusedName = `T19 unused ${suffix}`;
  const renamedName = `T19 renamed ${suffix}`;
  const longVibeName = `T19 long Vibe ${suffix} ${'extended-definition-name-'.repeat(1)}`;
  const fixtureTitle = `T19 management fixture ${suffix}`;
  const fixtureArtist = `T19 artist ${suffix}`;
  const ownedDefinitions = [];
  let songId = null;
  let primaryError = null;
  const call = async (method, path, body) => {
    const response = await request.fetch(path, { method, ...(body === undefined ? {} : { data: body }) });
    return { response, value: response.ok() ? await response.json() : null };
  };
  const makeDefinition = async (category, name) => {
    const { response, value } = await call('POST', '/api/tag-definitions', { category, name });
    expect(response.status(), JSON.stringify(value)).toBe(201);
    ownedDefinitions.push({ category, id: value.id, name });
    return value;
  };
  try {
    const [source, target, stable, vibe, unused, longVibe] = await Promise.all([
      makeDefinition('Genre', sourceName), makeDefinition('Genre', targetName),
      makeDefinition('Genre', stableName), makeDefinition('Vibe', vibeName), makeDefinition('Genre', unusedName),
      makeDefinition('Vibe', longVibeName),
    ]);
    const created = await call('POST', '/api/songs', {
      title: fixtureTitle, artist: fixtureArtist,
      chart_written_key: 'C', chart_source: 'Verse\nInvented lyric',
      genres: [{ name: source.name, is_primary: true }, { name: stable.name, is_primary: false }],
      vibes: [vibe.name], operation_id: randomUUID(),
    });
    expect(created.response.status(), JSON.stringify(created.value)).toBe(201);
    songId = created.value.id;

    await page.goto('/');
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('region', { name: 'Shared definitions' })).toBeVisible();
    await expect(page.locator('.definition-row')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Manage Genres' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Manage Vibes' })).toBeVisible();
    await expect(page.getByText('Era', { exact: true })).toHaveCount(0);
    await page.getByRole('radio', { name: 'Dark' }).check();
    await page.getByRole('button', { name: 'Manage Genres' }).click();
    await expect(page.locator('.topbar-title')).toHaveText('Manage Genres');
    await expect(page.getByRole('heading', { name: 'Vibe', exact: true })).toHaveCount(0);

    const genreSection = page.locator('.definition-category');
    const sourceRow = genreSection.locator('.definition-row').filter({ hasText: sourceName });
    await expect(sourceRow).toContainText('1 song');
    await sourceRow.getByRole('button', { name: 'Delete' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('This affects 1 song');
    await expect(dialog.getByRole('button', { name: 'Confirm deletion' })).toBeDisabled();
    await dialog.getByLabel('Reassign to').check();
    await dialog.getByLabel('Replacement definition').selectOption(String(target.id));
    const reassignResponsePromise = page.waitForResponse(response => response.url().endsWith(`/api/tag-definitions/Genre/${source.id}`)
      && response.request().method() === 'DELETE');
    await dialog.getByRole('button', { name: 'Confirm deletion' }).click();
    const reassignResponse = await reassignResponsePromise;
    const reassignResult = await reassignResponse.json();
    expect(reassignResponse.status(), JSON.stringify(reassignResult)).toBe(200);
    expect(reassignResult).toMatchObject({ mode: 'reassign', replacement_id: target.id, affected_songs: 1 });
    await expect(page.getByText(sourceName, { exact: true })).toHaveCount(0);
    const afterReassign = await call('GET', `/api/songs/${songId}`);
    expect(afterReassign.value.genres.find(item => item.name === targetName)?.is_primary,
      JSON.stringify(afterReassign.value.genres.map(({ name, is_primary }) => ({ name, is_primary })))).toBe(true);

    const unusedRow = genreSection.locator('.definition-row').filter({ hasText: unusedName });
    await unusedRow.getByRole('button', { name: 'Rename' }).click();
    await page.getByRole('textbox', { name: 'Rename Genre' }).fill(renamedName);
    await page.getByRole('button', { name: 'Save' }).click();
    ownedDefinitions.find(item => item.id === unused.id).name = renamedName;
    const renamedRow = genreSection.locator('.definition-row').filter({ hasText: renamedName });
    await renamedRow.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirm deletion' }).click();
    await expect(page.getByText(renamedName, { exact: true })).toHaveCount(0);

    await page.getByRole('button', { name: 'Back to Settings' }).click();
    await expect(page.getByRole('region', { name: 'Shared definitions' })).toBeVisible();
    await expect(page.locator('.definition-row')).toHaveCount(0);
    await page.getByRole('radio', { name: 'Light' }).check();
    await page.getByRole('button', { name: 'Manage Vibes' }).click();
    await expect(page.locator('.topbar-title')).toHaveText('Manage Vibes');
    await expect(page.getByRole('heading', { name: 'Genre', exact: true })).toHaveCount(0);

    const vibeSection = page.locator('.definition-category');
    const vibeRow = vibeSection.locator('.definition-row').filter({ hasText: vibeName });
    await expect(vibeRow).toContainText('1 song');
    const longVibeRow = vibeSection.locator('.definition-row').filter({ hasText: longVibeName });
    await expect(longVibeRow).toContainText('0 songs');
    for (const viewport of [{ width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 1024, height: 768 }]) {
      await page.setViewportSize(viewport);
      await expect(page.locator('.topbar-title')).toHaveText('Manage Vibes');
      await expect(longVibeRow.getByRole('button', { name: 'Rename' })).toBeVisible();
      const nameBox = await longVibeRow.locator('.definition-name').boundingBox();
      expect(nameBox, JSON.stringify(viewport)).not.toBeNull();
      expect(nameBox.x).toBeGreaterThanOrEqual(0);
      expect(nameBox.x + nameBox.width).toBeLessThanOrEqual(viewport.width);
      expect(await longVibeRow.locator('.definition-name').evaluate(element => element.scrollWidth <= element.clientWidth + 1),
        `long Vibe name should wrap within ${viewport.width}px`).toBe(true);
    }
    await vibeRow.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('dialog').getByLabel('Remove these assignments').check();
    const removeVibeResponsePromise = page.waitForResponse(response => response.url().endsWith(`/api/tag-definitions/Vibe/${vibe.id}`)
      && response.request().method() === 'DELETE');
    await page.getByRole('dialog').getByRole('button', { name: 'Confirm deletion' }).click();
    const removeVibeResponse = await removeVibeResponsePromise;
    const removeVibeResult = await removeVibeResponse.json();
    expect(removeVibeResponse.status(), JSON.stringify(removeVibeResult)).toBe(200);
    expect(removeVibeResult).toMatchObject({ mode: 'remove', affected_songs: 1 });
    await expect(page.getByText(vibeName, { exact: true })).toHaveCount(0);
    expect((await call('GET', `/api/songs/${songId}`)).value.vibes).not.toContain(vibeName);

    await context.setOffline(true);
    await expect(page.getByText('Definition changes require a connection.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add' }).first()).toBeDisabled();
    await context.setOffline(false);
    await page.getByRole('button', { name: 'Back to Settings' }).click();
    await expect(page.getByRole('button', { name: 'Manage Genres' })).toBeVisible();
  } catch (error) {
    primaryError = error;
  } finally {
    const errors = [];
    try { await context.setOffline(false); } catch (error) { errors.push(error); }
    if (songId !== null) {
      try {
        const current = await call('GET', `/api/songs/${songId}`);
        if (current.response.status() !== 404) {
          if (!current.response.ok()) throw new Error(`Could not inspect owned song ${songId}: ${current.response.status()}`);
          const deleted = await call('DELETE', `/api/songs/${songId}`, { base_updated_at: current.value.updated_at });
          if (deleted.response.status() === 409) {
            const latest = await call('GET', `/api/songs/${songId}`);
            if (!latest.response.ok() || latest.value.title !== fixtureTitle || latest.value.artist !== fixtureArtist) {
              throw new Error(`Owned song ${songId} changed identity during cleanup; refusing retry`);
            }
            const retried = await call('DELETE', `/api/songs/${songId}`, { base_updated_at: latest.value.updated_at });
            if (!retried.response.ok()) throw new Error(`Could not delete unchanged owned song ${songId} after refreshed token: ${retried.response.status()}`);
          } else if (!deleted.response.ok()) throw new Error(`Could not delete owned song ${songId}: ${deleted.response.status()}`);
        }
        if ((await call('GET', `/api/songs/${songId}`)).response.status() !== 404) throw new Error(`Owned song ${songId} remains`);
      } catch (error) { errors.push(error); }
    }
    for (const definition of ownedDefinitions) {
      try {
        const listed = await call('GET', '/api/tag-definitions');
        if (!listed.response.ok()) throw new Error('Could not inspect owned definitions during cleanup');
        const rows = definition.category === 'Genre' ? listed.value.genres : listed.value.vibes;
        const present = rows.find(item => item.id === definition.id);
        if (present) {
          if (present.name !== definition.name && !(definition.name === unusedName && present.name === renamedName)) {
            throw new Error(`Owned definition ${definition.id} changed unexpectedly; refusing cleanup`);
          }
          if (present.song_count !== 0) throw new Error(`Owned definition ${definition.id} remains in use; refusing cleanup`);
          const deleted = await call('DELETE', `/api/tag-definitions/${definition.category}/${definition.id}`, { mode: 'remove', expected_song_count: 0 });
          if (!deleted.response.ok()) throw new Error(`Could not delete owned definition ${definition.id}: ${deleted.response.status()}`);
        }
        const finalList = await call('GET', '/api/tag-definitions');
        const finalRows = definition.category === 'Genre' ? finalList.value.genres : finalList.value.vibes;
        if (finalRows.some(item => item.id === definition.id)) throw new Error(`Owned definition ${definition.id} remains after cleanup`);
      } catch (error) { errors.push(error); }
    }
    if (primaryError && errors.length) throw new AggregateError([primaryError, ...errors], 'T19 test and fixture cleanup failed');
    if (errors.length) throw new AggregateError(errors, 'T19 fixture cleanup failed');
  }
  if (primaryError) throw primaryError;
});
