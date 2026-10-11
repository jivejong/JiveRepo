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
    const serverSettledRequests = new Set();
    fixture.testBodyEnded = false;
    fixture.holdUntilCleanup = release => pendingReleases.add(release);
    fixture.releaseHeldRequests = () => {
      for (const release of pendingReleases) release();
      pendingReleases.clear();
    };
    const outstanding = new Set();
    const mutationKey = req => `${req.method()} ${req.url()} ${req.postDataJSON()?.operation_id ?? ''}`;
    fixture.markServerMutationSettled = req => {
      const key = mutationKey(req);
      serverSettledRequests.add(key);
      let removed = false;
      for (const candidate of outstanding) {
        if (mutationKey(candidate) === key) {
          outstanding.delete(candidate);
          removed = true;
        }
      }
      return removed;
    };
    const trackRequest = req => {
      if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method())
          || !/\/api\/(songs|setlists)(\/mutations)?(?:\/|$)/.test(new URL(req.url()).pathname)) return;
      if (serverSettledRequests.has(mutationKey(req))) return;
      outstanding.add(req);
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

    fixture.testBodyEnded = true;
    const failures = [];
    try { fixture.releaseHeldRequests(); } catch (error) { failures.push(error); }
    try { await context.setOffline(false); } catch (error) { failures.push(error); }
    try {
      // The fixture receives its own 90-second teardown budget. Wait for
      // browser requests to finish so a delayed commit cannot race cleanup.
      const initialSettleDeadline = Date.now() + (fixture.settleBudgetMs ?? 20_000);
      const cleanupDeadline = Date.now() + 80_000;
      let settleTimeoutReported = false;
      let stablePolls = 0;
      while (Date.now() < cleanupDeadline) {
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
        if (Date.now() >= initialSettleDeadline && outstanding.size > 0 && !settleTimeoutReported) {
          settleTimeoutReported = true;
          const activePaths = [...outstanding].map(req => {
            const body = ['POST', 'PUT'].includes(req.method()) ? req.postDataJSON() : null;
            const title = req.method() === 'POST' && new URL(req.url()).pathname === '/api/songs' ? body?.title : null;
            const marker = fixture.titles.includes(title) ? ` (${title})`
              : req.method() === 'PUT' && fixture.songIds.has(Number(new URL(req.url()).pathname.split('/').pop()))
                ? ` (song ${new URL(req.url()).pathname.split('/').pop()}, bpm ${body?.bpm}, op ${body?.operation_id})` : '';
            return `${req.method()} ${new URL(req.url()).pathname}${marker}`;
          });
          console.warn(`T10 initial request-settle budget elapsed with ${activePaths.join(', ')}; continuing to wait before deletion.`);
        }
        if (Date.now() >= initialSettleDeadline && outstanding.size === 0) break;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      if (outstanding.size) failures.push(new Error(`T10 teardown still has ${outstanding.size} unfinished API request(s) after its independent wait budget.`));
      if (fixture.trackingError) failures.push(fixture.trackingError);
      if (fixture.lateCommitError) failures.push(fixture.lateCommitError);
      if (fixture.requireLateCommit && (!fixture.lateCommitAfterBody || !fixture.lateCommitVerifiedPresent
          || !Number.isSafeInteger(fixture.lateCommitId) || fixture.lateCommitId <= 0)) {
        failures.push(new Error('T10 late-commit fixture did not prove a successful post-body commit and exact-ID presence.'));
      }

      // A timed-out test can leave only local projections. Remove only cache
      // records and queue operations bearing this fixture's exact marker.
      try {
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
      } catch (error) { failures.push(error); }

      try {
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
      } catch (error) { failures.push(error); }

      try {
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
      } catch (error) { failures.push(error); }

      if (outstanding.size) {
        failures.push(new Error('T10 cleanup cannot certify absence while a tracked API request may still commit.'));
      }
      for (const id of fixture.setlistIds) if ((await request.get(`/api/setlists/${id}`)).status() !== 404) {
        throw new Error(`Owned T10 setlist ${id} reappeared after cleanup.`);
      }
      for (const id of fixture.songIds) if ((await request.get(`/api/songs/${id}`)).status() !== 404) {
        throw new Error(`Owned T10 song ${id} reappeared after cleanup.`);
      }
      if (fixture.requireLateCommit && (await request.get(`/api/songs/${fixture.lateCommitId}`)).status() !== 404) {
        throw new Error(`Late-commit song ${fixture.lateCommitId} remains after exact-ID cleanup.`);
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

async function readT10SyncState(page) {
  return page.evaluate(async () => {
    const db = await new Promise((resolve, reject) => {
      const opening = indexedDB.open('chart-manager');
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    const result = await new Promise((resolve, reject) => {
      const transaction = db.transaction('meta');
      const leaseRequest = transaction.objectStore('meta').get('sync_lease');
      const queueRequest = transaction.objectStore('meta').get('pending_mutations');
      transaction.oncomplete = () => resolve({ lease: leaseRequest.result?.value || null,
        pending: queueRequest.result?.value || [] });
      transaction.onerror = () => reject(transaction.error);
    });
    db.close();
    const syncButton = [...document.querySelectorAll('button')]
      .find(button => /^(Sync now|Syncing)/.test(button.textContent.trim()));
    const label = syncButton?.textContent.trim() || 'unavailable';
    return {
      leasePresent: Boolean(result.lease && result.lease.expires > Date.now()),
      leaseFence: result.lease?.fence ?? null,
      leaseExpiresInMs: result.lease ? Math.max(0, result.lease.expires - Date.now()) : 0,
      syncing: label.startsWith('Syncing'),
      syncControl: label === 'Sync now' ? 'idle' : label.startsWith('Syncing') ? 'syncing' : 'unavailable',
      hasSyncError: document.body.innerText.includes('Sync failed:'),
      pendingCount: result.pending.length,
    };
  });
}

function pageLabelForRequest(request, pages) {
  try {
    const source = request.frame().page();
    return pages.find(([, page]) => page === source)?.[0] || null;
  } catch { return null; }
}

function pullKind(request) {
  if (request.method() !== 'GET') return null;
  const path = new URL(request.url()).pathname;
  if (path === '/api/export') return 'export';
  if (path === '/api/setlists') return 'setlists';
  if (/^\/api\/setlists\/\d+$/.test(path)) return `setlist:${path.split('/').at(-1)}`;
  return null;
}

function createPullAttemptTracker(labels) {
  const activeAttempt = new Map(labels.map(label => [label, null]));
  const attemptStates = new Map();
  const requestAttempts = new Map();
  let nextAttemptId = 1;

  const attemptKey = (label, attemptId) => `${label}:${attemptId}`;
  const beginAttempt = label => {
    const attemptId = nextAttemptId++;
    activeAttempt.set(label, attemptId);
    attemptStates.set(attemptKey(label, attemptId), { label, attemptId, requests: [] });
    return attemptId;
  };
  const onRequest = (request, label) => {
    const kind = pullKind(request);
    const attemptId = activeAttempt.get(label);
    if (!kind || attemptId == null) return;
    const record = { label, attemptId, kind, status: 'pending' };
    requestAttempts.set(request, record);
    attemptStates.get(attemptKey(label, attemptId)).requests.push(record);
  };
  const onResponse = response => {
    const record = requestAttempts.get(response.request());
    if (record) record.status = response.ok() ? 'successful' : 'failed';
  };
  const onRequestFailed = request => {
    const record = requestAttempts.get(request);
    if (record && record.status === 'pending') record.status = 'failed';
  };
  const onRequestFinished = request => {
    const record = requestAttempts.get(request);
    if (record && record.status === 'pending') record.status = 'failed';
  };
  const current = label => {
    const attemptId = activeAttempt.get(label);
    if (attemptId == null) return { attemptId: null, started: [], successful: [], failed: [], unresolved: [] };
    const state = attemptStates.get(attemptKey(label, attemptId));
    const kinds = status => [...new Set(state.requests.filter(item => item.status === status).map(item => item.kind))].sort();
    return {
      attemptId,
      started: [...new Set(state.requests.map(item => item.kind))].sort(),
      successful: kinds('successful'),
      failed: kinds('failed'),
      unresolved: kinds('pending'),
    };
  };
  return { beginAttempt, onRequest, onResponse, onRequestFailed, onRequestFinished, current };
}

function pullCompletionFailures(snapshot, labels) {
  return labels.flatMap(label => {
    const state = snapshot.states[label];
    const pulls = snapshot.pulls[label];
    const missingStarted = ['export', 'setlists'].filter(kind => !pulls.started.includes(kind));
    const missingSuccessful = ['export', 'setlists'].filter(kind => !pulls.successful.includes(kind));
    const failures = [];
    if (missingStarted.length) failures.push(`missingPullStart:${missingStarted.join(',')}`);
    if (missingSuccessful.length) failures.push(`missingSuccessfulPull:${missingSuccessful.join(',')}`);
    if (pulls.failed.length) failures.push(`failedPull:${pulls.failed.join(',')}`);
    if (pulls.unresolved.length) failures.push(`unresolvedPull:${pulls.unresolved.join(',')}`);
    if (state.syncControl !== 'idle') failures.push(`syncControl:${state.syncControl}`);
    if (state.hasSyncError) failures.push('syncError:true');
    if (state.pendingCount !== 0) failures.push(`pendingCount:${state.pendingCount}`);
    if (state.outstanding.length !== 0) failures.push(`outstanding:${state.outstanding.join(',')}`);
    return failures.length ? [`${label}[${failures.join(';')}]`] : [];
  });
}

test.describe('T10 two-tab pull completion assertions', () => {
test.use({ serviceWorkers: 'block' });

test('a response from an earlier attempt cannot satisfy a later attempt', async () => {
  const tracker = createPullAttemptTracker(['tabA']);
  const earlierAttempt = tracker.beginAttempt('tabA');
  const earlierRequest = { method: () => 'GET', url: () => 'http://localhost/api/export' };
  tracker.onRequest(earlierRequest, 'tabA');

  const currentAttempt = tracker.beginAttempt('tabA');
  expect(currentAttempt).toBeGreaterThan(earlierAttempt);
  tracker.onResponse({ request: () => earlierRequest, ok: () => true });

  const currentPulls = tracker.current('tabA');
  expect(currentPulls.started).toEqual([]);
  expect(currentPulls.successful).toEqual([]);
  const failures = pullCompletionFailures({
    states: { tabA: { syncControl: 'idle', hasSyncError: false, pendingCount: 0, outstanding: [] } },
    pulls: { tabA: currentPulls },
  }, ['tabA']);
  expect(failures.join(' ')).toContain('missingPullStart:export,setlists');
  expect(failures.join(' ')).toContain('missingSuccessfulPull:export,setlists');
});

test('offline song creation verifies successful two-tab pulls and rejects a failed waiter pull', async ({ page, context, request, t10Owned }) => {
  const leaseDurationMs = 30_000; // Must match withSyncOwnership's documented lease interval.
  const syncCompletionAllowanceMs = 8_000; // Allow the bounded push/pull after one lease interval.
  const setupAndAssertionsAllowanceMs = 7_000;
  test.setTimeout(leaseDurationMs + syncCompletionAllowanceMs + setupAndAssertionsAllowanceMs);
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

  const pages = [['ownerCandidateA', page], ['ownerCandidateB', peer]];
  const pullTracker = createPullAttemptTracker(pages.map(([label]) => label));
  const apiOutstanding = new Map();
  const samples = [];
  let waiterLabel = null;
  let waiterPullFailureInjected = false;
  const capture = async () => {
    const states = Object.fromEntries(await Promise.all(pages.map(async ([label, tab]) => [label,
      { ...(await readT10SyncState(tab)), outstanding: [...apiOutstanding.values()]
        .filter(item => item.label === label).map(item => item.path) }] )));
    const sample = { atMs: Date.now(), ownerRequestPage: heldOwnerLabel || null,
      states, pulls: Object.fromEntries(pages.map(([label]) => [label, pullTracker.current(label)])), waiterPullFailureInjected };
    samples.push(sample);
    if (samples.length > 40) samples.shift();
    return sample;
  };
  let heldOwnerLabel = null;
  let ownerPostStarted = false;
  let releaseOwnerRequest;
  const ownerRequestHeld = new Promise(resolve => { releaseOwnerRequest = resolve; });
  const onRequest = req => {
    const path = new URL(req.url()).pathname;
    if (!path.startsWith('/api/')) return;
    const label = pageLabelForRequest(req, pages);
    if (!label) return;
    const key = `${req.method()} ${path}`;
    apiOutstanding.set(req, { label, path: key });
    pullTracker.onRequest(req, label);
  };
  const onResponse = response => {
    const req = response.request();
    pullTracker.onResponse(response);
  };
  const onRequestEnd = req => {
    pullTracker.onRequestFinished(req);
    apiOutstanding.delete(req);
  };
  const onRequestFailed = req => {
    pullTracker.onRequestFailed(req);
    apiOutstanding.delete(req);
  };
  context.on('request', onRequest);
  context.on('response', onResponse);
  context.on('requestfinished', onRequestEnd);
  context.on('requestfailed', onRequestFailed);
  await context.route('**/api/songs', async route => {
    if (route.request().method() !== 'POST' || route.request().postDataJSON()?.title !== t10Owned.titles[0]) {
      return route.continue();
    }
    heldOwnerLabel = pageLabelForRequest(route.request(), pages);
    ownerPostStarted = true;
    await ownerRequestHeld;
    const response = await route.fetch();
    await route.fulfill({ response });
  });
  const diagnostics = async () => {
    try { await capture(); } catch { samples.push({ snapshotUnavailable: true }); }
    return JSON.stringify(samples.slice(-12));
  };
  const assertTwoTabComplete = snapshot => {
    const failures = pullCompletionFailures(snapshot, pages.map(([label]) => label));
    if (failures.length) {
      // Snapshot contains only local labels, endpoint names, queue counts,
      // lease counters, and UI booleans; it excludes payloads and credentials.
      throw new Error(`two-tab sync incomplete: ${failures.join(' | ')}; state=${JSON.stringify(snapshot)}`);
    }
  };
  let syncedSetlist;
  try {
  for (const [label] of pages) pullTracker.beginAttempt(label);
  await context.setOffline(false);
  try {
    await expect.poll(() => ownerPostStarted, { timeout: 5_000, intervals: [25, 50, 100] }).toBeTruthy();
    expect(heldOwnerLabel, 'The held song-create request must belong to one of the two browser tabs.').toBeTruthy();
  } catch (error) {
    throw new Error(`T10 two-tab sync did not start a tracked owner create request. Sanitized state: ${await diagnostics()}`, { cause: error });
  }
  const firstOwnerWaiter = async () => {
    const state = await capture();
    const owner = state.ownerRequestPage;
    const waiter = pages.find(([label]) => label !== owner)?.[0];
    const ownerState = state.states[owner];
    const waiterState = state.states[waiter];
    return Boolean(owner && ownerState?.leasePresent && ownerState.syncing
      && ownerState.outstanding.some(path => path.startsWith('POST /api/songs'))
      && waiterState?.leasePresent && waiterState.syncing && waiterState.outstanding.length === 0);
  };
  try {
    await expect.poll(firstOwnerWaiter, { timeout: 5_000, intervals: [25, 50, 100] }).toBeTruthy();
  } catch (error) {
    throw new Error(`T10 two-tab sync did not expose an owner request and waiting tab under the live lease. Sanitized state: ${await diagnostics()}`, { cause: error });
  }
  waiterLabel = pages.find(([label]) => label !== heldOwnerLabel)?.[0] || null;
  expect(waiterLabel, 'The non-owner tab must be identified before injecting its pull failure.').toBeTruthy();
  releaseOwnerRequest();

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

  const waitForCompleteTabs = async (timeout = leaseDurationMs + syncCompletionAllowanceMs) => expect.poll(async () => {
    const snapshot = await capture();
    try { assertTwoTabComplete(snapshot); return true; } catch { return false; }
  }, { timeout, intervals: [50, 100, 200, 400] }).toBeTruthy();
  try {
    await waitForCompleteTabs();
  } catch (error) {
    throw new Error(`T10 tabs did not complete successful export and setlist pulls within the bounded lease interval. Sanitized state: ${await diagnostics()}`, { cause: error });
  }

  // The automatic waiter completed above. Now fail one explicit sync attempt
  // in that same waiter tab to prove the checker rejects idle + shared-empty
  // state when this attempt's export pull did not succeed.
  const waiterPage = pages.find(([label]) => label === waiterLabel)?.[1];
  expect(waiterPage).toBeTruthy();
  await waiterPage.route('**/api/export', async route => {
    if (!waiterPullFailureInjected && route.request().method() === 'GET') {
      waiterPullFailureInjected = true;
      await route.abort('internetdisconnected');
      return;
    }
    await route.continue();
  });
  await waiterPage.bringToFront();
  await expect(waiterPage.getByRole('button', { name: 'Sync now' })).toBeVisible();
  pullTracker.beginAttempt(waiterLabel);
  await waiterPage.getByRole('button', { name: 'Sync now' }).click();
  const waiterFailedPullSettled = async () => {
    const state = await capture();
    const waiter = state.states[waiterLabel];
    return state.waiterPullFailureInjected && waiter.syncControl === 'idle' && waiter.hasSyncError
      && state.pulls[waiterLabel].failed.includes('export')
      && waiter.pendingCount === 0 && waiter.outstanding.length === 0;
  };
  try {
    await expect.poll(waiterFailedPullSettled, { timeout: syncCompletionAllowanceMs, intervals: [50, 100, 200, 400] }).toBeTruthy();
  } catch (error) {
    throw new Error(`T10 controlled waiter pull failure did not settle as expected. Sanitized state: ${await diagnostics()}`, { cause: error });
  }
  const failedWaiterSnapshot = await capture();
  let failedCompletionDiagnostic = '';
  try { assertTwoTabComplete(failedWaiterSnapshot); } catch (error) { failedCompletionDiagnostic = error.message; }
  expect(failedCompletionDiagnostic, 'The completion checker must reject an idle waiter whose export pull failed.')
    .toMatch(/failedPull:export/);
  expect(failedCompletionDiagnostic).toContain('state=');
  expect(failedCompletionDiagnostic).toContain('"failed":["export"]');
  expect(failedCompletionDiagnostic).toContain('"hasSyncError":true');
  expect(failedCompletionDiagnostic).not.toContain('Sync failed:');

  await waiterPage.unroute('**/api/export');
  // Start a fresh explicit waiter attempt. Its request records cannot be
  // satisfied by responses that started in the failed attempt.
  await expect(waiterPage.getByRole('button', { name: 'Sync now' })).toBeVisible();
  pullTracker.beginAttempt(waiterLabel);
  await waiterPage.getByRole('button', { name: 'Sync now' }).click();
  try {
    await waitForCompleteTabs(syncCompletionAllowanceMs);
  } catch (error) {
    throw new Error(`T10 tabs did not complete successful export and setlist pulls after the controlled failure recovery. Sanitized state: ${await diagnostics()}`, { cause: error });
  }
  } finally {
    releaseOwnerRequest();
    await context.unroute('**/api/songs');
    await context.unroute('**/api/export');
    context.off('request', onRequest);
    context.off('response', onResponse);
    context.off('requestfinished', onRequestEnd);
    context.off('requestfailed', onRequestFailed);
  }

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

test('a waiting tab retries after a lease is renewed once and its owner closes', async ({ page, context }) => {
  await context.addInitScript(() => Object.defineProperty(navigator, 'locks', {
    configurable: true, value: undefined,
  }));
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText('Never', { exact: true })).toHaveCount(0);
  const owner = await context.newPage();
  await owner.goto('/');
  await expect(owner.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await owner.evaluate(() => {
    window.t10LeaseRenewal = new Promise((resolve, reject) => setTimeout(async () => {
      try {
        const opening = indexedDB.open('chart-manager');
        const db = await new Promise((ok, fail) => {
          opening.onerror = () => fail(opening.error);
          opening.onsuccess = () => ok(opening.result);
        });
        const transaction = db.transaction('meta', 'readwrite');
        const store = transaction.objectStore('meta');
        const request = store.get('sync_lease');
        request.onsuccess = () => {
          const lease = request.result.value;
          store.put({ ...request.result, value: { ...lease, expires: Date.now() + 700 } });
        };
        transaction.oncomplete = () => { db.close(); resolve(); };
        transaction.onerror = () => { db.close(); reject(transaction.error); };
      } catch (error) { reject(error); }
    }, 450));
  });
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
  // This peer renews the lease shortly before the waiter's first timer wakes,
  // then closes. Recovery must schedule another expiry wait from that value.
  await owner.evaluate(() => window.t10LeaseRenewal);
  await owner.close();
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

test('a lost song-update response replays its receipt and preserves a newer edit across reload', async ({ page, context, request, t10Owned }) => {
  const title = t10Owned.titles[0];
  const created = await request.post('/api/songs', { data: {
    operation_id: randomUUID(), title, artist: 'T10 revision fixture artist',
    chart_source: 'Invented revision acknowledgement fixture', genres: [], vibes: [],
  } });
  expect(created.ok()).toBeTruthy();
  const saved = await created.json();
  t10Owned.songIds.add(saved.id);
  await page.goto('/');
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.evaluate(async ({ song }) => {
    const db = await new Promise((resolve, reject) => {
      const opening = indexedDB.open('chart-manager');
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta'], 'readwrite');
      const local = { ...song, bpm: 111, _sync: { operation: 'update', base_updated_at: song.updated_at } };
      transaction.objectStore('songs').put(local);
      transaction.objectStore('meta').put({ key: 'pending_mutations', value: [{
        entity: 'song', id: song.id, op_id: crypto.randomUUID(), local_revision: 1,
      }], updatedAt: Date.now() });
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onerror = () => reject(transaction.error);
    });
  }, { song: saved });

  const committed = new Promise(resolve => { t10Owned.updateCommitted = resolve; });
  const requestBodies = [];
  let firstAttempt = true;
  await page.route(`**/api/songs/${saved.id}`, async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    requestBodies.push(route.request().postDataJSON());
    if (firstAttempt) {
      firstAttempt = false;
      const response = await route.fetch(); // commit, then actually drop the browser response
      expect(response.ok()).toBeTruthy();
      // route.fetch has received the server's committed response. The client
      // side request is intentionally aborted next, but can no longer commit.
      t10Owned.markServerMutationSettled(route.request());
      t10Owned.updateCommitted();
      await route.abort('failed');
      return;
    }
    await route.continue();
  });
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('button', { name: 'Sync now' }).click();
  await committed;
  await expect(page.getByText(/Sync failed:/)).toBeVisible();

  // A user edit after the uncertain commit must coexist with the frozen
  // operation envelope and survive a page reload before receipt replay.
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
      const rows = songs.getAll();
      const queueRequest = meta.get('pending_mutations');
      let ready = 0;
      const done = () => {
        if (++ready !== 2) return;
        const row = rows.result.find(item => item.title === title);
        const queue = queueRequest.result?.value || [];
        const operation = queue.find(item => item.entity === 'song' && String(item.id) === String(row?.id));
        if (!row || !operation) throw new Error('The update fixture is missing its cached song or queue operation.');
        row.bpm = 222;
        operation.local_revision = (operation.local_revision || 0) + 1;
        songs.put(row);
        meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      };
      rows.onsuccess = queueRequest.onsuccess = done;
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('Could not record the newer fixture edit.'));
    });
  }, { title });

  const frozenBeforeReload = await page.evaluate(async ({ title }) => {
    const opening = indexedDB.open('chart-manager');
    const db = await new Promise((resolve, reject) => {
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    const result = await new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta']);
      const songs = transaction.objectStore('songs').getAll();
      const queue = transaction.objectStore('meta').get('pending_mutations');
      transaction.oncomplete = () => resolve({ songs: songs.result, queue: queue.result?.value || [] });
      transaction.onerror = () => reject(transaction.error);
    });
    db.close();
    const row = result.songs.find(item => item.title === title);
    const operation = result.queue.find(item => item.entity === 'song' && String(item.id) === String(row?.id));
    return { bpm: row?.bpm, pending: Boolean(operation), sent: operation?.sent,
      revision: operation?.local_revision, payload: operation?.sent_payload };
  }, { title });
  expect(frozenBeforeReload.bpm).toBe(222);
  expect(frozenBeforeReload.pending).toBe(true);
  expect(frozenBeforeReload.sent).toBe(true);
  expect(frozenBeforeReload.payload.operation_id).toBeTruthy();
  expect(requestBodies[0]).toEqual(frozenBeforeReload.payload);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  const persisted = await page.evaluate(async ({ title }) => {
    const opening = indexedDB.open('chart-manager');
    const db = await new Promise((resolve, reject) => {
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    const result = await new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta']);
      const songs = transaction.objectStore('songs').getAll();
      const queue = transaction.objectStore('meta').get('pending_mutations');
      transaction.oncomplete = () => resolve({ songs: songs.result, queue: queue.result?.value || [] });
    });
    db.close();
    const row = result.songs.find(item => item.title === title);
    const operation = result.queue.find(item => item.entity === 'song' && String(item.id) === String(row?.id));
    return { bpm: row?.bpm, pending: Boolean(operation), revision: operation?.local_revision,
      operationId: operation?.op_id, payload: operation?.sent_payload };
  }, { title });
  expect(persisted).toMatchObject({ bpm: 222, pending: true,
    revision: frozenBeforeReload.revision, payload: frozenBeforeReload.payload });
  expect(persisted.operationId).toBe(frozenBeforeReload.payload.operation_id);
  await page.getByRole('button', { name: 'Settings' }).click();
  await context.setOffline(false);
  const syncNow = page.getByRole('button', { name: 'Sync now' });
  await expect.poll(() => requestBodies.length, { timeout: 20_000 }).toBeGreaterThan(1);
  await expect(syncNow).toBeEnabled();
  await syncNow.click();
  await expect(syncNow).toBeEnabled();
  expect(requestBodies[1]).toEqual(requestBodies[0]);
  await syncNow.click();
  await expect.poll(async () => {
    const response = await request.get(`/api/songs?q=${encodeURIComponent(title)}`);
    return response.ok() && (await response.json()).songs.find(row => row.title === title)?.bpm === 222;
  }, { timeout: 20_000 }).toBeTruthy();
  expect(requestBodies[2].bpm).toBe(222);
  expect(requestBodies[2].operation_id).not.toBe(requestBodies[0].operation_id);

  // Replay the already completed older receipt after the newer update has
  // changed the authoritative row. The client must retain that newer row.
  await context.setOffline(true);
  await page.evaluate(async ({ title, oldPayload }) => {
    const opening = indexedDB.open('chart-manager');
    const db = await new Promise((resolve, reject) => {
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta'], 'readwrite');
      const songs = transaction.objectStore('songs');
      const meta = transaction.objectStore('meta');
      const rows = songs.getAll();
      const queueRequest = meta.get('pending_mutations');
      let ready = 0;
      const done = () => {
        if (++ready !== 2) return;
        const row = rows.result.find(item => item.title === title);
        if (!row) throw new Error('Could not seed the historical-receipt browser case.');
        row.bpm = oldPayload.bpm;
        row._sync = { operation: 'update', base_updated_at: oldPayload.base_updated_at };
        songs.put(row);
        meta.put({ key: 'pending_mutations', value: [{ entity: 'song', id: row.id,
          op_id: oldPayload.operation_id, local_revision: 1, sent: true,
          sent_revision: 1, sent_payload: oldPayload }], updatedAt: Date.now() });
      };
      rows.onsuccess = queueRequest.onsuccess = done;
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('Could not seed the historical receipt.'));
    });
  }, { title, oldPayload: requestBodies[0] });
  await page.reload();
  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await page.getByRole('button', { name: 'Settings' }).click();
  await context.setOffline(false);
  await expect.poll(() => requestBodies.length, { timeout: 20_000 }).toBeGreaterThan(3);
  expect(requestBodies[3]).toEqual(requestBodies[0]);
  await expect.poll(async () => page.evaluate(async ({ title }) => {
    const opening = indexedDB.open('chart-manager');
    const db = await new Promise((resolve, reject) => {
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => resolve(opening.result);
    });
    const state = await new Promise((resolve, reject) => {
      const transaction = db.transaction(['songs', 'meta']);
      const rows = transaction.objectStore('songs').getAll();
      const queue = transaction.objectStore('meta').get('pending_mutations');
      transaction.oncomplete = () => resolve({ rows: rows.result, queue: queue.result?.value || [] });
      transaction.onerror = () => reject(transaction.error);
    });
    db.close();
    const row = state.rows.find(item => item.title === title);
    return { bpm: row?.bpm, pending: state.queue.some(item => item.entity === 'song' && String(item.id) === String(row?.id)) };
  }, { title }), { timeout: 10_000 }).toEqual({ bpm: 222, pending: false });
  await expect(syncNow).toBeEnabled();
  const authoritative = await request.get(`/api/songs?q=${encodeURIComponent(title)}`);
  expect((await authoritative.json()).songs.find(row => row.title === title)?.bpm).toBe(222);
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
  t10Owned.settleBudgetMs = 100;
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

test('fixture teardown catches a create that commits after the test body ends', async ({ page, request, t10Owned }) => {
  t10Owned.settleBudgetMs = 100;
  t10Owned.requireLateCommit = true;
  let requestStarted;
  const started = new Promise(resolve => { requestStarted = resolve; });
  let releaseCreate;
  const held = new Promise(resolve => { releaseCreate = resolve; });
  t10Owned.holdUntilCleanup(releaseCreate);
  await page.route('**/api/songs', async route => {
    if (route.request().method() !== 'POST' || route.request().postDataJSON()?.title !== t10Owned.titles[0]) {
      return route.continue();
    }
    requestStarted();
    await held;
    try {
      if (!t10Owned.testBodyEnded) throw new Error('Fixture create was released before the test body ended.');
      const response = await route.fetch();
      if (!response.ok()) throw new Error(`Late fixture create returned HTTP ${response.status()}.`);
      const created = await response.json();
      if (!Number.isSafeInteger(created?.id) || created.id <= 0) {
        throw new Error('Late fixture create returned no valid positive server ID.');
      }
      // Retain identity before any verification so teardown can safely remove
      // the exact row even if the existence assertion or route fulfillment fails.
      t10Owned.lateCommitId = created.id;
      t10Owned.songIds.add(created.id);
      const persisted = await request.get(`/api/songs/${created.id}`);
      if (!persisted.ok()) throw new Error(`Late-created song ${created.id} was not readable before cleanup (HTTP ${persisted.status()}).`);
      const persistedRow = await persisted.json();
      if (persistedRow.id !== created.id || persistedRow.title !== t10Owned.titles[0]) {
        throw new Error(`Late-created song ${created.id} did not match the exact fixture identity.`);
      }
      t10Owned.lateCommitAfterBody = true;
      t10Owned.lateCommitVerifiedPresent = true;
      await new Promise(resolve => setTimeout(resolve, 300));
      await route.fulfill({ response });
    } catch (error) {
      t10Owned.lateCommitError = error;
      try { await route.abort(); } catch { /* Retain the original failure for teardown. */ }
    }
  });
  await page.goto('/');
  await page.evaluate(({ title }) => {
    void fetch('/api/songs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title, artist: 'T10 late commit fixture artist',
        chart_source: 'Invented late cleanup fixture', genres: [], vibes: [],
      }),
    });
  }, { title: t10Owned.titles[0] });
  await started;
});

