const knownStaleVersions = new Map();
const INVALIDATION_KEY_PREFIX = 'ccm-invalidated-song:';

/**
 * Load a chart from IndexedDB first, then refresh it from the server when the
 * browser reports a connection. A pending local mutation remains authoritative
 * while a background GET is in flight.
 */
export async function loadChartSong(id, {
  online,
  readCached,
  resolveId,
  fetchRemote,
  deleteCachedIfUnchanged,
  writeCachedIfUnchanged,
  onCached,
  onRemote,
  onDiagnostic,
  invalidationStorage,
}) {
  let cached = null;
  try { cached = await readCached(id); } catch { /* an online fetch may still work */ }
  if (cached?._sync?.operation === 'delete') {
    onCached?.(null);
    return null;
  }
  const markerState = readInvalidationMarker(id, invalidationStorage);
  if (markerState.error) safelyDiagnose(onDiagnostic, markerReadDiagnostic(markerState));
  const staleVersion = markerState.version ?? knownStaleVersions.get(String(id));
  const cachedVersion = exactVersion(cached);
  const cachedIsKnownStale = Boolean(cached && !cached._sync && staleVersion
    && cachedVersion === staleVersion);
  if (cached && (cached._sync || !cachedIsKnownStale && staleVersion !== undefined)) {
    clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
  }
  if (cached && !cachedIsKnownStale) onCached?.(cached);

  if (cachedIsKnownStale && (!online || cached?._sync || String(id).startsWith('local-'))) {
    onCached?.(null);
    return null;
  }
  if (!online || cached?._sync || String(id).startsWith('local-')) return cached;

  let resolvedId;
  try {
    resolvedId = await resolveId(id);
    const remote = await fetchRemote(resolvedId);
    // Any intervening cache change wins over this older in-flight snapshot,
    // including an acknowledged update whose queue marker has been cleared.
    const latest = await readLatest(readCached, id);
    const safe = selectCurrentSnapshot(cached, latest);
    if (!safe.applyRemote) {
      clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
      onCached?.(safe.song);
      return safe.song;
    }

    let persisted = false;
    let persistError = null;
    try {
      if (typeof writeCachedIfUnchanged !== 'function') {
        throw new Error('conditional cache writer unavailable');
      }
      persisted = await writeCachedIfUnchanged(id, cached, remote);
    } catch (error) { persistError = error; }
    if (persisted) {
      // The atomic write protects the snapshot up to its commit. A different
      // tab can still edit or remove the record before this continuation runs;
      // prefer that newer cache state before publishing the accepted response.
      const afterPersist = await readLatest(readCached, id);
      const current = selectCurrentSnapshot(remote, afterPersist);
      clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
      if (!current.applyRemote) {
        onCached?.(current.song);
        return current.song;
      }
      onRemote?.(remote);
      return remote;
    }

    // The cache may have changed after the pre-write comparison. Preserve that
    // state; never turn a failed conditional write into an unconditional put.
    const afterWrite = await readLatest(readCached, id);
    const current = selectCurrentSnapshot(cached, afterWrite);
    if (afterWrite.available && !current.applyRemote) {
      clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
      onCached?.(current.song);
      return current.song;
    }

    // Keep (or create) suppression for the old cached version until the
    // accepted response is durably cached. The response can still be shown in
    // this live view, but an offline reopen will not mistake the old copy for
    // the refreshed snapshot.
    const marker = cached ? persistInvalidationMarker(id, cached, invalidationStorage) : null;
    safelyDiagnose(onDiagnostic, cacheWriteDiagnostic(persistError, marker));
    onRemote?.(remote);
    return remote;
  } catch (error) {
    if (error?.status === 404) {
      const latest = await readLatest(readCached, id);
      // The 404 describes the request-start snapshot. Any cache change since
      // then is newer local knowledge and wins over this stale response.
      if (!latest.available) {
        onCached?.(cachedIsKnownStale ? null : cached);
        return cachedIsKnownStale ? null : cached;
      }
      if (latest?.song?._sync) {
        clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
        const pending = latest.song._sync.operation === 'delete' ? null : latest.song;
        onCached?.(pending);
        return pending;
      }
      if (latest.song && (!cached || !sameSnapshot(cached, latest.song))) {
        clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
        onCached?.(latest.song);
        return latest.song;
      }
      if (!latest.song) {
        clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
        onCached?.(null);
        return null;
      }
      if (latest?.song && deleteCachedIfUnchanged) {
        let removed = false;
        let cleanupError = null;
        try { removed = await deleteCachedIfUnchanged(id, cached); }
        catch (errorDuringDelete) { cleanupError = errorDuringDelete; }
        const afterDeleteAttempt = await readLatest(readCached, id);
        if (afterDeleteAttempt.available && afterDeleteAttempt.song
            && (!sameSnapshot(afterDeleteAttempt.song, cached) || afterDeleteAttempt.song._sync)) {
          const current = afterDeleteAttempt.song._sync?.operation === 'delete'
            ? null : afterDeleteAttempt.song;
          clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
          onCached?.(current);
          return current;
        }
        if (removed && afterDeleteAttempt.available && !afterDeleteAttempt.song) {
          clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
          onCached?.(null);
          return null;
        }

        const marker = persistInvalidationMarker(id, cached, invalidationStorage);
        const detail = cleanupError?.message || (removed
          ? 'cache removal completed but its final state could not be verified'
          : 'cache removal did not complete');
        safelyDiagnose(onDiagnostic, marker.persisted
          ? `The server no longer has this chart, but its cached copy could not be cleared (${detail}). This exact version will stay hidden until the cache changes or it refreshes.`
          : `The server no longer has this chart, but its cached copy could not be cleared (${detail}). ${markerSuppressionStatus(marker)}`);
      } else if (cached) {
        const marker = persistInvalidationMarker(id, cached, invalidationStorage);
        safelyDiagnose(onDiagnostic, marker.persisted
          ? 'The server no longer has this chart, but cache invalidation is unavailable. This exact version will stay hidden until it refreshes.'
          : `The server no longer has this chart, but cache invalidation is unavailable. ${markerSuppressionStatus(marker)}`);
      }
      onCached?.(null);
      return null;
    }

    // A failed request is not evidence of deletion. Still honor cache changes
    // made while it was in flight, including a delete tombstone or removal.
    const latest = await readLatest(readCached, id);
    const safe = selectCurrentSnapshot(cached, latest);
    if (cachedIsKnownStale && safe.song && sameSnapshot(safe.song, cached)) {
      onCached?.(null);
      return null;
    }
    if (!safe.applyRemote && (!safe.song || !sameSnapshot(safe.song, cached))) {
      clearInvalidationMarker(id, invalidationStorage, onDiagnostic);
    }
    if (!safe.applyRemote) onCached?.(safe.song);
    return safe.applyRemote ? cached : safe.song;
  }
}

function exactVersion(song) {
  return typeof song?.updated_at === 'string' && song.updated_at.length
    ? song.updated_at : null;
}

function invalidationKey(id) {
  return `${INVALIDATION_KEY_PREFIX}${encodeURIComponent(String(id))}`;
}

function resolveStorage(storage) {
  if (storage !== undefined) {
    return storage
      ? { target: storage, failure: null }
      : { target: null, failure: 'unavailable' };
  }
  try {
    const target = globalThis.localStorage;
    return target
      ? { target, failure: null }
      : { target: null, failure: 'unavailable' };
  } catch (error) { return { target: null, failure: 'access', error }; }
}

function readInvalidationMarker(id, storage) {
  const resolved = resolveStorage(storage);
  if (!resolved.target) return { version: null, error: resolved.error || new Error(resolved.failure), failure: resolved.failure };
  try {
    const version = resolved.target.getItem(invalidationKey(id));
    return { version: version || null, error: null, failure: null };
  } catch (error) { return { version: null, error, failure: 'read' }; }
}

function persistInvalidationMarker(id, song, storage) {
  const version = exactVersion(song);
  if (!version) return { persisted: false, failure: 'version' };
  knownStaleVersions.set(String(id), version);
  const resolved = resolveStorage(storage);
  if (!resolved.target) return { persisted: false, failure: resolved.failure };
  try {
    resolved.target.setItem(invalidationKey(id), version);
    return { persisted: true, failure: null };
  } catch { return { persisted: false, failure: 'write' }; }
}

function clearInvalidationMarker(id, storage, onDiagnostic) {
  knownStaleVersions.delete(String(id));
  const resolved = resolveStorage(storage);
  if (!resolved.target) {
    safelyDiagnose(onDiagnostic, markerStorageDiagnostic(resolved.failure, 'clear'));
    return;
  }
  try { resolved.target.removeItem(invalidationKey(id)); }
  catch { safelyDiagnose(onDiagnostic, markerStorageDiagnostic('remove', 'clear')); }
}

function markerReadDiagnostic(markerState) {
  if (markerState.failure === 'access') {
    return 'Browser storage access failed; this chart’s saved deletion marker could not be checked. Reconnect and refresh before relying on its cached copy.';
  }
  if (markerState.failure === 'unavailable') {
    return 'Browser storage is unavailable; this chart’s saved deletion marker could not be checked. Reconnect and refresh before relying on its cached copy.';
  }
  return 'Browser storage could not read this chart’s saved deletion marker. Reconnect and refresh before relying on its cached copy.';
}

function markerStorageDiagnostic(failure, action) {
  const reason = failure === 'access' ? 'access to browser storage failed'
    : failure === 'unavailable' ? 'browser storage is unavailable'
      : 'the saved marker could not be removed';
  return `Could not ${action} this chart’s saved deletion marker because ${reason}; its exact version may remain hidden until storage is available.`;
}

function cacheWriteDiagnostic(error, marker) {
  const reason = error?.message || 'conditional cache persistence did not complete';
  const persistence = marker
    ? markerSuppressionStatus(marker)
    : 'No prior cached snapshot was available; this refreshed response is shown only in the current view.';
  return `The refreshed chart could not be saved to local storage (${reason}). ${persistence}`;
}

function markerSuppressionStatus(marker) {
  if (marker.persisted) {
    return 'The prior cached version will stay hidden until it changes or a later refresh is saved.';
  }
  const reason = marker.failure === 'access' ? 'Access to browser storage failed.'
    : marker.failure === 'unavailable' ? 'Browser storage is unavailable.'
      : marker.failure === 'write' ? 'Browser storage rejected the marker write.'
        : 'A marker could not be stored.';
  return `${reason} The prior cached version is suppressed only for this runtime; reconnect and refresh before reloading.`;
}

function safelyDiagnose(callback, message) {
  try { callback?.(message); } catch { /* diagnostics must not create an unhandled rejection */ }
}

async function readLatest(readCached, id) {
  try { return { available: true, song: await readCached(id) || null }; }
  catch { return { available: false, song: null }; }
}

function sameSnapshot(left, right) {
  // IndexedDB returns structured clones of the same persisted plain record.
  // Exact serialization detects all fields without timestamp coercion.
  return JSON.stringify(left) === JSON.stringify(right);
}

function selectCurrentSnapshot(initial, latestState) {
  if (!latestState.available) return { applyRemote: !initial, song: initial };
  const latest = latestState.song;
  if (latest?._sync?.operation === 'delete') return { applyRemote: false, song: null };
  if (latest?._sync) return { applyRemote: false, song: latest };
  if (initial && !latest) return { applyRemote: false, song: null };
  if (latest && (!initial || !sameSnapshot(initial, latest))) {
    return { applyRemote: false, song: latest };
  }
  return { applyRemote: true, song: latest };
}
