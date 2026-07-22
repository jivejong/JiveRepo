/**
 * hooks/index.js — custom React hooks
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../lib/api';
import * as cache from '../lib/cache';

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
export function useSwipe(onSwipeLeft, onSwipeRight, threshold = 60) {
  const startX = useRef(null);
  const startY = useRef(null);

  const onTouchStart = useCallback((e) => {
    startX.current = e.touches[0].clientX;
    startY.current = e.touches[0].clientY;
  }, []);

  const onTouchEnd = useCallback((e) => {
    if (startX.current === null) return;
    const dx = e.changedTouches[0].clientX - startX.current;
    const dy = e.changedTouches[0].clientY - startY.current;
    // Only trigger if horizontal swipe is dominant
    if (Math.abs(dx) > threshold && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) onSwipeLeft?.();
      else        onSwipeRight?.();
    }
    startX.current = null;
  }, [onSwipeLeft, onSwipeRight, threshold]);

  return { onTouchStart, onTouchEnd };
}

// ── Song list with search + filter ────────────────────────────────────────────
export function useSongs(filters, online) {
  const [songs,   setSongs]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const load = async () => {
      try {
        if (online) {
          const result = await api.songs.search(filters);
          if (!cancelled) setSongs(result.songs);
        } else {
          const result = await cache.searchCached(filters);
          if (!cancelled) setSongs(result);
        }
        setError(null);
      } catch (err) {
        if (!cancelled) {
          // Fall back to cache on network error
          try {
            const result = await cache.searchCached(filters);
            setSongs(result);
            setError('Using cached data');
          } catch {
            setError(err.message);
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [JSON.stringify(filters), online]);

  return { songs, loading, error };
}

// ── Single song ───────────────────────────────────────────────────────────────
export function useSong(id, online) {
  const [song,    setSong]    = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);

    const load = async () => {
      try {
        const data = online
          ? await api.songs.get(id)
          : await cache.getCachedSong(id);
        if (!cancelled) setSong(data);
      } catch {
        if (!cancelled) {
          const cached = await cache.getCachedSong(id).catch(() => null);
          if (!cancelled) setSong(cached);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [id, online]);

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const data = online ? await api.songs.get(id) : await cache.getCachedSong(id);
      setSong(data);
    } catch {}
  }, [id, online]);

  return { song, loading, setSong, refresh };
}

// ── All tags ──────────────────────────────────────────────────────────────────
export function useTags(online) {
  const [tags,    setTags]    = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!online) { setLoading(false); return; }
    api.tags.all()
      .then(setTags)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [online]);

  return { tags, loading };
}

// ── Setlists ──────────────────────────────────────────────────────────────────
export function useSetlists(online) {
  const [setlists, setSetlists] = useState([]);
  const [loading,  setLoading]  = useState(true);

  const load = useCallback(async () => {
    try {
      const data = online
        ? await api.setlists.all()
        : await cache.getCachedSetlists();
      setSetlists(data || []);
    } catch {
      const cached = await cache.getCachedSetlists().catch(() => []);
      setSetlists(cached);
    } finally {
      setLoading(false);
    }
  }, [online]);

  useEffect(() => { load(); }, [load]);

  return { setlists, loading, refresh: load };
}

// ── Sync on reconnect ─────────────────────────────────────────────────────────
export function useSync(online) {
  const [syncing,  setSyncing]  = useState(false);
  const [lastSync, setLastSync] = useState(null);
  const [dirty,    setDirty]    = useState([]);
  const prevOnline = useRef(false);

  // Load last sync time and dirty list on mount
  useEffect(() => {
    cache.getMeta('last_synced').then(ts => setLastSync(ts));
    cache.getDirtySongs().then(setDirty);
  }, []);

  // Auto-sync when connection returns
  useEffect(() => {
    if (online && !prevOnline.current) {
      setSyncing(true);
      cache.syncFromServer({ songs: api.songs, setlists: api.setlists })
        .then(result => {
          if (result.ok) setLastSync(Date.now());
        })
        .finally(() => setSyncing(false));
    }
    prevOnline.current = online;
  }, [online]);

  return { syncing, lastSync, dirty };
}
