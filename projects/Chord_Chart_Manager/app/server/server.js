/*
 * server.js — Express API + static host for the chord app.
 *
 * In production, serves the built React/Vite PWA from ../dist and exposes the
 * JSON API from the same origin. Transposition remains a client concern so it
 * works offline; the server persists preferred key/capo and editable chart
 * source when the user saves or synchronizes.
 */
const path = require("path");
const fs = require("fs");
const express = require("express");
const db = require("./db");
const dbx = require("./db_ext");

const app = express();
app.use(express.json({ limit: "2mb" }));

// Serve the built React app (dist/) in production; fall back to public/ if a
// build hasn't been produced yet (e.g. running the API alone in dev).
const distDir = path.join(__dirname, "..", "dist");
const PUBLIC_DIR = fs.existsSync(distDir) ? distDir : path.join(__dirname, "..", "public");
app.use(express.static(PUBLIC_DIR));

// --- API ---------------------------------------------------------------

// Liveness+readiness in one: 200 only if the DB answers. Used by the
// container HEALTHCHECK and any uptime monitor.
app.get("/healthz", async (_req, res) => {
  try {
    await db.pool.query("SELECT 1");
    res.json({ status: "ok" });
  } catch {
    res.status(503).json({ status: "db_unavailable" });
  }
});
app.get("/api/health", async (_req, res) => {
  try { await db.pool.query("SELECT 1"); res.json({ status: "ok" }); }
  catch { res.status(503).json({ status: "db_unavailable" }); }
});

app.get("/api/facets", async (_req, res, next) => {
  try { res.json(await db.getFacets()); } catch (e) { next(e); }
});

app.get("/api/tags", async (_req, res, next) => {
  try { res.json(await dbx.getTags()); } catch (e) { next(e); }
});

app.get("/api/stats", async (_req, res, next) => {
  try { res.json(await dbx.getStats()); } catch (e) { next(e); }
});

// Full library for offline sync. Map songs to the app's field names so the
// offline copy renders identically to online responses.
app.get("/api/export", async (_req, res, next) => {
  try {
    const data = await db.exportAll();
    data.songs = (data.songs || []).map((s) =>
      dbx.mapSongOut({ ...s, feel: (s.vibes || [])[0] || null }));
    res.json(data);
  } catch (e) { next(e); }
});

// Song tags (map onto genres/vibes; Era is derived)
app.post("/api/songs/:id/tags", async (req, res, next) => {
  try {
    const { name, category } = req.body || {};
    res.json(await dbx.addSongTag(parseInt(req.params.id, 10), name, category));
  } catch (e) { next(e); }
});
app.delete("/api/songs/:id/tags/:name", async (req, res, next) => {
  try {
    res.json(await dbx.removeSongTag(parseInt(req.params.id, 10),
                                     decodeURIComponent(req.params.name)));
  } catch (e) { next(e); }
});

// Search — accepts q, artist, genre, vibe, era, and a comma-separated `tags`
// list (tag names across categories, resolved against genre/vibe/era).
app.get("/api/songs", async (req, res, next) => {
  try {
    const { q, artist, genre, vibe, era, tags } = req.query;
    const filters = { q, artist, genre, vibe, era };
    let rows = await db.searchSongs(filters);
    if (tags) {
      const wanted = String(tags).split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
      rows = rows.filter((s) => {
        const have = new Set();
        (s.vibes || []).forEach((v) => have.add(v.toLowerCase()));
        if (s.primary_genre) have.add(s.primary_genre.toLowerCase());
        if (s.era) have.add(s.era.toLowerCase());
        return wanted.every((t) => have.has(t));
      });
    }
    // shape to the app's response contract
    res.json({ songs: rows.map(dbx.mapSongOut) });
  } catch (e) { next(e); }
});

app.get("/api/songs/:id", async (req, res, next) => {
  try {
    const song = await dbx.getSongFull(parseInt(req.params.id, 10));
    if (!song) return res.status(404).json({ error: "Song not found" });
    res.json(song);
  } catch (e) { next(e); }
});

app.post("/api/songs", async (req, res, next) => {
  try { res.status(201).json(await dbx.createSong(req.body || {})); }
  catch (e) { next(e); }
});

app.put("/api/songs/:id", async (req, res, next) => {
  try {
    const song = await dbx.updateSongFull(parseInt(req.params.id, 10), req.body || {});
    if (!song) return res.status(404).json({ error: "Song not found" });
    res.json(song);
  } catch (e) { next(e); }
});

app.delete("/api/songs/:id", async (req, res, next) => {
  try {
    res.json(await dbx.deleteSong(
      parseInt(req.params.id, 10),
      (req.body || {}).base_updated_at || null,
    ));
  }
  catch (e) { next(e); }
});

// --- setlists ---
app.get("/api/setlists", async (_req, res, next) => {
  try { res.json(await dbx.listSetlists()); } catch (e) { next(e); }
});
app.get("/api/setlists/:id", async (req, res, next) => {
  try {
    const sl = await dbx.getSetlist(parseInt(req.params.id, 10));
    if (!sl) return res.status(404).json({ error: "Setlist not found" });
    res.json(sl);
  } catch (e) { next(e); }
});
app.post("/api/setlists", async (req, res, next) => {
  try { res.status(201).json(await dbx.createSetlist(req.body || {})); } catch (e) { next(e); }
});
app.put("/api/setlists/:id", async (req, res, next) => {
  try { res.json(await dbx.updateSetlist(parseInt(req.params.id, 10), req.body || {})); }
  catch (e) { next(e); }
});
app.delete("/api/setlists/:id", async (req, res, next) => {
  try { res.json(await dbx.deleteSetlist(parseInt(req.params.id, 10))); } catch (e) { next(e); }
});
app.post("/api/setlists/:id/songs", async (req, res, next) => {
  try { res.json(await dbx.addSetlistSong(parseInt(req.params.id, 10), req.body || {})); }
  catch (e) { next(e); }
});
app.delete("/api/setlists/:id/songs/:position", async (req, res, next) => {
  try {
    res.json(await dbx.removeSetlistSong(parseInt(req.params.id, 10),
                                         parseInt(req.params.position, 10)));
  } catch (e) { next(e); }
});
app.put("/api/setlists/:id/order", async (req, res, next) => {
  try {
    res.json(await dbx.reorderSetlist(parseInt(req.params.id, 10),
                                      (req.body || {}).songIds || []));
  } catch (e) { next(e); }
});

// SPA fallback: any non-API GET returns the app shell (so deep links work).
app.get(/^(?!\/api\/).*/, (_req, res) => {
  res.sendFile(path.join(PUBLIC_DIR, "index.html"));
});

// --- error handler -----------------------------------------------------
app.use((err, _req, res, _next) => {
  console.error(err);
  const status = Number.isInteger(err.status) ? err.status : 500;
  res.status(status).json({ error: status === 500 ? "Internal error" : err.message });
});

const PORT = process.env.PORT || 3000;
if (require.main === module) {
  app.listen(PORT, () => console.log(`chord-app listening on :${PORT}`));
}
module.exports = app;
