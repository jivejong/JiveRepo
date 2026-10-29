import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { expect, test } from '@playwright/test';

const require = createRequire(import.meta.url);
const { Pool } = require('../../server/node_modules/pg');
const source = 'G D C G | G D Em F | G';
const editedSource = 'G  D C G | G D Em F | G';
const transposed = 'A E D A | A E F#m G | A';
const transposedEdited = 'A  E D A | A E F#m G | A';

async function createSong(request, title, sourceText = 'G D C') {
  const response = await request.post('/api/songs', { data: {
    title, artist: 'T13 invented chord-order artist', chart_written_key: 'G',
    chart_source: sourceText,
  } });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function openSong(page, title) {
  await page.goto('/');
  await page.getByPlaceholder('Search songs, artists…').fill(title);
  const row = page.locator('.song-row').filter({ hasText: title });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.locator('.chart-topbar-title')).toHaveText(title);
}

test('FR-025 FR-034 online save preserves decorated chord order through storage, API, transpose, and reopen',
  async ({ page, request }) => {
    const title = `T13 online chord order ${randomUUID()}`;
    const song = await createSong(request, title);
    try {
      await openSong(page, title);
      await page.getByRole('button', { name: 'Edit' }).click();
      await page.locator('.form-textarea').fill(source);
      const saved = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
        && response.request().method() === 'PUT');
      await page.getByRole('button', { name: 'Save changes' }).click();
      const response = await saved;
      expect(response.ok()).toBeTruthy();
      expect(response.request().postDataJSON().chart_source).toBe(source);
      await expect(page.locator('.chord-only-line')).toHaveText(source);

      const stored = await (await request.get(`/api/songs/${song.id}`)).json();
      expect(stored.chart_source).toBe(source);
      expect(stored.chart_content.sections[0].lines[0]).toEqual({ type: 'raw', text: source });
      await page.getByRole('button', { name: 'Up whole step' }).click();
      await expect(page.locator('.chord-only-line')).toHaveText(transposed);
      await page.getByRole('button', { name: 'Reset' }).click();
      await page.getByRole('button', { name: 'Edit' }).click();
      await expect(page.locator('.form-textarea')).toHaveValue(source);
    } finally {
      const current = await request.get(`/api/songs/${song.id}`);
      if (current.ok()) {
        const latest = await current.json();
        const deleted = await request.delete(`/api/songs/${song.id}`, {
          data: { base_updated_at: latest.updated_at },
        });
        expect(deleted.ok()).toBeTruthy();
      }
    }
  });

test('FR-021 FR-057 offline metadata save preserves legacy structure, then chart edit syncs and reopens literally',
  async ({ page, context, request }) => {
  const title = `T13 offline chord order ${randomUUID()}`;
  const song = await createSong(request, title, source);
  const db = new Pool({ connectionString: process.env.DATABASE_URL });
  const legacy = { type: 'progression',
    chords: ['G', 'D', 'C', 'G', 'G', 'D', 'Em', 'F', 'G'], note: '| |' };
  try {
    expect((await db.query('SELECT current_database() AS name')).rows[0].name).toBe('ccm_test');
    await db.query('UPDATE public.songs SET chart_content=$2::jsonb WHERE id=$1',
      [song.id, JSON.stringify({ sections: [{ label: null, lines: [legacy] }] })]);
    await openSong(page, title);
    await expect(page.locator('.section-with-chords-line')).toBeVisible();

    await context.setOffline(true);
    await page.getByRole('button', { name: 'Edit' }).click();
    await page.getByPlaceholder('optional').first().fill('128');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.section-with-chords-line')).toBeVisible();
    const cachedPending = await page.evaluate(async ({ id }) => {
      const open = indexedDB.open('chart-manager');
      const db = await new Promise((resolve, reject) => {
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const result = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').get(id);
        get.onsuccess = () => resolve(get.result || null);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return {
        exists: Boolean(result),
        chartSource: result?.chart_source ?? null,
        line: result?.chart_content?.sections?.[0]?.lines?.[0] ?? null,
        operation: result?._sync?.operation ?? null,
      };
    }, { id: song.id });
    expect(cachedPending.exists).toBe(true);
    expect(cachedPending.chartSource).toBe(source);
    expect(cachedPending.line).toEqual(legacy);
    expect(cachedPending.operation).toBe('update');

    const metadataSynced = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
      && response.request().method() === 'PUT');
    await context.setOffline(false);
    const metadataResponse = await metadataSynced;
    expect(metadataResponse.ok()).toBeTruthy();
    expect(Object.hasOwn(metadataResponse.request().postDataJSON(), 'chart_source')).toBe(false);
    let stored = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(stored.chart_source).toBe(source);
    expect(stored.chart_content.sections[0].lines[0]).toEqual(legacy);

    await context.setOffline(true);
    await page.getByRole('button', { name: 'Edit' }).click();
    await page.locator('.form-textarea').fill(editedSource);
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.locator('.chord-only-line')).toHaveText(editedSource);
    const synced = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
      && response.request().method() === 'PUT');
    await context.setOffline(false);
    expect((await synced).ok()).toBeTruthy();
    stored = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(stored.chart_source).toBe(editedSource);
    expect(stored.chart_content.sections[0].lines[0]).toEqual({ type: 'raw', text: editedSource });
    await page.reload();
    await openSong(page, title);
    await expect(page.locator('.chord-only-line')).toHaveText(editedSource);
    await page.getByRole('button', { name: 'Up whole step' }).click();
    await expect(page.locator('.chord-only-line')).toHaveText(transposedEdited);
    await page.getByRole('button', { name: 'Reset' }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.locator('.form-textarea')).toHaveValue(editedSource);
  } finally {
    await page.close().catch(() => {});
    const removed = await db.query('DELETE FROM public.songs WHERE id=$1', [song.id]);
    expect(removed.rowCount).toBe(1);
    const remaining = await db.query('SELECT count(*)::int AS count FROM public.songs WHERE id=$1', [song.id]);
    expect(remaining.rows[0].count).toBe(0);
    await db.end();
  }
});

test('FR-025 FR-034 saving a legacy decorated source refreshes structure, then reload renders and transposes it',
  async ({ page, request }) => {
    const title = `T13 legacy chord order ${randomUUID()}`;
    const song = await createSong(request, title, source);
    const db = new Pool({ connectionString: process.env.DATABASE_URL });
    try {
      expect((await db.query('SELECT current_database() AS name')).rows[0].name).toBe('ccm_test');
      const legacy = { type: 'progression',
        chords: ['G', 'D', 'C', 'G', 'G', 'D', 'Em', 'F', 'G'], note: '| |' };
      await db.query('UPDATE public.songs SET chart_content=$2::jsonb WHERE id=$1',
        [song.id, JSON.stringify({ sections: [{ label: null, lines: [legacy] }] })]);
      const before = await (await request.get(`/api/songs/${song.id}`)).json();
      expect(before.chart_source).toBe(source);
      expect(before.chart_content.sections[0].lines[0]).toEqual(legacy);

      const sameSource = await request.put(`/api/songs/${song.id}`, { data: {
        chart_source: source,
        base_updated_at: before.updated_at,
        operation_id: randomUUID(),
      } });
      expect(sameSource.ok()).toBeTruthy();
      const sameSourceResult = await sameSource.json();
      expect(sameSourceResult.chart_source).toBe(source);
      expect(sameSourceResult.chart_content.sections[0].lines[0]).toEqual(legacy);

      await openSong(page, title);
      await expect(page.locator('.section-with-chords-line')).toBeVisible();
      await page.getByRole('button', { name: 'Edit' }).click();
      await expect(page.locator('.form-textarea')).toHaveValue(source);
      const saved = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
        && response.request().method() === 'PUT');
      await page.getByRole('button', { name: 'Save changes' }).click();
      const response = await saved;
      expect(response.ok()).toBeTruthy();
      expect(Object.hasOwn(response.request().postDataJSON(), 'chart_source')).toBe(false);

      let stored = await (await request.get(`/api/songs/${song.id}`)).json();
      expect(stored.chart_source).toBe(source);
      expect(stored.chart_content.sections[0].lines[0]).toEqual(legacy);

      await page.getByRole('button', { name: 'Edit' }).click();
      const noOpSave = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
        && response.request().method() === 'PUT');
      await page.getByRole('button', { name: 'Save changes' }).click();
      const noOpResponse = await noOpSave;
      expect(noOpResponse.ok()).toBeTruthy();
      expect(Object.hasOwn(noOpResponse.request().postDataJSON(), 'chart_source')).toBe(false);
      stored = await (await request.get(`/api/songs/${song.id}`)).json();
      expect(stored.chart_source).toBe(source);
      expect(stored.chart_content.sections[0].lines[0]).toEqual(legacy);

      await page.getByRole('button', { name: 'Edit' }).click();
      await page.locator('.form-textarea').fill(editedSource);
      const sourceSave = page.waitForResponse(response => response.url().endsWith(`/api/songs/${song.id}`)
        && response.request().method() === 'PUT');
      await page.getByRole('button', { name: 'Save changes' }).click();
      const sourceResponse = await sourceSave;
      expect(sourceResponse.ok()).toBeTruthy();
      expect(sourceResponse.request().postDataJSON().chart_source).toBe(editedSource);

      stored = await (await request.get(`/api/songs/${song.id}`)).json();
      expect(stored.chart_source).toBe(editedSource);
      expect(stored.chart_content.sections[0].lines[0]).toEqual({ type: 'raw', text: editedSource });

      await page.reload();
      await openSong(page, title);
      await expect(page.locator('.chord-only-line')).toHaveText(editedSource);
      await page.getByRole('button', { name: 'Up whole step' }).click();
      await expect(page.locator('.chord-only-line')).toHaveText(transposedEdited);
      await page.getByRole('button', { name: 'Reset' }).click();
      await page.getByRole('button', { name: 'Edit' }).click();
      await expect(page.locator('.form-textarea')).toHaveValue(editedSource);
    } finally {
      await db.end();
      const current = await request.get(`/api/songs/${song.id}`);
      if (current.ok()) {
        const latest = await current.json();
        const deleted = await request.delete(`/api/songs/${song.id}`, {
          data: { base_updated_at: latest.updated_at },
        });
        expect(deleted.ok()).toBeTruthy();
      }
    }
  });
