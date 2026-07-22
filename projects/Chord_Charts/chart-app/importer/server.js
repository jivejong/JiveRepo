/**
 * server.js
 *
 * Express REST API for the Chart Manager app.
 * The PWA talks exclusively to this server.
 *
 * Routes:
 *   GET    /api/songs                  search/filter songs
 *   GET    /api/songs/:id              single song with tags
 *   POST   /api/songs                  create song manually
 *   PUT    /api/songs/:id              update song fields
 *   DELETE /api/songs/:id              delete song
 *
 *   GET    /api/songs/:id/tags         tags for a song
 *   POST   /api/songs/:id/tags         add tag to song
 *   DELETE /api/songs/:id/tags/:name   remove tag from song
 *
 *   GET    /api/tags                   all tags with counts
 *   PUT    /api/tags/:name             rename or recategorize tag
 *
 *   GET    /api/setlists               all setlists
 *   POST   /api/setlists               create setlist
 *   GET    /api/setlists/:id           setlist with songs
 *   PUT    /api/setlists/:id           update setlist
 *   DELETE /api/setlists/:id           delete setlist
 *   POST   /api/setlists/:id/songs     add song to setlist
 *   DELETE /api/setlists/:id/songs/:pos remove song from setlist
 *   PUT    /api/setlists/:id/order     reorder setlist
 *
 *   POST   /api/import                 trigger import from a folder path
 *   GET    /api/import/log             recent import log entries
 *   GET    /api/stats                  dashboard counts
 *   GET    /api/sync                   trigger cloud sync (stub for Phase 6)
 *
 * Error convention:
 *   All errors return { error: "message" } with appropriate HTTP status.
 *   404 for not found, 400 for bad input, 500 for unexpected.
 */

'use strict';

const express = require('express');
const cors    = require('cors');
const path    = require('path');

const { getDb, saveDb }  = require('./db/schema');
const q                  = require('./db/queries');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, 'charts.db');
const PORT    = process.env.PORT    || 3001;

// ---------------------------------------------------------------------------
// App setup
// ---------------------------------------------------------------------------
const app = express();

app.use(cors({
  origin: [
    'http://localhost:3000',   // React dev server
    'http://localhost:5173',   // Vite dev server
    /^http:\/\/192\.168\./,    // local network (tablet on same wifi)
    /^http:\/\/10\./,          // local network alt range
  ],
  credentials: true,
}));

app.use(express.json());

// ---------------------------------------------------------------------------
// DB middleware — attach db to every request
// Opens once at startup; saves after every mutating request
// ---------------------------------------------------------------------------
let _db = null;

async function ensureDb() {
  if (!_db) _db = await getDb(DB_PATH);
  return _db;
}

app.use(async (req, res, next) => {
  try {
    req.db = await ensureDb();
    next();
  } catch (err) {
    next(err);
  }
});

// Save DB after every mutating request
app.use((req, res, next) => {
  const orig = res.json.bind(res);
  res.json = (body) => {
    if (['POST','PUT','DELETE','PATCH'].includes(req.method) && res.statusCode < 400) {
      try { saveDb(req.db, DB_PATH); } catch (e) { console.error('saveDb error:', e); }
    }
    return orig(body);
  };
  next();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const wrap = fn => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

function notFound(res, what) {
  return res.status(404).json({ error: `${what} not found` });
}

// ---------------------------------------------------------------------------
// Songs
// ---------------------------------------------------------------------------

// GET /api/songs?q=&genre=&feel=&era=&artist=&tags=rock,70s&sort=title
app.get('/api/songs', wrap(async (req, res) => {
  const { q: query, genre, feel, era, artist, tags, sort } = req.query;
  const tagList = tags ? tags.split(',').map(t => t.trim()).filter(Boolean) : undefined;
  const songs = q.searchSongs(req.db, { query, genre, feel, era, artist, tags: tagList }, sort);
  res.json({ songs, count: songs.length });
}));

// GET /api/songs/:id
app.get('/api/songs/:id', wrap(async (req, res) => {
  const song = q.getSongById(req.db, Number(req.params.id));
  if (!song) return notFound(res, 'Song');
  const tags = q.getTagsForSong(req.db, song.id);
  res.json({ ...song, tags });
}));

// POST /api/songs  (manual song creation)
app.post('/api/songs', wrap(async (req, res) => {
  const { title, artist, chartContent } = req.body;
  if (!title)        return res.status(400).json({ error: 'title is required' });
  if (!chartContent) return res.status(400).json({ error: 'chartContent is required' });

  const { parseChart } = require('./parser');
  const parsed = parseChart(chartContent);
  // Allow body fields to override parsed values
  const merged = { ...parsed, ...req.body };
  const id = q.insertSong(req.db, merged);
  const song = q.getSongById(req.db, id);
  res.status(201).json(song);
}));

// PUT /api/songs/:id
app.put('/api/songs/:id', wrap(async (req, res) => {
  const id = Number(req.params.id);
  if (!q.getSongById(req.db, id)) return notFound(res, 'Song');

  // If chartContent changed, re-parse metadata
  if (req.body.chart_content) {
    const { parseChart } = require('./parser');
    const reparsed = parseChart(req.body.chart_content);
    // Keep any user-edited fields from body; backfill from reparse
    req.body = {
      chart_written_key: reparsed.chartWrittenKey,
      sounding_key:      reparsed.soundingKey,
      original_key:      reparsed.originalKey,
      default_capo:      reparsed.defaultCapo,
      bpm:               reparsed.bpm,
      beatbuddy_structure: reparsed.beatbuddyStructure,
      ...req.body,
      user_edited_at: new Date().toISOString(),
    };
    q.updateChartContent(req.db, id, req.body.chart_content);
  }

  q.updateSong(req.db, id, req.body);
  res.json(q.getSongById(req.db, id));
}));

// DELETE /api/songs/:id
app.delete('/api/songs/:id', wrap(async (req, res) => {
  const id = Number(req.params.id);
  if (!q.getSongById(req.db, id)) return notFound(res, 'Song');
  q.deleteSong(req.db, id);
  res.json({ deleted: id });
}));

// ---------------------------------------------------------------------------
// Song tags
// ---------------------------------------------------------------------------

// GET /api/songs/:id/tags
app.get('/api/songs/:id/tags', wrap(async (req, res) => {
  const id = Number(req.params.id);
  if (!q.getSongById(req.db, id)) return notFound(res, 'Song');
  res.json(q.getTagsForSong(req.db, id));
}));

// POST /api/songs/:id/tags  { name, category? }
app.post('/api/songs/:id/tags', wrap(async (req, res) => {
  const id = Number(req.params.id);
  if (!q.getSongById(req.db, id)) return notFound(res, 'Song');
  const { name, category } = req.body;
  if (!name) return res.status(400).json({ error: 'tag name is required' });
  q.addTagToSong(req.db, id, name.trim(), category ?? null);
  res.status(201).json(q.getTagsForSong(req.db, id));
}));

// DELETE /api/songs/:id/tags/:name
app.delete('/api/songs/:id/tags/:name', wrap(async (req, res) => {
  const id = Number(req.params.id);
  if (!q.getSongById(req.db, id)) return notFound(res, 'Song');
  q.removeTagFromSong(req.db, id, decodeURIComponent(req.params.name));
  res.json(q.getTagsForSong(req.db, id));
}));

// ---------------------------------------------------------------------------
// Tags
// ---------------------------------------------------------------------------

// GET /api/tags
app.get('/api/tags', wrap(async (req, res) => {
  res.json(q.getAllTags(req.db));
}));

// PUT /api/tags/:name  { name?, category? }
app.put('/api/tags/:name', wrap(async (req, res) => {
  const oldName = decodeURIComponent(req.params.name);
  if (req.body.name && req.body.name !== oldName) {
    q.renameTag(req.db, oldName, req.body.name);
  }
  if (req.body.category !== undefined) {
    q.updateTagCategory(req.db, req.body.name || oldName, req.body.category);
  }
  res.json(q.getAllTags(req.db));
}));

// ---------------------------------------------------------------------------
// Setlists
// ---------------------------------------------------------------------------

// GET /api/setlists
app.get('/api/setlists', wrap(async (req, res) => {
  res.json(q.getAllSetlists(req.db));
}));

// POST /api/setlists  { name, gigDate?, notes? }
app.post('/api/setlists', wrap(async (req, res) => {
  const { name, gigDate, notes } = req.body;
  if (!name) return res.status(400).json({ error: 'name is required' });
  const id = q.createSetlist(req.db, { name, gigDate, notes });
  res.status(201).json(q.getSetlistWithSongs(req.db, id));
}));

// GET /api/setlists/:id
app.get('/api/setlists/:id', wrap(async (req, res) => {
  const setlist = q.getSetlistWithSongs(req.db, Number(req.params.id));
  if (!setlist) return notFound(res, 'Setlist');
  res.json(setlist);
}));

// PUT /api/setlists/:id
app.put('/api/setlists/:id', wrap(async (req, res) => {
  const id = Number(req.params.id);
  q.updateSetlist(req.db, id, req.body);
  const setlist = q.getSetlistWithSongs(req.db, id);
  if (!setlist) return notFound(res, 'Setlist');
  res.json(setlist);
}));

// DELETE /api/setlists/:id
app.delete('/api/setlists/:id', wrap(async (req, res) => {
  const id = Number(req.params.id);
  if (!q.getSetlistWithSongs(req.db, id)) return notFound(res, 'Setlist');
  q.deleteSetlist(req.db, id);
  res.json({ deleted: id });
}));

// POST /api/setlists/:id/songs  { songId, position?, transposedKey?, capoFret?, notes? }
app.post('/api/setlists/:id/songs', wrap(async (req, res) => {
  const setlistId = Number(req.params.id);
  const { songId, position, transposedKey, capoFret, notes } = req.body;
  if (!songId) return res.status(400).json({ error: 'songId is required' });
  if (!q.getSongById(req.db, songId)) return notFound(res, 'Song');
  q.addSongToSetlist(req.db, setlistId, songId, { position, transposedKey, capoFret, notes });
  res.status(201).json(q.getSetlistWithSongs(req.db, setlistId));
}));

// DELETE /api/setlists/:id/songs/:position
app.delete('/api/setlists/:id/songs/:position', wrap(async (req, res) => {
  const setlistId = Number(req.params.id);
  const position  = Number(req.params.position);
  q.removeSongFromSetlist(req.db, setlistId, position);
  res.json(q.getSetlistWithSongs(req.db, setlistId));
}));

// PUT /api/setlists/:id/order  { songIds: [id, id, ...] }
app.put('/api/setlists/:id/order', wrap(async (req, res) => {
  const setlistId = Number(req.params.id);
  const { songIds } = req.body;
  if (!Array.isArray(songIds)) return res.status(400).json({ error: 'songIds array required' });
  q.reorderSetlist(req.db, setlistId, songIds);
  res.json(q.getSetlistWithSongs(req.db, setlistId));
}));

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

// POST /api/import  { folderPath, dryRun? }
app.post('/api/import', wrap(async (req, res) => {
  const { folderPath, dryRun = false } = req.body;
  if (!folderPath) return res.status(400).json({ error: 'folderPath is required' });

  const fs = require('fs');
  if (!fs.existsSync(folderPath)) {
    return res.status(400).json({ error: `Path not found: ${folderPath}` });
  }

  // Run importer programmatically
  const { extractFromDocx } = require('./importer/extract');
  const { parseChart }      = require('./parser');

  function findDocx(dir) {
    const results = [];
    const stat = fs.statSync(dir);
    if (stat.isFile()) return dir.endsWith('.docx') ? [dir] : [];
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) results.push(...findDocx(full));
      else if (e.name.endsWith('.docx') && !e.name.startsWith('~$')) results.push(full);
    }
    return results;
  }

  function splitSongs(text) {
    const lines = text.split('\n');
    const blocks = []; let cur = [];
    for (const line of lines) {
      if (line.match(/^#\s+\S/) && cur.length > 0) {
        const j = cur.join('\n').trim(); if (j) blocks.push(j); cur = [line];
      } else cur.push(line);
    }
    const last = cur.join('\n').trim(); if (last) blocks.push(last);
    return blocks.filter(b => b.match(/^#\s+\S/m));
  }

  const files = findDocx(folderPath);
  const results = { imported: 0, skipped: 0, errors: [] };

  for (const filePath of files) {
    try {
      const { text } = await extractFromDocx(filePath);
      for (const block of splitSongs(text)) {
        try {
          const parsed = parseChart(block);
          if (!parsed.title || parsed.title === 'Unknown') continue;
          if (parsed.artist && q.getSongByTitleArtist(req.db, parsed.title, parsed.artist)) {
            results.skipped++; continue;
          }
          if (!dryRun) {
            q.insertSong(req.db, { ...parsed, sourceFile: path.basename(filePath), cloudProvider: 'local' });
            q.logImport(req.db, { sourceFile: filePath, status: 'ok' });
          }
          results.imported++;
        } catch (e) { results.errors.push(`${path.basename(filePath)}: ${e.message}`); }
      }
    } catch (e) { results.errors.push(`${path.basename(filePath)}: ${e.message}`); }
  }

  res.json({ ...results, dryRun, filesScanned: files.length });
}));

// GET /api/import/log?limit=50
app.get('/api/import/log', wrap(async (req, res) => {
  res.json(q.getImportLog(req.db, Number(req.query.limit) || 100));
}));

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

// GET /api/stats
app.get('/api/stats', wrap(async (req, res) => {
  res.json(q.getStats(req.db));
}));

// ---------------------------------------------------------------------------
// Sync stub (Phase 6)
// ---------------------------------------------------------------------------

// GET /api/sync/status
app.get('/api/sync/status', (req, res) => {
  res.json({ status: 'not_configured', message: 'Cloud sync not yet set up' });
});

// POST /api/sync/trigger
app.post('/api/sync/trigger', (req, res) => {
  res.json({ status: 'not_configured', message: 'Cloud sync not yet set up' });
});

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------
app.get('/api/health', (req, res) => {
  res.json({ ok: true, db: DB_PATH, port: PORT });
});

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------
app.use((err, req, res, next) => {
  console.error(err);
  res.status(500).json({ error: err.message || 'Internal server error' });
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
app.listen(PORT, '0.0.0.0', () => {
  console.log(`\nChart Manager API`);
  console.log(`  http://localhost:${PORT}/api/health`);
  console.log(`  Database: ${DB_PATH}`);
  console.log(`  Listening on all interfaces (tablet access enabled)\n`);
});

module.exports = app; // for testing
