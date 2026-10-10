import React, { useRef, useState } from 'react';
import { ArrowLeft, CheckIcon, TrashIcon } from '../components/Icons';
import * as api from '../lib/api';
import * as cache from '../lib/cache';
import {
  isGeneratedChartSource, isGeneratedSourceUnchanged, parseChartBody,
} from '../lib/chartParser';
import { useTags } from '../hooks';
import { eraForYear, normalizeSongTags } from '../tagMutations';

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
    release_year:        song?.release_year        ?? '',
    beatbuddy_structure: song?.beatbuddy_structure || '',
    // Editable source of truth: the raw chart TEXT. On save the server
    // re-parses this into the structured chart_content used for display.
    chart_source:        song?.chart_source        || '',
  });
  const originalChartSource = useRef(form.chart_source);
  const originalChartContent = useRef(song?.chart_content);
  const [tagSets, setTagSets] = useState(() => normalizeSongTags(song?.genres || [], song?.vibes || []));
  const [newGenre, setNewGenre] = useState('');
  const [newVibe, setNewVibe] = useState('');
  const { tags: availableTags } = useTags(online);

  const [saving,   setSaving]   = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error,    setError]    = useState(null);

  const set = (field, value) => setForm(p => ({ ...p, [field]: value }));

  const toggleTag = (category, name) => {
    const field = category === 'Genre' ? 'genres' : 'vibes';
    setTagSets(current => {
      const rows = current[field];
      const exists = rows.some(tag => (tag.name || tag).toLocaleLowerCase() === name.toLocaleLowerCase());
      let next = exists
        ? rows.filter(tag => (tag.name || tag).toLocaleLowerCase() !== name.toLocaleLowerCase())
        : [...rows, category === 'Genre'
          ? { name, is_primary: rows.length === 0 }
          : name];
      if (field === 'genres' && next.length && !next.some(tag => tag.is_primary)) {
        next = next.map((tag, index) => ({ ...tag, is_primary: index === 0 }));
      }
      return { ...current, [field]: next };
    });
  };

  const addNewTag = (category) => {
    const input = category === 'Genre' ? newGenre : newVibe;
    const name = input.trim().replace(/\s+/g, ' ');
    if (!name) return;
    const rows = category === 'Genre' ? tagSets.genres : tagSets.vibes;
    if (!rows.some(tag => (tag.name || tag).toLocaleLowerCase() === name.toLocaleLowerCase())) {
      toggleTag(category, name);
    }
    if (category === 'Genre') setNewGenre(''); else setNewVibe('');
  };

  const save = async () => {
    if (!form.title.trim()) { setError('Title is required'); return; }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        ...form,
        default_capo: Number(form.default_capo) || 0,
        bpm:          form.bpm ? Number(form.bpm) : null,
        release_year: form.release_year === '' ? null : Number(form.release_year),
        genres: tagSets.genres,
        vibes: tagSets.vibes,
      };
      if (payload.release_year !== null
          && (!Number.isInteger(payload.release_year) || payload.release_year < 1 || payload.release_year > 32767)) {
        setError('Release year must be a whole number from 1 to 32767');
        return;
      }
      if (song?.chart_source && isGeneratedChartSource(song.chart_source) &&
          payload.chart_source && !isGeneratedChartSource(payload.chart_source)) {
        throw new Error('This chart uses generated structured source; keep its CCM line markers when editing.');
      }
      if (online) {
        const isOfflineCreate = song?._sync?.operation === 'create';
        const saved = song?.id && !isOfflineCreate
          ? await api.songs.update(song.id, {
            ...payload,
            base_updated_at: song?._sync?.base_updated_at ?? song?.updated_at ?? null,
          })
          : await api.songs.create(payload);
        if (song?.id) {
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
        const preserveStructured = isGeneratedSourceUnchanged(
          originalChartSource.current, originalChartContent.current, payload.chart_source,
        );
        const parsedContent = preserveStructured ? null : parseChartBody(payload.chart_source);
        const patched = {
          ...song,
          ...payload,
          era: eraForYear(payload.release_year),
          genre: payload.genres.find(tag => tag.is_primary)?.name || payload.genres[0]?.name || null,
          feel: payload.vibes[0] || null,
          id,
          chart_content: preserveStructured
            ? originalChartContent.current
            : parsedContent,
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
                        padding: '8px 12px', background: 'var(--danger-surface)',
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
          <div className="form-group">
            <label className="form-label">Release year</label>
            <input className="form-input" type="number" aria-label="Release year" min={1} max={32767} step={1}
              value={form.release_year} placeholder="optional"
              onChange={e => set('release_year', e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">Era (derived)</label>
            <input className="form-input" aria-label="Era (derived)" value={eraForYear(form.release_year) || '—'} readOnly />
          </div>
        </div>

        {['Genre', 'Feel'].map(category => {
          const isGenre = category === 'Genre';
          const field = isGenre ? 'genres' : 'vibes';
          const categoryLabel = isGenre ? 'Genre' : 'Vibes';
          const selected = tagSets[field].map(tag => tag.name || tag);
          const options = availableTags.filter(tag => tag.category === (isGenre ? 'Genre' : 'Feel'))
            .map(tag => tag.name).concat(selected)
            .filter((name, index, list) => list.findIndex(value => value.toLocaleLowerCase() === name.toLocaleLowerCase()) === index)
            .sort((a, b) => a.localeCompare(b));
          const value = isGenre ? newGenre : newVibe;
          const setValue = isGenre ? setNewGenre : setNewVibe;
          return (
            <section className="form-group" key={category} aria-label={`${categoryLabel} tags`}>
              <label className="form-label">{isGenre ? 'Genres' : 'Vibes'}</label>
              <div className="tag-grid">
                {options.map(name => (
                  <button key={name} type="button"
                    className={`tag-chip ${selected.some(item => item.toLocaleLowerCase() === name.toLocaleLowerCase()) ? 'active' : ''}`}
                    aria-pressed={selected.some(item => item.toLocaleLowerCase() === name.toLocaleLowerCase())}
                    onClick={() => toggleTag(category, name)}>{name}</button>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <input className="form-input" value={value} placeholder={isGenre ? 'Add a genre' : 'Add a vibe'}
                  onChange={event => setValue(event.target.value)}
                  onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addNewTag(category); } }} />
                <button type="button" className="btn btn-ghost" onClick={() => addNewTag(category)}>Add</button>
              </div>
            </section>
          );
        })}

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
