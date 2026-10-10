import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { MusicIcon, ListIcon, SettingsIcon } from './components/Icons';
import HomeView     from './views/HomeView';
import ChartView    from './views/ChartView';
import SetlistListView from './views/SetlistListView';
import SetlistView  from './views/SetlistView';
import EditView     from './views/EditView';
import SettingsView from './views/SettingsView';
import { useOnline, useSync } from './hooks';
import { applyTheme, getSystemPrefersDark, readThemePreference, resolveTheme,
  subscribeToSystemTheme, writeThemePreference } from './lib/theme';

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
  const { syncing, lastSync, dirty, conflicts, syncError, syncNow } = useSync(online);
  const [themePreference, setThemePreference] = useState(readThemePreference);
  const [systemPrefersDark, setSystemPrefersDark] = useState(getSystemPrefersDark);

  useEffect(() => subscribeToSystemTheme(setSystemPrefersDark), []);
  useLayoutEffect(() => {
    applyTheme(document, resolveTheme(themePreference, systemPrefersDark));
  }, [themePreference, systemPrefersDark]);

  const chooseTheme = preference => {
    if (!writeThemePreference(preference)) {
      setThemePreference('system');
      return;
    }
    setThemePreference(preference);
  };

  const [tab,  setTab]  = useState('songs');
  const [view, setView] = useState(null);
  const navigationSession = useRef(0);

  const advanceNavigationSession = () => {
    navigationSession.current += 1;
    return navigationSession.current;
  };

  // ── Navigation helpers ──────────────────────────────────────────────────────

  const openChart = (song, songList = [], setlistContext = null) => {
    advanceNavigationSession();
    setView({
      type: 'chart',
      songId: song.id,
      songList,
      setlistId: setlistContext?.setlistId ?? null,
      setlistEntry: setlistContext?.setlistEntry ?? null,
    });
  };

  const openSetlist = (setlist) => {
    advanceNavigationSession();
    setView({ type: 'setlist', setlistId: setlist.id });
  };

  const openEdit = (song) => {
    const session = advanceNavigationSession();
    setView({ type: 'edit', song, session });
  };

  const openNew = () => {
    // EditView treats a null song as "New song" and routes save -> POST.
    const session = advanceNavigationSession();
    setView({ type: 'edit', song: null, session });
  };

  const goBack = () => {
    advanceNavigationSession();
    setView(null);
  };

  const switchTab = (newTab) => {
    advanceNavigationSession();
    setTab(newTab);
    setView(null);
  };

  // ── Render active view ──────────────────────────────────────────────────────

  let activeView;

  if (view?.type === 'chart') {
    activeView = (
      <ChartView
        key={view.setlistEntry
          ? `${view.setlistId}:${view.setlistEntry.position}`
          : view.songId}
        songId={view.songId}
        songList={view.songList}
        setlistId={view.setlistId}
        setlistEntry={view.setlistEntry}
        online={online}
        onBack={goBack}
        onEdit={openEdit}
        onNavigateSong={(item) => {
          advanceNavigationSession();
          setView(current => current.setlistEntry
            ? { ...current, songId: item.song_id, setlistEntry: item }
            : { ...current, songId: item.id });
        }}
        onSetlistUpdated={(updatedSetlist) => {
          setView(current => {
            const entry = updatedSetlist.songs?.find(
              item => item.position === current.setlistEntry?.position,
            );
            return {
              ...current,
              songList: updatedSetlist.songs || current.songList,
              setlistEntry: entry || current.setlistEntry,
            };
          });
        }}
      />
    );
  } else if (view?.type === 'edit') {
    activeView = (
      <EditView
        song={view.song}
        online={online}
        onBack={goBack}
        onSaved={(updated) => {
          if (navigationSession.current !== view.session) return;
          advanceNavigationSession();
          // Return to chart view after save
          setView({ type: 'chart', songId: updated.id, songList: [] });
        }}
        onDeleted={() => {
          advanceNavigationSession();
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
        onDeleted={() => setView(null)}
        onSelectSong={(entry, songs) => openChart(
          { id: entry.song_id, title: entry.title },
          songs,
          { setlistId: view.setlistId, setlistEntry: entry },
        )}
      />
    );
  } else if (tab === 'songs') {
    activeView = (
      <HomeView
        online={online}
        onSelectSong={openChart}
        onNewSong={openNew}
      />
    );
  } else if (tab === 'settings') {
    activeView = (
      <SettingsView
        online={online}
        syncing={syncing}
        lastSync={lastSync}
        dirty={dirty}
        conflicts={conflicts}
        syncError={syncError}
        onSync={syncNow}
        themePreference={themePreference}
        onThemeChange={chooseTheme}
      />
    );
  } else {
    activeView = (
      <SetlistListView
        online={online}
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
            className={`nav-item ${tab === 'setlists' ? 'active' : ''}`}
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
