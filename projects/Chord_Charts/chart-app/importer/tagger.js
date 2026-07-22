/**
 * importer/tagger.js
 *
 * AI metadata tagger for chord chart songs.
 * Uses the Anthropic API (claude-sonnet-4-6) with web search to look up
 * each song and return structured metadata: genre, feel, era, tempo_feel,
 * original_key, and suggested tags.
 *
 * Usage (CLI):
 *   ANTHROPIC_API_KEY=sk-... node importer/tagger.js --db ./charts.db
 *   ANTHROPIC_API_KEY=sk-... node importer/tagger.js --db ./charts.db --id 3
 *   ANTHROPIC_API_KEY=sk-... node importer/tagger.js --db ./charts.db --untagged
 *   ANTHROPIC_API_KEY=sk-... node importer/tagger.js --db ./charts.db --dry-run
 *
 * Usage (programmatic):
 *   const { tagSong } = require('./importer/tagger');
 *   const meta = await tagSong({ title: 'Already Gone', artist: 'Eagles' });
 *
 * NOTE for Claude Code:
 *   Install the Anthropic SDK: npm install @anthropic-ai/sdk
 *   The ANTHROPIC_API_KEY env var must be set before running.
 */

'use strict';

const path = require('path');

// ---------------------------------------------------------------------------
// Anthropic client (lazy-loaded so the file can be required without the key)
// ---------------------------------------------------------------------------
let _anthropic = null;

function getClient() {
  if (!_anthropic) {
    const Anthropic = require('@anthropic-ai/sdk');
    const key = process.env.ANTHROPIC_API_KEY;
    if (!key) throw new Error('ANTHROPIC_API_KEY environment variable not set');
    _anthropic = new Anthropic({ apiKey: key });
  }
  return _anthropic;
}

// ---------------------------------------------------------------------------
// The tagger prompt
// ---------------------------------------------------------------------------
const SYSTEM_PROMPT = `You are a music metadata assistant. Given a song title and artist,
use web search to look up the song and return accurate metadata as JSON.

Return ONLY a valid JSON object with no markdown, no preamble, no explanation.
If you are uncertain about a field, set it to null rather than guessing.

JSON schema:
{
  "genre":        string | null,   // Primary genre: Rock, Country, Blues, Folk, Pop, R&B, Jazz, Soul, Americana, etc.
  "feel":         string | null,   // Rhythmic feel: shuffle, straight, ballad, waltz, swing, funk, latin, bossa, 12/8, reggae
  "era":          string | null,   // Decade: 60s, 70s, 80s, 90s, 2000s, 2010s, 2020s
  "tempo_feel":   string | null,   // Tempo character: slow, medium, up-tempo, fast
  "original_key": string | null,   // Original recording key (e.g. "E", "Bb", "F#m")
  "bpm":          number | null,   // Approximate BPM of the original recording
  "tags":         string[],        // 3-8 descriptive tags useful for browsing
  "confidence":   number           // 0.0-1.0: how confident you are in this metadata overall
}

For tags, think about what a working musician would want to filter by:
genre, era, feel, tempo, mood, style, occasion (crowd pleaser, slow dance, closer, opener),
instrumental complexity, common request status, etc.
Do not include the genre or era as tags if they're already in those fields.`;

function buildUserPrompt(title, artist) {
  return `Song: "${title}" by ${artist}

Search for this song and return its metadata as JSON. Focus on:
1. The original recording key (not a cover version's key)
2. The rhythmic feel (shuffle, straight, ballad, etc.)
3. The era/decade it was released
4. 3-8 tags a musician would find useful for setlist planning`;
}

// ---------------------------------------------------------------------------
// Tag a single song via API
// ---------------------------------------------------------------------------
async function tagSong({ title, artist }) {
  const client = getClient();

  const response = await client.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: 1024,
    tools: [{ type: 'web_search_20250305', name: 'web_search' }],
    system: SYSTEM_PROMPT,
    messages: [
      { role: 'user', content: buildUserPrompt(title, artist) }
    ],
  });

  // Extract the text content block (may follow tool use blocks)
  const textBlock = response.content.find(b => b.type === 'text');
  if (!textBlock) throw new Error('No text response from API');

  // Parse JSON — strip any accidental markdown fences
  const clean = textBlock.text
    .replace(/^```json\s*/i, '')
    .replace(/```\s*$/, '')
    .trim();

  let meta;
  try {
    meta = JSON.parse(clean);
  } catch (e) {
    throw new Error(`Failed to parse API response as JSON: ${clean.slice(0, 200)}`);
  }

  return {
    genre:       meta.genre       ?? null,
    feel:        meta.feel        ?? null,
    era:         meta.era         ?? null,
    tempoFeel:   meta.tempo_feel  ?? null,
    originalKey: meta.original_key ?? null,
    bpm:         meta.bpm         ?? null,
    tags:        Array.isArray(meta.tags) ? meta.tags : [],
    confidence:  typeof meta.confidence === 'number' ? meta.confidence : null,
  };
}

// ---------------------------------------------------------------------------
// Batch tagger — processes songs from the DB
// ---------------------------------------------------------------------------
async function tagAllUntagged(db, queries, opts = {}) {
  const { dryRun = false, limit = null, delayMs = 1000 } = opts;

  // Find songs with no genre (proxy for "not yet AI-tagged")
  const songs = queries.searchSongs(db, {}).filter(s => !s.genre);
  const toTag = limit ? songs.slice(0, limit) : songs;

  console.log(`\nAI Tagger — ${toTag.length} song(s) to tag${dryRun ? ' (DRY RUN)' : ''}\n`);

  const results = { ok: 0, errors: 0, skipped: 0 };

  for (const song of toTag) {
    const label = `${song.title} – ${song.artist || 'Unknown'}`;
    process.stdout.write(`  Tagging: ${label} ... `);

    if (!song.artist) {
      console.log('skip (no artist)');
      results.skipped++;
      continue;
    }

    try {
      const meta = await tagSong({ title: song.title, artist: song.artist });

      if (!dryRun) {
        // Update song metadata fields
        queries.updateSong(db, song.id, {
          genre:               meta.genre,
          feel:                meta.feel,
          era:                 meta.era,
          tempo_feel:          meta.tempoFeel,
          original_key:        meta.originalKey || song.original_key,
          bpm:                 meta.bpm         || song.bpm,
          ai_confidence:       meta.confidence,
        });

        // Add AI-suggested tags
        for (const tagName of meta.tags) {
          queries.addTagToSong(db, song.id, tagName);
        }

        // Save after each song so progress isn't lost on interruption
        const { saveDb } = require('../db/schema');
        saveDb(db, process.env.DB_PATH || path.join(__dirname, '..', 'charts.db'));
      }

      console.log(`✓ [${meta.confidence?.toFixed(2) ?? '?'}] ${[meta.genre, meta.era, meta.feel].filter(Boolean).join(', ')}`);
      if (meta.tags.length) console.log(`         tags: ${meta.tags.join(', ')}`);
      results.ok++;

    } catch (err) {
      console.log(`✗ ${err.message}`);
      results.errors++;
    }

    // Rate limiting — pause between requests
    if (delayMs > 0 && toTag.indexOf(song) < toTag.length - 1) {
      await new Promise(r => setTimeout(r, delayMs));
    }
  }

  console.log(`\n── Summary ─────────────────────────`);
  console.log(`  Tagged:  ${results.ok}`);
  console.log(`  Skipped: ${results.skipped}`);
  console.log(`  Errors:  ${results.errors}`);

  return results;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
async function main() {
  const args    = process.argv.slice(2);
  const get     = f => { const i = args.indexOf(f); return i >= 0 ? args[i+1] : null; };
  const has     = f => args.includes(f);

  const dbPath  = get('--db')    || path.join(__dirname, '..', 'charts.db');
  const songId  = get('--id');
  const dryRun  = has('--dry-run');
  const limit   = get('--limit') ? Number(get('--limit')) : null;

  process.env.DB_PATH = dbPath;

  const { getDb }   = require('../db/schema');
  const queries     = require('../db/queries');
  const db          = await getDb(dbPath);

  if (songId) {
    // Tag a single specific song
    const song = queries.getSongById(db, Number(songId));
    if (!song) { console.error('Song not found'); process.exit(1); }
    console.log(`\nTagging: ${song.title} – ${song.artist}`);
    const meta = await tagSong({ title: song.title, artist: song.artist });
    console.log('\nResult:', JSON.stringify(meta, null, 2));

    if (!dryRun) {
      queries.updateSong(db, song.id, {
        genre: meta.genre, feel: meta.feel, era: meta.era,
        tempo_feel: meta.tempoFeel, original_key: meta.originalKey || song.original_key,
        bpm: meta.bpm || song.bpm, ai_confidence: meta.confidence,
      });
      for (const t of meta.tags) queries.addTagToSong(db, song.id, t);
      const { saveDb } = require('../db/schema');
      saveDb(db, dbPath);
      console.log('\nSaved.');
    }
  } else {
    // Tag all untagged
    await tagAllUntagged(db, queries, { dryRun, limit });
  }

  db.close();
}

if (require.main === module) {
  main().catch(err => { console.error('Fatal:', err.message); process.exit(1); });
}

module.exports = { tagSong, tagAllUntagged };
