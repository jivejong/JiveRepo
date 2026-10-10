import React, { useState, useEffect } from 'react';
import {
  ArrowLeft, PlusIcon, TrashIcon, MusicIcon, ChevronUp, ChevronDown, XIcon, EditIcon,
} from '../components/Icons';
import * as api from '../lib/api';
import * as cache from '../lib/cache';
import { buildSetlistEntryPayload, buildSetlistMetadataPayload, setlistWriteError } from '../setlistMutations';

const EMPTY_ENTRY = { song_id: '', transposed_key: '', capo_fret: '', notes: '' };
const EMPTY_METADATA = { name: '', gig_date: '', notes: '' };

function formatGigDate(value) {
  const dateOnly = String(value).slice(0, 10);
  return new Date(`${dateOnly}T12:00:00`).toLocaleDateString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

export default function SetlistView({ setlistId, online, onBack, onDeleted, onSelectSong }) {
  const [setlist, setSetlist] = useState(null);
  const [loading, setLoading] = useState(true);
  const [allSongs, setAllSongs] = useState([]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [entry, setEntry] = useState(EMPTY_ENTRY);
  const [editingMetadata, setEditingMetadata] = useState(false);
  const [metadata, setMetadata] = useState(EMPTY_METADATA);
  const [editingPosition, setEditingPosition] = useState(null);
  const [entryDraft, setEntryDraft] = useState({});

  const load = async () => {
    try {
      const data = online
        ? await api.setlists.get(setlistId)
        : await cache.getCachedSetlist(setlistId);
      if (online && data) {
        try { await cache.cacheSetlists([data]); } catch { /* Online viewing still works if storage is unavailable. */ }
      }
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

  const applyUpdatedSetlist = async (updated) => {
    setSetlist(updated);
    try {
      await cache.cacheSetlists([updated]);
    } catch {
      setError('Saved on the app server, but the offline copy could not be refreshed. Stay connected and sync again before using it offline.');
    }
  };

  const startMetadataEdit = () => {
    setMetadata({
      name: setlist.name || '',
      gig_date: setlist.gig_date ? String(setlist.gig_date).slice(0, 10) : '',
      notes: setlist.notes || '',
    });
    setEditingMetadata(true);
    setError(null);
  };

  const saveMetadata = async (event) => {
    event.preventDefault();
    if (!online || busy) return;
    let payload;
    try {
      payload = buildSetlistMetadataPayload(metadata);
    } catch (err) {
      setError(err.message);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setlists.update(setlistId, payload);
      await applyUpdatedSetlist(updated);
      setEditingMetadata(false);
    } catch (err) {
      setError(setlistWriteError(err));
    } finally {
      setBusy(false);
    }
  };

  const deleteCurrentSetlist = async () => {
    if (!online || busy) return;
    if (!window.confirm(`Delete “${setlist.name}” and its entries? The songs in your library will remain.`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.setlists.remove(setlistId);
      try {
        await cache.deleteCachedSetlist(setlistId);
      } catch {
        setError('The setlist was deleted on the app server, but its offline cache could not be cleared. Sync again before using the setlist list offline.');
        return;
      }
      onDeleted?.();
    } catch (err) {
      setError(setlistWriteError(err));
    } finally {
      setBusy(false);
    }
  };

  const startEntryEdit = (song) => {
    setEditingPosition(song.position);
    setEntryDraft({
      transposed_key: song.transposed_key ?? '',
      capo_fret: song.capo_fret == null ? '' : String(song.capo_fret),
      notes: song.notes ?? '',
    });
    setError(null);
  };

  const saveEntry = async (position) => {
    if (!online || busy) return;
    let payload;
    try {
      payload = buildSetlistEntryPayload(entryDraft);
    } catch (err) {
      setError(err.message);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setlists.updateSong(setlistId, position, payload);
      await applyUpdatedSetlist(updated);
      setEditingPosition(null);
      setEntryDraft({});
    } catch (err) {
      setError(setlistWriteError(err));
    } finally {
      setBusy(false);
    }
  };

  const removeSong = async (position) => {
    if (!online) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setlists.removeSong(setlistId, position);
      await applyUpdatedSetlist(updated);
    } catch (err) {
      setError(setlistWriteError(err));
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
      await applyUpdatedSetlist(updated);
      setEntry(EMPTY_ENTRY);
      setAdding(false);
    } catch (err) {
      setError(setlistWriteError(err));
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
      await applyUpdatedSetlist(updated);
    } catch (err) {
      setError(setlistWriteError(err));
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

      {!online && (
        <div className="form-help setlist-connection-help" role="status">
          Setlist changes require a connection to the app server.
        </div>
      )}

      {setlist.notes && <div className="setlist-notes">{setlist.notes}</div>}

      <div className="setlist-actions setlist-management-actions">
        <button
          className="btn btn-ghost btn-sm"
          onClick={startMetadataEdit}
          disabled={!online || busy}
        >
          <EditIcon size={15} /> Edit setlist
        </button>
        <button
          className="btn btn-danger btn-sm"
          onClick={deleteCurrentSetlist}
          disabled={!online || busy}
        >
          <TrashIcon size={15} /> Delete setlist
        </button>
        {online && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => { setAdding(value => !value); setError(null); }}
            disabled={busy}
          >
            {adding ? <XIcon size={16} /> : <PlusIcon size={16} />}
            {adding ? 'Cancel' : 'Add song'}
          </button>
        )}
      </div>

      {editingMetadata && (
        <form className="setlist-form" onSubmit={saveMetadata}>
          <div className="form-group">
            <label className="form-label" htmlFor="setlist-name">Name</label>
            <input
              id="setlist-name"
              className="form-input"
              value={metadata.name}
              onChange={event => setMetadata(current => ({ ...current, name: event.target.value }))}
              required
              disabled={!online || busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="setlist-date">Gig date</label>
            <input
              id="setlist-date"
              className="form-input"
              type="date"
              value={metadata.gig_date}
              onChange={event => setMetadata(current => ({ ...current, gig_date: event.target.value }))}
              disabled={!online || busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="setlist-notes">Notes</label>
            <textarea
              id="setlist-notes"
              className="form-input"
              value={metadata.notes}
              onChange={event => setMetadata(current => ({ ...current, notes: event.target.value }))}
              disabled={!online || busy}
            />
          </div>
          <div className="setlist-form-actions">
            <button className="btn btn-ghost btn-sm" type="button" onClick={() => { setEditingMetadata(false); setError(null); }} disabled={busy}>Cancel</button>
            <button className="btn btn-primary btn-sm" type="submit" disabled={!online || busy}>Save setlist</button>
          </div>
        </form>
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
            disabled={!online || busy || !availableSongs.length}
          >
            Add to setlist
          </button>
          {!availableSongs.length && (
            <div className="form-help">Every song is already in this setlist.</div>
          )}
        </div>
      )}

      {error && <div className="form-error setlist-error" role="alert">{error}</div>}

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
        <React.Fragment key={`${song.song_id}-${song.position}`}>
          <div
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
              <div className="setlist-order-controls">
                <button
                  className="icon-btn"
                  onClick={event => { event.stopPropagation(); startEntryEdit(song); }}
                  aria-label={`Edit ${song.title} entry`}
                  disabled={!online || busy}
                >
                  <EditIcon size={14} />
                </button>
                {online && (
                  <>
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
                  </>
                )}
              </div>
            </div>
          </div>
          {editingPosition === song.position && (
            <div className="setlist-form setlist-entry-editor">
              <div className="setlist-entry-grid">
                <div className="form-group">
                  <label className="form-label" htmlFor={`entry-key-${song.position}`}>Key override</label>
                  <input
                    id={`entry-key-${song.position}`}
                    className="form-input"
                    value={entryDraft.transposed_key}
                    placeholder="Song default"
                    onChange={event => setEntryDraft(current => ({ ...current, transposed_key: event.target.value }))}
                    disabled={!online || busy}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label" htmlFor={`entry-capo-${song.position}`}>Capo override</label>
                  <input
                    id={`entry-capo-${song.position}`}
                    className="form-input"
                    type="number"
                    min="0"
                    max="11"
                    value={entryDraft.capo_fret}
                    placeholder="Song default"
                    onChange={event => setEntryDraft(current => ({ ...current, capo_fret: event.target.value }))}
                    disabled={!online || busy}
                  />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor={`entry-notes-${song.position}`}>Performance note</label>
                <input
                  id={`entry-notes-${song.position}`}
                  className="form-input"
                  value={entryDraft.notes}
                  onChange={event => setEntryDraft(current => ({ ...current, notes: event.target.value }))}
                  disabled={!online || busy}
                />
              </div>
              <div className="setlist-form-actions">
                <button className="btn btn-ghost btn-sm" onClick={() => { setEditingPosition(null); setError(null); }} disabled={busy}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={() => saveEntry(song.position)} disabled={!online || busy}>Save entry</button>
              </div>
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}
