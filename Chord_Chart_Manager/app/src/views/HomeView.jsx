import React, { useState, useMemo } from 'react';
import { SearchIcon, FilterIcon, ChevronRight, MusicIcon, PlusIcon } from '../components/Icons';
import TagPanel from '../components/TagPanel';
import { useSongs, useTags } from '../hooks';
import { readSongSortPreference, SONG_SORTS, sortSongs, writeSongSortPreference } from '../lib/songSort';
import { assignedGenreNames } from '../lib/songMetadata';

export default function HomeView({ online, onSelectSong, onNewSong }) {
  const [query,      setQuery]      = useState('');
  const [activeTags, setActiveTags] = useState([]);
  const [panelOpen,  setPanelOpen]  = useState(false);
  const [sortPreference, setSortPreference] = useState(readSongSortPreference);

  const filters = useMemo(() => ({
    q:    query || undefined,
    tag_filters: activeTags.length ? JSON.stringify(activeTags) : undefined,
  }), [query, activeTags]);

  const { songs,    loading: songsLoading } = useSongs(filters, online);
  const sortedSongs = useMemo(() => sortSongs(songs, sortPreference), [songs, sortPreference]);
  const { tags                            } = useTags(online);

  const toggleTag = (tag) => {
    setActiveTags(prev => {
      const matches = item => item.category === tag.category
        && item.name.toLocaleLowerCase() === tag.name.toLocaleLowerCase();
      return prev.some(matches) ? prev.filter(item => !matches(item)) : [...prev, tag];
    });
  };

  const clearTags = () => setActiveTags([]);
  const clearCategory = category => setActiveTags(previous => previous.filter(tag => tag.category !== category));
  const displayCategory = category => category === 'Feel' ? 'Vibe' : category;
  const changeSort = patch => {
    const next = { ...sortPreference, ...patch };
    setSortPreference(next);
    writeSongSortPreference(next);
  };

  const filterActive = activeTags.length > 0 || panelOpen;

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
          {activeTags.length > 0 && (
            <span style={{
              background: 'var(--accent)', color: 'var(--text-on-accent)',
              borderRadius: 10, fontSize: 10, fontWeight: 600,
              padding: '1px 6px', marginLeft: 2,
            }}>
              {activeTags.length}
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

      <div className="song-sort-controls" role="group" aria-label="Song sorting controls">
        <label htmlFor="song-sort-key">Sort</label>
        <select id="song-sort-key" aria-label="Sort songs by" value={sortPreference.key}
          onChange={event => changeSort({ key: event.target.value })}>
          {SONG_SORTS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <button type="button" className="sort-direction-btn"
          aria-label={sortPreference.direction === 'asc' ? 'Sort descending' : 'Sort ascending'}
          onClick={() => changeSort({ direction: sortPreference.direction === 'asc' ? 'desc' : 'asc' })}>
          {sortPreference.direction === 'asc' ? '↑' : '↓'}
        </button>
      </div>

      {/* Tag panel */}
      {panelOpen && (
        <TagPanel
          tags={tags}
          activeTags={activeTags}
          onToggle={toggleTag}
          onClearAll={clearTags}
          onClearCategory={clearCategory}
        />
      )}

      {/* Songs list */}
      <div className="list-section-head">
        {activeTags.length > 0
          ? `${activeTags.map(tag => `${displayCategory(tag.category)}: ${tag.name}`).join(' · ')} · `
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
          {activeTags.length > 0 && (
            <button className="btn btn-ghost btn-sm" onClick={clearTags}>
              Clear filters
            </button>
          )}
        </div>
      )}

      {sortedSongs.map(song => (
        <div key={song.id} className="song-row" onClick={() => onSelectSong(song, sortedSongs)}>
          <div className="song-info">
            <div className="song-title">{song.title}</div>
            <div className="song-meta">
              {[song.artist, assignedGenreNames(song.genres).join(', '), song.feel, song.era].filter(Boolean).join(' · ')}
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
