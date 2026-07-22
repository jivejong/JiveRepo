import React, { useState, useEffect } from 'react';
import { ArrowLeft, PlusIcon, TrashIcon, MusicIcon, ChevronRight } from '../components/Icons';
import * as api from '../lib/api';
import * as cache from '../lib/cache';

export default function SetlistView({ setlistId, online, onBack, onSelectSong }) {
  const [setlist, setSetlist] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const data = online
        ? await api.setlists.get(setlistId)
        : await cache.getCachedSetlist(setlistId);
      setSetlist(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [setlistId, online]);

  const removeSong = async (position) => {
    if (!online) return; // offline: read-only for setlists
    try {
      const updated = await api.setlists.removeSong(setlistId, position);
      setSetlist(updated);
    } catch (err) { console.error(err); }
  };

  if (loading)  return <div className="loading">Loading…</div>;
  if (!setlist) return <div className="loading">Setlist not found</div>;

  const songs = setlist.songs || [];

  return (
    <div className="screen">
      <div className="topbar">
        <button className="icon-btn" onClick={onBack} aria-label="Back">
          <ArrowLeft size={20} />
        </button>
        <div style={{ flex: 1 }}>
          <div className="topbar-title">{setlist.name}</div>
          {setlist.gig_date && (
            <div className="topbar-sub">
              {new Date(setlist.gig_date + 'T12:00').toLocaleDateString(undefined, {
                weekday: 'short', month: 'short', day: 'numeric'
              })}
            </div>
          )}
        </div>
      </div>

      {setlist.notes && (
        <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-muted)',
                      borderBottom: '1px solid var(--border)' }}>
          {setlist.notes}
        </div>
      )}

      <div className="list-section-head">
        {songs.length} song{songs.length !== 1 ? 's' : ''}
      </div>

      {songs.length === 0 && (
        <div className="empty-state">
          <MusicIcon size={48} />
          <p>No songs in this setlist yet</p>
        </div>
      )}

      {songs.map((entry, idx) => (
        <div key={entry.song_id}
          className="song-row"
          onClick={() => onSelectSong(entry, songs)}
          style={{ alignItems: 'flex-start', paddingTop: 12, paddingBottom: 12 }}
        >
          <div style={{
            width: 28, flexShrink: 0, fontSize: 13,
            color: 'var(--text-muted)', paddingTop: 2, fontFamily: 'var(--font-chord)'
          }}>
            {entry.position}
          </div>
          <div className="song-info">
            <div className="song-title">{entry.title}</div>
            <div className="song-meta">
              {[entry.artist, entry.feel].filter(Boolean).join(' · ')}
            </div>
            {entry.beatbuddy_structure && (
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                ♩ {entry.beatbuddy_structure}
              </div>
            )}
            {entry.notes && (
              <div style={{ fontSize: 12, color: 'var(--accent)', marginTop: 3 }}>
                {entry.notes}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            <span className="song-key-badge">
              {entry.transposed_key || entry.chart_written_key || '?'}
              {(entry.capo_fret ?? entry.default_capo) > 0 && (
                <span style={{ fontSize: 11, opacity: 0.6 }}>
                  {' '}c{entry.capo_fret ?? entry.default_capo}
                </span>
              )}
            </span>
            {online && (
              <button
                className="icon-btn"
                style={{ width: 28, height: 28 }}
                onClick={e => { e.stopPropagation(); removeSong(entry.position); }}
                aria-label={`Remove ${entry.title}`}
              >
                <TrashIcon size={14} />
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
