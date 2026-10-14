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

async function markCachedSongDeleted(page, id) {
  await page.evaluate(songId => new Promise((resolve, reject) => {
    const opening = indexedDB.open('chart-manager', 1);
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const db = opening.result;
      const tx = db.transaction('songs', 'readwrite');
      const store = tx.objectStore('songs');
      const get = store.get(songId);
      get.onerror = () => reject(get.error);
      get.onsuccess = () => {
        if (!get.result) { reject(new Error('Synthetic cached row disappeared before deletion probe.')); return; }
        store.put({ ...get.result, _sync: { operation: 'delete', base_updated_at: get.result.updated_at } });
      };
      tx.oncomplete = () => {
        db.close();
        window.dispatchEvent(new Event('chart-cache-change'));
        resolve();
      };
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('Synthetic pending-delete transaction aborted.'));
    };
  }), id);
}

async function installHeldListFetch(page) {
  await page.addInitScript(() => {
    window.__t13ListHold = false;
    window.__t13ListStarted = [];
    window.__t13ListRelease = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
      if (url.pathname === '/api/songs' && window.__t13ListHold) {
        window.__t13ListStarted.push(url.search);
        return new Promise((resolve, reject) => window.__t13ListRelease.push(() =>
          reject(new TypeError('Synthetic held song-list request'))));
      }
      return originalFetch(input, init);
    };
  });
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
test('cached song list appears before an unreachable online request and on browser-offline navigation',
  async ({ page, context, cachedOpenFixtures }) => {
    const { prefix, titles } = cachedOpenFixtures;
    await page.goto('/');
    await page.getByPlaceholder('Search songs, artists…').fill(prefix);
    const firstRow = page.locator('.song-row').filter({ hasText: titles[0] });
    await expect(firstRow).toBeVisible();
    await expect.poll(() => readCachedFixtureIds(page, titles)).not.toBeNull();

    // Model an unreachable API while the browser still reports online. The
    // delay and failed fetch occur in this page only; no service or main
    // browser storage is changed.
    await page.addInitScript(() => {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
        if (url.pathname === '/api/songs') {
          window.__t12ListFetchStarted = true;
          await new Promise(resolve => setTimeout(resolve, 5_000));
          throw new TypeError('Synthetic unreachable song-list server');
        }
        return originalFetch(input, init);
      };
    });
    const onlineAt = Date.now();
    await page.reload();
    expect(await page.evaluate(() => navigator.onLine)).toBe(true);
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toBeVisible({ timeout: 1_500 });
    const unreachableMs = Date.now() - onlineAt;
    expect(unreachableMs).toBeLessThan(1_000);
    expect(await page.evaluate(() => window.__t12ListFetchStarted)).toBe(true);
    console.log(`T12 cached song list appeared in ${unreachableMs} ms with navigator online and /api/songs held for 5000 ms.`);

    await context.setOffline(true);
    await expect.poll(() => page.evaluate(() => navigator.onLine)).toBe(false);
    const offlineAt = Date.now();
    await page.reload();
    await expect(page.locator('.song-row').filter({ hasText: titles[1] })).toBeVisible({ timeout: 1_500 });
    const offlineMs = Date.now() - offlineAt;
    expect(offlineMs).toBeLessThan(1_000);
    console.log(`T12 cached song list appeared in ${offlineMs} ms with browser offline emulation.`);
    await context.setOffline(false);
  });

test('successful new-song save caches the first chart open for a later offline reopen',
  async ({ page, context, request }) => {
    const suffix = randomUUID();
    const title = `T12 first-open save ${suffix}`;
    let id;
    try {
      await page.goto('/');
      await page.getByRole('button', { name: 'Add a new song' }).click();
      await page.locator('.form-group').nth(0).locator('input').fill(title);
      await page.getByRole('combobox', { name: 'Artist' }).fill(`T12 first-open artist ${suffix}`);
      await page.locator('.form-textarea').fill('Verse\nC\tSynthetic first-open lyric');
      const createdResponse = page.waitForResponse(response => response.url().endsWith('/api/songs')
        && response.request().method() === 'POST');
      await page.getByRole('button', { name: 'Save changes' }).click();
      const created = await (await createdResponse).json();
      id = created.id;
      await expect(page.locator('.chart-topbar-title')).toHaveText(title);
      await expect(page.getByText(/refreshed chart could not be saved/i)).toHaveCount(0);
      await expect.poll(() => page.evaluate(async ({ songId, version, source }) => {
        const opened = indexedDB.open('chart-manager', 1);
        const db = await new Promise((resolve, reject) => {
          opened.onsuccess = () => resolve(opened.result);
          opened.onerror = () => reject(opened.error);
        });
        const result = await new Promise((resolve, reject) => {
          const get = db.transaction('songs').objectStore('songs').get(songId);
          get.onsuccess = () => resolve(get.result || null);
          get.onerror = () => reject(get.error);
        });
        db.close();
        return result?.updated_at === version && result?.chart_source === source;
      }, { songId: id, version: created.updated_at, source: created.chart_source })).toBe(true);

      await context.setOffline(true);
      await page.getByRole('button', { name: 'Back' }).click();
      await expect(page.locator('.song-row').filter({ hasText: title })).toBeVisible();
      await page.locator('.song-row').filter({ hasText: title }).click();
      await expect(page.locator('.chart-topbar-title')).toHaveText(title);
      await expect(page.getByText(/refreshed chart could not be saved/i)).toHaveCount(0);
      console.log('T12 new song was cached after the successful save and reopened offline without a cache warning.');
    } finally {
      await context.setOffline(false).catch(() => {});
      if (id) {
        const current = await request.get(`/api/songs/${id}`);
        if (current.ok()) {
          const song = await current.json();
          const deleted = await request.delete(`/api/songs/${id}`, { data: { base_updated_at: song.updated_at } });
          if (!deleted.ok()) throw new Error(`Could not remove synthetic first-open song ${id}: HTTP ${deleted.status()}.`);
        } else if (current.status() !== 404) {
          throw new Error(`Could not inspect synthetic first-open song ${id}: HTTP ${current.status()}.`);
        }
        if ((await request.get(`/api/songs/${id}`)).status() !== 404) {
          throw new Error(`Synthetic first-open song ${id} remains after cleanup.`);
        }
      }
    }
  });

test('clears prior search rows for a successful empty cache projection while the server request is held',
  async ({ page, cachedOpenFixtures }) => {
    const { prefix, titles } = cachedOpenFixtures;
    await installHeldListFetch(page);
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toBeVisible();
    await expect.poll(() => readCachedFixtureIds(page, titles)).not.toBeNull();

    await page.evaluate(() => { window.__t13ListHold = true; });
    await search.fill('no cached fixture matches this term');
    await expect.poll(() => page.evaluate(() => window.__t13ListStarted.length)).toBeGreaterThan(0);
    await expect(page.locator('.song-row')).toHaveCount(0);
    await expect(page.locator('.loading')).toBeVisible();
    await expect(page.getByText('No songs found')).toHaveCount(0);
    await page.evaluate(() => window.__t13ListRelease.splice(0).forEach(release => release()));
  });

test('clears the final matching cached row when a pending delete arrives during a held list request',
  async ({ page, cachedOpenFixtures }) => {
    const { prefix, titles, ids } = cachedOpenFixtures;
    await installHeldListFetch(page);
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toBeVisible();
    await expect.poll(() => readCachedFixtureIds(page, titles)).not.toBeNull();

    await page.evaluate(() => { window.__t13ListHold = true; });
    await search.fill(titles[0]);
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__t13ListStarted.length)).toBeGreaterThan(0);
    await markCachedSongDeleted(page, ids[0]);

    await expect(page.locator('.song-row')).toHaveCount(0);
    await expect(page.locator('.loading')).toBeVisible();
    await expect(page.getByText('No songs found')).toHaveCount(0);
    await page.evaluate(() => window.__t13ListRelease.splice(0).forEach(release => release()));
  });

test('rapid search changes keep the newest cached projection while all server responses are held',
  async ({ page, cachedOpenFixtures }) => {
    const { prefix, titles } = cachedOpenFixtures;
    await installHeldListFetch(page);
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toBeVisible();
    await expect.poll(() => readCachedFixtureIds(page, titles)).not.toBeNull();

    await page.evaluate(() => { window.__t13ListHold = true; });
    await search.fill(titles[0]);
    await search.fill(titles[1]);
    await expect.poll(() => page.evaluate(() => window.__t13ListStarted.length)).toBeGreaterThanOrEqual(2);
    await expect(page.locator('.song-row').filter({ hasText: titles[1] })).toBeVisible();
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toHaveCount(0);
    await expect(page.locator('.loading')).toHaveCount(0);
    await page.evaluate(() => window.__t13ListRelease.splice(0).forEach(release => release()));
  });

test('late cache fallback after a failed older search cannot replace the newer search',
  async ({ page, cachedOpenFixtures }) => {
    const { prefix, titles } = cachedOpenFixtures;
    await page.addInitScript(() => {
      window.__t13FallbackProbe = { delaySongReads: false, rejectNextList: false, heldReads: [] };
      const nativeGetAll = IDBObjectStore.prototype.getAll;
      IDBObjectStore.prototype.getAll = function (...args) {
        const request = nativeGetAll.apply(this, args);
        if (this.name !== 'songs' || !window.__t13FallbackProbe.delaySongReads) return request;
        return {
          get result() { return request.result; },
          get error() { return request.error; },
          set onsuccess(handler) {
            request.onsuccess = () => window.__t13FallbackProbe.heldReads.push(() => handler());
          },
          set onerror(handler) { request.onerror = handler; },
        };
      };
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
        if (url.pathname === '/api/songs' && window.__t13FallbackProbe.rejectNextList) {
          window.__t13FallbackProbe.rejectNextList = false;
          return Promise.reject(new TypeError('Synthetic failed list request'));
        }
        return nativeFetch(input, init);
      };
    });
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(prefix);
    await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toBeVisible();
    await expect.poll(() => readCachedFixtureIds(page, titles)).not.toBeNull();

    await page.evaluate(() => {
      window.__t13FallbackProbe.delaySongReads = true;
      window.__t13FallbackProbe.rejectNextList = true;
    });
    try {
      await search.fill(titles[0]);
      // The first getAll belongs to the immediate cached projection; the
      // second belongs to the failed request's cache fallback.
      await expect.poll(() => page.evaluate(() => window.__t13FallbackProbe.heldReads.length))
        .toBeGreaterThanOrEqual(2);
      await page.evaluate(() => { window.__t13FallbackProbe.delaySongReads = false; });

      await search.fill(titles[1]);
      await expect(page.locator('.song-row').filter({ hasText: titles[1] })).toBeVisible();
      await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toHaveCount(0);
      await expect(page.locator('.loading')).toHaveCount(0);
      await expect(page.getByText('No songs found')).toHaveCount(0);

      await page.evaluate(() => window.__t13FallbackProbe.heldReads[1]?.());
      await expect(page.locator('.song-row').filter({ hasText: titles[1] })).toBeVisible();
      await expect(page.locator('.song-row').filter({ hasText: titles[0] })).toHaveCount(0);
      await expect(page.locator('.loading')).toHaveCount(0);
      await expect(page.locator('.list-section-head')).toContainText('1 songs');
      await expect(page.getByText('Using cached data')).toHaveCount(0);
    } finally {
      await page.evaluate(() => window.__t13FallbackProbe.heldReads.forEach(release => release())).catch(() => {});
    }
  });
