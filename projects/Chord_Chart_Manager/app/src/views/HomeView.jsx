import React, { useState, useMemo } from 'react';
import { SearchIcon, FilterIcon, ChevronRight, MusicIcon, PlusIcon } from '../components/Icons';
import TagPanel from '../components/TagPanel';
import { useSongs, useTags } from '../hooks';

export default function HomeView({ online, onSelectSong, onNewSong }) {
  const [query,      setQuery]      = useState('');
  const [activeTags, setActiveTags] = useState(new Set());
  const [panelOpen,  setPanelOpen]  = useState(false);

  const filters = useMemo(() => ({
    q:    query || undefined,
    tags: activeTags.size ? [...activeTags].join(',') : undefined,
  }), [query, activeTags]);

  const { songs,    loading: songsLoading } = useSongs(filters, online);
  const { tags                            } = useTags(online);

  const toggleTag = (name) => {
    setActiveTags(prev => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  };

  const clearTags = () => setActiveTags(new Set());

  const filterActive = activeTags.size > 0 || panelOpen;

  return (
    <div className="screen">
      {/* Search + filter button */}
      <div className="search-wrap">
        <div className="search-box">
          <SearchIcon size={18} />
          <input
            type="search"
            placeholder="Search songs, artists…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoCorrect="off"
            autoCapitalize="none"
          />
        </div>
        <button
          className={`filter-btn ${filterActive ? 'active' : ''}`}
          onClick={() => setPanelOpen(p => !p)}
          aria-label="Filter by tags"
        >
          <FilterIcon size={16} />
          Filter
          {activeTags.size > 0 && (
            <span style={{
              background: 'var(--accent)', color: '#1A1A1F',
              borderRadius: 10, fontSize: 10, fontWeight: 600,
              padding: '1px 6px', marginLeft: 2,
            }}>
              {activeTags.size}
            </span>
          )}
        </button>
        <button
          className="filter-btn"
          onClick={onNewSong}
          aria-label="Add a new song"
        >
          <PlusIcon size={16} />
          New
        </button>
      </div>

      {/* Tag panel */}
      {panelOpen && (
        <TagPanel
          tags={tags}
          activeTags={activeTags}
          onToggle={toggleTag}
          onClearAll={clearTags}
        />
      )}

      {/* Songs list */}
      <div className="list-section-head">
        {activeTags.size > 0
          ? `${[...activeTags].join(' · ')} · `
          : 'All songs · '}
        <span style={{ color: 'var(--accent)' }}>
          {songsLoading ? '…' : `${songs.length} songs`}
        </span>
      </div>

      {songsLoading && songs.length === 0 && (
        <div className="loading">Loading…</div>
      )}

      {!songsLoading && songs.length === 0 && (
        <div className="empty-state">
          <MusicIcon size={48} />
          <p>No songs found</p>
          {activeTags.size > 0 && (
            <button className="btn btn-ghost btn-sm" onClick={clearTags}>
              Clear filters
            </button>
          )}
        </div>
      )}

      {songs.map(song => (
        <div key={song.id} className="song-row" onClick={() => onSelectSong(song, songs)}>
          <div className="song-info">
            <div className="song-title">{song.title}</div>
            <div className="song-meta">
              {[song.artist, song.feel, song.era].filter(Boolean).join(' · ')}
            </div>
          </div>
          <div className="song-key-badge">
            {song.chart_written_key || '?'}
            {song.default_capo ? <span style={{ fontSize: 11, opacity: 0.6 }}> c{song.default_capo}</span> : null}
          </div>
          <ChevronRight size={18} className="chevron" />
        </div>
      ))}
    </div>
  );
}
