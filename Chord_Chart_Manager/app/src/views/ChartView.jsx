import React, { useState, useEffect, useCallback, useRef } from 'react';
import { ArrowLeft, EditIcon, ChevronLeft, ChevronRight } from '../components/Icons';
import { renderChart } from '../lib/chartRenderer';
import { transposeDecoratedRawLines } from '../lib/chartParser';
import { shouldIgnoreChartRowSwipe } from '../lib/chartSwipe';
import { transposeChart, transposeChord, soundingKey, keyDelta, keyAfter } from '../../transpose';
import { useSong, useSwipe } from '../hooks';
import * as api from '../lib/api';
import * as cache from '../lib/cache';
import { buildPerformanceSaveTarget, resolveStartingPerformance } from '../setlistOverrides';
import { setlistWriteError } from '../setlistMutations';
import { AUTO_SCROLL_DEFAULT_SPEED, AUTO_SCROLL_MAX_SPEED, AUTO_SCROLL_MIN_SPEED,
  AUTO_SCROLL_STEP, createAutoScrollController } from '../lib/autoScroll';
import { assignedGenreNames } from '../lib/songMetadata';

/**
 * ChartView — displays a single chart with:
 *   - Transpose controls (+/- half/whole step)
 *   - Capo spinner
 *   - Swipe left/right to navigate to adjacent song in current list
 *   - Offline edit with dirty flag
 *
 * Props:
 *   songId      — id of the song to display
 *   songList    — array of songs (for swipe navigation order)
 *   online      — boolean
 *   onBack      — () => void
 *   onEdit      — (song) => void
 */
export default function ChartView({
  songId, songList = [], setlistId = null, setlistEntry = null,
  online, onSync, onBack, onEdit, onNavigateSong, onSetlistUpdated,
  chartScale = 1,
}) {
  const { song, loading, loadError, setSong } = useSong(songId, online);

  // Local transpose state — offsets from the song's WRITTEN key.
  // On open we start at the preferred key (falling back to the written /
  // performance key), so `initialOffset` is the baseline the chart opens in;
  // `hasChanges` is measured against that baseline, not against zero.
  const [semiOffset,   setSemiOffset]   = useState(0);
  const [initialOffset, setInitialOffset] = useState(0);
  const [initialCapo, setInitialCapo] = useState(0);
  const [capoOverride, setCapoOverride] = useState(null); // null = use entry/song baseline
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [scrollSpeed, setScrollSpeed] = useState(AUTO_SCROLL_DEFAULT_SPEED);
  const [autoScrolling, setAutoScrolling] = useState(false);
  const [atBottom, setAtBottom] = useState(false);
  const scrollRef = useRef(null);
  const autoScrollRef = useRef(null);

  useEffect(() => {
    const controller = createAutoScrollController({
      onRunningChange: setAutoScrolling,
      onBottom: setAtBottom,
    });
    autoScrollRef.current = controller;
    const onVisibilityChange = () => {
      if (document.hidden) controller.pause();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      controller.stop();
      autoScrollRef.current = null;
    };
  }, [songId]);

  useEffect(() => { autoScrollRef.current?.setSpeed(scrollSpeed); }, [scrollSpeed]);

  // Resolve the starting key and capo from this setlist entry when present,
  // otherwise preserve the song-list preference/default behavior.
  useEffect(() => {
    if (!song) return;
    const written = song.chart_written_key || 'C';
    const start = resolveStartingPerformance(song, setlistEntry);
    const off = keyDelta(written, start.key);
    setInitialOffset(off);
    setSemiOffset(off);
    setInitialCapo(start.capo);
    setCapoOverride(null);
    setSaveError(null);
  }, [song?.id, song?.preferred_key, song?.chart_written_key,
    song?.default_capo, song?.capo_fret, setlistId, setlistEntry?.entry_id,
    setlistEntry?.transposed_key, setlistEntry?.capo_fret]);

  // Navigation within song list
  const inSetlist = setlistId != null && setlistEntry != null;
  const currentIdx = inSetlist
    ? songList.findIndex(s => s.position === setlistEntry.position)
    : songList.findIndex(s => s.id === songId);
  const prevSong   = currentIdx > 0                  ? songList[currentIdx - 1] : null;
  const nextSong   = currentIdx < songList.length - 1 ? songList[currentIdx + 1] : null;

  const goNext = useCallback(() => {
    if (nextSong) onNavigateSong?.(nextSong);
  }, [nextSong, onNavigateSong]);
  const goPrev = useCallback(() => {
    if (prevSong) onNavigateSong?.(prevSong);
  }, [prevSong, onNavigateSong]);

  const ignoreScrollableRowSwipe = useCallback(target => {
    return shouldIgnoreChartRowSwipe(target);
  }, []);
  const swipeHandlers = useSwipe(goNext, goPrev, 60, ignoreScrollableRowSwipe);

  const onChartScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    setAtBottom(element.scrollTop >= element.scrollHeight - element.clientHeight - 1);
  };

  // Retain an already-open chart while refreshing after online/offline changes
  // so its active scroller and animation controller keep the same DOM node.
  if (loading && (!song || String(song.id) !== String(songId))) {
    return <div className="loading">Loading…</div>;
  }
  if (!song || String(song.id) !== String(songId)) {
    return <div className="loading">{loadError
      ? <div className="form-error" role="status">{loadError}</div>
      : 'Song not found'}</div>;
  }

  const capo    = capoOverride ?? initialCapo;
  const baseKey = song.chart_written_key || 'C';
  const songMetadata = [assignedGenreNames(song.genres).join(', '),
    Array.isArray(song.vibes) ? song.vibes.filter(Boolean).join(', ') : song.feel,
    song.era].filter(Boolean);

  // Current key after offset, in its CONVENTIONAL spelling (Bb, not A#).
  const currentWrittenKey = keyAfter(baseKey, semiOffset);

  // Apply transposition to the STRUCTURED chart content, spelling every chord
  // toward the current key so accidentals match the key you're in.
  const displayContent = song.chart_content;
  const transposedContent = semiOffset !== 0
    ? transposeDecoratedRawLines(
      transposeChart(displayContent, semiOffset, currentWrittenKey),
      symbol => transposeChord(symbol, semiOffset, currentWrittenKey),
    )
    : displayContent;

  // Sounding key (what the audience hears)
  const currentSoundingKey = soundingKey(currentWrittenKey, capo);

  const adjustSemi  = (n) => setSemiOffset(p => p + n);
  const adjustCapo  = (n) => setCapoOverride(p => Math.max(0, Math.min(11, (p ?? capo) + n)));
  const resetAll    = () => { setSemiOffset(initialOffset); setCapoOverride(null); };

  const hasChanges = semiOffset !== initialOffset || capo !== initialCapo;

  // Save to the setlist entry in setlist context; retain song preference saves
  // for the ordinary song list.
  const saveTransposition = async () => {
    if (!hasChanges || saving) return;
    setSaveError(null);
    const target = buildPerformanceSaveTarget({ song, setlistId, setlistEntry }, currentWrittenKey, capo);
    setSaving(true);
    try {
      if (target.kind === 'setlist') {
        const updatedSetlist = await cache.queueSetlistMutation({
          type: 'entry_edit', setlistId: target.setlistId,
          payload: { ...target.payload, entry_id: target.entryId },
        });
        onSetlistUpdated?.(updatedSetlist);
        let cacheWarning = 'Saved on this device.';
        if (online) {
          const result = await onSync?.();
          const resolved = await cache.getCachedSetlist(target.setlistId);
          if (resolved) onSetlistUpdated?.(resolved);
          if (result?.conflicts?.some(conflict => String(conflict.id) === String(target.setlistId))) {
            cacheWarning = 'The server setlist changed; its version was kept.';
          } else if (result?.ok) cacheWarning = null;
          else cacheWarning = 'Saved on this device; sync will retry when the app server is available.';
        }
        setInitialOffset(semiOffset);
        setInitialCapo(capo);
        setCapoOverride(null);
        setSaveError(cacheWarning);
      } else {
        if (online) {
          if (song._sync?.operation === 'create') {
            const patched = { ...song, ...target.payload, _sync: song._sync };
            await cache.savePendingSong(patched, song._sync.operation_id);
            const result = await cache.syncWithServer({
              library: api.library, setlists: api.setlists, songs: api.songs,
            });
            if (!result.ok) throw new Error(result.error || 'The pending song could not be synced.');
            const redirectedId = await cache.getRedirectedSongId(song.id);
            setSong(await cache.getCachedSong(redirectedId));
          } else {
            const patched = {
              ...song,
              ...target.payload,
              _sync: {
                operation: 'update',
                base_updated_at: song._sync?.base_updated_at ?? song.updated_at ?? null,
              },
            };
            await cache.savePendingSong(patched);
            const result = await cache.syncWithServer({
              library: api.library, setlists: api.setlists, songs: api.songs,
            });
            if (!result.ok) throw new Error(result.error || 'The song update remains queued for sync.');
            if (result.conflicts?.some(conflict => String(conflict.id) === String(song.id))) {
              throw new Error('The server song changed; its version was kept.');
            }
            setSong(await cache.getCachedSong(song.id));
          }
        } else {
          const patched = {
            ...song,
            ...target.payload,
            _sync: song._sync?.operation === 'create'
              ? song._sync
              : {
                  operation: 'update',
                  base_updated_at: song._sync?.base_updated_at ?? song.updated_at ?? null,
                },
          };
          await cache.savePendingSong(patched, song._sync?.operation_id);
          setSong(patched);
        }
        setInitialOffset(semiOffset);
        setInitialCapo(capo);
        setCapoOverride(null);
      }
    } catch (error) {
      setSaveError(target.kind === 'setlist'
        ? setlistWriteError(error)
        : error.message || 'Could not save the performance settings.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="chart-view" style={{ '--chart-font-scale': chartScale }}>
    <div className="screen chart-scroll" ref={scrollRef} tabIndex={0} aria-label="Chart content"
      {...swipeHandlers} onScroll={onChartScroll}>
      {/* Top bar */}
      <div className={`chart-topbar ${songMetadata.length ? 'has-meta' : ''}`}>
        <button className="icon-btn" onClick={onBack} aria-label="Back">
          <ArrowLeft size={20} />
        </button>
        <div className="chart-topbar-info">
          <div className="chart-topbar-title">{song.title}</div>
          <div className="chart-topbar-artist">{song.artist}</div>
        </div>
        {songMetadata.length > 0 && (
          <div className="chart-topbar-meta">{songMetadata.join(' · ')}</div>
        )}
        <button className="icon-btn" onClick={() => onEdit(song)} aria-label="Edit">
          <EditIcon size={18} />
        </button>
      </div>
      {loadError && <div className="form-error" role="status">{loadError}</div>}

      {/* Meta bar: key, capo, BB */}
      <div className="chart-meta-bar">
        <div className="key-pill">
          {currentWrittenKey}
          {capo > 0 && ` → ${currentSoundingKey}`}
        </div>
        {capo > 0 && (
          <div className="capo-pill">Capo {capo}</div>
        )}
        {song.bpm && (
          <div className="capo-pill">{song.bpm} BPM</div>
        )}
        {song.beatbuddy_structure && (
          <div className="bb-pill">♩ {song.beatbuddy_structure}</div>
        )}
        {hasChanges && (
          <button
            className="btn btn-ghost btn-sm"
            style={{ marginLeft: 'auto', fontSize: 12 }}
            onClick={() => { void saveTransposition(); }}
            disabled={saving}
          >
            {inSetlist ? 'Save to setlist' : 'Save key'}
          </button>
        )}
      </div>
      {saveError && <div className="form-error" role="alert">{saveError}</div>}

      {/* Transpose + capo controls */}
      <div className="transpose-bar">
        <span className="transpose-label">Transpose</span>
        <div className="t-btns">
          <button className="t-btn" onClick={() => adjustSemi(-1)} aria-label="Down half step">−½</button>
          <button className="t-btn" onClick={() => adjustSemi(-2)} aria-label="Down whole step">−1</button>
        </div>
        <span className="transpose-key">{currentWrittenKey}</span>
        <div className="t-btns">
          <button className="t-btn" onClick={() => adjustSemi(1)} aria-label="Up half step">+½</button>
          <button className="t-btn" onClick={() => adjustSemi(2)} aria-label="Up whole step">+1</button>
        </div>

        <div className="capo-controls">
          <span className="transpose-label">Capo</span>
          <button className="t-btn" onClick={() => adjustCapo(-1)} aria-label="Decrease capo">−</button>
          <span className="capo-val">{capo}</span>
          <button className="t-btn" onClick={() => adjustCapo(1)} aria-label="Increase capo">+</button>
        </div>

        <div className="transpose-original-key" aria-label={`Original key: ${song.original_key || 'Unknown'}`}>
          <span>Original key</span>
          <strong>{song.original_key || 'Unknown'}</strong>
        </div>

        {hasChanges && (
          <button
            className="t-btn"
            style={{ marginLeft: 4, color: 'var(--text-muted)' }}
            onClick={resetAll}
            aria-label="Reset"
          >
            ↺
          </button>
        )}
      </div>

      {/* Chart content */}
      {renderChart(transposedContent, displayContent)}

      {/* Swipe navigation hint */}
      {(prevSong || nextSong) && (
        <div className="swipe-hint">
          {prevSong
            ? <><ChevronLeft size={14} /><span>{prevSong.title}</span></>
            : <span style={{ opacity: 0 }}>—</span>}
          <span style={{ flex: 1, textAlign: 'center', color: 'var(--text-muted)', fontSize: 11 }}>
            swipe to navigate
          </span>
          {nextSong
            ? <><span>{nextSong.title}</span><ChevronRight size={14} /></>
            : <span style={{ opacity: 0 }}>—</span>}
        </div>
      )}
    </div>
      <div className="auto-scroll-controls" role="group" aria-label="Chart auto-scroll controls">
        <button className="auto-scroll-button" onClick={() => {
          if (autoScrolling) autoScrollRef.current?.pause();
          else autoScrollRef.current?.start(scrollRef.current, scrollSpeed);
        }} aria-label={autoScrolling ? 'Pause auto-scroll' : 'Start auto-scroll'}>
          {autoScrolling ? 'Pause' : 'Start'}
        </button>
        <button className="auto-scroll-button" onClick={() => setScrollSpeed(value => value - AUTO_SCROLL_STEP)}
          disabled={scrollSpeed <= AUTO_SCROLL_MIN_SPEED} aria-label="Decrease auto-scroll speed">Slower</button>
        <span className="auto-scroll-speed" aria-live="polite">{scrollSpeed} CSS px/s</span>
        <button className="auto-scroll-button" onClick={() => setScrollSpeed(value => value + AUTO_SCROLL_STEP)}
          disabled={scrollSpeed >= AUTO_SCROLL_MAX_SPEED} aria-label="Increase auto-scroll speed">Faster</button>
        {atBottom && <span className="auto-scroll-status" role="status">At bottom</span>}
      </div>
    </div>
  );
}
