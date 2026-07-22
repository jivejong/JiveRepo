/**
 * lib/api.js
 *
 * All API calls to the Express server.
 * Centralised here so the server URL is in one place,
 * and offline detection is handled consistently.
 *
 * NOTE for Claude Code:
 *   Set VITE_API_URL in .env.local:
 *     VITE_API_URL=http://192.168.1.X:3001   ← your server's LAN IP
 *   The tablet needs to reach this address over wifi.
 */

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:3001';

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(`${BASE}${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

const get    = (path)        => request('GET',    path);
const post   = (path, body)  => request('POST',   path, body);
const put    = (path, body)  => request('PUT',    path, body);
const del    = (path)        => request('DELETE', path);

// ── Songs ────────────────────────────────────────────────────────────────────
export const songs = {
  search:    (params = {}) => get(`/api/songs?${new URLSearchParams(filterEmpty(params))}`),
  get:       (id)          => get(`/api/songs/${id}`),
  create:    (data)        => post('/api/songs', data),
  update:    (id, data)    => put(`/api/songs/${id}`, data),
  remove:    (id)          => del(`/api/songs/${id}`),
  addTag:    (id, name, category) => post(`/api/songs/${id}/tags`, { name, category }),
  removeTag: (id, name)    => del(`/api/songs/${id}/tags/${encodeURIComponent(name)}`),
  getTags:   (id)          => get(`/api/songs/${id}/tags`),
};

// ── Tags ─────────────────────────────────────────────────────────────────────
export const tags = {
  all:    ()                       => get('/api/tags'),
  update: (name, data)             => put(`/api/tags/${encodeURIComponent(name)}`, data),
};

// ── Setlists ──────────────────────────────────────────────────────────────────
export const setlists = {
  all:         ()               => get('/api/setlists'),
  get:         (id)             => get(`/api/setlists/${id}`),
  create:      (data)           => post('/api/setlists', data),
  update:      (id, data)       => put(`/api/setlists/${id}`, data),
  remove:      (id)             => del(`/api/setlists/${id}`),
  addSong:     (id, data)       => post(`/api/setlists/${id}/songs`, data),
  removeSong:  (id, position)   => del(`/api/setlists/${id}/songs/${position}`),
  reorder:     (id, songIds)    => put(`/api/setlists/${id}/order`, { songIds }),
};

// ── Stats & import ───────────────────────────────────────────────────────────
export const stats  = () => get('/api/stats');
export const health = () => get('/api/health');

// ── Helpers ───────────────────────────────────────────────────────────────────
function filterEmpty(obj) {
  return Object.fromEntries(
    Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== '')
  );
}
