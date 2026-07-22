import React, { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, EditIcon, ChevronLeft, ChevronRight } from '../components/Icons';
import { renderChart } from '../lib/chartRenderer';
import { transposeChart, soundingKey, keyDelta } from '../../transpose';
import { useSong, useSwipe } from '../hooks';
import * as api from '../lib/api';
import * as cache from '../lib/cache';

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
export default function ChartView({ songId, songList = [], online, onBack, onEdit }) {
  const { song, loading, setSong } = useSong(songId, online);

  // Local transpose state — offsets from the song's stored key
  const [semiOffset,  setSemiOffset]  = useState(0);
  const [capoOverride, setCapoOverride] = useState(null); // null = use song default

  // Reset transpose when song changes
  useEffect(() => {
    setSemiOffset(0);
    setCapoOverride(null);
  }, [songId]);

  // Navigation within song list
  const currentIdx = songList.findIndex(s => s.id === songId);
  const prevSong   = currentIdx > 0                  ? songList[currentIdx - 1] : null;
  const nextSong   = currentIdx < songList.length - 1 ? songList[currentIdx + 1] : null;

  const [navSongId, setNavSongId] = useState(songId);
  const goNext = useCallback(() => { if (nextSong) setNavSongId(nextSong.id); }, [nextSong]);
  const goPrev = useCallback(() => { if (prevSong) setNavSongId(prevSong.id); }, [prevSong]);

  // Use navSongId to drive navigation — parent should update songId prop
  // (This component calls onBack equivalents via the nav arrows instead)

  const swipeHandlers = useSwipe(goNext, goPrev);

  if (loading) return <div className="loading">Loading…</div>;
  if (!song)   return <div className="loading">Song not found</div>;

  const capo    = capoOverride ?? song.default_capo ?? 0;
  const baseKey = song.chart_written_key || 'C';

  // Apply transposition to chart content
  const transposedContent = semiOffset !== 0
    ? transposeChart(song.chart_content, semiOffset)
    : song.chart_content;

  // Current written key after offset
  const currentWrittenKey = semiOffset !== 0
    ? transposeChord(baseKey, semiOffset)
    : baseKey;

  // Sounding key (what the audience hears)
  const currentSoundingKey = soundingKey(currentWrittenKey, capo);

  const adjustSemi  = (n) => setSemiOffset(p => p + n);
  const adjustCapo  = (n) => setCapoOverride(p => Math.max(0, Math.min(11, (p ?? capo) + n)));
  const resetAll    = () => { setSemiOffset(0); setCapoOverride(null); };

  const hasChanges = semiOffset !== 0 || capoOverride !== null;

  // Save transposition back to song record
  const saveTransposition = async () => {
    if (!hasChanges) return;
    const updated = {
      preferred_key: currentWrittenKey,
      default_capo:  capo,
    };
    if (online) {
      await api.songs.update(song.id, updated);
    } else {
      const patched = { ...song, ...updated };
      await cache.updateCachedSong(patched);
      await cache.markDirty(song.id);
      setSong(patched);
    }
    setSemiOffset(0);
    setCapoOverride(null);
  };

  // Helper to transpose a single chord token (for key display)
  function transposeChord(root, semis) {
    const SHARPS = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
    const FLATS  = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B'];
    const ENARH  = {Db:'C#',Eb:'D#',Fb:'E',Gb:'F#',Ab:'G#',Bb:'A#',Cb:'B'};
    const normalized = ENARH[root.replace(/m$/,'')] || root.replace(/m$/,'');
    const isMinor = root.endsWith('m');
    const idx = SHARPS.indexOf(normalized);
    if (idx < 0) return root;
    const newIdx = ((idx + semis) % 12 + 12) % 12;
    const FLAT_KEYS = new Set(['F','Bb','Eb','Ab','Db','Gb','Dm','Gm','Cm','Fm','Bbm','Ebm']);
    const sharpR = SHARPS[newIdx], flatR = FLATS[newIdx];
    const pref = FLAT_KEYS.has(isMinor ? sharpR+'m' : sharpR) || FLAT_KEYS.has(isMinor ? flatR+'m' : flatR);
    const newRoot = pref ? FLATS[newIdx] : SHARPS[newIdx];
    return isMinor ? newRoot + 'm' : newRoot;
  }

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
            onClick={saveTransposition}
          >
            Save key
          </button>
        )}
      </div>

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
