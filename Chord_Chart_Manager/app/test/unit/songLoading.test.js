import { describe, expect, it } from 'vitest';
import 'fake-indexeddb/auto';
import * as cache from '../../src/lib/cache.js';
import { loadChartSong } from '../../src/lib/songLoading.js';
import { createLatestRequestGate } from '../../src/lib/latestRequestGate.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function makeStorage() {
  const values = new Map();
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
}

describe('cached chart opening', () => {
  it('FR-066 reports an initial cache read exception instead of calling it a conditional refusal', async () => {
    const diagnostics = [];
    let reads = 0;
    const result = await loadChartSong(959999, {
      online: true,
      invalidationStorage: makeStorage(),
      readCached: async () => {
        reads += 1;
        if (reads === 1) throw new Error('Synthetic initial read failure');
        return null;
      },
      resolveId: async id => id,
      fetchRemote: async () => ({ id: 959999, title: 'Synthetic remote chart', updated_at: 'v1' }),
      writeCachedIfUnchanged: async (_id, expected) => {
        expect(expected).toBeUndefined();
        return false;
      },
      onDiagnostic: message => diagnostics.push(message),
    });

    expect(result.title).toBe('Synthetic remote chart');
    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatch(/initial IndexedDB cache read failed: Synthetic initial read failure/i);
    expect(diagnostics[0]).toMatch(/persistence was withheld/i);
    expect(diagnostics[0]).not.toMatch(/conditional write was declined/i);
  });

  it('FR-066 ignores stale asynchronous list projections after a newer request starts', async () => {
    const gate = createLatestRequestGate();
    let resolveOld;
    const oldRead = new Promise(resolve => { resolveOld = resolve; });
    const state = { songs: ['newer search'], loading: false, error: null };
    const olderTicket = gate.begin();
    const olderPublish = oldRead.then(result => {
      gate.publishIfCurrent(olderTicket, false, () => {
        state.songs = result;
        state.loading = true;
        state.error = 'Using cached data';
      });
    });
    gate.begin();
    resolveOld(['stale prior-query row']);
    await olderPublish;
    expect(state).toEqual({ songs: ['newer search'], loading: false, error: null });
  });

  it('FR-066 normalizes a successful IndexedDB miss and caches the first online chart for offline reopen', async () => {
    const id = 960000 + Math.floor(Math.random() * 10000);
    const remote = { id, title: 'Synthetic first-open chart', updated_at: 'v1' };
    const diagnostics = [];
    try {
      expect(await cache.getCachedSong(id)).toBeUndefined();
      const loaded = await loadChartSong(id, {
        online: true,
        invalidationStorage: makeStorage(),
        readCached: cache.getCachedSong,
        resolveId: async value => value,
        fetchRemote: async () => remote,
        writeCachedIfUnchanged: cache.writeCachedSongIfUnchanged,
        onDiagnostic: message => diagnostics.push(message),
      });
      expect(loaded).toEqual(remote);
      expect(await cache.getCachedSong(id)).toEqual(remote);
      expect(diagnostics).toEqual([]);

      const reopened = await loadChartSong(id, {
        online: false, readCached: cache.getCachedSong, invalidationStorage: makeStorage(),
      });
      expect(reopened).toEqual(remote);
    } finally {
      await cache.deleteCachedSong(id);
    }
  });

  it('FR-066 does not overwrite a song inserted by another tab between the miss read and conditional write', async () => {
    const id = 961000 + Math.floor(Math.random() * 10000);
    const remote = { id, title: 'Synthetic stale response', updated_at: 'v1' };
    const intervening = { id, title: 'Synthetic other-tab edit', updated_at: 'v2',
      _sync: { operation: 'update', base_updated_at: 'v1' } };
    try {
      expect(await cache.getCachedSong(id)).toBeUndefined();
      const loaded = await loadChartSong(id, {
        online: true,
        readCached: cache.getCachedSong,
        resolveId: async value => value,
        fetchRemote: async () => remote,
        writeCachedIfUnchanged: async (...args) => {
          // Model the other tab's commit after loadChartSong's last read and
          // before the real atomic conditional transaction.
          await cache.savePendingSong(intervening);
          return cache.writeCachedSongIfUnchanged(...args);
        },
      });
      expect(loaded).toEqual(intervening);
      expect(await cache.getCachedSong(id)).toEqual(intervening);
    } finally {
      await cache.deleteCachedSong(id);
      await cache.clearDirty(id);
    }
  });

  it('FR-066 applies a successful background refresh when the request-start snapshot is unchanged', async () => {
    const remote = deferred();
    const started = deferred();
    let cached = { id: 41, title: 'Unchanged cached snapshot', updated_at: 'v1' };
    const refreshed = { id: 41, title: 'Refreshed server snapshot', updated_at: 'v2' };
    const displayed = [];
    const loading = loadChartSong(41, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: () => { started.resolve(); return remote.promise; },
      writeCachedIfUnchanged: async (id, expected, replacement) => {
        if (JSON.stringify(cached) !== JSON.stringify(expected)) return false;
        cached = replacement;
        return true;
      },
      onCached: song => displayed.push(song.title),
      onRemote: song => displayed.push(song.title),
    });

    await started.promise;
    remote.resolve(refreshed);
    expect(await loading).toEqual(refreshed);
    expect(displayed).toEqual(['Unchanged cached snapshot', 'Refreshed server snapshot']);
  });

  it('FR-066 publishes cached content before a slow refresh and preserves an edit queued during it', async () => {
    const remote = deferred();
    const refreshStarted = deferred();
    const cached = { id: 42, title: 'Invented cached chart', updated_at: 'v1' };
    const pendingEdit = { ...cached, title: 'Invented pending edit', _sync: { operation: 'update' } };
    let currentCache = cached;
    const displayed = [];

    const loading = loadChartSong(42, {
      online: true,
      readCached: async () => currentCache,
      resolveId: async id => id,
      fetchRemote: () => { refreshStarted.resolve(); return remote.promise; },
      onCached: song => displayed.push(song.title),
      onRemote: song => displayed.push(song.title),
    });

    await refreshStarted.promise;
    expect(displayed).toEqual(['Invented cached chart']);
    currentCache = pendingEdit;
    remote.resolve({ id: 42, title: 'Stale server snapshot', updated_at: 'v2' });
    await loading;

    expect(displayed).toEqual(['Invented cached chart', 'Invented pending edit']);
  });

  it('FR-066 does not make a server request when offline or when a queued mutation exists', async () => {
    const cached = { id: 42, title: 'Invented offline chart' };
    let remoteCalls = 0;
    const readCached = async () => cached;
    const dependencies = {
      readCached,
      resolveId: async id => id,
      fetchRemote: async () => { remoteCalls += 1; return cached; },
    };

    const offline = await loadChartSong(42, { ...dependencies, online: false });
    expect(offline).toEqual(cached);
    const pending = await loadChartSong(42, {
      ...dependencies, online: true,
      readCached: async () => ({ ...cached, _sync: { operation: 'update' } }),
    });
    expect(pending?._sync?.operation).toBe('update');
    expect(remoteCalls).toBe(0);
  });

  it('FR-066 never publishes a queued song deletion', async () => {
    const deleted = { id: 42, title: 'Deleted synthetic chart',
      _sync: { operation: 'delete', base_updated_at: '2026-10-11T12:00:00.123456Z' } };
    const displayed = [];
    let remoteCalls = 0;
    const result = await loadChartSong(42, {
      online: true,
      readCached: async () => deleted,
      resolveId: async id => id,
      fetchRemote: async () => { remoteCalls += 1; return deleted; },
      onCached: song => displayed.push(song?.title ?? null),
    });

    expect(result).toBeNull();
    expect(displayed).toEqual([null]);
    expect(remoteCalls).toBe(0);
  });

  it('FR-066 removes the unchanged cached song after an authoritative 404', async () => {
    const cached = { id: 42, title: 'Deleted synthetic chart',
      updated_at: '2026-10-11T12:00:00.123456Z' };
    const displayed = [];
    let removed;
    const result = await loadChartSong(42, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw Object.assign(new Error('Not found'), { status: 404 }); },
      deleteCachedIfUnchanged: async (id, expected) => {
        removed = { id, expected };
        return true;
      },
      onCached: song => displayed.push(song?.title ?? null),
    });

    expect(result).toBeNull();
    expect(removed).toEqual({ id: 42, expected: cached });
    expect(displayed).toEqual(['Deleted synthetic chart', null]);
  });

  it('FR-066 does not reject if cache cleanup itself fails after a 404', async () => {
    const cached = { id: 4200, title: 'Cached chart', updated_at: 'v1' };
    const displayed = [];
    const diagnostics = [];
    const storage = makeStorage();
    const result = await loadChartSong(4200, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw Object.assign(new Error('Not found'), { status: 404 }); },
      deleteCachedIfUnchanged: async () => { throw new Error('Synthetic IndexedDB failure'); },
      invalidationStorage: storage,
      onCached: song => displayed.push(song?.title ?? null),
      onDiagnostic: message => diagnostics.push(message),
    });
    expect(result).toBeNull();
    expect(displayed).toEqual(['Cached chart', null]);
    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatch(/could not be cleared/);
    expect(storage.getItem('ccm-invalidated-song:4200')).toBe('v1');

    const reopened = [];
    const offlineResult = await loadChartSong(4200, {
      online: false,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw new Error('must remain offline'); },
      invalidationStorage: storage,
      onCached: song => reopened.push(song?.title ?? null),
    });
    expect(offlineResult).toBeNull();
    expect(reopened).toEqual([null]);
  });

  it('FR-066 keeps invalidation suppression across a fresh loader runtime without storing chart content', async () => {
    const cached = { id: 4201, title: 'Private synthetic chart', updated_at: 'v1' };
    const storage = makeStorage();
    const first = await loadChartSong(4201, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw Object.assign(new Error('Not found'), { status: 404 }); },
      deleteCachedIfUnchanged: async () => { throw new Error('Synthetic IndexedDB failure'); },
      invalidationStorage: storage,
    });
    expect(first).toBeNull();
    expect(storage.getItem('ccm-invalidated-song:4201')).toBe('v1');

    const reloadedModule = await import('../../src/lib/songLoading.js?fresh-runtime');
    const displayed = [];
    const afterReload = await reloadedModule.loadChartSong(4201, {
      online: false,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw new Error('offline'); },
      invalidationStorage: storage,
      onCached: song => displayed.push(song?.title ?? null),
    });
    expect(afterReload).toBeNull();
    expect(displayed).toEqual([null]);
    expect(storage.getItem('ccm-invalidated-song:4201')).toBe('v1');
  });

  it('FR-066 shows changed versions and pending edits instead of suppressing them', async () => {
    const storage = makeStorage();
    storage.setItem('ccm-invalidated-song:4202', 'v1');
    const newer = { id: 4202, title: 'New clean version', updated_at: 'v2' };
    const newerDisplayed = [];
    expect(await loadChartSong(4202, {
      online: false,
      readCached: async () => newer,
      invalidationStorage: storage,
      onCached: song => newerDisplayed.push(song?.title ?? null),
    })).toEqual(newer);
    expect(newerDisplayed).toEqual(['New clean version']);
    expect(storage.getItem('ccm-invalidated-song:4202')).toBeNull();

    storage.setItem('ccm-invalidated-song:4202', 'v2');
    const pending = { ...newer, title: 'Pending local edit',
      _sync: { operation: 'update', base_updated_at: 'v2' } };
    const pendingDisplayed = [];
    expect(await loadChartSong(4202, {
      online: false,
      readCached: async () => pending,
      invalidationStorage: storage,
      onCached: song => pendingDisplayed.push(song?.title ?? null),
    })).toEqual(pending);
    expect(pendingDisplayed).toEqual(['Pending local edit']);
    expect(storage.getItem('ccm-invalidated-song:4202')).toBeNull();
  });

  it('FR-066 clears a persisted invalidation marker after a valid background refresh', async () => {
    const cached = { id: 4203, title: 'Invalidated cached version', updated_at: 'v1' };
    const remote = { id: 4203, title: 'Valid refreshed version', updated_at: 'v2' };
    const storage = makeStorage();
    storage.setItem('ccm-invalidated-song:4203', 'v1');
    let current = cached;
    const displayed = [];
    const result = await loadChartSong(4203, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: async () => remote,
      writeCachedIfUnchanged: async (id, expected, replacement) => {
        if (JSON.stringify(current) !== JSON.stringify(expected)) return false;
        current = replacement;
        return true;
      },
      invalidationStorage: storage,
      onCached: song => displayed.push(song?.title ?? null),
      onRemote: song => displayed.push(song.title),
    });
    expect(result).toEqual(remote);
    expect(displayed).toEqual(['Valid refreshed version']);
    expect(storage.getItem('ccm-invalidated-song:4203')).toBeNull();

    const reloadedModule = await import('../../src/lib/songLoading.js?refreshed-runtime');
    const offlineDisplayed = [];
    const offline = await reloadedModule.loadChartSong(4203, {
      online: false,
      readCached: async () => current,
      invalidationStorage: storage,
      onCached: song => offlineDisplayed.push(song?.title ?? null),
    });
    expect(offline).toEqual(remote);
    expect(offlineDisplayed).toEqual(['Valid refreshed version']);
  });

  it('FR-066 keeps the exact invalidated snapshot hidden after reload when online revalidation fails', async () => {
    const cached = { id: 4207, title: 'Invalidated stale chart', updated_at: 'v1' };
    const storage = makeStorage();
    storage.setItem('ccm-invalidated-song:4207', 'v1');
    const displayed = [];
    const result = await loadChartSong(4207, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw new TypeError('offline despite online flag'); },
      invalidationStorage: storage,
      onCached: song => displayed.push(song?.title ?? null),
    });
    expect(result).toBeNull();
    expect(displayed).toEqual([null]);
    expect(storage.getItem('ccm-invalidated-song:4207')).toBe('v1');
  });

  it('FR-066 reports actionable diagnostics when persistent suppression storage fails', async () => {
    const cached = { id: 4204, title: 'Synthetic stale chart', updated_at: 'v1' };
    const diagnostics = [];
    const failingStorage = {
      getItem: () => null,
      setItem: () => { throw new Error('storage quota denied'); },
      removeItem: () => {},
    };
    const result = await loadChartSong(4204, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => { throw Object.assign(new Error('Not found'), { status: 404 }); },
      deleteCachedIfUnchanged: async () => { throw new Error('synthetic cache failure'); },
      invalidationStorage: failingStorage,
      onDiagnostic: message => diagnostics.push(message),
    });
    expect(result).toBeNull();
    expect(diagnostics.some(message => /only for this runtime; reconnect and refresh before reloading/i.test(message))).toBe(true);
  });

  it('FR-066 reports when acquiring the browser localStorage property throws', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() { throw new Error('Synthetic storage access denial'); },
    });
    const diagnostics = [];
    const cached = { id: 4208, title: 'Synthetic chart', updated_at: 'v1' };
    try {
      expect(await loadChartSong(4208, {
        online: false,
        readCached: async () => cached,
        onDiagnostic: message => diagnostics.push(message),
      })).toEqual(cached);
      expect(diagnostics).toHaveLength(1);
      expect(diagnostics[0]).toMatch(/storage access failed.*could not be checked/i);
    } finally {
      if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor);
      else delete globalThis.localStorage;
    }
  });

  it('FR-066 distinguishes unavailable browser storage from failed storage access', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    delete globalThis.localStorage;
    const diagnostics = [];
    try {
      await loadChartSong(4212, {
        online: false,
        readCached: async () => ({ id: 4212, title: 'Synthetic chart', updated_at: 'v1' }),
        onDiagnostic: message => diagnostics.push(message),
      });
      expect(diagnostics).toHaveLength(1);
      expect(diagnostics[0]).toMatch(/storage is unavailable.*could not be checked/i);
    } finally {
      if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor);
    }
  });

  it.each(['pending edit', 'newer clean version'])('does not replace an intervening %s during conditional refresh persistence', async kind => {
    const original = { id: kind === 'pending edit' ? 4209 : 4210,
      title: 'Original cached chart', updated_at: '2026-10-11T12:00:00.123456Z' };
    const intervening = kind === 'pending edit'
      ? { ...original, title: 'Pending local edit', _sync: { operation: 'update' } }
      : { ...original, title: 'Newer clean chart', updated_at: '2026-10-11T12:00:00.123457Z' };
    const remote = { ...original, title: 'Remote refreshed chart', updated_at: '2026-10-11T12:00:00.123458Z' };
    let current = original;
    const displayed = [];
    const result = await loadChartSong(original.id, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: async () => remote,
      writeCachedIfUnchanged: async (id, expected, replacement) => {
        current = intervening;
        if (JSON.stringify(current) !== JSON.stringify(expected)) return false;
        current = replacement;
        return true;
      },
      invalidationStorage: makeStorage(),
      onCached: song => displayed.push(song?.title ?? null),
      onRemote: song => displayed.push(song.title),
    });
    expect(result).toEqual(intervening);
    expect(current).toEqual(intervening);
    expect(displayed.at(-1)).toBe(intervening.title);
    expect(displayed).not.toContain(remote.title);
  });

  it('FR-066 retains stale suppression and reports a handled diagnostic when refreshed cache write fails', async () => {
    const cached = { id: 4211, title: 'Stale cached chart', updated_at: 'v1' };
    const remote = { id: 4211, title: 'Accepted remote chart', updated_at: 'v2' };
    const storage = makeStorage();
    storage.setItem('ccm-invalidated-song:4211', 'v1');
    const diagnostics = [];
    const displayed = [];
    const result = await loadChartSong(4211, {
      online: true,
      readCached: async () => cached,
      resolveId: async id => id,
      fetchRemote: async () => remote,
      writeCachedIfUnchanged: async () => { throw new Error('Synthetic IndexedDB write failure'); },
      invalidationStorage: storage,
      onDiagnostic: message => diagnostics.push(message),
      onCached: song => displayed.push(song?.title ?? null),
      onRemote: song => displayed.push(song.title),
    });
    expect(result).toEqual(remote);
    expect(displayed).toEqual(['Accepted remote chart']);
    expect(storage.getItem('ccm-invalidated-song:4211')).toBe('v1');
    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatch(/could not be saved.*prior cached version will stay hidden/i);
  });

  it.each([
    ['pending edit', { title: 'Pending edit after refresh', _sync: { operation: 'update' } }, 'Pending edit after refresh'],
    ['queued deletion', { title: 'Deleted after refresh', _sync: { operation: 'delete' } }, null],
    ['cache removal', null, null],
    ['newer clean version', { title: 'Newer clean version', updated_at: '2026-10-11T12:00:00.123459Z' }, 'Newer clean version'],
  ])('publishes the cache state that arrives after refresh commit: %s', async (_kind, change, expectedTitle) => {
    const cached = { id: 4213, title: 'Request-start chart', updated_at: '2026-10-11T12:00:00.123456Z' };
    const remote = { id: 4213, title: 'Accepted refreshed chart', updated_at: '2026-10-11T12:00:00.123458Z' };
    const intervening = change === null ? null : {
      ...cached,
      ...change,
      updated_at: change.updated_at || remote.updated_at,
    };
    let current = cached;
    const displayed = [];
    const result = await loadChartSong(4213, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: async () => remote,
      writeCachedIfUnchanged: async (_id, expected, replacement) => {
        expect(current).toEqual(expected);
        current = replacement; // The accepted refresh transaction commits.
        current = intervening; // Another tab/edit runs before the caller resumes.
        return true;
      },
      invalidationStorage: makeStorage(),
      onCached: song => displayed.push(song?.title ?? null),
      onRemote: song => displayed.push(song.title),
    });
    expect(current).toEqual(intervening);
    expect(result).toEqual(expectedTitle === null ? null : intervening);
    expect(displayed).toEqual(['Request-start chart', expectedTitle]);
  });

  it('FR-066 preserves a clean version written immediately after deletion succeeds', async () => {
    const initial = { id: 4205, title: 'Original synthetic chart',
      updated_at: '2026-10-11T12:00:00.123456Z' };
    const newer = { id: 4205, title: 'Newer synthetic chart',
      updated_at: '2026-10-11T12:00:00.123457Z' };
    let current = initial;
    const displayed = [];
    const result = await loadChartSong(4205, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: async () => { throw Object.assign(new Error('Not found'), { status: 404 }); },
      deleteCachedIfUnchanged: async () => { current = newer; return true; },
      invalidationStorage: makeStorage(),
      onCached: song => displayed.push(song?.updated_at ?? null),
    });
    expect(result).toEqual(newer);
    expect(current).toEqual(newer);
    expect(displayed).toEqual([initial.updated_at, newer.updated_at]);
  });

  it('FR-066 preserves a pending edit written immediately after deletion succeeds', async () => {
    const initial = { id: 4206, title: 'Original synthetic chart', updated_at: 'v1' };
    const pending = { ...initial, title: 'Pending synthetic edit',
      _sync: { operation: 'update', base_updated_at: 'v1' } };
    let current = initial;
    const displayed = [];
    const result = await loadChartSong(4206, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: async () => { throw Object.assign(new Error('Not found'), { status: 404 }); },
      deleteCachedIfUnchanged: async () => { current = pending; return true; },
      invalidationStorage: makeStorage(),
      onCached: song => displayed.push(song?.title ?? null),
    });
    expect(result).toEqual(pending);
    expect(current).toEqual(pending);
    expect(displayed).toEqual(['Original synthetic chart', 'Pending synthetic edit']);
  });

  it('FR-066 preserves and displays a newer clean cache snapshot when an older request returns 404', async () => {
    const held = deferred();
    const initial = { id: 43, title: 'Request-start snapshot', updated_at: 'v1' };
    const newer = { id: 43, title: 'Newer clean snapshot', updated_at: 'v2' };
    let current = initial;
    const displayed = [];
    let deleteCalls = 0;
    const loading = loadChartSong(43, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: () => held.promise,
      deleteCachedIfUnchanged: async () => { deleteCalls += 1; return true; },
      onCached: song => displayed.push(song?.title ?? null),
    });

    current = newer;
    held.reject(Object.assign(new Error('Not found'), { status: 404 }));
    expect(await loading).toEqual(newer);
    expect(displayed).toEqual(['Request-start snapshot', 'Newer clean snapshot']);
    expect(deleteCalls).toBe(0);
    expect(current).toEqual(newer);
  });

  it('FR-066 does not restore a song removed while its successful request was held', async () => {
    const held = deferred();
    const cached = { id: 42, title: 'Removed during request', updated_at: 'v1' };
    let current = cached;
    const displayed = [];
    const loading = loadChartSong(42, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: () => held.promise,
      onCached: song => displayed.push(song?.title ?? null),
      onRemote: song => displayed.push(song.title),
    });

    current = null;
    held.resolve({ id: 42, title: 'Stale remote copy', updated_at: 'v1' });
    expect(await loading).toBeNull();
    expect(displayed).toEqual(['Removed during request', null]);
  });

  it('FR-066 keeps a newer acknowledged full-precision cache version over a held response', async () => {
    const held = deferred();
    const initial = { id: 42, title: 'Initial', updated_at: '2026-10-11T12:00:00.123456Z' };
    const newer = { id: 42, title: 'Newer acknowledged version',
      updated_at: '2026-10-11T12:00:00.123457Z' };
    let current = initial;
    const displayed = [];
    const loading = loadChartSong(42, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: () => held.promise,
      onCached: song => displayed.push(song?.title ?? null),
      onRemote: song => displayed.push(song.title),
    });

    current = newer;
    held.resolve({ id: 42, title: 'Older in-flight response',
      updated_at: '2026-10-11T12:00:00.123450Z' });
    expect(await loading).toEqual(newer);
    expect(displayed).toEqual(['Initial', 'Newer acknowledged version']);
  });

  it('FR-066 retains pending local edits when a not-found response races with the edit', async () => {
    const held = deferred();
    const cached = { id: 42, title: 'Original', updated_at: 'v1' };
    const pending = { ...cached, title: 'Pending edit',
      _sync: { operation: 'update', base_updated_at: 'v1' } };
    let current = cached;
    const displayed = [];
    const loading = loadChartSong(42, {
      online: true,
      readCached: async () => current,
      resolveId: async id => id,
      fetchRemote: () => held.promise,
      deleteCachedIfUnchanged: async () => { throw new Error('must not delete pending edit'); },
      onCached: song => displayed.push(song?.title ?? null),
    });

    current = pending;
    held.reject(Object.assign(new Error('Not found'), { status: 404 }));
    expect(await loading).toEqual(pending);
    expect(displayed).toEqual(['Original', 'Pending edit']);
  });

  it('FR-066 conditionally removes only the same clean cache snapshot after a 404', async () => {
    const id = 970000 + Math.floor(Math.random() * 10000);
    const original = { id, title: 'Synthetic conditional delete',
      updated_at: '2026-10-11T12:00:00.123456Z' };
    try {
      await cache.updateCachedSong(original);
      await cache.updateCachedSong({ ...original, title: 'New pending edit',
        _sync: { operation: 'update', base_updated_at: original.updated_at } });
      expect(await cache.deleteCachedSongIfUnchanged(id, original)).toBe(false);
      expect((await cache.getCachedSong(id)).title).toBe('New pending edit');

      await cache.updateCachedSong(original);
      const newerClean = { ...original, title: 'Newer clean snapshot',
        updated_at: '2026-10-11T12:00:00.123457Z' };
      await cache.updateCachedSong(newerClean);
      expect(await cache.deleteCachedSongIfUnchanged(id, original)).toBe(false);
      expect(await cache.getCachedSong(id)).toEqual(newerClean);

      await cache.updateCachedSong(original);
      expect(await cache.deleteCachedSongIfUnchanged(id, original)).toBe(true);
      expect(await cache.getCachedSong(id)).toBeUndefined();
    } finally {
      await cache.deleteCachedSong(id);
    }
  });

  it('FR-066 atomically replaces only the exact request-start cache snapshot', async () => {
    const id = 980000 + Math.floor(Math.random() * 10000);
    const original = { id, title: 'Synthetic cached version', updated_at: 'v1' };
    const intervening = { id, title: 'Synthetic newer cache version', updated_at: 'v2' };
    const refreshed = { id, title: 'Synthetic accepted remote version', updated_at: 'v3' };
    try {
      await cache.updateCachedSong(original);
      await cache.updateCachedSong(intervening);
      expect(await cache.writeCachedSongIfUnchanged(id, original, refreshed)).toBe(false);
      expect(await cache.getCachedSong(id)).toEqual(intervening);

      expect(await cache.writeCachedSongIfUnchanged(id, intervening, refreshed)).toBe(true);
      expect(await cache.getCachedSong(id)).toEqual(refreshed);
      expect(await cache.writeCachedSongIfUnchanged(id, null, original)).toBe(false);
      expect(await cache.getCachedSong(id)).toEqual(refreshed);
    } finally {
      await cache.deleteCachedSong(id);
    }
  });
});
