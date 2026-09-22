/**
 * IndexedDB cache and bidirectional tablet sync.
 *
 * Each offline mutation carries the server updated_at value it was based on.
 * The API checks that version while holding a row lock. If the server changed
 * first, the queued tablet mutation is discarded and the server copy wins.
 */

const DB_NAME = 'chart-manager';
const DB_VERSION = 1;
const CACHE_CHANGE_EVENT = 'chart-cache-change';

let _db = null;

function notifyCacheChange() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(CACHE_CHANGE_EVENT));
  }
}

function openDb() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = event => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains('songs')) {
        const songs = db.createObjectStore('songs', { keyPath: 'id' });
        songs.createIndex('title', 'title', { unique: false });
        songs.createIndex('artist', 'artist', { unique: false });
        songs.createIndex('genre', 'genre', { unique: false });
      }
      if (!db.objectStoreNames.contains('setlists')) {
        db.createObjectStore('setlists', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('meta')) {
        db.createObjectStore('meta', { keyPath: 'key' });
      }
    };
    req.onsuccess = event => { _db = event.target.result; resolve(_db); };
    req.onerror = () => reject(req.error);
  });
}

function tx(storeName, mode = 'readonly') {
  return _db.transaction(storeName, mode).objectStore(storeName);
}

function wrap(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function transactionDone(store) {
  return new Promise((resolve, reject) => {
    store.transaction.oncomplete = resolve;
    store.transaction.onerror = () => reject(store.transaction.error);
    store.transaction.onabort = () => reject(store.transaction.error);
  });
}

function cleanServerSong(song) {
  const { _sync, ...clean } = song;
  return clean;
}

// ── Songs ────────────────────────────────────────────────────────────────────

export async function cacheSongs(songList) {
  await openDb();
  const store = tx('songs', 'readwrite');
  for (const song of songList) store.put(cleanServerSong(song));
  return transactionDone(store);
}

async function replaceCachedSongs(songList) {
  await openDb();
  const store = tx('songs', 'readwrite');
  store.clear();
  for (const song of songList) store.put(cleanServerSong(song));
  return transactionDone(store);
}

export async function getCachedSongs() {
  await openDb();
  return wrap(tx('songs').getAll());
}

export async function getCachedSong(id) {
  await openDb();
  return wrap(tx('songs').get(id));
}

export async function updateCachedSong(song) {
  await openDb();
  return wrap(tx('songs', 'readwrite').put(song));
}

export async function deleteCachedSong(id) {
  await openDb();
  return wrap(tx('songs', 'readwrite').delete(id));
}

export function makeLocalSongId() {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `local-${suffix}`;
}

// ── Setlists ─────────────────────────────────────────────────────────────────

export async function cacheSetlists(setlistList) {
  await openDb();
  const store = tx('setlists', 'readwrite');
  for (const setlist of setlistList) store.put(setlist);
  return transactionDone(store);
}

async function replaceCachedSetlists(setlistList) {
  await openDb();
  const store = tx('setlists', 'readwrite');
  store.clear();
  for (const setlist of setlistList) store.put(setlist);
  return transactionDone(store);
}

export async function getCachedSetlists() {
  await openDb();
  return wrap(tx('setlists').getAll());
}

export async function getCachedSetlist(id) {
  await openDb();
  return wrap(tx('setlists').get(id));
}

// ── Metadata / pending mutations ─────────────────────────────────────────────

export async function setMeta(key, value) {
  await openDb();
  return wrap(tx('meta', 'readwrite').put({ key, value, updatedAt: Date.now() }));
}

export async function getMeta(key) {
  await openDb();
  const row = await wrap(tx('meta').get(key));
  return row?.value;
}

export async function markDirty(songId) {
  const dirty = (await getMeta('dirty_songs')) || [];
  if (!dirty.includes(songId)) {
    await setMeta('dirty_songs', [...dirty, songId]);
  }
  notifyCacheChange();
}

export async function getDirtySongs() {
  return (await getMeta('dirty_songs')) || [];
}

export async function clearDirty(songId) {
  const dirty = (await getMeta('dirty_songs')) || [];
  await setMeta('dirty_songs', dirty.filter(id => id !== songId));
  notifyCacheChange();
}

export async function clearAllDirty() {
  await setMeta('dirty_songs', []);
  notifyCacheChange();
}

export function onCacheChange(listener) {
  window.addEventListener(CACHE_CHANGE_EVENT, listener);
  return () => window.removeEventListener(CACHE_CHANGE_EVENT, listener);
}

function editablePayload(song) {
  const fields = [
    'title', 'artist', 'chart_written_key', 'preferred_key', 'original_key',
    'default_capo', 'bpm', 'release_year', 'beatbuddy_structure', 'chart_source',
  ];
  return Object.fromEntries(fields.map(field => [field, song[field]]));
}

async function keepServerVersion(songId, songApi) {
  try {
    const serverSong = await songApi.get(songId);
    await updateCachedSong(cleanServerSong(serverSong));
  } catch (error) {
    if (error.status !== 404) throw error;
    await deleteCachedSong(songId);
  }
  await clearDirty(songId);
}

async function rememberRedirect(localId, serverId) {
  const redirects = (await getMeta('song_id_redirects')) || {};
  await setMeta('song_id_redirects', { ...redirects, [localId]: serverId });
}

export async function getRedirectedSongId(id) {
  const redirects = (await getMeta('song_id_redirects')) || {};
  return redirects[id] ?? id;
}

async function pushPendingSongs(songApi) {
  const dirtyIds = await getDirtySongs();
  const conflicts = [];
  let pushed = 0;

  for (const songId of dirtyIds) {
    const local = await getCachedSong(songId);
    if (!local) {
      await clearDirty(songId);
      continue;
    }

    const operation = local._sync?.operation || 'update';
    const baseUpdatedAt = local._sync?.base_updated_at || local.updated_at || null;

    try {
      if (operation === 'create') {
        const saved = await songApi.create(editablePayload(local));
        await rememberRedirect(songId, saved.id);
        await deleteCachedSong(songId);
        await updateCachedSong(cleanServerSong(saved));
      } else if (operation === 'delete') {
        await songApi.remove(songId, { base_updated_at: baseUpdatedAt });
        await deleteCachedSong(songId);
      } else {
        const saved = await songApi.update(songId, {
          ...editablePayload(local),
          base_updated_at: baseUpdatedAt,
        });
        await updateCachedSong(cleanServerSong(saved));
      }
      await clearDirty(songId);
      pushed += 1;
    } catch (error) {
      const serverWins = operation !== 'create'
        && (error.status === 404 || error.status === 409);
      if (!serverWins) throw error;

      await keepServerVersion(songId, songApi);
      conflicts.push({ id: songId, title: local.title, operation });
    }
  }

  return { pushed, conflicts };
}

// Push pending tablet mutations first, then replace the cache with a fresh
// server snapshot. This ordering prevents a pull from overwriting queued work.
export async function syncWithServer({ library, setlists: setlistApi, songs: songApi }) {
  try {
    const pending = await pushPendingSongs(songApi);
    const [lib, setlistList] = await Promise.all([library(), setlistApi.all()]);
    await Promise.all([
      replaceCachedSongs(lib.songs || []),
      replaceCachedSetlists(setlistList || []),
    ]);
    if (lib.facets) await setMeta('facets', lib.facets);
    const syncedAt = Date.now();
    await setMeta('last_synced', syncedAt);
    await setMeta('last_sync_conflicts', pending.conflicts);
    notifyCacheChange();
    return {
      ok: true,
      pushed: pending.pushed,
      conflicts: pending.conflicts,
      songs: (lib.songs || []).length,
      setlists: (setlistList || []).length,
      syncedAt,
    };
  } catch (error) {
    console.warn('Sync failed:', error.message);
    return { ok: false, error: error.message };
  }
}

// Backward-compatible name for callers outside the React hooks.
export const syncFromServer = syncWithServer;

// ── Offline search ───────────────────────────────────────────────────────────

export async function searchCached({ q, query, tags }) {
  const text = (q ?? query ?? '').toLowerCase();
  const wanted = (typeof tags === 'string' ? tags.split(',') : (tags || []))
    .map(tag => tag.trim().toLowerCase()).filter(Boolean);
  const all = await getCachedSongs();
  return all.filter(song => {
    if (song._sync?.operation === 'delete') return false;
    if (text && !song.title?.toLowerCase().includes(text)
        && !song.artist?.toLowerCase().includes(text)) return false;
    if (wanted.length) {
      const have = new Set();
      (song.genres || []).forEach(genre => have.add((genre.name || genre).toLowerCase()));
      (song.vibes || []).forEach(vibe => have.add(String(vibe).toLowerCase()));
      if (song.era) have.add(String(song.era).toLowerCase());
      if (!wanted.every(tag => have.has(tag))) return false;
    }
    return true;
  }).sort((a, b) => a.title?.localeCompare(b.title));
}
