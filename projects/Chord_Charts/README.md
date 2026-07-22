# Chart Manager

Chord chart management PWA for live performance.
Dark-stage optimised UI — amber chords, white lyrics, tablet-first.

## Project structure

```
chart-app/          ← Node.js backend
  parser.js         ← DOCX text → structured song JSON
  transpose.js      ← Key transposition engine (shared with PWA)
  db/
    schema.js       ← SQLite DDL
    queries.js      ← All DB operations
  importer/
    extract.js      ← DOCX → parser-ready text
    import.js       ← CLI batch importer
    tagger.js       ← AI metadata tagger (Claude API)
  server.js         ← Express REST API (port 3001)

chart-pwa/          ← React PWA frontend
  src/
    App.jsx         ← Root navigation state machine
    index.css       ← Design tokens + all styles
    components/
      Icons.jsx     ← Inline SVG icons
      TagPanel.jsx  ← Collapsible tag filter panel
    hooks/
      hooks.js      ← useOnline, useSwipe, useSongs, useSetlists, useSync
    lib/
      api.js        ← All fetch calls to Express server
      cache.js      ← IndexedDB offline cache
      chartRenderer.jsx ← Chart text → React components
    views/
      HomeView.jsx      ← Song browser + setlists
      ChartView.jsx     ← Chart display + transpose + swipe nav
      EditView.jsx      ← Song editor
      SetlistView.jsx   ← Setlist detail
      SettingsView.jsx  ← Sync status + stats
  public/
    sw.js           ← Service worker (offline shell caching)
    manifest.json   ← PWA manifest
```

## Setup (Claude Code instructions)

### 1. Backend dependencies

```bash
cd chart-app
npm install express cors mammoth sql.js @anthropic-ai/sdk
# On your actual machine, swap sql.js for better-sqlite3:
npm install better-sqlite3
# Then update db/schema.js and db/queries.js to use better-sqlite3 API
```

### 2. Import your charts

```bash
node importer/import.js --input /path/to/your/word/docs --db ./charts.db
```

### 3. AI tag your library

```bash
ANTHROPIC_API_KEY=sk-... node importer/tagger.js --db ./charts.db
# Tag a single song first to verify:
ANTHROPIC_API_KEY=sk-... node importer/tagger.js --db ./charts.db --id 1
```

### 4. Start the API server

```bash
node server.js
# Server runs on port 3001, accessible on your LAN
```

### 5. PWA setup

```bash
cd chart-pwa
npm install vite @vitejs/plugin-react react react-dom
```

Create `.env.local`:

```
VITE_API_URL=http://192.168.1.X:3001
```

Replace `192.168.1.X` with your server machine's LAN IP address.
Find it with `ipconfig` (Windows) or `ifconfig` / `ip addr` (Mac/Linux).

```bash
npm run dev -- --host
```

### 6. Access from tablet

Open `http://YOUR_MACHINE_IP:3000` in Safari (iOS) or Chrome (Android).
Add to Home Screen for full-screen PWA experience.

### 7. transpose.js path

`ChartView.jsx` imports transpose.js as:

```js
import { transposeChart, soundingKey } from "../../transpose";
```

This assumes the file is at `chart-app/transpose.js` and the PWA is at
`chart-app/chart-pwa/`. Adjust the relative path if your layout differs,
or copy `transpose.js` into `chart-pwa/src/lib/transpose.js` and update
the import in `ChartView.jsx`.

### 8. better-sqlite3 migration (recommended)

The backend was built with `sql.js` (pure JS) due to environment constraints.
On your machine, `better-sqlite3` is much better: synchronous, faster, no
explicit save call needed. Ask Claude Code to:

- Replace `sql.js` with `better-sqlite3`
- Remove the `saveDb()` calls (not needed with better-sqlite3)
- Update `getDb()` to return a synchronous DB instance

### 9. PWA icons

Add `icon-192.png` and `icon-512.png` to `public/` for home screen installation.
Any 192×192 and 512×512 PNG will work.

## API endpoints

| Method | Path                         | Description              |
| ------ | ---------------------------- | ------------------------ |
| GET    | /api/songs                   | Search/filter songs      |
| GET    | /api/songs/:id               | Single song with tags    |
| PUT    | /api/songs/:id               | Update song              |
| DELETE | /api/songs/:id               | Delete song              |
| POST   | /api/songs/:id/tags          | Add tag                  |
| DELETE | /api/songs/:id/tags/:name    | Remove tag               |
| GET    | /api/tags                    | All tags with counts     |
| GET    | /api/setlists                | All setlists             |
| GET    | /api/setlists/:id            | Setlist with songs       |
| POST   | /api/setlists                | Create setlist           |
| PUT    | /api/setlists/:id            | Update setlist           |
| POST   | /api/setlists/:id/songs      | Add song to setlist      |
| DELETE | /api/setlists/:id/songs/:pos | Remove song from setlist |
| GET    | /api/stats                   | Library counts           |
| GET    | /api/health                  | Server health check      |
