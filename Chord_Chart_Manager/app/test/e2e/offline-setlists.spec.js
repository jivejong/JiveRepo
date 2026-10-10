import { expect, test as base } from '@playwright/test';
import { randomUUID } from 'node:crypto';

const test = base.extend({
  t10Owned: [async ({ request, context, page }, use) => {
    const suffix = randomUUID();
    const fixture = {
      suffix,
      setlistName: `T10 offline setlist ${suffix}`,
      setlistNames: new Set([`T10 offline setlist ${suffix}`]),
      titles: [`T10 offline song A ${suffix}`, `T10 offline song B ${suffix}`],
      songIds: new Set(),
      setlistIds: new Set(),
    };
    const pendingReleases = new Set();
    fixture.holdUntilCleanup = release => pendingReleases.add(release);
    fixture.releaseHeldRequests = () => {
      for (const release of pendingReleases) release();
      pendingReleases.clear();
    };
    const outstanding = new Set();
    const trackRequest = req => {
      if (/\/api\/(songs|setlists)(\/mutations)?(?:\/|$)/.test(new URL(req.url()).pathname)) outstanding.add(req);
    };
    const finishRequest = req => outstanding.delete(req);
    context.on('request', trackRequest);
    context.on('requestfinished', finishRequest);
    context.on('requestfailed', finishRequest);
    context.on('response', response => {
      void (async () => {
        const req = response.request();
        if (!response.ok() || req.method() !== 'POST') return;
        const url = new URL(req.url());
        const body = req.postDataJSON();
        const result = await response.json();
        if (url.pathname === '/api/songs' && fixture.titles.includes(body?.title)) fixture.songIds.add(result.id);
        if (url.pathname === '/api/setlists/mutations' && body?.payload?.name === fixture.setlistName) fixture.setlistIds.add(result.id);
      })().catch(error => { fixture.trackingError = error; });
    });
    await use(fixture);

    const failures = [];
    try { fixture.releaseHeldRequests(); } catch (error) { failures.push(error); }
    try { await context.setOffline(false); } catch (error) { failures.push(error); }
    try {
      // The fixture receives its own 90-second teardown budget. Wait for
      // browser requests to finish so a delayed commit cannot race cleanup.
      const settleDeadline = Date.now() + 20_000;
      let stablePolls = 0;
      while (Date.now() < settleDeadline) {
        const pendingOwned = await page.evaluate(async ({ titles, setlistNames }) => {
          const db = await new Promise((resolve, reject) => {
            const opening = indexedDB.open('chart-manager');
            opening.onerror = () => reject(opening.error);
            opening.onsuccess = () => resolve(opening.result);
          });
          const values = await new Promise((resolve, reject) => {
            const transaction = db.transaction(['songs', 'setlists', 'meta']);
            const requests = [transaction.objectStore('songs').getAll(), transaction.objectStore('setlists').getAll(),
              transaction.objectStore('meta').get('pending_mutations')];
            transaction.oncomplete = () => resolve(requests.map(item => item.result));
            transaction.onerror = () => reject(transaction.error);
          });
          db.close();
          const songIds = new Set(values[0].filter(song => titles.includes(song.title)).map(song => String(song.id)));
          const listIds = new Set(values[1].filter(list => setlistNames.includes(list.name)).map(list => String(list.id)));
          return (values[2]?.value || []).filter(item => (item.entity === 'song' && songIds.has(String(item.id)))
            || (item.entity === 'setlist' && listIds.has(String(item.setlist_id))));
        }, { titles: fixture.titles, setlistNames: [...fixture.setlistNames] });
        if (outstanding.size === 0 && pendingOwned.length === 0) stablePolls += 1;
        else stablePolls = 0;
        if (stablePolls >= 3) break;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      if (outstanding.size) throw new Error(`T10 teardown found ${outstanding.size} unfinished API request(s).`);
      if (fixture.trackingError) failures.push(fixture.trackingError);

      // A timed-out test can leave only local projections. Remove only cache
      // records and queue operations bearing this fixture's exact marker.
      await page.evaluate(async ({ titles, setlistNames }) => {
        const db = await new Promise((resolve, reject) => {
          const opening = indexedDB.open('chart-manager');
          opening.onerror = () => reject(opening.error);
          opening.onsuccess = () => resolve(opening.result);
        });
        await new Promise((resolve, reject) => {
          const transaction = db.transaction(['songs', 'setlists', 'meta'], 'readwrite');
          const songs = transaction.objectStore('songs');
          const lists = transaction.objectStore('setlists');
          const meta = transaction.objectStore('meta');
          const songRequest = songs.getAll();
          const listRequest = lists.getAll();
          const queueRequest = meta.get('pending_mutations');
          let ready = 0;
          const done = () => {
            ready += 1;
            if (ready !== 3) return;
            const songIds = new Set(songRequest.result.filter(song => titles.includes(song.title)).map(song => String(song.id)));
            const ownedLists = listRequest.result.filter(list => setlistNames.includes(list.name));
            const listIds = new Set(ownedLists.map(list => String(list.id)));
            const entryIds = new Set(ownedLists.flatMap(list => list.songs || []).map(entry => String(entry.entry_id)));
            for (const song of songRequest.result) if (songIds.has(String(song.id))) songs.delete(song.id);
            for (const list of ownedLists) lists.delete(list.id);
            const queue = queueRequest.result?.value || [];
            const retained = queue.filter(item => {
              if (item.entity === 'song' && songIds.has(String(item.id))) return false;
              if (item.entity !== 'setlist') return true;
              if (listIds.has(String(item.setlist_id))) return false;
              if (item.type === 'entry_add' && songIds.has(String(item.payload.song_id))) return false;
              if (entryIds.has(String(item.payload?.entry_id))) return false;
              return true;
            });
            meta.put({ key: 'pending_mutations', value: retained, updatedAt: Date.now() });
          };
          songRequest.onsuccess = listRequest.onsuccess = queueRequest.onsuccess = done;
          transaction.oncomplete = () => { db.close(); resolve(); };
          transaction.onerror = () => reject(transaction.error);
          transaction.onabort = () => reject(transaction.error || new Error('T10 local cleanup aborted'));
        });
      }, { titles: fixture.titles, setlistNames: [...fixture.setlistNames] });
      const remainingLocal = await page.evaluate(async ({ titles, setlistNames }) => {
        const db = await new Promise((resolve, reject) => {
          const opening = indexedDB.open('chart-manager');
          opening.onerror = () => reject(opening.error);
          opening.onsuccess = () => resolve(opening.result);
        });
        const result = await new Promise((resolve, reject) => {
          const transaction = db.transaction(['songs', 'setlists', 'meta']);
          const songs = transaction.objectStore('songs').getAll();
          const lists = transaction.objectStore('setlists').getAll();
          const queue = transaction.objectStore('meta').get('pending_mutations');
          transaction.oncomplete = () => resolve({ songs: songs.result, lists: lists.result, queue: queue.result?.value || [] });
          transaction.onerror = () => reject(transaction.error);
        });
        db.close();
        const ownedSongIds = new Set(result.songs.filter(song => titles.includes(song.title)).map(song => String(song.id)));
        const ownedSetlistIds = new Set(result.lists.filter(list => setlistNames.includes(list.name)).map(list => String(list.id)));
        return {
          songs: [...ownedSongIds],
          lists: [...ownedSetlistIds],
          pending: result.queue.filter(item => (item.entity === 'song' && ownedSongIds.has(String(item.id)))
            || (item.entity === 'setlist' && ownedSetlistIds.has(String(item.setlist_id)))),
        };
      }, { titles: fixture.titles, setlistNames: [...fixture.setlistNames] });
      if (remainingLocal.songs.length || remainingLocal.lists.length || remainingLocal.pending.length) {
        failures.push(new Error(`T10 local cleanup remains: songs=${remainingLocal.songs.join(',')}; lists=${remainingLocal.lists.join(',')}; pending=${remainingLocal.pending.map(item => `${item.entity}:${item.type || item.id}`).join(',')}`));
      }

      const listsResponse = await request.get('/api/setlists');
      if (!listsResponse.ok()) throw new Error(`Could not inspect T10 setlists: HTTP ${listsResponse.status()}`);
      const lists = (await listsResponse.json()).filter(item => fixture.setlistNames.has(item.name));
      for (const name of fixture.setlistNames) {
        const sameName = lists.filter(item => item.name === name);
        if (sameName.length > 1) failures.push(new Error(`T10 fixture setlist name is not unique: ${name}`));
        else if (sameName.length === 1) fixture.setlistIds.add(sameName[0].id);
      }
      for (const id of fixture.setlistIds) {
        const detail = await request.get(`/api/setlists/${id}`);
        if (detail.status() === 404) continue;
        if (!detail.ok()) throw new Error(`Could not inspect owned T10 setlist ${id}: HTTP ${detail.status()}`);
        const row = await detail.json();
        const deleted = await request.post('/api/setlists/mutations', { data: {
          operation_id: randomUUID(), setlist_id: id, type: 'delete',
          base_updated_at: row.updated_at, payload: {},
        } });
        if (!deleted.ok() && deleted.status() !== 404) throw new Error(`Could not delete owned T10 setlist ${id}: HTTP ${deleted.status()}`);
        if ((await request.get(`/api/setlists/${id}`)).status() !== 404) {
          throw new Error(`Owned T10 setlist ${id} remains after cleanup.`);
        }
      }
      for (const title of fixture.titles) {
        const response = await request.get(`/api/songs?q=${encodeURIComponent(title)}`);
        if (!response.ok()) throw new Error(`Could not inspect T10 song ${title}: HTTP ${response.status()}`);
        const songs = (await response.json()).songs.filter(item => item.title === title);
        if (songs.length > 1) failures.push(new Error(`T10 fixture song title is not unique: ${title}`));
        else for (const song of songs) fixture.songIds.add(song.id);
      }
      for (const songId of fixture.songIds) {
        const detail = await request.get(`/api/songs/${songId}`);
        if (detail.status() === 404) continue;
        if (!detail.ok()) throw new Error(`Could not read owned T10 song ${songId}`);
        const row = await detail.json();
        const deleted = await request.delete(`/api/songs/${songId}`, {
          data: { base_updated_at: row.updated_at },
        });
        if (!deleted.ok() && deleted.status() !== 404) throw new Error(`Could not delete owned T10 song ${songId}: HTTP ${deleted.status()}`);
        if ((await request.get(`/api/songs/${songId}`)).status() !== 404) {
          throw new Error(`Owned T10 song ${songId} remains after cleanup.`);
        }
      }
      for (const id of fixture.setlistIds) if ((await request.get(`/api/setlists/${id}`)).status() !== 404) {
        throw new Error(`Owned T10 setlist ${id} reappeared after cleanup.`);
      }
      for (const id of fixture.songIds) if ((await request.get(`/api/songs/${id}`)).status() !== 404) {
        throw new Error(`Owned T10 song ${id} reappeared after cleanup.`);
      }
    } catch (error) { failures.push(error); }
    context.off('request', trackRequest);
    context.off('requestfinished', finishRequest);
    context.off('requestfailed', finishRequest);
    if (failures.length) throw new AggregateError(failures, 'T10 exact-ID fixture cleanup failed');
  }, { timeout: 90_000 }],
});

async function createServerSetlist(request, name, fixture = null) {
  const response = await request.post('/api/setlists/mutations', { data: {
    operation_id: randomUUID(), type: 'create', setlist_id: null,
    base_updated_at: null, payload: { name },
  } });
  expect(response.ok(), `T10 server fixture create returned HTTP ${response.status()}`).toBeTruthy();
  const setlist = await response.json();
  fixture?.setlistNames.add(name);
  fixture?.setlistIds.add(setlist.id);
  return setlist;
}

async function mutateSetlist(request, type, id, token, payload) {
  return request.post('/api/setlists/mutations', { data: {
    operation_id: randomUUID(), type, setlist_id: id,
    base_updated_at: token, payload,
  } });
}

test('offline song creation feeds an immediately editable setlist and syncs once', async ({ page, context, request, t10Owned }) => {
  await context.addInitScript(() => Object.defineProperty(navigator, 'locks', {
    configurable: true, value: undefined,
  }));
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await expect.poll(async () => (await request.get('/api/export')).ok()).toBeTruthy();
  const peer = await context.newPage();
  await peer.goto('/');
  await expect(peer.getByPlaceholder('Search songs, artists…')).toBeVisible();
  // Installed Chrome throttles requestAnimationFrame in background tabs;
  // keep the page under test foregrounded for reliable actionability checks.
  await page.bringToFront();
  await context.setOffline(true);

  for (const title of t10Owned.titles) {
    await page.getByRole('button', { name: 'Add a new song' }).click();
    await page.locator('.form-group').filter({ hasText: 'Title' }).locator('input').fill(title);
    await page.locator('.form-group').filter({ hasText: 'Artist' }).locator('input').fill('T10 invented artist');
    await page.locator('.form-textarea').fill('Verse\nInvented offline test words');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  }

  await page.getByRole('button', { name: 'Setlists' }).click();
  await page.getByRole('button', { name: 'Create a setlist' }).click();
  await page.locator('.setlist-form input').first().fill(t10Owned.setlistName);
  await page.getByRole('button', { name: 'Create setlist' }).click();
  await expect(page.getByText(t10Owned.setlistName, { exact: true })).toBeVisible();

  const addEntry = async title => {
    await page.getByRole('button', { name: 'Add song' }).click();
    await page.locator('.setlist-form select').selectOption({ label: `${title} — T10 invented artist` });
    await page.getByRole('button', { name: 'Add to setlist' }).click();
    await expect(page.locator('.song-title', { hasText: title })).toBeVisible();
  };
  await addEntry(t10Owned.titles[0]);
  await addEntry(t10Owned.titles[1]);
  await page.getByRole('button', { name: `Move ${t10Owned.titles[1]} up` }).click();
  await expect.poll(() => page.locator('.song-title').allTextContents())
    .toEqual([t10Owned.titles[1], t10Owned.titles[0]]);

  await page.getByRole('button', { name: 'Edit setlist' }).click();
  t10Owned.setlistName = `${t10Owned.setlistName} renamed`;
  t10Owned.setlistNames.add(t10Owned.setlistName);
  await page.locator('#setlist-name').fill(t10Owned.setlistName);
  await page.locator('#setlist-date').fill('2026-10-10');
  await page.locator('#setlist-notes').fill('Invented offline event note');
  await page.getByRole('button', { name: 'Save setlist' }).click();
  await expect(page.getByText(t10Owned.setlistName, { exact: true })).toBeVisible();

  await page.getByRole('button', { name: `Edit ${t10Owned.titles[0]} entry` }).click();
  await page.locator('.setlist-entry-editor input').nth(0).fill('D');
  await page.locator('.setlist-entry-editor input').nth(1).fill('0');
  await page.locator('.setlist-entry-editor input').nth(2).fill('Invented entry note');
  await page.getByRole('button', { name: 'Save entry' }).click();

  await page.locator('.song-row').filter({ hasText: t10Owned.titles[0] }).click();
  await page.getByRole('button', { name: 'Increase capo' }).click();
  await page.getByRole('button', { name: 'Save to setlist' }).click();
  await expect(page.getByRole('button', { name: 'Save to setlist' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Back' }).click();
  await page.getByRole('button', { name: 'Setlists' }).click();
  await page.getByText(t10Owned.setlistName, { exact: true }).click();

  await page.getByRole('button', { name: `Remove ${t10Owned.titles[1]}` }).click();
  await expect(page.locator('.song-title', { hasText: t10Owned.titles[1] })).toHaveCount(0);
  await addEntry(t10Owned.titles[1]);
  await page.getByRole('button', { name: `Move ${t10Owned.titles[1]} up` }).click();
  await expect.poll(() => page.locator('.song-title').allTextContents())
    .toEqual([t10Owned.titles[1], t10Owned.titles[0]]);

  const pendingDebug = await page.evaluate(async () => new Promise((resolve, reject) => {
    const request = indexedDB.open('chart-manager');
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const tx = request.result.transaction('meta');
      const get = tx.objectStore('meta').get('pending_mutations');
      get.onsuccess = () => resolve(get.result?.value?.map(item => `${item.entity}:${item.type || item.id}`) || []);
    };
  }));
  expect(pendingDebug.filter(item => item.startsWith('song:'))).toHaveLength(2);
  expect(pendingDebug.filter(item => item.startsWith('setlist:')).sort()).toEqual([
    'setlist:create', 'setlist:entry_add', 'setlist:entry_add', 'setlist:entry_edit', 'setlist:reorder',
  ]);

  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText(/2 song changes, 5 setlist changes waiting to sync/)).toBeVisible();
  await peer.bringToFront();
  await peer.getByRole('button', { name: 'Settings' }).click();
  await expect(peer.getByText(/2 song changes, 5 setlist changes waiting to sync/)).toBeVisible();
  await page.bringToFront();
  await context.setOffline(false);

  let syncedSetlist;
  await expect.poll(async () => {
    const response = await request.get('/api/setlists');
    if (!response.ok()) return false;
    syncedSetlist = (await response.json()).find(item => item.name === t10Owned.setlistName);
    return Boolean(syncedSetlist);
  }, { timeout: 20_000 }).toBeTruthy();
  let synced;
  await expect.poll(async () => {
    const detail = await request.get(`/api/setlists/${syncedSetlist.id}`);
    if (!detail.ok()) return false;
    synced = await detail.json();
    return JSON.stringify(synced.songs.map(item => item.title))
      === JSON.stringify([t10Owned.titles[1], t10Owned.titles[0]]);
  }, { timeout: 20_000 }).toBeTruthy();
  expect(new Set(synced.songs.map(item => item.entry_id)).size).toBe(2);
  expect(synced.name).toBe(t10Owned.setlistName);
  expect(synced.gig_date).toBe('2026-10-10');
  expect(synced.notes).toBe('Invented offline event note');
  const editedEntry = synced.songs.find(item => item.title === t10Owned.titles[0]);
  expect(editedEntry.transposed_key).toBe('D');
  expect(editedEntry.capo_fret).toBe(1);
  expect(editedEntry.notes).toBe('Invented entry note');

  const syncButton = page.getByRole('button', { name: 'Sync now' });
  await expect(syncButton).toBeVisible();
  await syncButton.click();
  await expect.poll(async () => {
    const response = await request.get(`/api/setlists/${syncedSetlist.id}`);
    if (!response.ok()) return false;
    const after = await response.json();
    return after.songs.length === 2 && after.songs[0].title === t10Owned.titles[1];
  }).toBeTruthy();

  await context.setOffline(true);
  await page.getByRole('button', { name: 'Setlists' }).click();
  await page.getByText(t10Owned.setlistName, { exact: true }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Delete setlist' }).click();
  await expect(page.getByText(t10Owned.setlistName, { exact: true })).toHaveCount(0);
  await context.setOffline(false);
  await expect.poll(async () => (await request.get(`/api/setlists/${syncedSetlist.id}`)).status()).toBe(404);
});

test('stale setlist sync discards the whole local aggregate and shows the server version', async ({ page, context, request, t10Owned }) => {
  const name = t10Owned.setlistName;
  const setlist = await createServerSetlist(request, name, t10Owned);
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Setlists' }).click();
  await expect(page.getByText(name, { exact: true })).toBeVisible();
  await page.getByText(name, { exact: true }).click();
  await expect(page.locator('.topbar-title')).toHaveText(name);
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Edit setlist' }).click();
  await page.locator('#setlist-notes').fill('Invented tablet change');
  await page.getByRole('button', { name: 'Save setlist' }).click();

  const latest = await (await request.get(`/api/setlists/${setlist.id}`)).json();
  const concurrent = await mutateSetlist(request, 'metadata', setlist.id, latest.updated_at,
    { notes: 'Invented server change' });
  expect(concurrent.ok()).toBeTruthy();
  await context.setOffline(false);
  await expect(page.getByText('Invented server change', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText(/tablet change was replaced/i)).toBeVisible();
  const authoritative = await (await request.get(`/api/setlists/${setlist.id}`)).json();
  expect(authoritative.notes).toBe('Invented server change');
  expect(authoritative.songs).toHaveLength(0);
});

test('a server-deleted song dependency conflicts without retaining the queued entry', async ({ page, context, request, t10Owned }) => {
  const setlist = await createServerSetlist(request, t10Owned.setlistName, t10Owned);
  const songResponse = await request.post('/api/songs', { data: {
    title: t10Owned.titles[0], artist: 'T10 dependency fixture artist',
    chart_source: 'Verse\nInvented dependency fixture',
  } });
  expect(songResponse.ok()).toBeTruthy();
  const song = await songResponse.json();
  t10Owned.songIds.add(song.id);
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Setlists' }).click();
  await page.getByText(t10Owned.setlistName, { exact: true }).click();
  await expect(page.locator('.topbar-title')).toHaveText(t10Owned.setlistName);
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Add song' }).click();
  await page.locator('.setlist-form select').selectOption(String(song.id));
  await page.getByRole('button', { name: 'Add to setlist' }).click();
  await expect(page.locator('.song-title', { hasText: t10Owned.titles[0] })).toBeVisible();

  const latestSong = await (await request.get(`/api/songs/${song.id}`)).json();
  const deleted = await request.delete(`/api/songs/${song.id}`, {
    data: { base_updated_at: latestSong.updated_at },
  });
  expect(deleted.ok()).toBeTruthy();
  await context.setOffline(false);
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText(/tablet change was replaced/i)).toBeVisible();
  const authoritative = await (await request.get(`/api/setlists/${setlist.id}`)).json();
  expect(authoritative.songs).toHaveLength(0);
});

test('a closed fallback lease owner is recovered after its expiry without frame polling', async ({ page, context }) => {
  await context.addInitScript(() => Object.defineProperty(navigator, 'locks', {
    configurable: true, value: undefined,
  }));
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText('Never', { exact: true })).toHaveCount(0);
  await page.evaluate(async () => {
    const db = await new Promise((resolve, reject) => {
      const opening = indexedDB.open('chart-manager');
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('meta', 'readwrite');
      transaction.objectStore('meta').put({ key: 'sync_lease', value: {
        owner: 'closed-t10-owner', fence: 12, expires: Date.now() + 500,
      }, updatedAt: Date.now() });
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onerror = () => reject(transaction.error);
    });
  });
  let exports = 0;
  page.on('request', requestEvent => {
    if (new URL(requestEvent.url()).pathname === '/api/export') exports += 1;
  });
  const before = exports;
  await page.getByRole('button', { name: 'Sync now' }).click();
  await expect.poll(() => exports, { timeout: 8_000 }).toBeGreaterThan(before);
  await expect(page.getByText('Never', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Sync failed:/)).toHaveCount(0);
});

test('a lost song-create response replays its frozen body and maps dependent setlist entries', async ({ page, context, request, t10Owned }) => {
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await context.setOffline(true);
  const title = t10Owned.titles[0];
  await page.getByRole('button', { name: 'Add a new song' }).click();
  await page.locator('.form-group').filter({ hasText: 'Title' }).locator('input').fill(title);
  await page.locator('.form-group').filter({ hasText: 'Artist' }).locator('input').fill('T10 invented artist');
  await page.locator('.form-textarea').fill('Invented create replay fixture');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
  await page.getByRole('button', { name: 'Back' }).click();

  await page.getByRole('button', { name: 'Setlists' }).click();
  await page.getByRole('button', { name: 'Create a setlist' }).click();
  await page.locator('.setlist-form input').first().fill(t10Owned.setlistName);
  await page.getByRole('button', { name: 'Create setlist' }).click();
  await page.getByRole('button', { name: 'Add song' }).click();
  await page.locator('.setlist-form select').selectOption({ label: `${title} — T10 invented artist` });
  await page.getByRole('button', { name: 'Add to setlist' }).click();
  await expect(page.locator('.song-title', { hasText: title })).toBeVisible();

  let firstCommit;
  const committed = new Promise(resolve => { firstCommit = resolve; });
  let loseResponse = true;
  await page.route('**/api/songs', async route => {
    if (route.request().method() === 'POST' && loseResponse) {
      loseResponse = false;
      await route.fetch();
      firstCommit();
      await route.abort('failed');
      return;
    }
    await route.continue();
  });
  await context.setOffline(false);
  await committed;
  await context.setOffline(true);

  await page.evaluate(async ({ title }) => {
    const db = await new Promise((resolve, reject) => {
      const opening = indexedDB.open('chart-manager');
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta'], 'readwrite');
      const songs = transaction.objectStore('songs');
      const meta = transaction.objectStore('meta');
      const allSongs = songs.getAll();
      const queueRequest = meta.get('pending_mutations');
      let ready = 0;
      const done = () => {
        ready += 1;
        if (ready !== 2) return;
        const local = allSongs.result.find(song => song.title === title && String(song.id).startsWith('local-'));
        if (!local) throw new Error('Lost-response fixture local song was not retained.');
        local.bpm = 123;
        songs.put(local);
        const queue = queueRequest.result.value;
        const operation = queue.find(item => item.entity === 'song' && String(item.id) === String(local.id));
        if (!operation?.sent_payload) throw new Error('Create body was not frozen at dispatch.');
        operation.local_revision += 1;
        meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      };
      allSongs.onsuccess = queueRequest.onsuccess = done;
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onerror = () => reject(transaction.error);
    });
  }, { title });
  await page.unroute('**/api/songs');
  await context.setOffline(false);

  let serverSetlist;
  await expect.poll(async () => {
    const response = await request.get('/api/setlists');
    if (!response.ok()) return false;
    serverSetlist = (await response.json()).find(item => item.name === t10Owned.setlistName);
    return Boolean(serverSetlist);
  }, { timeout: 20_000 }).toBeTruthy();
  let details;
  await expect.poll(async () => {
    const response = await request.get(`/api/setlists/${serverSetlist.id}`);
    if (!response.ok()) return false;
    details = await response.json();
    return details.songs.length === 1;
  }, { timeout: 20_000 }).toBeTruthy();
  const mappedSongId = details.songs[0].song_id;
  expect(mappedSongId).toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('button', { name: 'Sync now' }).click();
  let songs;
  await expect.poll(async () => {
    const response = await request.get(`/api/songs?q=${encodeURIComponent(title)}`);
    if (!response.ok()) return false;
    songs = (await response.json()).songs.filter(song => song.title === title);
    return songs.length === 1 && songs[0].bpm === 123;
  }, { timeout: 20_000 }).toBeTruthy();
  expect(songs[0].id).toBe(mappedSongId);
});

test('online editing of a pending local song preserves its ID redirect and later field update', async ({ page, context, request, t10Owned }) => {
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await context.setOffline(true);
  const title = t10Owned.titles[0];
  await page.getByRole('button', { name: 'Add a new song' }).click();
  await page.locator('.form-group').filter({ hasText: 'Title' }).locator('input').fill(title);
  await page.locator('.form-group').filter({ hasText: 'Artist' }).locator('input').fill('T10 online edit artist');
  await page.locator('.form-textarea').fill('Invented pending create words');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();

  let commitStarted;
  let releaseCommit;
  const committed = new Promise(resolve => { commitStarted = resolve; });
  const release = new Promise(resolve => { releaseCommit = resolve; });
  t10Owned.holdUntilCleanup(releaseCommit);
  await page.route('**/api/songs', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch();
    commitStarted();
    await release;
    await route.fulfill({ response });
  });
  await context.setOffline(false);
  await committed;
  await page.getByRole('button', { name: 'Edit' }).click();
  await page.locator('.form-group').filter({ hasText: 'BPM' }).locator('input').fill('123');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect.poll(() => page.evaluate(async () => new Promise((resolve, reject) => {
    const opening = indexedDB.open('chart-manager');
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const request = opening.result.transaction('meta').objectStore('meta').get('pending_mutations');
      request.onsuccess = () => resolve(request.result.value.find(item => item.entity === 'song')?.local_revision || 0);
    };
  }))).toBeGreaterThan(1);
  releaseCommit();
  t10Owned.releaseHeldRequests();

  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
  let serverSongs;
  await expect.poll(async () => {
    const response = await request.get(`/api/songs?q=${encodeURIComponent(title)}`);
    if (!response.ok()) return false;
    serverSongs = (await response.json()).songs.filter(song => song.title === title);
    return serverSongs.length === 1 && serverSongs[0].bpm === 123;
  }, { timeout: 20_000 }).toBeTruthy();
  expect(serverSongs[0].id).toBeGreaterThan(0);
});

test('setlist conflicts stay visible after a later unrelated push failure and reload', async ({ page, context, request, t10Owned }) => {
  const first = await createServerSetlist(request, `${t10Owned.setlistName} A`, t10Owned);
  const second = await createServerSetlist(request, `${t10Owned.setlistName} B`, t10Owned);
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Setlists' }).click();
  await expect(page.getByText(first.name, { exact: true })).toBeVisible();
  await expect(page.getByText(second.name, { exact: true })).toBeVisible();
  const editNotesOffline = async (name, note) => {
    await page.getByText(name, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit setlist' }).click();
    await page.locator('#setlist-notes').fill(note);
    await page.getByRole('button', { name: 'Save setlist' }).click();
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  };
  await context.setOffline(true);
  await editNotesOffline(first.name, 'T10 local conflict');
  await editNotesOffline(second.name, 'T10 unrelated pending change');

  const current = await (await request.get(`/api/setlists/${first.id}`)).json();
  const concurrent = await mutateSetlist(request, 'metadata', first.id, current.updated_at,
    { notes: 'T10 authoritative server note' });
  expect(concurrent.ok()).toBeTruthy();
  await page.route('**/api/setlists/mutations', async route => {
    const body = route.request().postDataJSON();
    if (Number(body.setlist_id) === Number(second.id) && body.type === 'metadata') {
      await route.abort('failed');
      return;
    }
    await route.continue();
  });
  await context.setOffline(false);
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText('1 tablet change was replaced')).toBeVisible();
  await expect(page.getByText(/Sync failed:/)).toBeVisible();
  await page.unroute('**/api/setlists/mutations');
  await page.reload();
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText('1 tablet change was replaced')).toBeVisible();
  expect((await (await request.get(`/api/setlists/${first.id}`)).json()).notes)
    .toBe('T10 authoritative server note');
});

test('fixture teardown waits for a delayed server commit after the test body completes', async ({ page, context, t10Owned }) => {
  let commitStarted;
  const committed = new Promise(resolve => { commitStarted = resolve; });
  await page.route('**/api/songs', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch();
    commitStarted();
    await new Promise(resolve => setTimeout(resolve, 700));
    await route.fulfill({ response });
  });
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Add a new song' }).click();
  await page.locator('.form-group').filter({ hasText: 'Title' }).locator('input').fill(t10Owned.titles[0]);
  await page.locator('.form-group').filter({ hasText: 'Artist' }).locator('input').fill('T10 delayed fixture artist');
  await page.locator('.form-textarea').fill('Invented delayed cleanup fixture');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
  await context.setOffline(false);
  // The server has committed, but the app has not received the response when
  // the test body ends. Fixture teardown must wait, discover the exact ID,
  // delete it, and verify it remains absent.
  await committed;
});

