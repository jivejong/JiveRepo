import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

const source = '[Verse] G - C  / D \\ Em * (4x)';
const originalTail = source.slice(source.indexOf(']') + 1);
const transposedTail = ' A - D  / E \\ F#m * (4x)';

test('renders ordered chord punctuation and spacing literally, including transposition',
  async ({ page, request }) => {
    const title = `T26 ordered raw row ${randomUUID()}`;
    const created = await request.post('/api/songs', { data: {
      title, artist: 'T26 ordered row fixture artist', chart_written_key: 'G', chart_source: source,
    } });
    expect(created.ok()).toBeTruthy();
    const song = await created.json();
    try {
      await page.goto('/');
      await page.getByPlaceholder('Search songs, artists…').fill(title);
      const row = page.locator('.song-row').filter({ hasText: title });
      await expect(row).toBeVisible();
      await row.click();
      await expect(page.locator('.chart-topbar-title')).toHaveText(title);

      const chordRow = page.locator('.chord-only-line');
      await expect(chordRow).toBeVisible();
      expect(await chordRow.evaluate(element => element.textContent)).toBe(originalTail);
      expect(await chordRow.evaluate(element => getComputedStyle(element).whiteSpace)).toBe('pre');

      await page.getByRole('button', { name: 'Up whole step' }).click();
      expect(await chordRow.evaluate(element => element.textContent)).toBe(transposedTail);
      expect(await chordRow.evaluate(element => getComputedStyle(element).whiteSpace)).toBe('pre');
    } finally {
      const latestResponse = await request.get(`/api/songs/${song.id}`);
      if (latestResponse.ok()) {
        const latest = await latestResponse.json();
        const deleted = await request.delete(`/api/songs/${song.id}`, {
          data: { base_updated_at: latest.updated_at },
        });
        expect(deleted.ok()).toBeTruthy();
      }
    }
  });
