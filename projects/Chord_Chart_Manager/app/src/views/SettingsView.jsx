import React, { useState, useEffect } from 'react';
import { SyncIcon } from '../components/Icons';
import * as api from '../lib/api';

export default function SettingsView({
  online, syncing, lastSync, dirty, conflicts, syncError, onSync,
}) {
  const [stats,    setStats]    = useState(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!online) return;
    api.stats().then(setStats).catch(() => {});
  }, [online]);

  const manualSync = async () => {
    setChecking(true);
    await onSync();
    setChecking(false);
  };

  const lastSyncStr = lastSync
    ? new Date(lastSync).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : 'Never';

  return (
    <div className="screen">
      <div className="topbar">
        <div className="topbar-title">Settings</div>
      </div>

      <div style={{ padding: '16px 16px 80px' }}>

        {/* Sync status */}
        <div style={{ marginBottom: 24 }}>
          <div className="form-label" style={{ marginBottom: 12 }}>Sync</div>
          <div style={{
            background: 'var(--bg-raised)', borderRadius: 'var(--radius)',
            border: '1px solid var(--border)', overflow: 'hidden',
          }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)',
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Status</span>
              <span style={{
                fontSize: 13, fontWeight: 500,
                color: online ? 'var(--success)' : 'var(--danger)',
              }}>
                {online ? '● Online' : '○ Offline'}
              </span>
            </div>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)',
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Last synced</span>
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{lastSyncStr}</span>
            </div>
            {dirty.length > 0 && (
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)',
                            background: 'rgba(232,168,56,0.06)' }}>
                <div style={{ fontSize: 13, color: 'var(--accent)', fontWeight: 500, marginBottom: 4 }}>
                  {dirty.length} tablet change{dirty.length !== 1 ? 's' : ''} waiting to sync
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  They will be pushed before the latest server copy is downloaded.
                </div>
              </div>
            )}
            {conflicts.length > 0 && (
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)',
                            background: 'rgba(224,92,92,0.08)' }}>
                <div style={{ fontSize: 13, color: 'var(--danger)', fontWeight: 500, marginBottom: 4 }}>
                  {conflicts.length} tablet change{conflicts.length !== 1 ? 's were' : ' was'} replaced
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  The desktop changed {conflicts.length === 1 ? 'that song' : 'those songs'} first,
                  so the desktop version was kept.
                </div>
              </div>
            )}
            {syncError && (
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)',
                            color: 'var(--danger)', fontSize: 12 }}>
                Sync failed: {syncError}
              </div>
            )}
            <div style={{ padding: 12 }}>
              <button
                className="btn btn-ghost btn-full"
                onClick={manualSync}
                disabled={!online || syncing || checking}
              >
                <SyncIcon size={16} />
                {syncing || checking ? 'Syncing…' : 'Sync now'}
              </button>
            </div>
          </div>
        </div>

        {/* Library stats */}
        {stats && (
          <div style={{ marginBottom: 24 }}>
            <div className="form-label" style={{ marginBottom: 12 }}>Library</div>
            <div style={{
              background: 'var(--bg-raised)', borderRadius: 'var(--radius)',
              border: '1px solid var(--border)', overflow: 'hidden',
            }}>
              {[
                ['Songs',    stats.totalSongs],
                ['Tags',     stats.totalTags],
                ['Setlists', stats.totalSetlists],
              ].map(([label, count], i, arr) => (
                <div key={label} style={{
                  padding: '12px 16px',
                  borderBottom: i < arr.length - 1 ? '1px solid var(--border)' : 'none',
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center'
                }}>
                  <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>{label}</span>
                  <span style={{ fontSize: 15, fontWeight: 500, color: 'var(--text-primary)',
                                 fontFamily: 'var(--font-chord)' }}>
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Server config reminder */}
        <div style={{ marginBottom: 24 }}>
          <div className="form-label" style={{ marginBottom: 12 }}>Server</div>
          <div style={{
            background: 'var(--bg-raised)', borderRadius: 'var(--radius)',
            border: '1px solid var(--border)', padding: '12px 16px',
            fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6,
          }}>
            Set <code style={{ color: 'var(--text-chord)', fontFamily: 'var(--font-chord)' }}>
              VITE_API_URL
            </code> in <code style={{ color: 'var(--text-chord)', fontFamily: 'var(--font-chord)' }}>
              .env.local
            </code> to your server's LAN IP address so the tablet can reach it over wifi.
          </div>
        </div>
      </div>
    </div>
  );
}
