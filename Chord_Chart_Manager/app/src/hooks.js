/**
 * hooks/hooks.js — custom React hooks
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from './lib/api';
import * as cache from './lib/cache';
import { loadChartSong } from './lib/songLoading';
import { buildCachedTagOptions, filterSongList, mergePendingSongProjections } from './tagMutations';
import { createLatestRequestGate } from './lib/latestRequestGate';

// ── Online/offline detection ──────────────────────────────────────────────────
export function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on  = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online',  on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);
  return online;
}

// ── Swipe gesture detection ───────────────────────────────────────────────────
export function useSwipe(onSwipeLeft, onSwipeRight, threshold = 60, shouldIgnoreSwipe = null) {
  const startX = useRef(null);
  const startY = useRef(null);
  const startTarget = useRef(null);

  const onTouchStart = useCallback((e) => {
    startX.current = e.touches[0].clientX;
    startY.current = e.touches[0].clientY;
    startTarget.current = e.target;
  }, []);

  const onTouchEnd = useCallback((e) => {
    if (startX.current === null) return;
    const dx = e.changedTouches[0].clientX - startX.current;
    const dy = e.changedTouches[0].clientY - startY.current;
    // Only trigger if horizontal swipe is dominant
    const ignored = shouldIgnoreSwipe?.(startTarget.current, e) === true;
    if (!ignored && Math.abs(dx) > threshold && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) onSwipeLeft?.();
      else        onSwipeRight?.();
    }
    startX.current = null;
    startY.current = null;
    startTarget.current = null;
  }, [onSwipeLeft, onSwipeRight, threshold, shouldIgnoreSwipe]);

  return { onTouchStart, onTouchEnd };
}

// ── Song list with search + filter ────────────────────────────────────────────
export function useSongs(filters, online) {
  const [songs,   setSongs]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    let cancelled = false;
    let serverSongs = null;
    const cacheReads = createLatestRequestGate();
    setLoading(true);

    const publishCachedFallback = async () => {
      if (cancelled || serverSongs) return;
      const ticket = cacheReads.begin();
      const result = await cache.searchCached(filters);
      if (!cancelled && !serverSongs && cacheReads.isCurrent(ticket)) {
        // Keep the shell responsive while the authoritative online list is
        // still loading. An empty cache does not end loading or mask a remote
        // library that has not arrived yet.
        setSongs(result);
        setLoading(result.length === 0);
      }
    };

    const publishOnline = async () => {
      if (cancelled || !serverSongs) return;
      const ticket = cacheReads.begin();
      const snapshot = serverSongs;
      const cached = await cache.getCachedSongs();
      if (!cancelled && cacheReads.isCurrent(ticket) && serverSongs === snapshot) {
        setSongs(filterSongList(mergePendingSongProjections(snapshot, cached), filters));
      }
    };
    const load = async () => {
      try {
        if (online) {
          // Render any cached library immediately, then reconcile it with the
          // unfiltered server snapshot and pending mutation projections.
          void publishCachedFallback().catch(() => {});
          // Fetch every song; filters apply locally so pending edits count.
          const result = await api.songs.list();
          serverSongs = result.songs;
          cacheReads.invalidate();
          await publishOnline();
        } else {
          const result = await cache.searchCached(filters);
          if (!cancelled) setSongs(result);
        }
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          // Fall back to cache on network error
          const ticket = cacheReads.begin();
          try {
            const result = await cache.searchCached(filters);
            cacheReads.publishIfCurrent(ticket, cancelled, () => {
              setSongs(result);
              setError('Using cached data');
            });
          } catch {
            cacheReads.publishIfCurrent(ticket, cancelled, () => setError(err.message));
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    const unsubscribe = online ? cache.onCacheChange(() => {
      const publish = serverSongs ? publishOnline : publishCachedFallback;
      void publish().catch(err => {
        if (!cancelled) setError(`Could not refresh pending songs: ${err.message}`);
      });
    }) : () => {};
    return () => { cancelled = true; unsubscribe(); };
  }, [JSON.stringify(filters), online]);

  return { songs, loading, error };
}

// ── Single song ───────────────────────────────────────────────────────────────
export function useSong(id, online) {
  const [song,    setSong]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);

    loadChartSong(id, {
      online,
      readCached: cache.getCachedSong,
      resolveId: cache.getRedirectedSongId,
      fetchRemote: api.songs.get,
      deleteCachedIfUnchanged: cache.deleteCachedSongIfUnchanged,
      writeCachedIfUnchanged: cache.writeCachedSongIfUnchanged,
      onDiagnostic: message => { if (!cancelled) setLoadError(message); },
      onCached: data => {
        if (!cancelled) {
          setSong(data);
          setLoading(false);
        }
      },
      onRemote: data => { if (!cancelled) setSong(data); },
    }).finally(() => {
      if (!cancelled) setLoading(false);
      // A deterministic lifecycle signal lets browser checks distinguish a
      // delivered response from completion of its cache reconciliation.
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('ccm:song-load-settled', { detail: { id: String(id) } }));
      }
    });

    return () => { cancelled = true; };
  }, [id, online]);

  const refresh = useCallback(async () => {
    if (!id) return;
    await loadChartSong(id, {
      online,
      readCached: cache.getCachedSong,
      resolveId: cache.getRedirectedSongId,
      fetchRemote: api.songs.get,
      deleteCachedIfUnchanged: cache.deleteCachedSongIfUnchanged,
      writeCachedIfUnchanged: cache.writeCachedSongIfUnchanged,
      onDiagnostic: setLoadError,
      onCached: setSong,
      onRemote: setSong,
    });
  }, [id, online]);

  return { song, loading, loadError, setSong, refresh };
}

// ── All tags ──────────────────────────────────────────────────────────────────
export function useTags(online) {
  const [tags,    setTags]    = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let serverTags = [];
    const setFromSongs = async () => {
      const [songs, facets] = await Promise.all([
        cache.getCachedSongs(), cache.getMeta('facets'),
      ]);
      if (!cancelled) setTags(buildCachedTagOptions(songs, facets));
    };
    const setOnlineOptions = async () => {
      const songs = await cache.getCachedSongs();
      const pendingOptions = buildCachedTagOptions(songs.filter(song => song._sync), {});
      const known = new Set(serverTags.map(tag => `${tag.category === 'Vibe' ? 'Feel' : tag.category}:${tag.name.toLocaleLowerCase()}`));
      const extras = pendingOptions.filter(tag => {
        const key = `${tag.category}:${tag.name.toLocaleLowerCase()}`;
        if (known.has(key)) return false;
        known.add(key);
        return true;
      });
      if (!cancelled) setTags([...serverTags, ...extras]);
    };
    const load = async () => {
      setLoading(true);
      try {
        if (online) {
          serverTags = await api.tags.all();
          await setOnlineOptions();
        }
        else await setFromSongs();
      } catch {
        await setFromSongs().catch(() => {});
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const unsubscribe = online ? cache.onCacheChange(() => { void setOnlineOptions().catch(() => {}); }) : () => {};
    return () => { cancelled = true; unsubscribe(); };
  }, [online]);

  return { tags, loading };
}

// ── Setlists ──────────────────────────────────────────────────────────────────
export function useSetlists(online) {
  const [setlists, setSetlists] = useState([]);
  const [loading,  setLoading]  = useState(true);

  const load = useCallback(async () => {
    try {
      const cached = await cache.getCachedSetlists();
      setSetlists(cached || []);
      const data = online
        ? await api.setlists.all()
        : await cache.getCachedSetlists();
      if (online) await cache.cacheSetlists(data || []);
      setSetlists(online ? await cache.getCachedSetlists() : (data || []));
    } catch {
      const cached = await cache.getCachedSetlists().catch(() => []);
      setSetlists(cached);
    } finally {
      setLoading(false);
    }
  }, [online]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => cache.onCacheChange(load), [load]);

  return { setlists, loading, refresh: load };
}

// ── Sync on reconnect ─────────────────────────────────────────────────────────
export function useSync(online) {
  const [syncing,  setSyncing]  = useState(false);
  const [lastSync, setLastSync] = useState(null);
  const [dirty,    setDirty]    = useState([]);
  const [conflicts, setConflicts] = useState([]);
  const [syncError, setSyncError] = useState(null);
  const prevOnline = useRef(false);

  const refreshSyncState = useCallback(async () => {
    const [timestamp, pending, lastConflicts] = await Promise.all([
      cache.getMeta('last_synced'),
      cache.getPendingMutations(),
      cache.getMeta('last_sync_conflicts'),
    ]);
    setLastSync(timestamp || null);
    setDirty(pending);
    setConflicts(lastConflicts || []);
  }, []);

  useEffect(() => {
    refreshSyncState();
    return cache.onCacheChange(refreshSyncState);
  }, [refreshSyncState]);

  const syncNow = useCallback(async () => {
    if (!online) return { ok: false, error: 'Offline' };
    setSyncing(true);
    setSyncError(null);
    try {
      const result = await cache.syncWithServer({
        library: api.library,
        setlists: api.setlists,
        songs: api.songs,
      });
      if (!result.ok) setSyncError(result.error);
      await refreshSyncState();
      return result;
    } finally {
      setSyncing(false);
    }
  }, [online, refreshSyncState]);

  // Auto-sync when connection returns
  useEffect(() => {
    if (online && !prevOnline.current) {
      syncNow();
    }
    prevOnline.current = online;
  }, [online, syncNow]);

  return { syncing, lastSync, dirty, conflicts, syncError, syncNow };
}
