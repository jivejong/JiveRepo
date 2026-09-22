import React, { useState, useEffect } from 'react';
import {
  ArrowLeft, PlusIcon, TrashIcon, MusicIcon, ChevronUp, ChevronDown, XIcon,
} from '../components/Icons';
import * as api from '../lib/api';
import * as cache from '../lib/cache';

const EMPTY_ENTRY = { song_id: '', transposed_key: '', capo_fret: '', notes: '' };

function formatGigDate(value) {
  const dateOnly = String(value).slice(0, 10);
  return new Date(`${dateOnly}T12:00:00`).toLocaleDateString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

export default function SetlistView({ setlistId, online, onBack, onSelectSong }) {
  const [setlist, setSetlist] = useState(null);
  const [loading, setLoading] = useState(true);
  const [allSongs, setAllSongs] = useState([]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [entry, setEntry] = useState(EMPTY_ENTRY);

  const load = async () => {
    try {
      const data = online
        ? await api.setlists.get(setlistId)
        : await cache.getCachedSetlist(setlistId);
      setSetlist(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [setlistId, online]);

  useEffect(() => {
    if (!online) return;
    api.songs.search()
      .then(result => setAllSongs(result.songs || []))
      .catch(err => setError(err.message));
  }, [online]);

  const removeSong = async (position) => {
    if (!online) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setlists.removeSong(setlistId, position);
      setSetlist(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const addSong = async () => {
    if (!entry.song_id) {
      setError('Choose a song to add');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setlists.addSong(setlistId, {
        song_id: Number(entry.song_id),
        transposed_key: entry.transposed_key.trim() || null,
        capo_fret: entry.capo_fret === '' ? null : Number(entry.capo_fret),
        notes: entry.notes.trim() || null,
      });
      setSetlist(updated);
      setEntry(EMPTY_ENTRY);
      setAdding(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const songs = setlist?.songs || [];

  const moveSong = async (index, delta) => {
    const target = index + delta;
    if (!online || target < 0 || target >= songs.length) return;
    const reordered = [...songs];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setlists.reorder(
        setlistId,
        reordered.map(song => song.song_id),
      );
      setSetlist(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="loading">Loading…</div>;
  if (!setlist) return <div className="loading">Setlist not found</div>;

  const songIds = new Set(songs.map(song => song.song_id));
  const availableSongs = allSongs.filter(song => !songIds.has(song.id));

  return (
    <div className="screen">
      <div className="topbar">
        <button className="icon-btn" onClick={onBack} aria-label="Back">
          <ArrowLeft size={20} />
        </button>
        <div style={{ flex: 1 }}>
          <div className="topbar-title">{setlist.name}</div>
          {setlist.gig_date && (
            <div className="topbar-sub">{formatGigDate(setlist.gig_date)}</div>
          )}
        </div>
      </div>

      {setlist.notes && <div className="setlist-notes">{setlist.notes}</div>}

      {online && (
        <div className="setlist-actions">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => { setAdding(value => !value); setError(null); }}
          >
            {adding ? <XIcon size={16} /> : <PlusIcon size={16} />}
            {adding ? 'Cancel' : 'Add song'}
          </button>
        </div>
      )}

      {adding && (
        <div className="setlist-form">
          <div className="form-group">
            <label className="form-label">Song</label>
            <select
              className="form-input"
              value={entry.song_id}
              onChange={event => setEntry(prev => ({ ...prev, song_id: event.target.value }))}
            >
              <option value="">Choose a song…</option>
              {availableSongs.map(song => (
                <option key={song.id} value={song.id}>{song.title} — {song.artist}</option>
              ))}
            </select>
          </div>
          <div className="setlist-entry-grid">
            <div className="form-group">
              <label className="form-label">Key override</label>
              <input
                className="form-input"
                value={entry.transposed_key}
                onChange={event => setEntry(prev => ({ ...prev, transposed_key: event.target.value }))}
                placeholder="Default"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Capo override</label>
              <input
                className="form-input"
                type="number"
                min="0"
                max="11"
                value={entry.capo_fret}
                onChange={event => setEntry(prev => ({ ...prev, capo_fret: event.target.value }))}
                placeholder="Default"
              />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Performance note</label>
            <input
              className="form-input"
              value={entry.notes}
              onChange={event => setEntry(prev => ({ ...prev, notes: event.target.value }))}
              placeholder="Optional"
            />
          </div>
          <button
            className="btn btn-primary btn-full"
            onClick={addSong}
            disabled={busy || !availableSongs.length}
          >
            Add to setlist
          </button>
          {!availableSongs.length && (
            <div className="form-help">Every song is already in this setlist.</div>
          )}
        </div>
      )}

      {error && <div className="form-error setlist-error">{error}</div>}

      <div className="list-section-head">
        {songs.length} song{songs.length !== 1 ? 's' : ''}
      </div>

      {songs.length === 0 && (
        <div className="empty-state">
          <MusicIcon size={48} />
          <p>No songs in this setlist yet</p>
        </div>
      )}

      {songs.map((song, index) => (
        <div
          key={`${song.song_id}-${song.position}`}
          className="song-row"
          onClick={() => onSelectSong(song, songs)}
          style={{ alignItems: 'flex-start', paddingTop: 12, paddingBottom: 12 }}
        >
          <div className="setlist-position">{song.position}</div>
          <div className="song-info">
            <div className="song-title">{song.title}</div>
            <div className="song-meta">
              {[song.artist, song.feel].filter(Boolean).join(' · ')}
            </div>
            {song.beatbuddy_structure && (
              <div className="setlist-song-detail">♪ {song.beatbuddy_structure}</div>
            )}
            {song.notes && <div className="setlist-song-note">{song.notes}</div>}
          </div>
          <div className="setlist-song-actions">
            <span className="song-key-badge">
              {song.transposed_key || song.chart_written_key || '?'}
              {(song.capo_fret ?? song.default_capo) > 0 && (
                <span className="setlist-capo">
                  {' '}c{song.capo_fret ?? song.default_capo}
                </span>
              )}
            </span>
            {online && (
              <div className="setlist-order-controls">
                <button
                  className="icon-btn"
                  onClick={event => { event.stopPropagation(); moveSong(index, -1); }}
                  aria-label={`Move ${song.title} up`}
                  disabled={busy || index === 0}
                >
                  <ChevronUp size={14} />
                </button>
                <button
                  className="icon-btn"
                  onClick={event => { event.stopPropagation(); moveSong(index, 1); }}
                  aria-label={`Move ${song.title} down`}
                  disabled={busy || index === songs.length - 1}
                >
                  <ChevronDown size={14} />
                </button>
                <button
                  className="icon-btn"
                  onClick={event => { event.stopPropagation(); removeSong(song.position); }}
                  aria-label={`Remove ${song.title}`}
                  disabled={busy}
                >
                  <TrashIcon size={14} />
                </button>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
