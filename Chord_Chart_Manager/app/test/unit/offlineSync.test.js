import 'fake-indexeddb/auto';
import { describe, expect, it, vi } from 'vitest';
import * as cache from '../../src/lib/cache.js';
import { offlineSongCreatedAt, primaryGenreFromAssignments } from '../../src/lib/offlineSongFields.js';
import { sortSongs } from '../../src/lib/songSort.js';

const version = '2026-10-10T12:00:00.000001Z';

describe('offline cache and push-then-pull sync', () => {
  it('omits unchanged chart source from queued updates and includes edited text', () => {
    const source = '[Verse]\nG D | C';
    expect(cache.editablePayload({ chart_source: source,
      _sync: { chart_source_unchanged: true } })).not.toHaveProperty('chart_source');
    expect(cache.editablePayload({ chart_source: `${source} `,
      _sync: { chart_source_unchanged: false } }).chart_source).toBe(`${source} `);
  });

  it('merges a stale offline metadata save onto the latest chart and rejects same-field conflicts atomically', async () => {
    const id = 987640;
    const source = 'Invented original chart';
    const original = { id, title: 'Invented stale editor song', artist: 'Synthetic', bpm: 100,
      chart_source: source, chart_content: { sections: [{ lines: [{ type: 'raw', text: source }] }] },
      genres: [], vibes: [], updated_at: version };
    const baseline = { title: original.title, artist: original.artist, bpm: original.bpm,
      chart_source: source, chart_content: original.chart_content, updated_at: version };
    const chartSource = 'Invented chart changed in tab A';
    const chartContent = { sections: [{ lines: [{ type: 'raw', text: chartSource }] }] };
    try {
      await cache.cacheSongs([original]);
      await cache.savePendingSong({ ...original, chart_source: chartSource, chart_content: chartContent,
        _sync: { operation: 'update', base_updated_at: version, chart_source_unchanged: false } }, null,
      { baseline, changedFields: ['chart_source'] });

      const staleMetadata = await cache.savePendingSong({ ...original, bpm: 128,
        _sync: { operation: 'update', base_updated_at: version, chart_source_unchanged: true } }, null,
      { baseline, changedFields: ['bpm'] });
      expect(staleMetadata).toMatchObject({ bpm: 128, chart_source: chartSource, chart_content: chartContent,
        _sync: { operation: 'update', base_updated_at: version, chart_source_unchanged: false } });
      const queued = (await cache.getPendingMutations()).find(item => String(item.id) === String(id));
      expect(queued).toMatchObject({ entity: 'song', local_revision: 2 });
      expect(queued.sent).toBeUndefined();

      const beforeConflict = await cache.getCachedSong(id);
      const queueBeforeConflict = await cache.getPendingMutations();
      await expect(cache.savePendingSong({ ...original, chart_source: 'Invented stale competing chart',
        chart_content: { sections: [] }, _sync: { operation: 'update', base_updated_at: version } }, null,
      { baseline, changedFields: ['chart_source'] })).rejects.toThrow(/changed in another tab.*reload.*retry/i);
      expect(await cache.getCachedSong(id)).toEqual(beforeConflict);
      expect(await cache.getPendingMutations()).toEqual(queueBeforeConflict);
    } finally {
      await cache.clearDirty(id);
      await cache.deleteCachedSong(id);
    }
  });

  it('rebases edits made while an earlier offline revision awaits acknowledgement', async () => {
    const id = 987641;
    const source = 'Invented original in-flight chart';
    const chartSource = 'Invented first chart revision';
    const changedChart = 'Invented second chart revision';
    const original = { id, title: 'Invented in-flight stale editor', artist: 'Synthetic', bpm: 90,
      chart_source: source, chart_content: { sections: [{ lines: [{ type: 'raw', text: source }] }] },
      genres: [], vibes: [], updated_at: version };
    const baseline = { title: original.title, artist: original.artist, bpm: original.bpm,
      chart_source: source, chart_content: original.chart_content, updated_at: version };
    const ackVersion = '2026-10-10T12:00:00.000002Z';
    const finalVersion = '2026-10-10T12:00:00.000003Z';
    let releaseUpdate;
    let updateStarted;
    const started = new Promise(resolve => { updateStarted = resolve; });
    const heldUpdate = new Promise(resolve => { releaseUpdate = resolve; });
    const firstSaved = { ...original, chart_source: chartSource,
      chart_content: { sections: [{ lines: [{ type: 'raw', text: chartSource }] }] }, updated_at: ackVersion };
    try {
      await cache.cacheSongs([original]);
      await cache.savePendingSong({ ...original, chart_source: chartSource, chart_content: firstSaved.chart_content,
        _sync: { operation: 'update', base_updated_at: version, chart_source_unchanged: false } }, null,
      { baseline, changedFields: ['chart_source'] });
      const firstSync = cache.syncWithServer({
        library: async () => ({ songs: [firstSaved] }),
        songs: { update: async (_songId, payload) => {
          expect(payload.base_updated_at).toBe(version);
          expect(payload.chart_source).toBe(chartSource);
          updateStarted();
          return heldUpdate;
        } },
        setlists: { all: async () => [] },
      });
      await started;

      await cache.savePendingSong({ ...firstSaved, chart_source: changedChart,
        chart_content: { sections: [{ lines: [{ type: 'raw', text: changedChart }] }] }, bpm: 120,
        _sync: { operation: 'update', base_updated_at: version, chart_source_unchanged: false } }, null,
      { baseline: { ...baseline, chart_source: chartSource, chart_content: firstSaved.chart_content },
        changedFields: ['chart_source', 'bpm'] });
      const duringRequest = (await cache.getPendingMutations()).find(item => String(item.id) === String(id));
      expect(duringRequest).toMatchObject({ local_revision: 2, sent: true, sent_revision: 1,
        sent_payload: expect.objectContaining({ chart_source: chartSource, base_updated_at: version }) });

      releaseUpdate(firstSaved);
      expect((await firstSync).ok).toBe(true);
      const rebased = await cache.getCachedSong(id);
      expect(rebased).toMatchObject({ chart_source: changedChart, bpm: 120,
        updated_at: ackVersion, _sync: { operation: 'update', base_updated_at: ackVersion,
          chart_source_unchanged: false } });
      const rebasedQueue = (await cache.getPendingMutations()).find(item => String(item.id) === String(id));
      expect(rebasedQueue).toMatchObject({ local_revision: 2, sent: false });
      expect(rebasedQueue.sent_payload).toBeUndefined();

      const finalServer = { ...rebased, updated_at: finalVersion };
      const secondSync = await cache.syncWithServer({
        library: async () => ({ songs: [finalServer] }),
        songs: { update: async (_songId, payload) => {
          expect(payload.chart_source).toBe(changedChart);
          expect(payload.bpm).toBe(120);
          expect(payload.base_updated_at).toBe(ackVersion);
          return finalServer;
        } },
        setlists: { all: async () => [] },
      });
      expect(secondSync.ok).toBe(true);
      expect(await cache.getCachedSong(id)).toMatchObject({ chart_source: changedChart, bpm: 120,
        updated_at: finalVersion });
      expect((await cache.getPendingMutations()).some(item => String(item.id) === String(id))).toBe(false);
    } finally {
      releaseUpdate?.(firstSaved);
      await cache.clearDirty(id);
      await cache.deleteCachedSong(id);
    }
  });

  it('keeps a real offline creation timestamp through cache edits and replaces it with the server timestamp on acknowledgement', async () => {
    const id = cache.makeLocalSongId();
    const localCreatedAt = '2026-10-11T12:00:00.000Z';
    let clockCalls = 0;
    expect(offlineSongCreatedAt({ id: 42, title: 'Existing record without a date' }, 'create', () => {
      clockCalls += 1;
      return localCreatedAt;
    })).toBeUndefined();
    const createdAt = offlineSongCreatedAt(null, 'create', () => {
      clockCalls += 1;
      return localCreatedAt;
    });
    const local = { id, title: 'Invented new local date song', artist: 'T17 synthetic', created_at: createdAt,
      genres: [], vibes: [], _sync: { operation: 'create', operation_id: cache.makeMutationId() } };
    try {
      await cache.savePendingSong(local);
      const cached = await cache.getCachedSong(id);
      expect(cached.created_at).toBe(localCreatedAt);
      expect(sortSongs([
        { id: 90, title: 'Older server song', created_at: '2025-01-01T00:00:00Z' }, cached,
        { id: 91, title: 'Undated legacy song' },
      ], { key: 'created_at' }).map(song => song.id)).toEqual([90, id, 91]);

      const editedCreatedAt = offlineSongCreatedAt(cached, 'create', () => {
        clockCalls += 1;
        return '2099-01-01T00:00:00Z';
      });
      await cache.savePendingSong({ ...cached, title: 'Invented edited local date song', created_at: editedCreatedAt });
      expect((await cache.getCachedSong(id)).created_at).toBe(localCreatedAt);
      expect(clockCalls).toBe(1);

      const serverCreatedAt = '2026-10-11T12:00:03.000Z';
      const saved = { ...local, id: 987654, title: 'Invented edited local date song', created_at: serverCreatedAt,
        updated_at: version, _sync: undefined };
      const result = await cache.syncWithServer({
        library: async () => ({ songs: [saved] }),
        songs: { create: async () => saved },
        setlists: { all: async () => [] },
      });
      expect(result.ok).toBe(true);
      expect((await cache.getCachedSong(saved.id)).created_at).toBe(serverCreatedAt);
      expect(sortSongs([
        { id: 90, title: 'Older server song', created_at: '2025-01-01T00:00:00Z' },
        await cache.getCachedSong(saved.id),
        { id: 91, title: 'Undated legacy song' },
      ], { key: 'created_at' }).map(song => song.id)).toEqual([90, saved.id, 91]);
      expect(await cache.getCachedSong(id)).toBeUndefined();
    } finally {
      await cache.clearDirty(id);
      await cache.deleteCachedSong(id);
      await cache.clearDirty(987654);
      await cache.deleteCachedSong(987654);
    }
  });

  it('uses server created_at when a newer edit lands during an in-flight create, then syncs that edit after reload', async () => {
    const localId = cache.makeLocalSongId();
    const localCreatedAt = '2026-10-11T12:00:00.000Z';
    const serverCreatedAt = '2026-10-11T12:07:43.123Z';
    const serverVersion = '2026-10-11T12:07:44.000001Z';
    const finalVersion = '2026-10-11T12:08:01.000001Z';
    const local = { id: localId, title: 'Invented in-flight create', artist: 'T17 synthetic',
      created_at: localCreatedAt, chart_source: 'Invented original line', genres: [], vibes: [],
      _sync: { operation: 'create', operation_id: cache.makeMutationId() } };
    let releaseCreate;
    let createStarted;
    const started = new Promise(resolve => { createStarted = resolve; });
    const heldCreate = new Promise(resolve => { releaseCreate = resolve; });
    const serverCreated = { ...local, id: 987657, created_at: serverCreatedAt, updated_at: serverVersion,
      _sync: undefined };
    const newTitle = 'Invented in-flight edited title';
    const newerContent = 'Invented newer line kept through create acknowledgement';
    try {
      await cache.savePendingSong(local);
      const firstSync = cache.syncWithServer({
        library: async () => ({ songs: [serverCreated] }),
        songs: { create: async () => { createStarted(); return heldCreate; } },
        setlists: { all: async () => [] },
      });
      await started;

      const editing = await cache.getCachedSong(localId);
      await cache.savePendingSong({ ...editing, title: newTitle, chart_source: newerContent });
      releaseCreate(serverCreated);
      expect((await firstSync).ok).toBe(true);

      const acknowledged = await cache.getCachedSong(serverCreated.id);
      expect(await cache.getRedirectedSongId(localId)).toBe(serverCreated.id);
      expect(acknowledged).toMatchObject({ id: serverCreated.id, title: newTitle,
        chart_source: newerContent, created_at: serverCreatedAt, updated_at: serverVersion,
        _sync: { operation: 'update', base_updated_at: serverVersion } });
      const pendingAfterCreate = (await cache.getPendingMutations()).find(item => String(item.id) === String(serverCreated.id));
      expect(pendingAfterCreate).toMatchObject({ entity: 'song', local_revision: 1, sent: false });
      expect(sortSongs([acknowledged, { id: 987658, title: 'Invented date comparator',
        created_at: '2026-10-11T12:05:00.000Z' }], { key: 'created_at' }).map(song => song.id))
        .toEqual([987658, serverCreated.id]);

      // Reload the cache module while retaining IndexedDB, as a page reload does.
      vi.resetModules();
      const reloadedCache = await import('../../src/lib/cache.js');
      const afterReload = await reloadedCache.getCachedSong(serverCreated.id);
      expect(afterReload).toMatchObject({ title: newTitle, chart_source: newerContent, created_at: serverCreatedAt });
      const updatedServer = { ...afterReload, updated_at: finalVersion, _sync: undefined };
      const secondSync = await reloadedCache.syncWithServer({
        library: async () => ({ songs: [updatedServer] }),
        songs: { update: async (id, payload) => {
          expect(id).toBe(serverCreated.id);
          expect(payload.title).toBe(newTitle);
          expect(payload.chart_source).toBe(newerContent);
          expect(payload.base_updated_at).toBe(serverVersion);
          return updatedServer;
        } },
        setlists: { all: async () => [] },
      });
      expect(secondSync.ok).toBe(true);
      expect(await reloadedCache.getCachedSong(serverCreated.id)).toMatchObject({
        title: newTitle, chart_source: newerContent, created_at: serverCreatedAt, updated_at: finalVersion,
      });
      expect(await reloadedCache.getPendingMutations()).toEqual([]);
    } finally {
      await cache.clearDirty(localId);
      await cache.deleteCachedSong(localId);
      await cache.clearDirty(serverCreated.id);
      await cache.deleteCachedSong(serverCreated.id);
    }
  });

  it('sorts offline primary Genre changes and explicit clears from the cached projection through sync', async () => {
    const id = 987655;
    const original = { id, title: 'Invented genre edit song', artist: 'T17 synthetic', primary_genre: 'Rock',
      genre: 'Rock', genres: [{ name: 'Rock', is_primary: true }], vibes: [], updated_at: version };
    const other = { id: 987656, title: 'Invented comparator song', primary_genre: 'Mambo',
      genres: [{ name: 'Mambo', is_primary: true }] };
    const updated = (genres, token) => ({ ...original, genres, primary_genre: primaryGenreFromAssignments(genres),
      genre: primaryGenreFromAssignments(genres), updated_at: token });
    try {
      await cache.cacheSongs([original]);
      const jazz = updated([{ name: 'Jazz', is_primary: true }], version);
      await cache.savePendingSong({ ...original, ...jazz,
        _sync: { operation: 'update', base_updated_at: version } });
      const changed = await cache.getCachedSong(id);
      expect(sortSongs([other, changed], { key: 'genre' }).map(song => song.id)).toEqual([id, other.id]);

      const jazzAck = { ...jazz, updated_at: '2026-10-10T12:00:00.000002Z' };
      let nextServer = jazzAck;
      const sync = () => cache.syncWithServer({
        library: async () => ({ songs: [nextServer, other] }),
        songs: { update: async (_songId, payload) => {
          expect(payload.genres).toEqual(nextServer.genres);
          return nextServer;
        } },
        setlists: { all: async () => [] },
      });
      expect((await sync()).ok).toBe(true);
      expect((await cache.getCachedSong(id)).primary_genre).toBe('Jazz');

      const cleared = updated([], '2026-10-10T12:00:00.000002Z');
      nextServer = { ...cleared, updated_at: '2026-10-10T12:00:00.000003Z' };
      await cache.savePendingSong({ ...(await cache.getCachedSong(id)), ...cleared,
        _sync: { operation: 'update', base_updated_at: jazzAck.updated_at } });
      const pendingClear = await cache.getCachedSong(id);
      expect(pendingClear.primary_genre).toBeNull();
      expect(sortSongs([other, pendingClear], { key: 'genre' }).map(song => song.id)).toEqual([other.id, id]);
      expect((await sync()).ok).toBe(true);
      expect((await cache.getCachedSong(id)).primary_genre).toBeNull();
      expect(sortSongs([other, await cache.getCachedSong(id)], { key: 'genre' }).map(song => song.id)).toEqual([other.id, id]);
    } finally {
      await cache.clearDirty(id);
      await cache.deleteCachedSong(id);
    }
  });

  it('pushes a local song before dependent setlist work, maps IDs, then pulls', async () => {
    const localSongId = cache.makeLocalSongId();
    const localSong = { id: localSongId, title: 'Invented offline sync song', artist: 'T11 synthetic',
      chart_source: 'Invented verse\nInvented lyric', _sync: { operation: 'create', operation_id: cache.makeMutationId() } };
    await cache.savePendingSong(localSong);
    const localList = await cache.queueSetlistMutation({ type: 'create', payload: { name: 'T11 synthetic list' } });
    await cache.queueSetlistMutation({ type: 'entry_add', setlistId: localList.id,
      payload: { song_id: localSongId, capo_fret: 0 } });

    const order = [];
    let authoritativeSetlist = { id: 501, name: 'T11 synthetic list', updated_at: version, songs: [] };
    const songApi = {
      create: async payload => { order.push('song-create'); expect(payload.title).toBe(localSong.title);
        return { ...localSong, id: 401, updated_at: version, _sync: undefined }; },
      get: async id => ({ id, title: localSong.title, artist: localSong.artist, updated_at: version }),
    };
    const listApi = {
      mutate: async operation => {
        order.push(`setlist-${operation.type}`);
        if (operation.type === 'create') return authoritativeSetlist;
        expect(operation.payload.song_id).toBe(401);
        expect(operation.payload.capo_fret).toBe(0);
        authoritativeSetlist = { id: 501, name: 'T11 synthetic list', updated_at: '2026-10-10T12:00:00.000002Z',
          created_entry_id: '601', songs: [{ entry_id: '601', song_id: 401, title: localSong.title, position: 1 }] };
        return authoritativeSetlist;
      },
      all: async () => { order.push('setlist-pull'); return [{ id: 501, name: authoritativeSetlist.name,
        updated_at: authoritativeSetlist.updated_at }]; },
      get: async () => authoritativeSetlist,
    };
    const result = await cache.syncWithServer({
      library: async () => { order.push('song-pull'); return { songs: [{ id: 401, title: localSong.title, artist: localSong.artist, updated_at: version }] }; },
      songs: songApi,
      setlists: listApi,
    });

    expect(result.ok).toBe(true);
    expect(order.slice(0, 3)).toEqual(['song-create', 'setlist-create', 'setlist-entry_add']);
    expect(order.slice(3).sort()).toEqual(['setlist-pull', 'song-pull']);
    expect(await cache.getRedirectedSongId(localSongId)).toBe(401);
    expect(await cache.getPendingMutations()).toEqual([]);
    const lists = await cache.getCachedSetlists();
    expect(lists.map(list => list.name)).toContain('T11 synthetic list');
  });

  it('cancels an unsent local song create and its queued edits on delete', async () => {
    const id = cache.makeLocalSongId();
    await cache.savePendingSong({ id, title: 'Invented cancelled song', artist: 'T11 synthetic',
      _sync: { operation: 'create', operation_id: cache.makeMutationId() } });
    await cache.cancelPendingLocalSong(id);
    expect(await cache.getCachedSong(id)).toBeUndefined();
    expect((await cache.getPendingMutations()).filter(item => String(item.id) === id)).toEqual([]);
  });

  it('queues deletion of an existing song and clears it only after server acknowledgement', async () => {
    const id = 7412;
    const saved = { id, title: 'Invented persisted song', artist: 'T11 synthetic', updated_at: version };
    await cache.cacheSongs([saved]);
    await cache.updateCachedSong({ ...saved, _sync: { operation: 'delete', base_updated_at: version } });
    await cache.markDirty(id);
    try {
      const pending = await cache.getPendingMutations();
      expect(pending).toContainEqual(expect.objectContaining({ entity: 'song', id }));
      const cached = await cache.getCachedSong(id);
      expect(cached._sync).toEqual({ operation: 'delete', base_updated_at: version });

      let removed = false;
      const result = await cache.syncWithServer({
        library: async () => ({ songs: [] }),
        songs: { remove: async (songId, payload) => {
          expect(songId).toBe(id);
          expect(payload.base_updated_at).toBe(version);
          removed = true;
        } },
        setlists: { all: async () => [] },
      });
      expect(result.ok).toBe(true);
      expect(removed).toBe(true);
      expect(await cache.getCachedSong(id)).toBeUndefined();
      expect((await cache.getPendingMutations()).some(item => Number(item.id) === id)).toBe(false);
    } finally {
      await cache.clearDirty(id);
      await cache.deleteCachedSong(id);
    }
  });

  it('keeps push before pull and applies server-wins after a 409', async () => {
    const id = 7401;
    const server = { id, title: 'Authoritative server title', artist: 'T11 synthetic', updated_at: version };
    await cache.savePendingSong({ ...server, title: 'Stale offline title',
      _sync: { operation: 'update', base_updated_at: version } });
    const order = [];
    const result = await cache.syncWithServer({
      library: async () => { order.push('pull'); return { songs: [server] }; },
      songs: { update: async () => { order.push('push'); const error = new Error('stale'); error.status = 409; throw error; },
        get: async () => server, remove: async () => {} },
      setlists: { all: async () => [] },
    });
    expect(result.ok).toBe(true);
    expect(result.conflicts).toHaveLength(1);
    expect(order).toEqual(['push', 'pull']);
    expect((await cache.getCachedSong(id)).title).toBe(server.title);
    expect((await cache.getPendingMutations()).some(item => String(item.id) === String(id))).toBe(false);
  });

  it('signals exact-pull completion only after metadata transactions commit', async () => {
    const originalWindow = globalThis.window;
    const testWindow = new EventTarget();
    testWindow.__CCM_TEST_OBSERVE_SYNC_PULL__ = true;
    const signals = [];
    testWindow.addEventListener('ccm:test-sync-pull-applied', event => signals.push(event.detail));
    vi.stubGlobal('window', testWindow);
    try {
      const response = { songs: [], exported_at: 'export-commit-synthetic',
        __t10_test_pull_id: 'pull-commit-synthetic' };
      const success = await cache.syncWithServer({
        library: async () => response,
        songs: {},
        setlists: { all: async () => [] },
      });
      expect(success.ok).toBe(true);
      expect(signals).toHaveLength(1);
      expect(signals[0]).toMatchObject({ pullId: 'pull-commit-synthetic', exportedAt: response.exported_at });
      expect(await cache.getMeta('last_synced')).toBe(signals[0].syncedAt);

      signals.length = 0;
      const originalPut = IDBObjectStore.prototype.put;
      let lastSyncedPutSucceeded = false;
      const putSpy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (value, key) {
        const request = originalPut.call(this, value, key);
        if (value?.key === 'last_synced') {
          const transaction = this.transaction;
          request.addEventListener('success', () => {
            lastSyncedPutSucceeded = true;
            transaction.abort();
          });
        }
        return request;
      });
      try {
        const failed = await cache.syncWithServer({
          library: async () => ({ ...response, __t10_test_pull_id: 'pull-abort-synthetic' }),
          songs: {},
          setlists: { all: async () => [] },
        });
        expect(lastSyncedPutSucceeded).toBe(true);
        expect(failed.ok).toBe(false);
        expect(signals).toEqual([]);

        // This is the same fail-closed condition used by teardown: without the
        // exact applied-pull signal, local fixture cleanup is not authorized.
        const requestFinished = true;
        const noPendingWork = true;
        const cleanupAuthorized = signals.some(signal => signal.pullId === 'pull-abort-synthetic')
          && requestFinished && noPendingWork;
        expect(cleanupAuthorized).toBe(false);
      } finally {
        putSpy.mockRestore();
      }
    } finally {
      if (originalWindow === undefined) vi.unstubAllGlobals();
      else vi.stubGlobal('window', originalWindow);
    }
  });
});
