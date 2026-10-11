import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import * as cache from '../../src/lib/cache.js';

const version = '2026-10-10T12:00:00.000001Z';

describe('offline cache and push-then-pull sync', () => {
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
});
