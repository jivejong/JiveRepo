import React, { useState } from 'react';
import { ArrowLeft, CheckIcon, TrashIcon } from '../components/Icons';
import * as api from '../lib/api';
import * as cache from '../lib/cache';
import { parseChartBody } from '../lib/chartParser';

/**
 * EditView — edit a song's chart content and key metadata fields.
 * Saves to server when online; marks dirty and saves to cache when offline.
 */
export default function EditView({ song, online, onBack, onSaved, onDeleted }) {
  const [form, setForm] = useState({
    title:               song?.title               || '',
    artist:              song?.artist              || '',
    chart_written_key:   song?.chart_written_key   || '',
    original_key:        song?.original_key        || '',
    default_capo:        song?.default_capo        ?? 0,
    bpm:                 song?.bpm                 || '',
    beatbuddy_structure: song?.beatbuddy_structure || '',
    // Editable source of truth: the raw chart TEXT. On save the server
    // re-parses this into the structured chart_content used for display.
    chart_source:        song?.chart_source        || '',
  });

  const [saving,   setSaving]   = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error,    setError]    = useState(null);

  const set = (field, value) => setForm(p => ({ ...p, [field]: value }));

  const save = async () => {
    if (!form.title.trim()) { setError('Title is required'); return; }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        ...form,
        default_capo: Number(form.default_capo) || 0,
        bpm:          form.bpm ? Number(form.bpm) : null,
      };
      if (online) {
        const isOfflineCreate = song?._sync?.operation === 'create';
        const saved = song?.id && !isOfflineCreate
          ? await api.songs.update(song.id, payload)
          : await api.songs.create(payload);
        if (song?._sync) {
          await cache.deleteCachedSong(song.id);
          await cache.updateCachedSong(saved);
          await cache.clearDirty(song.id);
        }
        onSaved(saved);
      } else {
        const id = song?.id ?? cache.makeLocalSongId();
        const operation = song?._sync?.operation === 'create'
          ? 'create'
          : (song?.id ? 'update' : 'create');
        const patched = {
          ...song,
          ...payload,
          id,
          chart_content: parseChartBody(payload.chart_source),
          _sync: {
            operation,
            base_updated_at: operation === 'create'
              ? null
              : (song?._sync?.base_updated_at ?? song?.updated_at ?? null),
          },
        };
        await cache.updateCachedSong(patched);
        await cache.markDirty(id);
        onSaved(patched);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirm(`Delete "${song.title}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      if (online) {
        await api.songs.remove(song.id);
        onDeleted(song.id);
      } else {
        if (song._sync?.operation === 'create') {
          await cache.deleteCachedSong(song.id);
          await cache.clearDirty(song.id);
        } else {
          await cache.updateCachedSong({
            ...song,
            _sync: {
              operation: 'delete',
              base_updated_at: song._sync?.base_updated_at ?? song.updated_at ?? null,
            },
          });
          await cache.markDirty(song.id);
        }
        onDeleted(song.id);
      }
    } catch (err) {
      setError(err.message);
      setDeleting(false);
    }
  };

  return (
    <div className="screen">
      <div className="topbar">
        <button className="icon-btn" onClick={onBack} aria-label="Back">
          <ArrowLeft size={20} />
        </button>
        <div className="topbar-title" style={{ flex: 1 }}>
          {song?.id ? 'Edit song' : 'New song'}
        </div>
        <button className="icon-btn active" onClick={save} disabled={saving} aria-label="Save">
          <CheckIcon size={20} />
        </button>
      </div>

      {!online && (
        <div className="offline-banner">
          Offline — changes saved locally until you sync
        </div>
      )}

      <div style={{ padding: '16px 16px 80px' }}>
        {error && (
          <div style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 12,
                        padding: '8px 12px', background: 'rgba(224,92,92,0.1)',
                        borderRadius: 'var(--radius-sm)' }}>
            {error}
          </div>
        )}

        <div className="form-group">
          <label className="form-label">Title</label>
          <input className="form-input" value={form.title}
            onChange={e => set('title', e.target.value)} />
        </div>

        <div className="form-group">
          <label className="form-label">Artist</label>
          <input className="form-input" value={form.artist}
            onChange={e => set('artist', e.target.value)} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Written key</label>
            <input className="form-input" value={form.chart_written_key}
              placeholder="e.g. G" onChange={e => set('chart_written_key', e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Original key</label>
            <input className="form-input" value={form.original_key}
              placeholder="e.g. Ab" onChange={e => set('original_key', e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Capo fret</label>
            <input className="form-input" type="number" min={0} max={11}
              value={form.default_capo}
              onChange={e => set('default_capo', e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">BPM</label>
            <input className="form-input" type="number" min={40} max={300}
              value={form.bpm} placeholder="optional"
              onChange={e => set('bpm', e.target.value)} />
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">BeatBuddy structure</label>
          <input className="form-input" value={form.beatbuddy_structure}
            placeholder="e.g. Intro / VS-CH / End"
            onChange={e => set('beatbuddy_structure', e.target.value)} />
        </div>

        <div className="form-group">
          <label className="form-label">Chart</label>
          <textarea className="form-textarea" value={form.chart_source}
            onChange={e => set('chart_source', e.target.value)}
            spellCheck={false} />
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <button className="btn btn-primary btn-full" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          {song?.id && (
            <button className="btn btn-danger" onClick={remove} disabled={deleting}
              style={{ flexShrink: 0 }}>
              <TrashIcon size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
