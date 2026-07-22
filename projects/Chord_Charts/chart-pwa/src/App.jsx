import React, { useState } from 'react';
import { MusicIcon, ListIcon, SettingsIcon } from './components/Icons';
import HomeView     from './views/HomeView';
import ChartView    from './views/ChartView';
import SetlistView  from './views/SetlistView';
import EditView     from './views/EditView';
import SettingsView from './views/SettingsView';
import { useOnline, useSync } from './hooks';

/**
 * Navigation state machine:
 *
 * tab: 'songs' | 'setlists' | 'settings'
 *
 * view: null             → show the current tab's list view
 *       { type: 'chart', songId, songList }
 *       { type: 'edit',  song }
 *       { type: 'setlist', setlistId }
 */

export default function App() {
  const online = useOnline();
  const { syncing, lastSync, dirty } = useSync(online);

  const [tab,  setTab]  = useState('songs');
  const [view, setView] = useState(null);

  // ── Navigation helpers ──────────────────────────────────────────────────────

  const openChart = (song, songList = []) => {
    setView({ type: 'chart', songId: song.id, songList });
  };

  const openSetlist = (setlist) => {
    setView({ type: 'setlist', setlistId: setlist.id });
  };

  const openEdit = (song) => {
    setView({ type: 'edit', song });
  };

  const goBack = () => setView(null);

  const switchTab = (newTab) => {
    setTab(newTab);
    setView(null);
  };

  // ── Render active view ──────────────────────────────────────────────────────

  let activeView;

  if (view?.type === 'chart') {
    activeView = (
      <ChartView
        key={view.songId}
        songId={view.songId}
        songList={view.songList}
        online={online}
        onBack={goBack}
        onEdit={openEdit}
      />
    );
  } else if (view?.type === 'edit') {
    activeView = (
      <EditView
        song={view.song}
        online={online}
        onBack={goBack}
        onSaved={(updated) => {
          // Return to chart view after save
          setView({ type: 'chart', songId: updated.id, songList: [] });
        }}
        onDeleted={() => {
          setTab('songs');
          setView(null);
        }}
      />
    );
  } else if (view?.type === 'setlist') {
    activeView = (
      <SetlistView
        setlistId={view.setlistId}
        online={online}
        onBack={goBack}
        onSelectSong={(entry, songs) => openChart(
          { id: entry.song_id, title: entry.title },
          songs.map(s => ({ id: s.song_id, title: s.title }))
        )}
      />
    );
  } else if (tab === 'songs') {
    activeView = (
      <HomeView
        online={online}
        onSelectSong={openChart}
        onSelectSetlist={openSetlist}
      />
    );
  } else if (tab === 'settings') {
    activeView = (
      <SettingsView
        online={online}
        syncing={syncing}
        lastSync={lastSync}
        dirty={dirty}
      />
    );
  } else {
    // setlists tab — show same HomeView, it includes setlists at the top
    activeView = (
      <HomeView
        online={online}
        onSelectSong={openChart}
        onSelectSetlist={openSetlist}
      />
    );
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="app">
      {!online && (
        <div className="offline-banner">Offline — showing cached charts</div>
      )}

      {activeView}

      {/* Bottom navigation — hidden when in chart/edit view for maximum reading space */}
      {view?.type !== 'chart' && view?.type !== 'edit' && (
        <nav className="bottom-nav">
          <button
            className={`nav-item ${tab === 'songs' && !view ? 'active' : ''}`}
            onClick={() => switchTab('songs')}
          >
            <MusicIcon size={22} />
            Songs
          </button>
          <button
            className={`nav-item ${tab === 'setlists' && !view ? 'active' : ''}`}
            onClick={() => switchTab('setlists')}
          >
            <ListIcon size={22} />
            Setlists
          </button>
          <button
            className={`nav-item ${tab === 'settings' ? 'active' : ''}`}
            onClick={() => switchTab('settings')}
          >
            <SettingsIcon size={22} />
            Settings
          </button>
        </nav>
      )}
    </div>
  );
}
