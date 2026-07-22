/**
 * public/sw.js — Service Worker
 *
 * Cache-first strategy for app shell (HTML, JS, CSS, fonts).
 * Network-first for API calls (falls back to IndexedDB via the app, not the SW).
 *
 * NOTE for Claude Code:
 *   Register this in index.html or main.jsx:
 *   if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
 *
 *   Vite-plugin-pwa can generate this automatically — see README.
 *   This manual SW is provided as a fallback if the plugin isn't used.
 */

const CACHE_NAME = 'chart-manager-v1';

// App shell files to cache on install
const SHELL_FILES = [
  '/',
  '/index.html',
];

// ── Install: cache shell ──────────────────────────────────────────────────────
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL_FILES))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: clean old caches ────────────────────────────────────────────────
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Fetch: cache-first for shell, pass-through for API ───────────────────────
self.addEventListener('fetch', (e) => {
  const { request } = e;
  const url = new URL(request.url);

  // API calls — always go to network (offline handled by IndexedDB in the app)
  if (url.pathname.startsWith('/api/')) return;

  // External resources (Google Fonts, etc.) — network-first, cache fallback
  if (url.origin !== self.location.origin) {
    e.respondWith(
      fetch(request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(request, clone));
          return res;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // App shell — cache-first
  e.respondWith(
    caches.match(request)
      .then(cached => cached || fetch(request).then(res => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then(c => c.put(request, clone));
        return res;
      }))
      .catch(() => caches.match('/index.html')) // SPA fallback
  );
});
