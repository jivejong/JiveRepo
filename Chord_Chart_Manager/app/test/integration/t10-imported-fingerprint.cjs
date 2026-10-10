// Stores only IDs, exact versions, and SHA-256 fingerprints under ignored
// backups/. The fingerprint covers full song rows and their associations.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pool } = require('../../server/db');

async function capture() {
  const result = await pool.query(`
    SELECT s.id,
      to_char(s.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS version,
      s.source_document, s.chart_source,
      to_jsonb(s) AS song,
      to_jsonb(a) AS artist,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('join',to_jsonb(sg),'definition',to_jsonb(g)) ORDER BY sg.genre_id)
        FROM public.song_genres sg JOIN public.genres g ON g.id=sg.genre_id WHERE sg.song_id=s.id),'[]'::jsonb) AS genres,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('join',to_jsonb(sv),'definition',to_jsonb(v)) ORDER BY sv.vibe_id)
        FROM public.song_vibes sv JOIN public.vibes v ON v.id=sv.vibe_id WHERE sv.song_id=s.id),'[]'::jsonb) AS vibes,
      COALESCE((SELECT jsonb_agg(to_jsonb(ss) ORDER BY ss.setlist_id,ss.position)
        FROM public.setlist_songs ss WHERE ss.song_id=s.id),'[]'::jsonb) AS setlist_entries
    FROM public.songs s JOIN public.artists a ON a.id=s.artist_id
    WHERE s.id BETWEEN 29 AND 36 ORDER BY s.id`);
  assert.deepEqual(result.rows.map(row => row.id), [29, 30, 31, 32, 33, 34, 35, 36],
    'Expected all eight approved sample IDs to exist');
  for (const row of result.rows) {
    assert.equal(row.source_document, 'Sample.docx', `Unexpected source document for ID ${row.id}`);
    assert.equal(row.chart_source, null, `Expected NULL chart_source for ID ${row.id}`);
  }
  return result.rows.map(row => {
    const fingerprintData = {
      song: row.song, artist: row.artist, genres: row.genres, vibes: row.vibes,
      setlist_entries: row.setlist_entries,
    };
    return { id: row.id, version: row.version,
      sha256: crypto.createHash('sha256').update(JSON.stringify(fingerprintData)).digest('hex') };
  });
}

async function main() {
  const [mode, fileName, priorName] = process.argv.slice(2);
  assert.ok(['before', 'compare'].includes(mode));
  const file = path.resolve(fileName);
  const current = await capture();
  if (mode === 'before') {
    fs.writeFileSync(file, JSON.stringify(current, null, 2), { flag: 'wx' });
    console.log(`Captured 8 imported-row fingerprints and exact versions: ${path.basename(file)}`);
  } else {
    const prior = JSON.parse(fs.readFileSync(path.resolve(priorName), 'utf8'));
    assert.deepEqual(current, prior, 'Imported rows, exact versions, or associations changed');
    fs.writeFileSync(file, JSON.stringify(current, null, 2), { flag: 'wx' });
    console.log(`All 8 imported-row fingerprints, exact versions, and associations match: ${path.basename(file)}`);
  }
}

main().catch(error => {
  console.error(`T10 imported-row preservation check failed: ${error.message}`);
  process.exitCode = 1;
}).finally(() => pool.end());
