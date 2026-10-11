import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { expect, test as base } from '@playwright/test';

const require = createRequire(import.meta.url);
const { chartToText } = require('../../server/chartParser.js');

const test = base.extend({
  cachedOpenFixtures: [async ({ request, context }, use) => {
    const suffix = randomUUID();
    const prefix = `T12 Cached Open ${suffix}`;
    const titles = [`${prefix} A`, `${prefix} B`];
    const ids = [];
    const chart = { sections: [{ label: 'Invented verse', lines: [
      { type: 'lyric', text: 'Invented cached test line', confidence: 'high',
        chords: [{ sym: 'C', pos: 0 }, { sym: 'G', pos: 8 }] },
    ] }] };
    try {
      for (const title of titles) {
        const created = await request.post('/api/songs', { data: {
          title, artist: `T12 synthetic artist ${suffix}`, chart_written_key: 'C',
          chart_source: chartToText(chart),
        } });
        if (!created.ok()) throw new Error(`T12 synthetic chart creation returned HTTP ${created.status()}.`);
        const song = await created.json();
        if (!Number.isInteger(song.id) || song.id < 1) throw new Error('T12 synthetic chart returned no valid ID.');
        ids.push(song.id);
      }
      await use({ ids, prefix, titles });
    } finally {
      await context.setOffline(false).catch(() => {});
      const errors = [];
      for (const id of ids) {
        try {
          const current = await request.get(`/api/songs/${id}`);
          if (current.status() === 404) continue;
          if (!current.ok()) throw new Error(`Could not inspect T12 fixture ID ${id}: HTTP ${current.status()}.`);
          const song = await current.json();
          const deleted = await request.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
          if (!deleted.ok()) throw new Error(`Could not delete T12 fixture ID ${id}: HTTP ${deleted.status()}.`);
          if ((await request.get(`/api/songs/${id}`)).status() !== 404) {
            throw new Error(`T12 fixture ID ${id} remains after cleanup.`);
          }
        } catch (error) { errors.push(error); }
      }
      if (errors.length) throw new AggregateError(errors, 'T12 cached-open fixture cleanup failed.');
    }
  }, { timeout: 60_000 }],
});

async function readCachedFixtureIds(page, titles) {
  return page.evaluate(titles => new Promise((resolve, reject) => {
    const request = indexedDB.open('chart-manager', 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const read = db.transaction('songs').objectStore('songs').getAll();
      read.onsuccess = () => {
        const result = {};
        for (const title of titles) {
          const song = read.result.find(item => item.title === title);
          if (!song) { db.close(); resolve(null); return; }
          result[title] = { id: song.id, pending: Boolean(song._sync) };
        }
        db.close();
        resolve(result);
      };
      read.onerror = () => reject(read.error);
    };
  }), titles);
}

test('cached charts open promptly on song selection, swipe, and browser-offline navigation',
  async ({ page, context, cachedOpenFixtures }) => {
    const { ids, prefix, titles } = cachedOpenFixtures;
    const started = new Set();
    await page.exposeFunction('t12SongGetStarted', id => started.add(Number(id)));
    await page.addInitScript(targetIds => {
      const targets = new Set(targetIds.map(String));
      window.__t12ReleaseSongGets = Object.create(null);
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
        const match = url.pathname.match(/^\/api\/songs\/(\d+)$/);
        if (!match || !targets.has(match[1])) return originalFetch(input, init);
        const id = Number(match[1]);
        await window.t12SongGetStarted(id);
        await new Promise(resolve => { window.__t12ReleaseSongGets[match[1]] = resolve; });
        throw new TypeError('Synthetic network unavailable for song GET');
      };
    }, ids);
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    for (const title of titles) await expect(page.locator('.song-row').filter({ hasText: title })).toBeVisible();

    await expect.poll(() => readCachedFixtureIds(page, titles)).not.toBeNull();
    const cached = await readCachedFixtureIds(page, titles);
    for (const title of titles) expect(cached[title].pending).toBeFalsy();
    expect(cached[titles[0]].id).toBe(ids[0]);
    expect(cached[titles[1]].id).toBe(ids[1]);

    try {
      const openingAt = Date.now();
      await page.locator('.song-row').filter({ hasText: titles[0] }).click();
      await expect.poll(() => started.has(ids[0]), { timeout: 2_000 }).toBeTruthy();
      await expect(page.locator('.chart-topbar-title')).toHaveText(titles[0], { timeout: 1_000 });
      const openMs = Date.now() - openingAt;
      expect(openMs).toBeLessThan(1_000);
      console.log(`T12 cached open appeared in ${openMs} ms while its API request was held.`);

      await page.evaluate(() => {
        const element = document.querySelector('.chart-scroll');
        const start = new Touch({ identifier: 1, target: element, clientX: 260, clientY: 180 });
        const end = new Touch({ identifier: 1, target: element, clientX: 140, clientY: 180 });
        element.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: [start], changedTouches: [start] }));
        element.dispatchEvent(new TouchEvent('touchend', { bubbles: true, touches: [], changedTouches: [end] }));
      });
      await expect.poll(() => started.has(ids[1]), { timeout: 2_000 }).toBeTruthy();
      const swipeAt = Date.now();
      await expect(page.locator('.chart-topbar-title')).toHaveText(titles[1], { timeout: 1_000 });
      const swipeMs = Date.now() - swipeAt;
      expect(swipeMs).toBeLessThan(1_000);
      console.log(`T12 cached swipe destination appeared in ${swipeMs} ms while its API request was held.`);

      const firstLoadSettled = page.evaluate(id => new Promise(resolve => {
        const target = String(id);
        const onSettled = event => {
          if (event.detail?.id !== target) return;
          window.removeEventListener('ccm:song-load-settled', onSettled);
          resolve(true);
        };
        window.addEventListener('ccm:song-load-settled', onSettled);
      }), ids[0]);
      await page.evaluate(id => window.__t12ReleaseSongGets[String(id)]?.(), ids[0]);
      expect(await firstLoadSettled).toBe(true);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await expect(page.locator('.chart-topbar-title')).toHaveText(titles[1]);
      await page.getByRole('button', { name: 'Back' }).click();

      await context.setOffline(true);
      await expect.poll(() => page.evaluate(() => navigator.onLine)).toBeFalsy();
      const offlineAt = Date.now();
      await page.locator('.song-row').filter({ hasText: titles[0] }).click();
      await expect(page.locator('.chart-topbar-title')).toHaveText(titles[0], { timeout: 1_000 });
      const offlineMs = Date.now() - offlineAt;
      expect(offlineMs).toBeLessThan(1_000);
      expect(started.size).toBe(2);
      console.log(`T12 browser-offline cached open appeared in ${offlineMs} ms with no song GET.`);
    } finally {
      await page.evaluate(targetIds => {
        for (const id of targetIds) window.__t12ReleaseSongGets?.[String(id)]?.();
      }, ids).catch(() => {});
      await context.setOffline(false).catch(() => {});
    }
  });
