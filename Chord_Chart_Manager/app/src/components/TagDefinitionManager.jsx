import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft } from './Icons';
import * as api from '../lib/api';

export default function TagDefinitionManager({ category, online, onSync, onBack }) {
  const [definitions, setDefinitions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState('');
  const [rename, setRename] = useState(null);
  const [deletion, setDeletion] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const mountedRef = useRef(false);
  const categoryRef = useRef(category);
  const loadSequenceRef = useRef(0);
  categoryRef.current = category;

  const isCurrent = () => mountedRef.current && categoryRef.current === category;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadSequenceRef.current += 1;
    };
  }, []);

  const reload = useCallback(async () => {
    if (!online) return false;
    const sequence = ++loadSequenceRef.current;
    let loadedResult = null;
    let published = false;
    if (isCurrent()) setLoading(true);
    try {
      loadedResult = await api.tagDefinitions.all();
      if (!isCurrent() || sequence !== loadSequenceRef.current) return false;
      setDefinitions(category === 'Genre' ? loadedResult.genres : loadedResult.vibes);
      setError('');
      published = true;
      return true;
    } catch (reason) {
      if (isCurrent() && sequence === loadSequenceRef.current) {
        setError(`Could not load shared definitions: ${reason.message}`);
      }
      return false;
    } finally {
      if (isCurrent() && sequence === loadSequenceRef.current) setLoading(false);
      const requestId = loadedResult?.__ccmTestLoadId;
      if (typeof window !== 'undefined' && window.__CCM_TEST_OBSERVE_TAG_LOAD__ === true
        && typeof requestId === 'string') {
        window.dispatchEvent(new CustomEvent('ccm:test-tag-definition-load-settled', {
          detail: { requestId, category, published },
        }));
      }
    }
  }, [category, online]);

  useEffect(() => { void reload(); }, [reload]);

  const refreshAfterMutation = async message => {
    if (isCurrent()) {
      setNotice(`${message} Shared definitions were saved on the server.`);
      setError('');
    }
    await reload();
    try {
      const result = await onSync();
      if (!isCurrent()) return;
      if (!result?.ok) setError(`${message} Cache refresh did not complete: ${result?.error || 'sync unavailable'}. Use Sync now when ready.`);
      else if (result.conflicts?.length) setNotice(`${message} Pending song edits that used older versions were resolved using the server version; review Sync status.`);
    } catch (reason) {
      if (isCurrent()) setError(`${message} Cache refresh failed: ${reason.message}. The definition change succeeded; use Sync now when connected.`);
    }
  };

  const create = async event => {
    event.preventDefault();
    const value = name.trim();
    if (!value) return;
    if (isCurrent()) { setBusy(true); setError(''); setNotice(''); }
    try {
      await api.tagDefinitions.create(category, value);
      if (isCurrent()) setName('');
      await refreshAfterMutation(`${category} created.`);
    } catch (reason) {
      if (isCurrent()) setError(reason.message);
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const saveRename = async () => {
    const currentRename = rename;
    if (!currentRename) return;
    if (isCurrent()) { setBusy(true); setError(''); setNotice(''); }
    try {
      await api.tagDefinitions.rename(category, currentRename.id, currentRename.name);
      if (isCurrent()) setRename(null);
      await refreshAfterMutation(`${category} renamed.`);
    } catch (reason) {
      if (isCurrent()) setError(reason.message);
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const remove = async () => {
    const currentDeletion = deletion;
    if (!currentDeletion) return;
    if (isCurrent()) { setBusy(true); setError(''); setNotice(''); }
    try {
      const decision = currentDeletion.song_count > 0
        ? { mode: currentDeletion.mode, expected_song_count: currentDeletion.song_count,
            ...(currentDeletion.mode === 'reassign' ? { replacement_id: Number(currentDeletion.replacementId) } : {}) }
        : { mode: 'remove', expected_song_count: 0 };
      await api.tagDefinitions.remove(category, currentDeletion.id, decision);
      const message = `${category} “${currentDeletion.name}” deleted${currentDeletion.song_count ? `; ${currentDeletion.song_count} song assignment${currentDeletion.song_count === 1 ? '' : 's'} ${currentDeletion.mode === 'remove' ? 'removed' : 'reassigned'}` : ''}.`;
      if (isCurrent()) setDeletion(null);
      await refreshAfterMutation(message);
    } catch (reason) {
      if (isCurrent()) setError(reason.message);
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  return <div className="screen">
    <div className="topbar">
      <button className="icon-btn" onClick={onBack} aria-label="Back to Settings"><ArrowLeft size={20} /></button>
      <div className="topbar-title">Manage {category}s</div>
    </div>
    <div className="tag-manager-content">
      <section className="tag-definition-manager" aria-labelledby="tag-definitions-title">
        <h1 id="tag-definitions-title" className="form-label">{category} definitions</h1>
        {!online && <p role="status" className="form-help">Definition changes require a connection. Existing song assignments can still be edited offline and will sync later.</p>}
        {error && <p role="alert" className="form-error">{error}</p>}
        {notice && <p role="status" className="form-help">{notice}</p>}
        {loading && <p className="form-help">Loading definitions…</p>}
        <div className="definition-category">
          <form className="definition-create" onSubmit={create}>
            <label className="sr-only" htmlFor={`create-${category}`}>New {category} name</label>
            <input id={`create-${category}`} className="form-input" value={name}
              onChange={event => setName(event.target.value)}
              maxLength={120} placeholder={`New ${category}`} disabled={!online || busy} />
            <button className="btn btn-primary btn-sm" type="submit" disabled={!online || busy || !name.trim()}>Add</button>
          </form>
          {definitions.map(item => <div className="definition-row" key={item.id}>
            {rename?.id === item.id
              ? <>
                <input className="form-input" aria-label={`Rename ${category}`} value={rename.name}
                  onChange={event => setRename(current => ({ ...current, name: event.target.value }))} disabled={busy} />
                <button className="btn btn-primary btn-sm" onClick={() => void saveRename()} disabled={!online || busy || !rename.name.trim()}>Save</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setRename(null)} disabled={busy}>Cancel</button>
              </>
              : <>
                <span className="definition-name">{item.name}<small>{item.song_count} song{item.song_count === 1 ? '' : 's'}</small></span>
                <button className="btn btn-ghost btn-sm" onClick={() => setRename({ id: item.id, name: item.name })} disabled={!online || busy}>Rename</button>
                <button className="btn btn-danger btn-sm" onClick={() => setDeletion({ ...item, mode: null, replacementId: '' })} disabled={!online || busy}>Delete</button>
              </>}
          </div>)}
          {!definitions.length && !loading && online && <p className="form-help">No {category} definitions.</p>}
        </div>
      </section>
    </div>
    {deletion && <div className="definition-confirm" role="dialog" aria-modal="true" aria-labelledby="definition-delete-title">
      <h3 id="definition-delete-title">Delete {category} “{deletion.name}”?</h3>
      {deletion.song_count > 0 ? <>
        <p>This affects {deletion.song_count} song{deletion.song_count === 1 ? '' : 's'}. Choose what happens to those assignments before deleting.</p>
        <label><input type="radio" name="definition-delete-mode" checked={deletion.mode === 'remove'} onChange={() => setDeletion(current => ({ ...current, mode: 'remove' }))} /> Remove these assignments</label>
        <label><input type="radio" name="definition-delete-mode" checked={deletion.mode === 'reassign'} onChange={() => setDeletion(current => ({ ...current, mode: 'reassign' }))} /> Reassign to</label>
        {deletion.mode === 'reassign' && <select className="form-input" aria-label="Replacement definition" value={deletion.replacementId}
          onChange={event => setDeletion(current => ({ ...current, replacementId: event.target.value }))}>
          <option value="">Choose {category}</option>
          {definitions.filter(item => item.id !== deletion.id).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
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
  </div>;
}
