import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeft, CheckIcon, TrashIcon } from '../components/Icons';
import * as api from '../lib/api';
import * as cache from '../lib/cache';
import { parseChartBody } from '../lib/chartParser';
import { eraForYear, normalizeSongTags } from '../tagMutations';
import { offlineSongCreatedAt, primaryGenreFromAssignments } from '../lib/offlineSongFields';
import ArtistAutocomplete from '../components/ArtistAutocomplete';
import { loadManagedTagDefinitions } from '../lib/managedTagDefinitions';
import { definitionKey, matchingTagDefinitions, normalizeDefinitionName } from '../lib/tagDefinitionOptions';

const EDITABLE_FIELDS = [
  'title', 'artist', 'chart_written_key', 'original_key', 'default_capo', 'bpm',
  'release_year', 'beatbuddy_structure', 'chart_source', 'genres', 'vibes',
];

function sameEditValue(left, right) {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && sameEditValue(left[key], right[key]));
}

function normalizedEditValues(form, tagSets) {
  return {
    ...form,
    default_capo: Number(form.default_capo) || 0,
    bpm: form.bpm ? Number(form.bpm) : null,
    release_year: form.release_year === '' ? null : Number(form.release_year),
    genres: tagSets.genres,
    vibes: tagSets.vibes,
  };
}

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
  const originalEditableValues = useRef(null);
  if (!originalEditableValues.current) originalEditableValues.current = normalizedEditValues(form, tagSets);
  const [newGenre, setNewGenre] = useState('');
  const [newVibe, setNewVibe] = useState('');
  const [managedDefinitions, setManagedDefinitions] = useState({ loading: true, definitions: null, source: 'loading', cacheWarning: '', error: '' });
  const [openTagCategory, setOpenTagCategory] = useState(null);
  const [activeTagOption, setActiveTagOption] = useState({ category: null, index: -1 });
  const [tagAnnouncement, setTagAnnouncement] = useState('');
  const genreInputRef = useRef(null);
  const vibeInputRef = useRef(null);
  const tagLoadSequence = useRef(0);

  useEffect(() => {
    let live = true;
    const sequence = ++tagLoadSequence.current;
    setManagedDefinitions({ loading: true, definitions: null, source: 'loading', cacheWarning: '', error: '' });
    loadManagedTagDefinitions(online).then(result => {
      if (live && sequence === tagLoadSequence.current) setManagedDefinitions({ loading: false, ...result });
    });
    return () => { live = false; };
  }, [online]);

  const [saving,   setSaving]   = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error,    setError]    = useState(null);

  const set = (field, value) => setForm(p => ({ ...p, [field]: value }));

  const toggleTag = (category, name) => {
    const field = category === 'Genre' ? 'genres' : 'vibes';
    setTagSets(current => {
      const rows = current[field];
      const exists = rows.some(tag => definitionKey(tag.name || tag) === definitionKey(name));
      let next = exists
        ? rows.filter(tag => definitionKey(tag.name || tag) !== definitionKey(name))
        : [...rows, category === 'Genre'
          ? { name, is_primary: rows.length === 0 }
          : name];
      if (field === 'genres' && next.length && !next.some(tag => tag.is_primary)) {
        next = next.map((tag, index) => ({ ...tag, is_primary: index === 0 }));
      }
      return { ...current, [field]: next };
    });
  };

  const chooseTagDefinition = (category, definition) => {
    const name = normalizeDefinitionName(definition.name);
    if (!name) return;
    toggleTag(category, name);
    if (category === 'Genre') setNewGenre(''); else setNewVibe('');
    setOpenTagCategory(null);
    setActiveTagOption({ category: null, index: -1 });
    setTagAnnouncement(`${name} added to ${category}.`);
  };

  const onTagKeyDown = (event, category, options) => {
    const activeIndex = activeTagOption.category === category ? activeTagOption.index : -1;
    if (event.key === 'ArrowDown' && options.length) {
      event.preventDefault();
      setOpenTagCategory(category);
      setActiveTagOption({ category, index: (activeIndex + 1) % options.length });
    } else if (event.key === 'ArrowUp' && options.length) {
      event.preventDefault();
      setOpenTagCategory(category);
      setActiveTagOption({ category, index: activeIndex <= 0 ? options.length - 1 : activeIndex - 1 });
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (openTagCategory === category && activeIndex >= 0 && activeIndex < options.length && options[activeIndex]) {
        chooseTagDefinition(category, options[activeIndex]);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setOpenTagCategory(null);
      setActiveTagOption({ category, index: -1 });
    }
  };

  const removeAssignedTag = (category, name) => {
    toggleTag(category, name);
    setOpenTagCategory(category);
    setActiveTagOption({ category, index: -1 });
    setTagAnnouncement(`${name} removed from ${category}.`);
    (category === 'Genre' ? genreInputRef : vibeInputRef).current?.focus();
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
      const currentEditableValues = { ...payload, chart_source: form.chart_source };
      const changedFields = EDITABLE_FIELDS.filter(field =>
        !sameEditValue(currentEditableValues[field], originalEditableValues.current[field]));
      if (payload.chart_source === originalChartSource.current) delete payload.chart_source;
      const preserveStructured = !Object.hasOwn(payload, 'chart_source');
      const primaryGenre = primaryGenreFromAssignments(payload.genres);
      if (payload.release_year !== null
          && (!Number.isInteger(payload.release_year) || payload.release_year < 1 || payload.release_year > 32767)) {
        setError('Release year must be a whole number from 1 to 32767');
        return;
      }
      if (online) {
        const isOfflineCreate = song?._sync?.operation === 'create';
        if (isOfflineCreate) {
          const patched = {
            ...song,
            ...payload,
            era: eraForYear(payload.release_year),
            genre: primaryGenre,
            primary_genre: primaryGenre,
            feel: payload.vibes[0] || null,
            chart_content: preserveStructured ? originalChartContent.current : parseChartBody(payload.chart_source),
            _sync: { ...song._sync, operation: 'create' },
          };
          await cache.savePendingSong(patched, song._sync?.operation_id);
          const result = await cache.syncWithServer({
            library: api.library, setlists: api.setlists, songs: api.songs,
          });
          if (!result.ok) throw new Error(result.error || 'The pending song could not be synced.');
          const serverId = await cache.getRedirectedSongId(song.id);
          if (String(serverId) === String(song.id)) throw new Error('The song remains queued for sync.');
          onSaved(await cache.getCachedSong(serverId));
          return;
        }
        if (song?.id && !isOfflineCreate) {
          const patched = {
            ...song,
            ...payload,
            era: eraForYear(payload.release_year),
            genre: primaryGenre,
            primary_genre: primaryGenre,
            feel: payload.vibes[0] || null,
            chart_content: preserveStructured ? originalChartContent.current : parseChartBody(payload.chart_source),
            _sync: {
              operation: 'update',
              base_updated_at: song?._sync?.base_updated_at ?? song?.updated_at ?? null,
              chart_source_unchanged: !Object.hasOwn(payload, 'chart_source')
                && song?._sync?.chart_source_unchanged !== false,
            },
          };
          const savedLocal = await cache.savePendingSong(patched, null, {
            baseline: {
              ...originalEditableValues.current,
              chart_content: originalChartContent.current,
              updated_at: song?.updated_at ?? null,
            },
            changedFields,
          });
          const result = await cache.syncWithServer({
            library: api.library, setlists: api.setlists, songs: api.songs,
          });
          if (!result.ok) {
            if (result.status === 422
                && ['UNKNOWN_TAG_DEFINITION', 'AMBIGUOUS_TAG_DEFINITION'].includes(result.code)) {
              originalEditableValues.current = Object.fromEntries(EDITABLE_FIELDS.map(field => [field, savedLocal[field]]));
              originalChartSource.current = savedLocal.chart_source || '';
              originalChartContent.current = savedLocal.chart_content;
            }
            throw new Error(result.error || 'The song update remains queued for sync.');
          }
          if (result.conflicts?.some(conflict => String(conflict.id) === String(song.id))) {
            throw new Error('The server song changed; its version was kept.');
          }
          onSaved(await cache.getCachedSong(song.id));
        } else {
          const saved = await api.songs.create({ ...payload, operation_id: cache.makeMutationId() });
          onSaved(saved);
        }
      } else {
        const id = song?.id ?? cache.makeLocalSongId();
        const operation = song?._sync?.operation === 'create'
          ? 'create'
          : (song?.id ? 'update' : 'create');
        const parsedContent = preserveStructured ? null : parseChartBody(payload.chart_source);
        const createdAt = offlineSongCreatedAt(song, operation);
        const patched = {
          ...song,
          ...payload,
          era: eraForYear(payload.release_year),
          genre: primaryGenre,
          primary_genre: primaryGenre,
          feel: payload.vibes[0] || null,
          id,
          ...(createdAt ? { created_at: createdAt } : {}),
          chart_content: preserveStructured
            ? originalChartContent.current
            : parsedContent,
          _sync: {
            operation,
            operation_id: operation === 'create'
              ? (song?._sync?.operation_id || cache.makeMutationId())
              : song?._sync?.operation_id,
            base_updated_at: operation === 'create'
              ? null
              : (song?._sync?.base_updated_at ?? song?.updated_at ?? null),
            ...(operation === 'update'
              ? { chart_source_unchanged: !Object.hasOwn(payload, 'chart_source')
                && song?._sync?.chart_source_unchanged !== false }
              : {}),
          },
        };
        const editMerge = operation === 'update' ? {
          baseline: {
            ...originalEditableValues.current,
            chart_content: originalChartContent.current,
            updated_at: song?.updated_at ?? null,
          },
          changedFields,
        } : null;
        const savedLocal = await cache.savePendingSong(patched, patched._sync.operation_id, editMerge);
        onSaved(savedLocal);
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
      if (song._sync?.operation === 'create') {
        await cache.cancelPendingLocalSong(song.id);
      } else {
        // Queue the delete with its version so a stale delete keeps the server copy (FR-111).
        await cache.updateCachedSong({
          ...song,
          _sync: {
            operation: 'delete',
            base_updated_at: song._sync?.base_updated_at ?? song.updated_at ?? null,
          },
        });
        await cache.markDirty(song.id);
        if (online) {
          const result = await cache.syncWithServer({
            library: api.library, setlists: api.setlists, songs: api.songs,
          });
          if (!result.ok) throw new Error(result.error || 'The song delete remains queued for sync.');
          if (result.conflicts?.some(conflict => String(conflict.id) === String(song.id))
              && await cache.getCachedSong(song.id)) {
            throw new Error('The server song changed; its version was kept.');
          }
        }
      }
      onDeleted(song.id);
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
          <label className="form-label" htmlFor="song-artist">Artist</label>
          <ArtistAutocomplete value={form.artist} onChange={value => set('artist', value)} />
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

        {[
          { category: 'Genre', field: 'genres', label: 'Genres', value: newGenre,
            setValue: setNewGenre, inputRef: genreInputRef, definitions: managedDefinitions.definitions?.genres || [] },
          { category: 'Vibe', field: 'vibes', label: 'Vibes', value: newVibe,
            setValue: setNewVibe, inputRef: vibeInputRef, definitions: managedDefinitions.definitions?.vibes || [] },
        ].map(({ category, field, label, value, setValue, inputRef, definitions }) => {
          const selectedRows = tagSets[field];
          const selected = selectedRows.map(tag => tag.name || tag);
          const options = matchingTagDefinitions(definitions, value, selected);
          const expanded = openTagCategory === category && options.length > 0;
          const listId = `song-${category.toLocaleLowerCase()}-options`;
          const inputId = `song-add-${category.toLocaleLowerCase()}`;
          const activeIndex = activeTagOption.category === category
            && activeTagOption.index >= 0 && activeTagOption.index < options.length
            ? activeTagOption.index : -1;
          return (
            <section className="form-group assigned-tag-section" key={category} aria-label={`${category} tags`}>
              <label className="form-label" htmlFor={inputId}>{label}</label>
              <div className="tag-grid assigned-tag-grid" aria-label={`Assigned ${label}`}>
                {selectedRows.map((tag, index) => {
                  const name = tag.name || tag;
                  return <button key={`${definitionKey(name)}-${index}`} type="button"
                    className="tag-chip active assigned-tag-chip"
                    aria-label={`Remove ${name} from ${category}`}
                    onClick={() => removeAssignedTag(category, name)}>{name}<span aria-hidden="true"> ×</span></button>;
                })}
                {!selectedRows.length && <span className="form-help">No {label.toLocaleLowerCase()} assigned.</span>}
              </div>
              <div className="tag-autocomplete">
                <input id={inputId} ref={inputRef} className="form-input" role="combobox"
                  aria-label={`Add ${category}`} aria-autocomplete="list" aria-expanded={expanded}
                  aria-controls={expanded ? listId : undefined}
                  aria-activedescendant={expanded && activeIndex >= 0 ? `${listId}-option-${activeIndex}` : undefined}
                  autoComplete="off" maxLength={120} disabled={managedDefinitions.loading || !managedDefinitions.definitions || !definitions.length}
                  value={value} placeholder={`Search existing ${category.toLocaleLowerCase()} definitions`}
                  onChange={event => { setValue(event.target.value); setOpenTagCategory(category); setActiveTagOption({ category, index: -1 }); }}
                  onFocus={() => {
                    setActiveTagOption({ category, index: -1 });
                    if (normalizeDefinitionName(value)) setOpenTagCategory(category);
                  }}
                  onKeyDown={event => onTagKeyDown(event, category, options)} />
                {expanded && <div id={listId} className="tag-suggestions" role="listbox" aria-label={`${label} definitions`}>
                  {options.map((definition, index) => <div key={definition.id ?? definition.name}
                    id={`${listId}-option-${index}`} role="option" aria-selected={activeIndex === index}
                    className={`tag-suggestion${activeIndex === index ? ' active' : ''}`}
                    ref={element => {
                      if (!element || !expanded || activeIndex !== index) return;
                      const list = element.parentElement;
                      if (element.offsetTop < list.scrollTop) list.scrollTop = element.offsetTop;
                      else if (element.offsetTop + element.offsetHeight > list.scrollTop + list.clientHeight) {
                        list.scrollTop = element.offsetTop + element.offsetHeight - list.clientHeight;
                      }
                    }}
                    onPointerDown={event => event.preventDefault()}
                    onClick={() => chooseTagDefinition(category, definition)}>{definition.name}</div>)}
                </div>}
              </div>
              <div className="form-help tag-load-status" aria-live="polite">
                {managedDefinitions.loading && `Loading ${label.toLocaleLowerCase()} definitions…`}
                {!managedDefinitions.loading && managedDefinitions.source === 'cache' && `Showing saved ${label.toLocaleLowerCase()} definitions; changes will be checked when synced.`}
                {!managedDefinitions.loading && !managedDefinitions.error && !definitions.length && `No ${label.toLocaleLowerCase()} definitions are available. Add them in Settings → Manage ${label.toLocaleLowerCase()}.`}
                {!managedDefinitions.loading && definitions.length > 0 && normalizeDefinitionName(value) && !options.length && 'No matching unassigned definitions.'}
                {!managedDefinitions.loading && definitions.length > 0 && !normalizeDefinitionName(value) && 'Type to search existing definitions.'}
              </div>
              {managedDefinitions.error && <div className="form-error" role="alert">{managedDefinitions.error}</div>}
              {managedDefinitions.cacheWarning && managedDefinitions.source === 'server'
                && <div className="form-help" role="status">{managedDefinitions.cacheWarning}</div>}
            </section>
          );
        })}
        <span className="sr-only" aria-live="polite">{tagAnnouncement}</span>

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
