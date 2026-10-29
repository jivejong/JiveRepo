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

// Relative by default so the app works same-origin behind HTTPS (Caddy /
// Tailscale) in production — which is required for the service worker anyway.
// In dev, the Vite proxy forwards /api to the server (see vite.config.js), so
// relative also works there. Set VITE_API_URL only for a cross-origin setup.
const BASE = import.meta.env.VITE_API_URL || '';

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(`${BASE}${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    const error = new Error(err.error || `HTTP ${res.status}`);
    error.status = res.status;
    if (typeof err.code === 'string') error.code = err.code;
    if (typeof err.category === 'string') error.category = err.category;
    throw error;
  }
  const result = await res.json();
  if (res.headers.get('X-Operation-Replayed') === 'true' && result && typeof result === 'object') {
    Object.defineProperty(result, '_operation_replayed', { value: true });
  }
  return result;
}

const get    = (path)        => request('GET',    path);
const post   = (path, body)  => request('POST',   path, body);
const put    = (path, body)  => request('PUT',    path, body);
const del    = (path, body)  => request('DELETE', path, body);

// ── Songs ────────────────────────────────────────────────────────────────────
export const songs = {
  search:    (params = {}) => get(`/api/songs?${new URLSearchParams(filterEmpty(params))}`),
  get:       (id)          => get(`/api/songs/${id}`),
  create:    (data)        => post('/api/songs', data),
  update:    (id, data)    => put(`/api/songs/${id}`, data),
  remove:    (id, data)    => del(`/api/songs/${id}`, data),
};

// ── Tags ─────────────────────────────────────────────────────────────────────
export const tags = {
  all:    ()                       => get('/api/tags'),
};

export const tagDefinitions = {
  all: () => get('/api/tag-definitions'),
  create: (category, name) => post('/api/tag-definitions', { category, name }),
  rename: (category, id, name) => put(`/api/tag-definitions/${category}/${id}`, { name }),
  remove: (category, id, decision) => del(`/api/tag-definitions/${category}/${id}`, decision),
};

// ── Setlists ──────────────────────────────────────────────────────────────────
export const setlists = {
  all:         ()               => get('/api/setlists'),
  get:         (id)             => get(`/api/setlists/${id}`),
  mutate:      operation       => post('/api/setlists/mutations', operation),
};

// ── Full library export (for offline sync) ────────────────────────────────────
// Returns { songs: [...full songs incl. chart_content...], facets, exported_at }
export const library = () => get('/api/export');

// ── Stats & import ───────────────────────────────────────────────────────────
export const stats  = () => get('/api/stats');

// ── Helpers ───────────────────────────────────────────────────────────────────
function filterEmpty(obj) {
  return Object.fromEntries(
    Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== '')
  );
}
