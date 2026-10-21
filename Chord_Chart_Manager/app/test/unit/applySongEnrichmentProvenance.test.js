import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { buildEnrichmentPreview } = require('../../server/existingSongEnrichment.js');
const { validateManifest } = require('../../server/applySongEnrichment.js');

const RESPONSE_DIGEST = 'a'.repeat(64);

function manifestWithSchema(schemaVersion) {
  const snapshot = { id: 42, updated_at: '2026-10-14T18:30:00.123456Z', release_year: null,
    original_key: null, bpm: null, genres: [], vibes: [] };
  const candidate = { value: 1978, source: 'Gemini', response_digest: RESPONSE_DIGEST,
    model_id: 'gemini-3.1-flash-lite', prompt_version: 'missing-metadata-fallback-v2', schema_version: schemaVersion };
  const evidence = { mbid: null, scalars: { release_year: [candidate] }, genres: [], vibes: [] };
  const reviewDecision = { mbid: null, scalars: { release_year: { value: candidate.value, source: candidate.source,
    response_digest: candidate.response_digest } }, genres: [], vibes: [] };
  const preview = buildEnrichmentPreview(snapshot, evidence, reviewDecision, { genres: [], vibes: [] });
  return { format_version: 1, operation_id: '11111111-1111-4111-8111-111111111111', songs: [{ song_id: 42,
    base_version: snapshot.updated_at, identity: { title: 'Synthetic', artist_id: 1, artist_name: 'Synthetic Artist' },
    evidence, review_decision: reviewDecision, preview }] };
}

describe('B3 Gemini schema provenance versions', () => {
  it('accepts historical schema v1 and current schema v2 evidence', () => {
    for (const version of ['missing-metadata-schema-v1', 'missing-metadata-schema-v2']) {
      expect(validateManifest(manifestWithSchema(version))).toHaveLength(1);
    }
  });

  it('rejects unsupported schema provenance versions', () => {
    const manifest = manifestWithSchema('missing-metadata-schema-v1');
    manifest.songs[0].evidence.scalars.release_year[0].schema_version = 'missing-metadata-schema-v3';
    manifest.songs[0].preview.scalar_changes[0].evidence.schema_version = 'missing-metadata-schema-v3';
    let failure;
    try { validateManifest(manifest); } catch (error) { failure = error; }
    expect(failure).toMatchObject({ code: 'INVALID_MANIFEST' });
  });
});
