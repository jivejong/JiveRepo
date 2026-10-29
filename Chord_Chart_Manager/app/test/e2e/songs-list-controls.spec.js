import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

// Two synthetic songs and their own Genre/Vibe definitions, removed afterwards.
async function withFixtures(request, run) {
  const suffix = randomUUID();
  const genre = `T25 controls genre ${suffix}`;
  const vibe = `T25 controls vibe ${suffix}`;
  const prefix = `T25 controls ${suffix}`;
  const definitions = [];
  const songs = [];
  try {
    for (const [category, name] of [['Genre', genre], ['Vibe', vibe]]) {
      const created = await request.post('/api/tag-definitions', { data: { category, name } });
      expect(created.status()).toBe(201);
      definitions.push({ category, ...(await created.json()) });
    }
    for (const fixture of [
      { title: `${prefix} Alpha`, chart_written_key: 'D', default_capo: 3, vibes: [vibe] },
      { title: `${prefix} Bravo`, chart_written_key: 'E', default_capo: 0, vibes: [] },
    ]) {
      const created = await request.post('/api/songs', { data: { ...fixture, artist: `T25 artist ${suffix}`,
        genres: [{ name: genre, is_primary: true }], chart_source: 'Verse\nInvented controls line',
        operation_id: randomUUID() } });
      expect(created.status()).toBe(201);
      songs.push(await created.json());
    }
    await run({ prefix, genre, vibe, songs });
  } finally {
    for (const song of songs) {
      const current = await request.get(`/api/songs/${song.id}`);
      if (current.ok()) {
        await request.delete(`/api/songs/${song.id}`, { data: { base_updated_at: (await current.json()).updated_at } });
      }
    }
    for (const definition of definitions) {
      await request.delete(`/api/tag-definitions/${definition.category}/${definition.id}`,
        { data: { mode: 'remove', expected_song_count: 0 } });
    }
  }
}

test('FR-008 FR-009 category All clears one filter category, Clear all clears every one, and rows show key and capo',
  async ({ page, request }) => {
    await withFixtures(request, async ({ prefix, genre, vibe }) => {
      await page.goto('/');
      await page.getByPlaceholder('Search songs, artists…').fill(prefix);
      const rows = page.locator('.song-row');
      await expect(rows).toHaveCount(2);
      const alpha = rows.filter({ hasText: `${prefix} Alpha` });
      const bravo = rows.filter({ hasText: `${prefix} Bravo` });
      await expect(alpha.locator('.song-key-badge')).toHaveText('D c3');
      await expect(bravo.locator('.song-key-badge')).toHaveText('E');

      await page.getByRole('button', { name: 'Filter by tags' }).click();
      const chip = name => page.locator('.tag-panel .tag-chip', { hasText: name });
      await chip(genre).click();
      await chip(vibe).click();
      await expect(rows).toHaveCount(1);
      await expect(alpha).toBeVisible();

      await page.getByRole('button', { name: 'All Vibe' }).click();
      await expect(rows).toHaveCount(2);
      await expect(page.locator('.active-tag')).toHaveCount(1);
      await expect(page.locator('.active-tag')).toContainText(genre);

      await chip(vibe).click();
      await expect(rows).toHaveCount(1);
      await page.getByRole('button', { name: 'Clear all' }).click();
      await expect(page.locator('.active-tag')).toHaveCount(0);
      await expect(rows).toHaveCount(2);
    });
  });

test('FR-012 FR-039 the editor refuses an empty title and the bottom navigation is hidden on chart and edit screens',
  async ({ page, request }) => {
    await withFixtures(request, async ({ prefix, songs }) => {
      const nav = page.locator('.bottom-nav');
      await page.goto('/');
      await expect(nav).toBeVisible();
      await page.getByPlaceholder('Search songs, artists…').fill(prefix);
      await page.getByText(`${prefix} Alpha`, { exact: true }).click();
      await expect(page.locator('.chart-topbar-title')).toHaveText(`${prefix} Alpha`);
      await expect(nav).toHaveCount(0);

      await page.getByRole('button', { name: 'Edit' }).click();
      await expect(page.locator('.topbar-title')).toHaveText('Edit song');
      await expect(nav).toHaveCount(0);
      const title = page.locator('.form-group').filter({ has: page.locator('label', { hasText: /^Title$/ }) }).locator('input');
      await title.fill('   ');
      await page.getByRole('button', { name: 'Save changes' }).click();
      await expect(page.getByText('Title is required')).toBeVisible();
      await expect(page.locator('.topbar-title')).toHaveText('Edit song');
      expect((await (await request.get(`/api/songs/${songs[0].id}`)).json()).title).toBe(`${prefix} Alpha`);
    });
  });
