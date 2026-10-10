import React, { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, EditIcon, ChevronLeft, ChevronRight } from '../components/Icons';
import { renderChart } from '../lib/chartRenderer';
import { transposeChart, transposeChord, soundingKey, keyDelta, keyAfter } from '../../transpose';
import { useSong, useSwipe } from '../hooks';
import * as api from '../lib/api';
import * as cache from '../lib/cache';
import { buildPerformanceSaveTarget, resolveStartingPerformance } from '../setlistOverrides';
import { setlistWriteError } from '../setlistMutations';

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
  online, onBack, onEdit, onNavigateSong, onSetlistUpdated,
}) {
  const { song, loading, setSong } = useSong(songId, online);

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
    song?.default_capo, song?.capo_fret, setlistId, setlistEntry?.position,
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

  const swipeHandlers = useSwipe(goNext, goPrev);

  if (loading) return <div className="loading">Loading…</div>;
  if (!song)   return <div className="loading">Song not found</div>;

  const capo    = capoOverride ?? initialCapo;
  const baseKey = song.chart_written_key || 'C';

  // Current key after offset, in its CONVENTIONAL spelling (Bb, not A#).
  const currentWrittenKey = keyAfter(baseKey, semiOffset);

  // Apply transposition to the STRUCTURED chart content, spelling every chord
  // toward the current key so accidentals match the key you're in.
  const transposedContent = semiOffset !== 0
    ? transposeChart(song.chart_content, semiOffset, currentWrittenKey)
    : song.chart_content;

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
    if (inSetlist && !online) {
      setSaveError('Saving setlist overrides requires an internet connection.');
      return;
    }
    const target = buildPerformanceSaveTarget({ song, setlistId, setlistEntry }, currentWrittenKey, capo);
    setSaving(true);
    try {
      if (target.kind === 'setlist') {
        const updatedSetlist = await api.setlists.updateSong(
          target.setlistId, target.position, target.payload,
        );
        onSetlistUpdated?.(updatedSetlist);
        let cacheWarning = null;
        try {
          await cache.cacheSetlists([updatedSetlist]);
        } catch {
          cacheWarning = 'Saved to the setlist, but the offline copy could not be refreshed.';
        }
        setInitialOffset(semiOffset);
        setInitialCapo(capo);
        setCapoOverride(null);
        setSaveError(cacheWarning);
      } else {
        if (online) {
          await api.songs.update(target.songId, target.payload);
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
          await cache.updateCachedSong(patched);
          await cache.markDirty(song.id);
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
    <div className="screen" {...swipeHandlers}>
      {/* Top bar */}
      <div className="chart-topbar">
        <button className="icon-btn" onClick={onBack} aria-label="Back">
          <ArrowLeft size={20} />
        </button>
        <div className="chart-topbar-info">
          <div className="chart-topbar-title">{song.title}</div>
          <div className="chart-topbar-artist">{song.artist}</div>
        </div>
        <button className="icon-btn" onClick={() => onEdit(song)} aria-label="Edit">
          <EditIcon size={18} />
        </button>
      </div>

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
            disabled={saving || (inSetlist && !online)}
          >
            {inSetlist ? 'Save to setlist' : 'Save key'}
          </button>
        )}
      </div>
      {inSetlist && !online && hasChanges && (
        <div className="form-help" role="status">
          Saving setlist overrides requires a connection to the app server.
        </div>
      )}
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
      {renderChart(transposedContent)}

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
  );
}
