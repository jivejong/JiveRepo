import React, { useCallback, useEffect, useState } from 'react';
import * as api from '../lib/api';

const CATEGORIES = ['Genre', 'Vibe'];

export default function TagDefinitionManager({ online, onSync }) {
  const [definitions, setDefinitions] = useState({ genres: [], vibes: [] });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState({ Genre: '', Vibe: '' });
  const [rename, setRename] = useState(null);
  const [deletion, setDeletion] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const reload = useCallback(async () => {
    if (!online) return;
    setLoading(true);
    try {
      setDefinitions(await api.tagDefinitions.all());
      setError('');
    } catch (reason) {
      setError(`Could not load shared definitions: ${reason.message}`);
    } finally { setLoading(false); }
  }, [online]);

  useEffect(() => { reload(); }, [reload]);

  const refreshAfterMutation = async (message) => {
    setNotice(`${message} Shared definitions were saved on the server.`);
    setError('');
    await reload();
    try {
      const result = await onSync();
      if (!result?.ok) setError(`${message} Cache refresh did not complete: ${result?.error || 'sync unavailable'}. Use Sync now when ready.`);
      else if (result.conflicts?.length) setNotice(`${message} Pending song edits that used older versions were resolved using the server version; review Sync status.`);
    } catch (reason) {
      setError(`${message} Cache refresh failed: ${reason.message}. The definition change succeeded; use Sync now when connected.`);
    }
  };

  const create = async (category) => {
    setBusy(true); setError(''); setNotice('');
    try {
      await api.tagDefinitions.create(category, name[category]);
      setName(current => ({ ...current, [category]: '' }));
      await refreshAfterMutation(`${category} created.`);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const saveRename = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      await api.tagDefinitions.rename(rename.category, rename.id, rename.name);
      const message = `${rename.category} renamed.`;
      setRename(null);
      await refreshAfterMutation(message);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const remove = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const decision = deletion.song_count > 0
        ? { mode: deletion.mode, expected_song_count: deletion.song_count,
            ...(deletion.mode === 'reassign' ? { replacement_id: Number(deletion.replacementId) } : {}) }
        : { mode: 'remove', expected_song_count: 0 };
      await api.tagDefinitions.remove(deletion.category, deletion.id, decision);
      const message = `${deletion.category} “${deletion.name}” deleted${deletion.song_count ? `; ${deletion.song_count} song assignment${deletion.song_count === 1 ? '' : 's'} ${deletion.mode === 'remove' ? 'removed' : 'reassigned'}` : ''}.`;
      setDeletion(null);
      await refreshAfterMutation(message);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  return <section className="tag-definition-manager" aria-labelledby="tag-definitions-title">
    <h2 id="tag-definitions-title" className="form-label">Genre and Vibe definitions</h2>
    {!online && <p role="status" className="form-help">Definition changes require a connection. Existing song assignments can still be edited offline and will sync later.</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
    {notice && <p role="status" className="form-help">{notice}</p>}
    {loading && <p className="form-help">Loading definitions…</p>}
    {CATEGORIES.map(category => {
      const rows = category === 'Genre' ? definitions.genres : definitions.vibes;
      return <div className="definition-category" key={category}>
        <h3>{category}</h3>
        <form className="definition-create" onSubmit={event => { event.preventDefault(); void create(category); }}>
          <label className="sr-only" htmlFor={`create-${category}`}>New {category} name</label>
          <input id={`create-${category}`} className="form-input" value={name[category]}
            onChange={event => setName(current => ({ ...current, [category]: event.target.value }))}
            maxLength={120} placeholder={`New ${category}`} disabled={!online || busy} />
          <button className="btn btn-primary btn-sm" type="submit" disabled={!online || busy || !name[category].trim()}>Add</button>
        </form>
        {rows.map(item => <div className="definition-row" key={item.id}>
          {rename?.category === category && rename.id === item.id
            ? <>
              <input className="form-input" aria-label={`Rename ${category}`} value={rename.name}
                onChange={event => setRename(current => ({ ...current, name: event.target.value }))} disabled={busy} />
              <button className="btn btn-primary btn-sm" onClick={() => void saveRename()} disabled={!online || busy || !rename.name.trim()}>Save</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setRename(null)} disabled={busy}>Cancel</button>
            </>
            : <>
              <span className="definition-name">{item.name}<small>{item.song_count} song{item.song_count === 1 ? '' : 's'}</small></span>
              <button className="btn btn-ghost btn-sm" onClick={() => setRename({ category, id: item.id, name: item.name })} disabled={!online || busy}>Rename</button>
              <button className="btn btn-danger btn-sm" onClick={() => setDeletion({ ...item, category, mode: null, replacementId: '' })} disabled={!online || busy}>Delete</button>
            </>}
        </div>)}
        {!rows.length && !loading && <p className="form-help">No {category} definitions.</p>}
      </div>;
    })}
    {deletion && <div className="definition-confirm" role="dialog" aria-modal="true" aria-labelledby="definition-delete-title">
      <h3 id="definition-delete-title">Delete {deletion.category} “{deletion.name}”?</h3>
      {deletion.song_count > 0 ? <>
        <p>This affects {deletion.song_count} song{deletion.song_count === 1 ? '' : 's'}. Choose what happens to those assignments before deleting.</p>
        <label><input type="radio" name="definition-delete-mode" checked={deletion.mode === 'remove'} onChange={() => setDeletion(current => ({ ...current, mode: 'remove' }))} /> Remove these assignments</label>
        <label><input type="radio" name="definition-delete-mode" checked={deletion.mode === 'reassign'} onChange={() => setDeletion(current => ({ ...current, mode: 'reassign' }))} /> Reassign to</label>
        {deletion.mode === 'reassign' && <select className="form-input" aria-label="Replacement definition" value={deletion.replacementId}
          onChange={event => setDeletion(current => ({ ...current, replacementId: event.target.value }))}>
          <option value="">Choose {deletion.category}</option>
          {(deletion.category === 'Genre' ? definitions.genres : definitions.vibes)
            .filter(item => item.id !== deletion.id).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>}
      </> : <p>This definition is not assigned to any songs.</p>}
      <div className="definition-confirm-actions">
        <button className="btn btn-ghost" onClick={() => setDeletion(null)} disabled={busy}>Cancel</button>
        <button className="btn btn-danger" onClick={() => void remove()}
          disabled={!online || busy || (deletion.song_count > 0 && (!deletion.mode || (deletion.mode === 'reassign' && !deletion.replacementId)))}>
          Confirm deletion
        </button>
      </div>
    </div>}
  </section>;
}
