/**
 * importer/import.js  v2
 *
 * Batch imports all DOCX files from a folder into the SQLite database.
 *
 * Usage:
 *   node importer/import.js --input ./docs --db ./charts.db
 *   node importer/import.js --input ./docs --db ./charts.db --dry-run
 *   node importer/import.js --input ./docs --db ./charts.db --verbose
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const { extractFromDocx }                  = require('./extract');
const { parseChart }                       = require('../parser');
const { getDb, saveDb }                    = require('../db/schema');
const { insertSong, getSongByTitleArtist,
        logImport }                        = require('../db/queries');

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------
function parseArgs() {
  const args = process.argv.slice(2);
  const get  = (flag) => { const i = args.indexOf(flag); return i >= 0 ? args[i + 1] : null; };
  return {
    inputPath: get('--input') || './docs',
    dbPath:    get('--db')    || './charts.db',
    dryRun:    args.includes('--dry-run'),
    verbose:   args.includes('--verbose'),
  };
}

// ---------------------------------------------------------------------------
// File discovery — recursive, skips Word temp files (~$...)
// ---------------------------------------------------------------------------
function findDocxFiles(inputPath) {
  const stat = fs.statSync(inputPath);
  if (stat.isFile()) return inputPath.endsWith('.docx') ? [inputPath] : [];
  const results = [];
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.docx') && !entry.name.startsWith('~$'))
        results.push(full);
    }
  }
  walk(inputPath);
  return results.sort();
}

// ---------------------------------------------------------------------------
// Multi-song splitter
// Documents may contain multiple songs separated by # headers
// ---------------------------------------------------------------------------
function splitIntoSongs(text) {
  const lines  = text.split('\n');
  const blocks = [];
  let current  = [];

  for (const line of lines) {
    if (line.match(/^#\s+\S/) && current.length > 0) {
      const joined = current.join('\n').trim();
      if (joined) blocks.push(joined);
      current = [line];
    } else {
      current.push(line);
    }
  }
  const last = current.join('\n').trim();
  if (last) blocks.push(last);

  return blocks.filter(b => b.match(/^#\s+\S/m));
}

// ---------------------------------------------------------------------------
// Import one parsed song
// ---------------------------------------------------------------------------
function importSong(db, song, sourceFile, opts) {
  const { dryRun, verbose } = opts;
  const label = `${song.title}${song.artist ? ` – ${song.artist}` : ''}`;

  // Duplicate check
  if (song.title && song.artist) {
    const existing = getSongByTitleArtist(db, song.title, song.artist);
    if (existing) {
      if (verbose) console.log(`  ⟳  SKIP  ${label}`);
      logImport(db, { sourceFile, songId: existing.id, status: 'skipped',
                      message: 'duplicate title+artist' });
      return 'skipped';
    }
  }

  if (dryRun) {
    console.log(`  ✓  DRY   ${label}`);
    return 'dryRun';
  }

  try {
    const songId = insertSong(db, {
      ...song,
      sourceFile:    path.basename(sourceFile),
      cloudProvider: 'local',
    });
    console.log(`  ✓  OK    ${label} → id=${songId}`);
    logImport(db, { sourceFile, songId, status: 'ok' });
    return 'ok';
  } catch (err) {
    console.error(`  ✗  ERR   ${label}: ${err.message}`);
    logImport(db, { sourceFile, status: 'error', message: err.message });
    return 'error';
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  const opts = parseArgs();
  const { inputPath, dbPath, dryRun } = opts;

  console.log(`\nChart Importer${dryRun ? ' (DRY RUN)' : ''}`);
  console.log(`  Input:    ${inputPath}`);
  console.log(`  Database: ${dbPath}\n`);

  const files = findDocxFiles(inputPath);
  if (files.length === 0) { console.log('No .docx files found.'); process.exit(0); }
  console.log(`Found ${files.length} DOCX file(s)\n`);

  const db = await getDb(dbPath);
  const totals = { ok: 0, skipped: 0, error: 0, dryRun: 0 };

  for (const filePath of files) {
    const label = path.relative(process.cwd(), filePath);
    console.log(`── ${label}`);

    let text;
    try {
      const { text: extracted, warnings } = await extractFromDocx(filePath);
      text = extracted;
      if (warnings.length) console.log(`  ⚠ ${warnings.length} mammoth warning(s)`);
    } catch (err) {
      console.error(`  ✗ Extraction failed: ${err.message}`);
      totals.error++; continue;
    }

    const songBlocks = splitIntoSongs(text);
    if (songBlocks.length === 0) {
      console.log(`  ⚠ No song headers (# Title) found — skipping`);
      continue;
    }
    if (songBlocks.length > 1) console.log(`  Found ${songBlocks.length} songs`);

    for (const block of songBlocks) {
      let parsed;
      try {
        parsed = parseChart(block);
      } catch (err) {
        console.error(`  ✗ Parse error: ${err.message}`);
        logImport(db, { sourceFile: filePath, status: 'error', message: `parse: ${err.message}` });
        totals.error++; continue;
      }

      if (!parsed.title || parsed.title === 'Unknown') {
        console.log(`  ⚠ Could not determine title — skipping`);
        continue;
      }

      const status = importSong(db, parsed, filePath, opts);
      if (status === 'dryRun') totals.dryRun++;
      else totals[status]++;
    }
  }

  if (!dryRun) saveDb(db, dbPath);

  console.log('\n── Summary ─────────────────────────────');
  if (dryRun) console.log(`  Would import: ${totals.dryRun}`);
  else        console.log(`  Imported:     ${totals.ok}`);
  console.log(`  Skipped:      ${totals.skipped} (duplicates)`);
  console.log(`  Errors:       ${totals.error}`);
  if (!dryRun) console.log(`  Database:     ${dbPath}`);
  console.log('');

  db.close();
}

main().catch(err => { console.error('Fatal:', err); process.exit(1); });
