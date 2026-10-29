import { expect, test, request as apiRequest } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('FR-037 FR-054 Original key stays separate from transposition, capo, and setlist overrides', async ({ page, request, context }) => {
  const suffix = randomUUID();
  const knownTitle = `T21 Original key ${suffix}`;
  const missingTitle = `T21 Unknown key ${suffix}`;
  let knownId = null;
  let missingId = null;
  let setlistId = null;
  let primaryError = null;
  const cleanupRequest = await apiRequest.newContext({ baseURL: process.env.E2E_BASE_URL });
  const createSong = async (title, originalKey) => {
    const response = await request.post('/api/songs', { data: {
      title, artist: `T21 synthetic artist ${suffix}`, chart_written_key: 'C',
      ...(originalKey ? { original_key: originalKey } : {}),
      chart_source: 'Verse\nInvented test text', operation_id: randomUUID(),
    } });
    expect(response.status(), `song creation HTTP ${response.status()}`).toBe(201);
    return response.json();
  };
  try {
    const known = await createSong(knownTitle, 'D');
    knownId = known.id;
    const missing = await createSong(missingTitle, null);
    missingId = missing.id;
    const setlistResponse = await request.post('/api/setlists/mutations', { data: {
      operation_id: randomUUID(), type: 'create', setlist_id: null,
      base_updated_at: null, payload: { name: `T21 Override ${suffix}` },
    } });
    expect(setlistResponse.ok()).toBeTruthy();
    const setlist = await setlistResponse.json();
    setlistId = setlist.id;
    const entryResponse = await request.post('/api/setlists/mutations', { data: {
      operation_id: randomUUID(), type: 'entry_add', setlist_id: setlistId,
      base_updated_at: setlist.updated_at,
      payload: { song_id: knownId, transposed_key: 'E', capo_fret: 2 },
    } });
    expect(entryResponse.ok()).toBeTruthy();

    await page.goto('/');
    await page.getByPlaceholder(/Search songs/).fill(knownTitle);
    await page.getByText(knownTitle, { exact: true }).click();
    const original = page.locator('.transpose-original-key');
    const current = page.locator('.transpose-key');
    await expect(original).toContainText('Original key');
    await expect(original).toContainText('D');
    await expect(current).toHaveText('C');
    await page.getByRole('button', { name: 'Up half step' }).click();
    await expect(current).not.toHaveText('C');
    await expect(original).toContainText('D');
    await page.getByRole('button', { name: 'Increase capo' }).click();
    await expect(page.getByText('Capo 1')).toBeVisible();
    await expect(original).toContainText('D');
    await page.getByRole('button', { name: 'Save key' }).click();
    await expect(page.getByRole('button', { name: 'Save key' })).toHaveCount(0);
    const serverSongAfterSave = await (await request.get(`/api/songs/${knownId}`)).json();
    expect(serverSongAfterSave.original_key).toBe('D');
    expect(serverSongAfterSave.preferred_key).not.toBeNull();
    await page.getByRole('button', { name: 'Down half step' }).click();
    await page.getByRole('button', { name: 'Up whole step' }).click();
    await expect(original).toContainText('D');
    await page.getByRole('button', { name: 'Back' }).click();

    await page.getByRole('button', { name: 'Setlists' }).click();
    await page.getByText(`T21 Override ${suffix}`, { exact: true }).click();
    await page.getByText(knownTitle, { exact: true }).click();
    await expect(current).toHaveText('E');
    await expect(original).toContainText('D');
    await page.getByRole('button', { name: 'Up half step' }).click();
    await page.getByRole('button', { name: 'Save to setlist' }).click();
    await expect(page.getByRole('button', { name: 'Save to setlist' })).toHaveCount(0);
    const serverSong = await (await request.get(`/api/songs/${knownId}`)).json();
    expect(serverSong.original_key).toBe('D');
    // The setlist save updates locally first and pushes afterwards, so wait for the server copy.
    await expect.poll(async () => (await (await request.get(`/api/setlists/${setlistId}`)).json()).songs[0].transposed_key)
      .not.toBe('E');

    await page.getByRole('button', { name: 'Back' }).click();
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.getByPlaceholder(/Search songs/).fill(missingTitle);
    await page.getByText(missingTitle, { exact: true }).click();
    await expect(page.locator('.transpose-original-key')).toContainText('Unknown');
    await page.getByRole('button', { name: 'Back' }).click();
    await context.setOffline(true);
    await page.getByText(missingTitle, { exact: true }).click();
    await expect(page.locator('.transpose-original-key')).toContainText('Unknown');
    await page.getByRole('button', { name: 'Back' }).click();
    await page.getByPlaceholder(/Search songs/).fill(knownTitle);
    await page.getByText(knownTitle, { exact: true }).click();
    await expect(page.locator('.transpose-original-key')).toContainText('D');
    await context.setOffline(false);
  } catch (error) {
    primaryError = error;
  } finally {
    const errors = [];
    try { await context.setOffline(false); } catch (error) { errors.push(error); }
    if (setlistId !== null) {
      try {
        const latest = await cleanupRequest.get(`/api/setlists/${setlistId}`);
        if (latest.status() !== 404) {
          if (!latest.ok()) throw new Error(`Could not inspect T21 setlist ${setlistId}`);
          const currentSetlist = await latest.json();
          const deleted = await cleanupRequest.post('/api/setlists/mutations', { data: {
            operation_id: randomUUID(), type: 'delete', setlist_id: setlistId,
            base_updated_at: currentSetlist.updated_at, payload: {},
          } });
          if (!deleted.ok()) throw new Error(`Could not delete T21 setlist ${setlistId}: HTTP ${deleted.status()}`);
        }
        if ((await cleanupRequest.get(`/api/setlists/${setlistId}`)).status() !== 404) throw new Error(`T21 setlist ${setlistId} remains`);
      } catch (error) { errors.push(error); }
    }
    for (const id of [knownId, missingId].filter(value => value !== null)) {
      try {
        const latest = await cleanupRequest.get(`/api/songs/${id}`);
        if (latest.status() !== 404) {
          if (!latest.ok()) throw new Error(`Could not inspect T21 song ${id}`);
          const song = await latest.json();
          const deleted = await cleanupRequest.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
          if (!deleted.ok()) throw new Error(`Could not delete T21 song ${id}: HTTP ${deleted.status()}`);
        }
        if ((await cleanupRequest.get(`/api/songs/${id}`)).status() !== 404) throw new Error(`T21 song ${id} remains`);
      } catch (error) { errors.push(error); }
    }
    try { await cleanupRequest.dispose(); } catch (error) { errors.push(error); }
    if (primaryError && errors.length) throw new AggregateError([primaryError, ...errors], 'T21 test and cleanup failed');
    if (errors.length) throw new AggregateError(errors, 'T21 fixture cleanup failed');
  }
  if (primaryError) throw primaryError;
});
