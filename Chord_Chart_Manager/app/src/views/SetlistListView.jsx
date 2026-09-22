import React, { useState } from 'react';
import { ChevronRight, MusicIcon, PlusIcon, XIcon } from '../components/Icons';
import { useSetlists } from '../hooks';
import * as api from '../lib/api';

function formatGigDate(value, options) {
  const dateOnly = String(value).slice(0, 10);
  return new Date(`${dateOnly}T12:00:00`).toLocaleDateString(undefined, options);
}

export default function SetlistListView({ online, onSelectSetlist }) {
  const { setlists, loading, refresh } = useSetlists(online);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ name: '', gig_date: '', notes: '' });

  const set = (field, value) => setForm(prev => ({ ...prev, [field]: value }));

  const createSetlist = async () => {
    if (!form.name.trim()) {
      setError('Setlist name is required');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await api.setlists.create({
        name: form.name.trim(),
        gig_date: form.gig_date || null,
        notes: form.notes.trim() || null,
      });
      setForm({ name: '', gig_date: '', notes: '' });
      setCreating(false);
      await refresh();
      onSelectSetlist(created);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="screen">
      <div className="topbar">
        <div className="topbar-title" style={{ flex: 1 }}>Setlists</div>
        {online && (
          <button
            className="icon-btn"
            onClick={() => { setCreating(value => !value); setError(null); }}
            aria-label={creating ? 'Cancel new setlist' : 'Create a setlist'}
          >
            {creating ? <XIcon size={20} /> : <PlusIcon size={20} />}
          </button>
        )}
      </div>

      {creating && (
        <div className="setlist-form">
          <div className="form-group">
            <label className="form-label">Name</label>
            <input
              className="form-input"
              value={form.name}
              onChange={event => set('name', event.target.value)}
              placeholder="Friday night set"
              autoFocus
            />
          </div>
          <div className="form-group">
            <label className="form-label">Gig date</label>
            <input
              className="form-input"
              type="date"
              value={form.gig_date}
              onChange={event => set('gig_date', event.target.value)}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Notes</label>
            <input
              className="form-input"
              value={form.notes}
              onChange={event => set('notes', event.target.value)}
              placeholder="Optional"
            />
          </div>
          {error && <div className="form-error">{error}</div>}
          <button className="btn btn-primary btn-full" onClick={createSetlist} disabled={saving}>
            {saving ? 'Creating…' : 'Create setlist'}
          </button>
        </div>
      )}

      <div className="list-section-head">
        {loading ? 'Loading…' : `${setlists.length} setlist${setlists.length === 1 ? '' : 's'}`}
      </div>

      {!loading && setlists.length === 0 && (
        <div className="empty-state">
          <MusicIcon size={48} />
          <p>{online ? 'No setlists yet' : 'No setlists available offline'}</p>
        </div>
      )}

      {setlists.map(setlist => (
        <div key={setlist.id} className="setlist-row" onClick={() => onSelectSetlist(setlist)}>
          <div className="setlist-icon"><MusicIcon size={18} /></div>
          <div style={{ flex: 1 }}>
            <div className="setlist-name">{setlist.name}</div>
            <div className="setlist-sub">
              {setlist.gig_date
                ? `${formatGigDate(setlist.gig_date, {
                    month: 'short', day: 'numeric',
                  })} · `
                : ''}
              {setlist.song_count ?? 0} songs
            </div>
          </div>
          <ChevronRight size={18} className="chevron" />
        </div>
      ))}
    </div>
  );
}
