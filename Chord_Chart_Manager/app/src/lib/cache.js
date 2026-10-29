/**
 * IndexedDB cache and bidirectional tablet sync.
 *
 * Each offline mutation carries the server updated_at value it was based on.
 * The API checks that version while holding a row lock. If the server changed
 * first, the queued tablet mutation is discarded and the server copy wins.
 */
import { filterSongList } from '../tagMutations';
import { discardSetlistOperations, enqueueSetlistOperation, rebaseSetlistOperations,
  resolveSetlistOperationIds } from '../setlistQueue';

const DB_NAME = 'chart-manager';
const DB_VERSION = 1;
const CACHE_CHANGE_EVENT = 'chart-cache-change';

let _db = null;
let _changeChannel = null;

function ensureChangeChannel() {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel !== 'function') return null;
  _changeChannel ||= new window.BroadcastChannel('ccm-cache-change');
  if (!_changeChannel.onmessage) {
    _changeChannel.onmessage = () => window.dispatchEvent(new Event(CACHE_CHANGE_EVENT));
  }
  return _changeChannel;
}

function notifyCacheChange() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(CACHE_CHANGE_EVENT));
    ensureChangeChannel()?.postMessage({ at: Date.now() });
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

function mutationId() {
  return globalThis.crypto?.randomUUID?.()
    || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, character => {
      const random = Math.floor(Math.random() * 16);
      return (character === 'x' ? random : (random & 0x3) | 0x8).toString(16);
    });
}

export function makeMutationId() { return mutationId(); }

function transact(storeNames, mode, body) {
  return new Promise(async (resolve, reject) => {
    try {
      await openDb();
      const transaction = _db.transaction(storeNames, mode);
      let value;
      let failed = false;
      transaction.oncomplete = () => failed ? undefined : resolve(value);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted'));
      try { body(transaction, next => { value = next; }, error => {
        failed = true;
        try { transaction.abort(); } catch { /* already inactive */ }
        reject(error);
      }); } catch (error) {
        failed = true;
        try { transaction.abort(); } catch { /* already inactive */ }
        reject(error);
      }
    } catch (error) { reject(error); }
  });
}

function queueMetaValue(meta, key, value) { meta.put({ key, value, updatedAt: Date.now() }); }

function pendingQueueRequest(meta, callback, deferPersist = false) {
  const queueRequest = meta.get('pending_mutations');
  queueRequest.onsuccess = () => {
    const queue = queueRequest.result || { key: 'pending_mutations', value: [] };
    if (!Array.isArray(queue.value)) queue.value = [];
    callback(queue.value, () => queueMetaValue(meta, 'pending_mutations', queue.value));
    if (!deferPersist) queueMetaValue(meta, 'pending_mutations', queue.value);
  };
}

function cleanServerSong(song) {
  const { _sync, _operation_replayed, ...clean } = song;
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
  return transact(['songs', 'meta'], 'readwrite', transaction => {
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    const existingRequest = songs.getAll();
    const queueRequest = meta.get('pending_mutations');
    const ready = () => {
      if (existingRequest.readyState !== 'done' || queueRequest.readyState !== 'done') return;
      const pendingIds = new Set((queueRequest.result?.value || [])
        .filter(item => item.entity === 'song').map(item => String(item.id)));
      const pendingSongs = existingRequest.result.filter(song => pendingIds.has(String(song.id)));
      songs.clear();
      for (const song of songList) songs.put(cleanServerSong(song));
      // A mutation queued while the push phase was in flight must survive the
      // pull. Its local projection wins until a later push acknowledges it.
      for (const song of pendingSongs) songs.put(song);
    };
    existingRequest.onsuccess = queueRequest.onsuccess = ready;
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

// Commit the local projection and its queue revision together. This closes the
// window where an in-flight create acknowledgement could otherwise clear a
// just-saved edit before markDirty recorded it.
function sameEditValue(left, right) {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && sameEditValue(left[key], right[key]));
}

function assignmentKey(value) {
  return String(value?.name ?? value ?? '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();
}

function sameAssignmentValues(left, right, genre = false) {
  if (!Array.isArray(left) || !Array.isArray(right)) return false;
  const project = values => values.map(value => {
    const key = assignmentKey(value);
    return genre ? `${key}:${value && typeof value === 'object' && value.is_primary === true}` : key;
  }).filter(value => !value.startsWith(':')).sort();
  return sameEditValue(project(left), project(right));
}

export async function savePendingSong(song, operationId = null) {
  await openDb();
  return transact(['songs', 'meta'], 'readwrite', (transaction, setValue) => {
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    const allSongsRequest = songs.getAll();
    const queueRequest = meta.get('pending_mutations');
    const redirectsRequest = meta.get('song_id_redirects');
    let readyCount = 0;
    const ready = () => {
      readyCount += 1;
      if (readyCount !== 3) return;
      const allSongs = allSongsRequest.result;
      const redirects = redirectsRequest.result?.value || {};
      const redirectedId = redirects[song.id];
      const targetId = redirectedId ?? song.id;
      const queue = queueRequest.result?.value || [];
      let operation = queue.find(item => item.entity === 'song'
        && (String(item.id) === String(song.id) || String(item.id) === String(targetId)));
      const current = redirectedId
        ? allSongs.find(item => String(item.id) === String(redirectedId))
        : null;
      const patch = current
        ? { ...current, ...song, id: targetId,
          _sync: { ...song._sync, operation: 'update', base_updated_at: current.updated_at } }
        : song;
      songs.put(patch);
      if (!operation) {
        operation = { entity: 'song', id: targetId, op_id: operationId || mutationId(), local_revision: 0 };
        queue.push(operation);
      }
      if (redirectedId) {
        operation.id = targetId;
        // Keep a dispatched update's identity and frozen payload intact. Its
        // acknowledgement will observe this newer revision and rebase it.
        if (!operation.sent) {
          operation.op_id = mutationId();
          delete operation.sent_payload;
          delete operation.sent_revision;
        }
      }
      if (operation.rejected_code) {
        const rejectedField = operation.rejected_category === 'Genre' ? 'genres' : 'vibes';
        const candidate = rejectedField === 'genres' ? patch.genres : patch.vibes;
        const rejectedPayload = operation.sent_payload?.[rejectedField];
        const corrected = rejectedPayload !== undefined
          && !sameAssignmentValues(candidate, rejectedPayload, rejectedField === 'genres');
        if (corrected) {
          operation.op_id = mutationId();
          operation.sent = false;
          delete operation.sent_payload;
          delete operation.sent_revision;
          delete operation.rejected_code;
          delete operation.rejected_category;
        }
      }
      operation.local_revision = (operation.local_revision || 0) + 1;
      meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      setValue(patch);
    };
    for (const request of [allSongsRequest, queueRequest, redirectsRequest]) request.onsuccess = ready;
  }).then(patch => { notifyCacheChange(); return patch; });
}

export async function deleteCachedSong(id) {
  await openDb();
  return wrap(tx('songs', 'readwrite').delete(id));
}

// A remote 404 may retire only the exact clean snapshot that was checked.
// This transaction prevents a concurrent local edit from being erased between
// the caller's read and the cache deletion.
export async function deleteCachedSongIfUnchanged(id, expected) {
  await openDb();
  const removed = await transact('songs', 'readwrite', (transaction, setValue) => {
    const store = transaction.objectStore('songs');
    const request = store.get(id);
    request.onsuccess = () => {
      const current = request.result;
      if (current && !current._sync && JSON.stringify(current) === JSON.stringify(expected)) {
        store.delete(id);
        setValue(true);
      } else setValue(false);
    };
  });
  if (removed) notifyCacheChange();
  return removed;
}

// Persist an accepted server snapshot only while the exact cache state observed
// at request start is still current. This protects edits, deletions, and a
// newer clean version that arrive between the caller's read and this write.
export async function writeCachedSongIfUnchanged(id, expected, replacement) {
  await openDb();
  const written = await transact('songs', 'readwrite', (transaction, setValue) => {
    const store = transaction.objectStore('songs');
    const request = store.get(id);
    request.onsuccess = () => {
      const current = request.result || null;
      const matches = expected === null
        ? current === null
        : Boolean(current && !current._sync && JSON.stringify(current) === JSON.stringify(expected));
      if (matches) {
        store.put(cleanServerSong(replacement));
        setValue(true);
      } else setValue(false);
    };
  });
  if (written) notifyCacheChange();
  return written;
}

export async function cancelPendingLocalSong(id) {
  await openDb();
  return transact(['songs', 'setlists', 'meta'], 'readwrite', (transaction, _setValue, fail) => {
    const songs = transaction.objectStore('songs');
    const lists = transaction.objectStore('setlists');
    const meta = transaction.objectStore('meta');
    const allListsRequest = lists.getAll();
    const queueRequest = meta.get('pending_mutations');
    let readyCount = 0;
    const ready = () => {
      readyCount += 1;
      if (readyCount !== 2) return;
      const queue = queueRequest.result?.value || [];
      const pendingSongCreate = queue.find(item => item.entity === 'song'
        && String(item.id) === String(id) && item.sent);
      if (pendingSongCreate) return fail(new Error('Sync the pending song creation before deleting it.'));
      const dependentAdds = queue.filter(item => item.entity === 'setlist' && item.type === 'entry_add'
        && String(item.payload.song_id) === String(id));
      if (dependentAdds.some(item => item.sent)) {
        return fail(new Error('Sync the pending setlist entry before deleting its song.'));
      }
      const cancelledEntryIds = new Set(dependentAdds.map(item => String(item.payload.entry_id)));
      const nextQueue = queue.filter(item => {
        if (item.entity === 'song' && String(item.id) === String(id)) return false;
        if (item.entity !== 'setlist') return true;
        if (item.type === 'entry_add' && String(item.payload.song_id) === String(id)) return false;
        if (['entry_edit', 'entry_remove'].includes(item.type)
            && cancelledEntryIds.has(String(item.payload.entry_id)) && !item.sent) return false;
        if (item.type === 'reorder' && !item.sent) {
          item.payload.entry_ids = item.payload.entry_ids.filter(entryId => !cancelledEntryIds.has(String(entryId)));
        }
        return true;
      });
      for (const list of allListsRequest.result) {
        const before = list.songs || [];
        list.songs = before.filter(entry => String(entry.song_id) !== String(id)
          && String(entry.id) !== String(id));
        if (list.songs.length !== before.length) {
          list.songs.forEach((entry, index) => { entry.position = index + 1; });
          list.song_count = list.songs.length;
          lists.put(list);
        }
      }
      songs.delete(id);
      meta.put({ key: 'pending_mutations', value: nextQueue, updatedAt: Date.now() });
    };
    allListsRequest.onsuccess = queueRequest.onsuccess = ready;
  }).then(() => { notifyCacheChange(); });
}

export function makeLocalSongId() {
  const suffix = mutationId();
  return `local-${suffix}`;
}

// ── Setlists ─────────────────────────────────────────────────────────────────

export async function cacheSetlists(setlistList) {
  await openDb();
  return transact(['setlists', 'meta'], 'readwrite', transaction => {
    const lists = transaction.objectStore('setlists');
    const meta = transaction.objectStore('meta');
    const pendingRequest = meta.get('pending_mutations');
    pendingRequest.onsuccess = () => {
      const pendingIds = new Set((pendingRequest.result?.value || [])
        .filter(item => item.entity === 'setlist').map(item => String(item.setlist_id)));
      for (const setlist of setlistList) {
        if (pendingIds.has(String(setlist.id))) continue;
        const existingRequest = lists.get(setlist.id);
        existingRequest.onsuccess = () => {
          const existing = existingRequest.result;
          const cached = Array.isArray(setlist.songs) ? setlist
            : (existing?.songs ? { ...setlist, songs: existing.songs } : setlist);
          lists.put(Array.isArray(cached.songs)
            ? { ...cached, song_count: setlist.song_count ?? cached.songs.length }
            : cached);
        };
      }
    };
  });
}

async function replaceCachedSetlists(setlistList) {
  await openDb();
  return transact(['setlists', 'meta'], 'readwrite', transaction => {
    const lists = transaction.objectStore('setlists');
    const meta = transaction.objectStore('meta');
    const existingRequest = lists.getAll();
    const queueRequest = meta.get('pending_mutations');
    let readyCount = 0;
    const ready = () => {
      readyCount += 1;
      if (readyCount !== 2) return;
      const pendingIds = new Set((queueRequest.result?.value || [])
        .filter(item => item.entity === 'setlist').map(item => String(item.setlist_id)));
      const pending = existingRequest.result.filter(item => pendingIds.has(String(item.id)));
      lists.clear();
      for (const item of setlistList) lists.put(Array.isArray(item.songs)
        ? { ...item, song_count: item.songs.length }
        : item);
      for (const item of pending) lists.put(item);
    };
    existingRequest.onsuccess = queueRequest.onsuccess = ready;
  });
}

export async function getCachedSetlists() {
  await openDb();
  return (await wrap(tx('setlists').getAll())).filter(setlist => !setlist._deleted);
}

export async function getCachedSetlist(id) {
  await openDb();
  let setlist = await wrap(tx('setlists').get(id));
  if (!setlist && String(id).startsWith('local-')) {
    const redirects = await readRedirects('setlist_id_redirects');
    if (redirects[id]) setlist = await wrap(tx('setlists').get(redirects[id]));
  }
  return setlist?._deleted ? null : setlist;
}

export async function getRedirectedSetlistId(id) {
  return (await readRedirects('setlist_id_redirects'))[id] ?? id;
}

export async function queueSetlistMutation({ type, setlistId = null, payload = {}, entry = null }) {
  const localId = type === 'create' ? `local-${mutationId()}` : await getRedirectedSetlistId(setlistId);
  const entryRedirects = await readRedirects('entry_id_redirects');
  if (type === 'entry_add' && /^\d+$/.test(String(payload.song_id))) {
    payload = { ...payload, song_id: Number(payload.song_id) };
  }
  if (payload.entry_id) payload = { ...payload, entry_id: entryRedirects[payload.entry_id] ?? payload.entry_id };
  if (payload.entry_ids) payload = { ...payload, entry_ids: payload.entry_ids.map(id => entryRedirects[id] ?? id) };
  return transact(['setlists', 'songs', 'meta'], 'readwrite', (transaction, setValue, fail) => {
    const lists = transaction.objectStore('setlists');
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    pendingQueueRequest(meta, (queue, persistQueue) => {
      const addEnvelope = (target, operation, data, base = null) => enqueueSetlistOperation(queue, {
        entity: 'setlist', setlist_id: target, type: operation, payload: data,
        base_updated_at: base, op_id: mutationId(), local_revision: Date.now(),
      });
      if (type === 'create') {
        const list = { id: localId, ...payload, created_at: new Date().toISOString(), updated_at: null,
          songs: [], song_count: 0, _local: true, _pending: true };
        lists.put(list);
        addEnvelope(localId, 'create', payload);
        persistQueue();
        setValue(list);
        notifyCacheChange();
        return;
      }
      const request = lists.get(localId);
      request.onsuccess = () => {
        const list = request.result;
        if (!list || list._deleted) return fail(new Error('Setlist is no longer available offline.'));
        list.songs ||= [];
        if (type === 'metadata') Object.assign(list, payload);
        else if (type === 'delete') list._deleted = true;
        else if (type === 'entry_add') {
          const songRequest = songs.get(payload.song_id);
          songRequest.onsuccess = () => {
            const song = songRequest.result;
          if (!song) return fail(new Error('Song is not available in the offline library.'));
            const newEntry = { ...song, ...payload, entry_id: payload.entry_id || `local-${mutationId()}`,
              song_id: song.id, position: list.songs.length + 1 };
            list.songs.push(newEntry);
            list.song_count = list.songs.length;
            lists.put(list);
            addEnvelope(localId, 'entry_add', { entry_id: newEntry.entry_id, song_id: song.id,
              transposed_key: payload.transposed_key ?? null, capo_fret: payload.capo_fret ?? null,
              notes: payload.notes ?? null }, list.updated_at);
            setValue(list);
            persistQueue();
            notifyCacheChange();
          };
          return;
        } else if (type === 'entry_edit') {
          const target = list.songs.find(item => String(item.entry_id) === String(payload.entry_id));
          if (!target) return fail(new Error('Setlist entry is no longer available offline.'));
          Object.assign(target, payload);
        } else if (type === 'entry_remove') {
          list.songs = list.songs.filter(item => String(item.entry_id) !== String(payload.entry_id));
          list.songs.forEach((item, index) => { item.position = index + 1; });
          list.song_count = list.songs.length;
        } else if (type === 'reorder') {
          const byId = new Map(list.songs.map(item => [String(item.entry_id), item]));
          if (payload.entry_ids.length !== list.songs.length || payload.entry_ids.some(id => !byId.has(String(id)))) {
            return fail(new Error('Reorder must include every setlist entry exactly once.'));
          }
          list.songs = payload.entry_ids.map(id => byId.get(String(id)));
          list.songs.forEach((item, index) => { item.position = index + 1; });
        } else return fail(new Error(`Unsupported setlist mutation: ${type}`));

        list._pending = true;
        list.song_count = list.songs.length;
        lists.put(list);
        // Fold repeated unsent metadata and entry edits. The next serializer
        // keeps omitted properties absent, while null remains an explicit clear.
        const queued = addEnvelope(localId, type, payload, list.updated_at);
        if (queued.cancelledLocalSetlist) lists.delete(localId);
        setValue(list);
        persistQueue();
        notifyCacheChange();
      };
    }, true);
  });
}

// ── Metadata / pending mutations ─────────────────────────────────────────────

export async function setMeta(key, value) {
  await openDb();
  const store = tx('meta', 'readwrite');
  const request = store.put({ key, value, updatedAt: Date.now() });
  const [result] = await Promise.all([wrap(request), transactionDone(store)]);
  return result;
}

export async function getMeta(key) {
  await openDb();
  const row = await wrap(tx('meta').get(key));
  return row?.value;
}

export async function markDirty(songId, operationId = null) {
  await transact('meta', 'readwrite', (transaction, _setValue, fail) => {
    pendingQueueRequest(transaction.objectStore('meta'), queue => {
      let item = queue.find(entry => entry.entity === 'song' && String(entry.id) === String(songId));
      if (!item) {
        item = { entity: 'song', id: songId, op_id: operationId || mutationId(), local_revision: 0 };
        queue.push(item);
      }
      item.local_revision = (item.local_revision || 0) + 1;
    });
  }).catch(error => { throw error; });
  notifyCacheChange();
}

export async function clearDirty(songId) {
  await transact('meta', 'readwrite', transaction => {
    pendingQueueRequest(transaction.objectStore('meta'), queue => {
      queue.splice(0, queue.length, ...queue.filter(item => !(item.entity === 'song' && item.id === songId)));
    });
  });
  notifyCacheChange();
}

export async function getPendingMutations() {
  return transact('meta', 'readwrite', (transaction, setValue) => {
    pendingQueueRequest(transaction.objectStore('meta'), queue => setValue([...queue]));
  });
}

export function onCacheChange(listener) {
  window.addEventListener(CACHE_CHANGE_EVENT, listener);
  ensureChangeChannel();
  return () => window.removeEventListener(CACHE_CHANGE_EVENT, listener);
}

export function editablePayload(song) {
  const fields = [
    'title', 'artist', 'chart_written_key', 'preferred_key', 'original_key',
    'default_capo', 'bpm', 'release_year', 'beatbuddy_structure', 'chart_source',
    'genres', 'vibes',
  ];
  const payload = Object.fromEntries(fields.map(field => [field, song[field]]));
  if (song._sync?.chart_source_unchanged) delete payload.chart_source;
  if (song._sync?.operation !== 'create' && song._sync?.genres_unchanged === true) delete payload.genres;
  if (song._sync?.operation !== 'create' && song._sync?.vibes_unchanged === true) delete payload.vibes;
  return payload;
}

export async function markSongValidationRejected(songId, operationId, sentRevision, code, category) {
  await openDb();
  return transact(['songs', 'meta'], 'readwrite', (transaction, setValue, fail) => {
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    const songRequest = songs.get(songId);
    const queueRequest = meta.get('pending_mutations');
    const ready = () => {
      if (songRequest.readyState !== 'done' || queueRequest.readyState !== 'done') return;
      const local = songRequest.result;
      const queue = queueRequest.result?.value || [];
      const operation = queue.find(item => item.entity === 'song' && item.op_id === operationId);
      if (!operation || !local || (operation.sent_revision || 0) !== sentRevision) return setValue(false);
      if (!['UNKNOWN_TAG_DEFINITION', 'AMBIGUOUS_TAG_DEFINITION'].includes(code)
          || !['Genre', 'Vibe'].includes(category)) return fail(new Error('Invalid tag-definition rejection response.'));
      const field = category === 'Genre' ? 'genres' : 'vibes';
      const current = field === 'genres' ? local.genres : local.vibes;
      const dispatched = operation.sent_payload?.[field];
      const correctedWhileInFlight = operation.local_revision !== sentRevision
        && dispatched !== undefined
        && !sameAssignmentValues(current, dispatched, field === 'genres');
      if (correctedWhileInFlight) {
        operation.op_id = mutationId();
        operation.sent = false;
        delete operation.sent_payload;
        delete operation.sent_revision;
        delete operation.rejected_code;
        delete operation.rejected_category;
      } else {
        operation.sent = false;
        operation.rejected_code = code;
        operation.rejected_category = category;
      }
      meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      setValue(true);
    };
    songRequest.onsuccess = queueRequest.onsuccess = ready;
  });
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

async function markSongCreateDispatched(songId) {
  return transact(['songs', 'meta'], 'readwrite', (transaction, setValue, fail) => {
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    const songRequest = songs.get(songId);
    const queueRequest = meta.get('pending_mutations');
    let readyCount = 0;
    const ready = () => {
      readyCount += 1;
      if (readyCount !== 2) return;
      const local = songRequest.result;
      const queue = queueRequest.result?.value || [];
      const operation = queue.find(item => item.entity === 'song' && String(item.id) === String(songId));
      if (!local || !operation) return fail(new Error('Pending song create disappeared before dispatch.'));
      if (!operation.sent_payload) {
        operation.sent_payload = editablePayload(local);
        operation.sent_revision = operation.local_revision || 0;
      }
      operation.sent = true;
      meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      setValue({ local, operation: structuredClone(operation) });
    };
    songRequest.onsuccess = queueRequest.onsuccess = ready;
  });
}

async function markSongUpdateDispatched(songId) {
  return transact(['songs', 'meta'], 'readwrite', (transaction, setValue, fail) => {
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    const songRequest = songs.get(songId);
    const queueRequest = meta.get('pending_mutations');
    let readyCount = 0;
    const ready = () => {
      if (++readyCount !== 2) return;
      const local = songRequest.result;
      const queue = queueRequest.result?.value || [];
      const operation = queue.find(item => item.entity === 'song' && String(item.id) === String(songId));
      if (!local || !operation) return fail(new Error('Pending song update disappeared before dispatch.'));
      if (!operation.sent_payload) {
        operation.sent_payload = {
          ...editablePayload(local),
          base_updated_at: local._sync?.base_updated_at ?? local.updated_at ?? null,
          operation_id: operation.op_id,
        };
        operation.sent_revision = operation.local_revision || 0;
      } else if (!operation.sent_payload.operation_id) {
        // Add the already assigned UUID to updates queued by an older client.
        operation.sent_payload = { ...operation.sent_payload, operation_id: operation.op_id };
      }
      operation.sent = true;
      meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      setValue({ local, operation: structuredClone(operation) });
    };
    songRequest.onsuccess = queueRequest.onsuccess = ready;
  });
}

async function acknowledgeSongUpdate(songId, sentRevision, sentOperationId, saved, replayLatest = null) {
  const conflict = await transact(['songs', 'meta'], 'readwrite', (transaction, setValue) => {
    const songs = transaction.objectStore('songs');
    const meta = transaction.objectStore('meta');
    const songRequest = songs.get(songId);
    const queueRequest = meta.get('pending_mutations');
    let readyCount = 0;
    const ready = () => {
      if (++readyCount !== 2) return;
      const current = songRequest.result;
      const queue = queueRequest.result?.value || [];
      const operation = queue.find(item => item.entity === 'song' && item.op_id === sentOperationId);
      if (!operation) { setValue(false); return; }
      const changedAfterDispatch = (operation.local_revision || 0) !== sentRevision;
      const laterServerVersion = replayLatest && replayLatest.updated_at !== saved.updated_at;
      if (laterServerVersion && changedAfterDispatch) {
        songs.put(cleanServerSong(replayLatest));
        queue.splice(queue.indexOf(operation), 1);
        meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
        setValue(true);
        return;
      }
      if (changedAfterDispatch && current) {
        // Keep edits made during the request, but rebase them on its committed
        // server version. The next sync dispatches a fresh immutable payload.
        current.updated_at = saved.updated_at;
        current._sync = {
          ...current._sync,
          operation: 'update',
          base_updated_at: saved.updated_at,
          genres_unchanged: sameAssignmentValues(current.genres, saved.genres, true),
          vibes_unchanged: sameAssignmentValues(current.vibes, saved.vibes),
        };
        // The acknowledged UUID now names the dispatched revision's receipt;
        // later local work needs a new idempotency key.
        operation.op_id = mutationId();
        operation.sent = false;
        delete operation.sent_payload;
        delete operation.sent_revision;
        songs.put(current);
      } else {
        songs.put(cleanServerSong(laterServerVersion ? replayLatest : saved));
        queue.splice(queue.indexOf(operation), 1);
      }
      meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
      setValue(false);
    };
    songRequest.onsuccess = queueRequest.onsuccess = ready;
  });
  notifyCacheChange();
  return conflict;
}

async function acknowledgeSongCreate(localId, sentRevision, sentOperationId, saved) {
  const serverId = saved.id;
  await transact(['songs', 'setlists', 'meta'], 'readwrite', transaction => {
    const songs = transaction.objectStore('songs');
    const setlistStore = transaction.objectStore('setlists');
    const meta = transaction.objectStore('meta');
    const localRequest = songs.get(localId);
    const allListsRequest = setlistStore.getAll();
    const queueRequest = meta.get('pending_mutations');
    const redirectRequest = meta.get('song_id_redirects');
    let readyCount = 0;
    const ready = () => {
      readyCount += 1;
      if (readyCount !== 4) return;
      const queue = queueRequest.result?.value || [];
      const operation = queue.find(item => item.entity === 'song' && item.op_id === sentOperationId);
      const local = localRequest.result;
      const redirects = redirectRequest.result?.value || {};
      redirects[localId] = serverId;
      queueMetaValue(meta, 'song_id_redirects', redirects);

      for (const item of queue) {
        if (item.entity === 'setlist' && item.type === 'entry_add'
            && String(item.payload.song_id) === String(localId)) item.payload.song_id = serverId;
      }
      const cachedSetlists = allListsRequest.result;
      for (const list of cachedSetlists) {
        let changed = false;
        list.songs = (list.songs || []).map(entry => {
          if (String(entry.song_id) !== String(localId) && String(entry.id) !== String(localId)) return entry;
          changed = true;
          return { ...entry, id: String(entry.id) === String(localId) ? serverId : entry.id,
            song_id: String(entry.song_id) === String(localId) ? serverId : entry.song_id };
        });
        if (changed) setlistStore.put(list);
      }

      const changedAfterDispatch = Boolean(local && operation
        && (operation.local_revision || 0) > sentRevision);
      if (changedAfterDispatch) {
        const rebased = { ...local, id: serverId, created_at: saved.created_at, updated_at: saved.updated_at,
          _sync: { ...local._sync, operation: 'update', base_updated_at: saved.updated_at,
            genres_unchanged: sameAssignmentValues(local.genres, saved.genres, true),
            vibes_unchanged: sameAssignmentValues(local.vibes, saved.vibes) } };
        songs.delete(localId);
        songs.put(rebased);
        operation.id = serverId;
        operation.op_id = mutationId();
        operation.sent = false;
        operation.local_revision = 1;
        delete operation.sent_payload;
        delete operation.sent_revision;
      } else {
        if (local) songs.delete(localId);
        songs.put(cleanServerSong(saved));
        if (operation) queue.splice(queue.indexOf(operation), 1);
      }
      meta.put({ key: 'pending_mutations', value: queue, updatedAt: Date.now() });
    };
    for (const request of [localRequest, allListsRequest, queueRequest, redirectRequest]) request.onsuccess = ready;
  });
  notifyCacheChange();
}

export async function getRedirectedSongId(id) {
  const redirects = (await getMeta('song_id_redirects')) || {};
  return redirects[id] ?? id;
}

async function pushPendingSongs(songApi, assertSyncOwned) {
  const songOperations = (await getPendingMutations()).filter(item => item.entity === 'song');
  const conflicts = [];
  let pushed = 0;

  for (const queued of songOperations) {
    await assertSyncOwned();
    const songId = queued.id;
    let local = await getCachedSong(songId);
    if (!local) {
      await clearDirty(songId);
      continue;
    }

    const operation = local._sync?.operation || 'update';
    const baseUpdatedAt = local._sync?.base_updated_at || local.updated_at || null;
    if (queued.rejected_code) {
      const category = queued.rejected_category || 'tag';
      throw new Error(`${category} assignment needs correction before sync (${queued.rejected_code}).`);
    }
    const dispatch = operation === 'create' ? await markSongCreateDispatched(songId) : null;
    local = dispatch?.local || local;
    let dispatchedOperation = dispatch?.operation || null;

    try {
      if (operation === 'create') {
        const frozen = dispatch.operation.sent_payload;
        const saved = await songApi.create({ ...frozen, operation_id: dispatch.operation.op_id });
        await assertSyncOwned();
        await acknowledgeSongCreate(songId, dispatch.operation.sent_revision || 0,
          dispatch.operation.op_id, saved);
      } else if (operation === 'delete') {
        await songApi.remove(songId, { base_updated_at: baseUpdatedAt });
        await assertSyncOwned();
        await deleteCachedSong(songId);
      } else {
        const dispatchUpdate = await markSongUpdateDispatched(songId);
        local = dispatchUpdate.local;
        dispatchedOperation = dispatchUpdate.operation;
        const sent = dispatchUpdate.operation.sent_payload;
        const saved = await songApi.update(songId, sent);
        await assertSyncOwned();
        const replayLatest = saved._operation_replayed
          ? cleanServerSong(await songApi.get(songId))
          : null;
        const conflict = await acknowledgeSongUpdate(songId, dispatchUpdate.operation.sent_revision || 0,
          dispatchUpdate.operation.op_id, saved, replayLatest);
        if (conflict) conflicts.push({ id: songId, title: local.title, operation, replayConflict: true });
      }
      if (operation === 'delete') await clearDirty(songId);
      pushed += 1;
    } catch (error) {
      await assertSyncOwned();
      if (error.status === 422
          && ['UNKNOWN_TAG_DEFINITION', 'AMBIGUOUS_TAG_DEFINITION'].includes(error.code)
          && dispatchedOperation) {
        await markSongValidationRejected(songId, dispatchedOperation.op_id,
          dispatchedOperation.sent_revision || 0, error.code, error.category);
        error.message = `${error.category} (${error.code}): ${error.message}`;
        throw error;
      }
      const serverWins = operation !== 'create'
        && (error.status === 404 || error.status === 409);
      if (!serverWins) throw error;

      await keepServerVersion(songId, songApi);
      conflicts.push({ id: songId, title: local.title, operation });
    }
  }

  return { pushed, conflicts };
}

async function readRedirects(key) { return (await getMeta(key)) || {}; }

async function acknowledgeSetlistOperation(operation, response) {
  const localId = operation.setlist_id;
  const isCreate = operation.type === 'create';
  const actualId = isCreate ? response.id : localId;
  await transact(['setlists', 'meta'], 'readwrite', transaction => {
    const lists = transaction.objectStore('setlists');
    const meta = transaction.objectStore('meta');
    const listRequest = lists.get(localId);
    const queueRequest = meta.get('pending_mutations');
    const setlistMapRequest = meta.get('setlist_id_redirects');
    const entryMapRequest = meta.get('entry_id_redirects');
    let readyCount = 0;
    const ready = () => {
      readyCount += 1;
      if (readyCount !== 4) return;
      const remaining = (queueRequest.result?.value || []).filter(item => item.op_id !== operation.op_id);
      const setlistRedirects = setlistMapRequest.result?.value || {};
      const entryRedirects = entryMapRequest.result?.value || {};
      if (isCreate) {
        setlistRedirects[localId] = actualId;
        for (const item of remaining) if (item.entity === 'setlist' && String(item.setlist_id) === String(localId)) item.setlist_id = actualId;
        rebaseSetlistOperations(remaining, actualId, response.updated_at);
        queueMetaValue(meta, 'setlist_id_redirects', setlistRedirects);
      } else {
        rebaseSetlistOperations(remaining, localId, response.updated_at || operation.base_updated_at);
      }
      if (response.created_entry_id && operation.payload.entry_id) {
        entryRedirects[String(operation.payload.entry_id)] = String(response.created_entry_id);
        queueMetaValue(meta, 'entry_id_redirects', entryRedirects);
      }
      queueMetaValue(meta, 'pending_mutations', remaining);
      const cached = listRequest.result;
      if (!cached) return;
      if (operation.type === 'delete') {
        lists.delete(localId);
        return;
      }
      if (isCreate && String(actualId) !== String(localId)) lists.delete(localId);
      const current = { ...cached, id: actualId, updated_at: response.updated_at || cached.updated_at };
      if (response.created_entry_id && operation.payload.entry_id) {
        const clientEntryId = String(operation.payload.entry_id);
        current.songs = (current.songs || []).map(entry => String(entry.entry_id) === clientEntryId
          ? { ...entry, entry_id: String(response.created_entry_id) }
          : entry);
      }
      current._pending = remaining.some(item => item.entity === 'setlist' && String(item.setlist_id) === String(actualId));
      lists.put(current);
    };
    for (const request of [listRequest, queueRequest, setlistMapRequest, entryMapRequest]) request.onsuccess = ready;
  });
  notifyCacheChange();
}

async function discardSetlistAndRestore(operation, setlistApi) {
  const mapping = await readRedirects('setlist_id_redirects');
  const localId = operation.setlist_id;
  const serverId = mapping[localId] ?? localId;
  let authoritative = null;
  if (!String(localId).startsWith('local-')) {
    try { authoritative = await setlistApi.get(serverId); }
    catch (error) { if (error.status !== 404) throw error; }
  }
  await transact(['setlists', 'meta'], 'readwrite', transaction => {
    const lists = transaction.objectStore('setlists');
    const meta = transaction.objectStore('meta');
    pendingQueueRequest(meta, queue => {
      queue.splice(0, queue.length, ...discardSetlistOperations(queue, localId));
      if (!String(localId).startsWith('local-') && String(serverId) !== String(localId)) {
        queue.forEach(item => { if (String(item.setlist_id) === String(localId)) item.setlist_id = serverId; });
      }
    });
    const conflictsRequest = meta.get('last_sync_conflicts');
    conflictsRequest.onsuccess = () => {
      const conflicts = conflictsRequest.result?.value || [];
      const notice = { entity: 'setlist', id: serverId,
        name: operation.payload.name || authoritative?.name || '', operation: operation.type };
      const key = value => `${value.entity}:${value.id}:${value.operation}`;
      if (!conflicts.some(value => key(value) === key(notice))) conflicts.push(notice);
      queueMetaValue(meta, 'last_sync_conflicts', conflicts);
    };
    if (!authoritative) lists.delete(serverId);
    else lists.put(authoritative);
  });
  notifyCacheChange();
  return serverId;
}

async function appendConflictNotices(notices) {
  if (!notices.length) return;
  await transact('meta', 'readwrite', transaction => {
    const meta = transaction.objectStore('meta');
    const request = meta.get('last_sync_conflicts');
    request.onsuccess = () => {
      const existing = request.result?.value || [];
      const key = value => `${value.entity}:${value.id}:${value.operation}`;
      const seen = new Set(existing.map(key));
      for (const notice of notices) if (!seen.has(key(notice))) {
        existing.push(notice);
        seen.add(key(notice));
      }
      queueMetaValue(meta, 'last_sync_conflicts', existing);
    };
  });
  notifyCacheChange();
}

async function markMutationSent(operationId) {
  return transact('meta', 'readwrite', (transaction, setValue, fail) => {
    pendingQueueRequest(transaction.objectStore('meta'), queue => {
      const operation = queue.find(item => item.op_id === operationId);
      if (!operation) return fail(new Error('Pending mutation disappeared before send.'));
      operation.sent = true;
      setValue(structuredClone(operation));
    });
  });
}

async function pushPendingSetlists(setlistApi, assertSyncOwned) {
  const conflicts = [];
  let pushed = 0;
  const attempted = new Set();
  while (true) {
    await assertSyncOwned();
    const queue = (await getPendingMutations()).filter(item => item.entity === 'setlist');
    const candidate = queue.find(item => !attempted.has(item.op_id));
    if (!candidate) break;
    const operation = await markMutationSent(candidate.op_id);
    await assertSyncOwned();
    attempted.add(operation.op_id);
    try {
      const setlistRedirects = await readRedirects('setlist_id_redirects');
      const entryRedirects = await readRedirects('entry_id_redirects');
      const songRedirects = await readRedirects('song_id_redirects');
      const wireSetlistId = setlistRedirects[operation.setlist_id] ?? operation.setlist_id;
      const payload = resolveSetlistOperationIds(operation.type, operation.payload, {
        songs: songRedirects, entries: entryRedirects,
      });
      await assertSyncOwned();
      const response = await setlistApi.mutate({
        operation_id: operation.op_id,
        setlist_id: operation.type === 'create' ? null : Number(wireSetlistId),
        type: operation.type,
        base_updated_at: operation.type === 'create' ? null : operation.base_updated_at,
        payload,
      });
      await assertSyncOwned();
      await acknowledgeSetlistOperation(operation, response);
      pushed += 1;
    } catch (error) {
      if (error.status !== 404 && error.status !== 409) throw error;
      await assertSyncOwned();
      const serverId = await discardSetlistAndRestore(operation, setlistApi);
      conflicts.push({ entity: 'setlist', id: serverId, name: operation.payload.name || '', operation: operation.type });
      // Discard every pending operation for this aggregate. Later work for
      // different setlists remains eligible to push.
      attempted.clear();
    }
  }
  return { pushed, conflicts };
}

async function withSyncOwnership(callback) {
  if (typeof navigator !== 'undefined' && navigator.locks?.request) {
    return navigator.locks.request('ccm-offline-sync', { mode: 'exclusive' },
      () => callback(async () => {}));
  }
  const owner = mutationId();
  const leaseKey = 'sync_lease';
  const claim = async () => transact('meta', 'readwrite', (transaction, setValue) => {
    const meta = transaction.objectStore('meta');
    const request = meta.get(leaseKey);
    request.onsuccess = () => {
      const current = request.result?.value;
      if (current && current.expires > Date.now() && current.owner !== owner) {
        setValue({ fence: 0, waitMs: current.expires - Date.now() });
        return;
      }
      const fence = (current?.fence || 0) + 1;
      queueMetaValue(meta, leaseKey, { owner, fence, expires: Date.now() + 30000 });
      setValue({ fence, waitMs: 0 });
    };
  });
  let ownership = await claim();
  let cancelled = false;
  while (!ownership.fence && ownership.waitMs > 0 && !cancelled) {
    // Each wait is bounded by the latest observed expiry. If the owner renews
    // during that interval, claim() returns its new expiry and the waiter
    // schedules one new timer. Navigation cancels the current timer/listener.
    const ready = await new Promise(resolve => {
      let finished = false;
      const finish = value => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        if (typeof window !== 'undefined') window.removeEventListener('pagehide', onPageHide);
        resolve(value);
      };
      const onPageHide = () => finish(false);
      const timer = setTimeout(() => finish(true), Math.max(1, ownership.waitMs) + 50);
      if (typeof window !== 'undefined') window.addEventListener('pagehide', onPageHide, { once: true });
    });
    if (!ready) { cancelled = true; break; }
    ownership = await claim();
  }
  const fence = ownership.fence;
  if (!fence) return { ok: false, error: 'Sync is already running in another tab.' };
  const assertOwned = async () => {
    const valid = await transact('meta', 'readonly', (transaction, setValue) => {
      const request = transaction.objectStore('meta').get(leaseKey);
      request.onsuccess = () => setValue(request.result?.value?.owner === owner
        && request.result.value.fence === fence && request.result.value.expires > Date.now());
    });
    if (!valid) throw new Error('Sync ownership expired; a later sync owner will safely retry the pending operation.');
  };
  const heartbeat = setInterval(() => {
    void transact('meta', 'readwrite', transaction => {
      const meta = transaction.objectStore('meta');
      const request = meta.get(leaseKey);
      request.onsuccess = () => {
        if (request.result?.value?.owner === owner && request.result.value.fence === fence) {
          queueMetaValue(meta, leaseKey, { owner, fence, expires: Date.now() + 30000 });
        }
      };
    });
  }, 10000);
  try { return await callback(assertOwned); }
  finally {
    clearInterval(heartbeat);
    await transact('meta', 'readwrite', transaction => {
      const meta = transaction.objectStore('meta');
      const request = meta.get(leaseKey);
      request.onsuccess = () => {
        if (request.result?.value?.owner === owner && request.result.value.fence === fence) meta.delete(leaseKey);
      };
    });
  }
}

// Push pending tablet mutations first, then replace the cache with a fresh
// server snapshot. This ordering prevents a pull from overwriting queued work.
export async function syncWithServer({ library, setlists: setlistApi, songs: songApi }) {
  return withSyncOwnership(async assertSyncOwned => {
  try {
    const pendingSongs = await pushPendingSongs(songApi, assertSyncOwned);
    const pendingSetlists = await pushPendingSetlists(setlistApi, assertSyncOwned);
    const pending = {
      pushed: pendingSongs.pushed + pendingSetlists.pushed,
      conflicts: [...pendingSongs.conflicts, ...pendingSetlists.conflicts],
    };
    await assertSyncOwned();
    const [lib, setlistSummaries] = await Promise.all([library(), setlistApi.all()]);
    const setlistList = await Promise.all((setlistSummaries || []).map(setlist =>
      Array.isArray(setlist.songs) ? setlist : setlistApi.get(setlist.id),
    ));
    await assertSyncOwned();
    await Promise.all([
      replaceCachedSongs(lib.songs || []),
      replaceCachedSetlists(setlistList || []),
    ]);
    if (lib.facets) await setMeta('facets', lib.facets);
    const syncedAt = Date.now();
    await setMeta('last_synced', syncedAt);
    await appendConflictNotices(pending.conflicts);
    notifyCacheChange();
    // Test-only lifecycle signal. Its payload token comes from this exact
    // export response, and dispatch occurs only after all pull cache writes
    // and the last-synced marker have committed.
    if (typeof window !== 'undefined' && window.__CCM_TEST_OBSERVE_SYNC_PULL__ === true) {
      window.dispatchEvent(new CustomEvent('ccm:test-sync-pull-applied', {
        detail: { pullId: lib.__t10_test_pull_id ?? null, exportedAt: lib.exported_at, syncedAt },
      }));
    }
    return {
      ok: true,
      pushed: pending.pushed,
      conflicts: pending.conflicts,
      songs: (lib.songs || []).length,
      setlists: (setlistList || []).length,
      syncedAt,
    };
  } catch (error) {
    const message = error?.message || 'IndexedDB transaction failed';
    console.warn('Sync failed:', message);
    return {
      ok: false,
      error: message,
      ...(Number.isInteger(error?.status) ? { status: error.status } : {}),
      ...(typeof error?.code === 'string' ? { code: error.code } : {}),
      ...(typeof error?.category === 'string' ? { category: error.category } : {}),
    };
  }
  });
}

// ── Offline search ───────────────────────────────────────────────────────────

export async function searchCached({ q, query, tags, tag_filters }) {
  const all = await getCachedSongs();
  return filterSongList(all, { q, query, tags, tag_filters })
    .sort((a, b) => a.title?.localeCompare(b.title));
}
