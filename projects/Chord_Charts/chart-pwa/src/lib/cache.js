/**
 * lib/cache.js
 *
 * IndexedDB-backed offline cache for the tablet PWA.
 * Songs and setlists are cached locally so the app works at a gig
 * without a network connection.
 *
 * Schema:
 *   db: chart-manager  version: 1
 *     songs      — keyPath: id
 *     setlists   — keyPath: id
 *     meta       — keyPath: key  (sync timestamps, dirty flags)
 */

const DB_NAME    = 'chart-manager';
const DB_VERSION = 1;

let _db = null;

// ── Open DB ───────────────────────────────────────────────────────────────────
function openDb() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('songs')) {
        const s = db.createObjectStore('songs', { keyPath: 'id' });
        s.createIndex('title',  'title',  { unique: false });
        s.createIndex('artist', 'artist', { unique: false });
        s.createIndex('genre',  'genre',  { unique: false });
      }
      if (!db.objectStoreNames.contains('setlists')) {
        db.createObjectStore('setlists', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('meta')) {
        db.createObjectStore('meta', { keyPath: 'key' });
      }
    };

    req.onsuccess  = (e) => { _db = e.target.result; resolve(_db); };
    req.onerror    = ()  => reject(req.error);
  });
}

function tx(storeName, mode = 'readonly') {
  return _db.transaction(storeName, mode).objectStore(storeName);
}

function wrap(req) {
  return new Promise((res, rej) => {
    req.onsuccess = () => res(req.result);
    req.onerror   = () => rej(req.error);
  });
}

// ── Songs cache ───────────────────────────────────────────────────────────────

export async function cacheSongs(songList) {
  await openDb();
  const store = tx('songs', 'readwrite');
  for (const song of songList) store.put(song);
  return new Promise((res, rej) => {
    store.transaction.oncomplete = res;
    store.transaction.onerror    = rej;
  });
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

// ── Setlists cache ────────────────────────────────────────────────────────────

export async function cacheSetlists(setlistList) {
  await openDb();
  const store = tx('setlists', 'readwrite');
  for (const sl of setlistList) store.put(sl);
  return new Promise((res, rej) => {
    store.transaction.oncomplete = res;
    store.transaction.onerror    = rej;
  });
}

export async function getCachedSetlists() {
  await openDb();
  return wrap(tx('setlists').getAll());
}

export async function getCachedSetlist(id) {
  await openDb();
  return wrap(tx('setlists').get(id));
}

// ── Meta / sync state ─────────────────────────────────────────────────────────

export async function setMeta(key, value) {
  await openDb();
  return wrap(tx('meta', 'readwrite').put({ key, value, updatedAt: Date.now() }));
}

export async function getMeta(key) {
  await openDb();
  const row = await wrap(tx('meta').get(key));
  return row?.value;
}

// ── Dirty flag (offline edits) ────────────────────────────────────────────────

export async function markDirty(songId) {
  const dirty = (await getMeta('dirty_songs')) || [];
  if (!dirty.includes(songId)) {
    await setMeta('dirty_songs', [...dirty, songId]);
  }
}

export async function getDirtySongs() {
  return (await getMeta('dirty_songs')) || [];
}

export async function clearDirty(songId) {
  const dirty = (await getMeta('dirty_songs')) || [];
  await setMeta('dirty_songs', dirty.filter(id => id !== songId));
}

export async function clearAllDirty() {
  await setMeta('dirty_songs', []);
}

// ── Full sync ──────────────────────────────────────────────────────────────────
// Called when the PWA comes back online or on app open

export async function syncFromServer({ songs: songApi, setlists: setlistApi }) {
  try {
    const [songList, setlistList] = await Promise.all([
      songApi.search({ limit: 5000 }),   // get everything
      setlistApi.all(),
    ]);
    await Promise.all([
      cacheSongs(songList.songs),
      cacheSetlists(setlistList),
    ]);
    await setMeta('last_synced', Date.now());
    return { ok: true, songs: songList.songs.length, setlists: setlistList.length };
  } catch (err) {
    console.warn('Sync failed:', err.message);
    return { ok: false, error: err.message };
  }
}

// ── Offline search (basic substring match on cached data) ─────────────────────

export async function searchCached({ query, genre, feel, era, tags }) {
  const all = await getCachedSongs();
  return all.filter(song => {
    if (query) {
      const q = query.toLowerCase();
      if (!song.title?.toLowerCase().includes(q) &&
          !song.artist?.toLowerCase().includes(q)) return false;
    }
    if (genre && song.genre !== genre) return false;
    if (feel  && song.feel  !== feel)  return false;
    if (era   && song.era   !== era)   return false;
    // Tag filtering requires tags to be embedded on the song object
    if (tags?.length && song.tags) {
      const songTagNames = song.tags.map(t => t.name?.toLowerCase());
      if (!tags.every(t => songTagNames.includes(t.toLowerCase()))) return false;
    }
    return true;
  }).sort((a, b) => a.title?.localeCompare(b.title));
}
