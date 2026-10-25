import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { resolveRecordingCandidate, buildEnrichmentPreview } =
  require('../../server/existingSongEnrichment.js');

const UUID_A = 'a1111111-1111-4111-8111-111111111111';
const UUID_B = 'b2222222-2222-4222-8222-222222222222';
const UUID_C = 'c3333333-3333-4333-8333-333333333333';
const VERSION = '2026-10-14T18:30:00.123456Z';

function snapshot(overrides = {}) {
  return {
    id: 42,
    updated_at: VERSION,
    release_year: null,
    original_key: null,
    bpm: null,
    genres: [],
    vibes: [],
    ...overrides,
  };
}

function evidence(overrides = {}) {
  return {
    mbid: UUID_A,
    scalars: {},
    genres: [],
    vibes: [],
    provider_response_body: 'PRIVATE RAW RESPONSE MUST NOT LEAK',
    ...overrides,
  };
}

function decision(overrides = {}) {
  return { mbid: UUID_A, scalars: {}, genres: [], vibes: [], ...overrides };
}

function reference(source, response_digest, value) {
  return { ...(source === 'Gemini' ? geminiProvenance(response_digest) : { source, response_digest }), value };
}

function tagReference(source, response_digest, name) {
  return { ...(source === 'Gemini' ? geminiProvenance(response_digest) : { source, response_digest }), name };
}

function geminiProvenance(response_digest) {
  const digest = /^[a-f0-9]{64}$/.test(response_digest) ? response_digest : Buffer.from(String(response_digest)).toString('hex').padEnd(64, '0').slice(0, 64);
  return { source: 'Gemini', response_digest: digest,
    model_id: 'gemini-3.1-flash-lite', prompt_version: 'missing-metadata-fallback-v1', schema_version: 'missing-metadata-schema-v1' };
}

function definitions(overrides = {}) {
  return {
    genres: [
      { id: 1, name: 'Classic Rock' },
      { id: 2, name: 'Hard Rock' },
      { id: 3, name: 'Country' },
    ],
    vibes: [
      { id: 10, name: 'Upbeat' },
      { id: 11, name: 'Country' },
    ],
    ...overrides,
  };
}

describe('resolveRecordingCandidate', () => {
  it('requires exact normalized title and credited artist and ignores search scores', () => {
    const selected = resolveRecordingCandidate(
      { title: '  Lights ', artist: 'Journey' },
      [
        { mbid: UUID_B, title: 'Lights', artist: 'Other Artist', score: 100 },
        { mbid: UUID_A, title: ' lights ', artist: ' JOURNEY ', score: 2,
          first_release_date: '1977-06-18', response_digest: 'a'.repeat(64) },
      ],
    );
    expect(selected).toMatchObject({ matched: true, reason: null, mbid: UUID_A,
      selection_reason: 'unique_exact_match', eligible_candidate_count: 1, exclusion_counts: {},
      release_date_evidence: { first_release_date: '1977-06-18', response_digest: 'a'.repeat(64) } });
  });

  it('reports no eligible recording, unresolved disambiguation, and invalid MBIDs', () => {
    const identity = { title: 'Lights', artist: 'Journey' };
    expect(resolveRecordingCandidate(identity, [])).toMatchObject({ matched: false, reason: 'no_exact_match',
      selection_reason: 'no_eligible_recording' });
    expect(resolveRecordingCandidate(identity, [
      { mbid: UUID_A, title: 'Lights', artist: 'Journey', disambiguation: 'Unrecognized release note' },
    ])).toMatchObject({ matched: false, reason: 'unresolved_version_ambiguity',
      selection_reason: 'unresolved_version_ambiguity', exclusion_counts: { unresolved_version_ambiguity: 1 } });
    expect(resolveRecordingCandidate(identity, [
      { mbid: 'not-a-uuid', title: 'Lights', artist: 'Journey' },
    ])).toMatchObject({ matched: false, reason: 'no_eligible_recording',
      selection_reason: 'no_eligible_recording', exclusion_counts: { invalid_mbid: 1 } });
  });

  it('excludes marked alternate performances and selects the remaining studio candidate', () => {
    const identity = { title: 'Song', artist: 'Artist' };
    for (const [label, reason] of [['Live', 'live_version'], ['Remix', 'remix_version'],
      ['Acoustic', 'acoustic_version'], ['Demo', 'demo_version'], ['Alternate version', 'alternate_version']]) {
      const selected = resolveRecordingCandidate(identity, [
        { mbid: UUID_B, title: 'Song', artist: 'Artist', disambiguation: label, first_release_date: '1970' },
        { mbid: UUID_A, title: 'Song', artist: 'Artist', disambiguation: 'Studio recording', first_release_date: '1977', response_digest: 'b'.repeat(64) },
      ]);
      expect(selected).toMatchObject({ matched: true, mbid: UUID_A, selection_reason: 'unique_exact_match',
        exclusion_counts: { [reason]: 1 } });
    }
    expect(resolveRecordingCandidate({ title: 'Song (Live)', artist: 'Artist' }, [
      { mbid: UUID_A, title: 'Song (Live)', artist: 'Artist' },
    ])).toMatchObject({ matched: false, exclusion_counts: { live_version: 1 } });
    expect(resolveRecordingCandidate(identity, [
      { mbid: UUID_A, title: 'Song', artist: 'Artist', disambiguation: 'Studio', first_release_date: '1977' },
    ])).toMatchObject({ matched: true, selection_reason: 'unique_exact_match' });
    expect(resolveRecordingCandidate(identity, [
      { mbid: UUID_A, title: 'Song', artist: 'Artist', disambiguation: 'Studio recording', first_release_date: '1977' },
    ])).toMatchObject({ matched: true, selection_reason: 'unique_exact_match' });
  });

  it('selects the earliest release year and is independent of provider order or score', () => {
    const identity = { title: 'Song', artist: 'Artist' };
    const early = { mbid: UUID_A, title: 'Song', artist: 'Artist', disambiguation: '',
      first_release_date: '1977-06', response_digest: 'a'.repeat(64), score: 1 };
    const late = { mbid: UUID_B, title: 'Song', artist: 'Artist', disambiguation: 'Studio',
      first_release_date: '1978', response_digest: 'b'.repeat(64), score: 100 };
    const expected = { matched: true, reason: null, selection_reason: 'earliest_release_year',
      mbid: UUID_A, eligible_candidate_count: 2,
      earliest_year: 1977, missing_release_date_count: 0, same_year_candidate_mbids: [UUID_A],
      matched_genre_counts: [], richness_comparison_complete: true, provisional_mbid: null,
      tie_break_reason: 'earliest_release_year',
      release_date_evidence: { first_release_date: '1977-06', response_digest: 'a'.repeat(64) } };
    const candidates = [Object.freeze(early), Object.freeze(late)];
    const before = structuredClone(candidates);
    expect(resolveRecordingCandidate(identity, candidates)).toEqual({ ...expected, exclusion_counts: {} });
    expect(resolveRecordingCandidate(identity, [...candidates].reverse())).toEqual({ ...expected, exclusion_counts: {} });
    expect(candidates).toEqual(before);
  });

  it('treats dates within the same year as a Genre-richness tie', () => {
    const identity = { title: 'Song', artist: 'Artist' };
    const pair = [
      { mbid: UUID_A, title: 'Song', artist: 'Artist', first_release_date: '2000-01-01' },
      { mbid: UUID_B, title: 'Song', artist: 'Artist', first_release_date: '2000-12-31' },
    ];
    const incomplete = resolveRecordingCandidate(identity, pair, null, [{ id: 1, name: 'Rock' }]);
    expect(incomplete).toMatchObject({ matched: false, reason: 'genre_richness_incomplete', earliest_year: 2000,
      same_year_candidate_mbids: [UUID_A, UUID_B], provisional_mbid: UUID_A, richness_comparison_complete: false });
  });

  it('lets missing dates coexist with a dated eligible candidate and abstains when all dates are missing or invalid', () => {
    const identity = { title: 'Song', artist: 'Artist' };
    const dated = resolveRecordingCandidate(identity, [
      { mbid: UUID_A, title: 'Song', artist: 'Artist' },
      { mbid: UUID_B, title: 'Song', artist: 'Artist', first_release_date: '1978-11-29' },
    ]);
    expect(dated).toMatchObject({ matched: true, mbid: UUID_B, earliest_year: 1978, missing_release_date_count: 1,
      selection_reason: 'earliest_release_year' });
    for (const values of [[undefined, undefined], ['1977-02-29', '1977-13']]) {
      const candidates = values.map((date, index) => ({ mbid: index ? UUID_B : UUID_A, title: 'Song', artist: 'Artist', first_release_date: date }));
      expect(resolveRecordingCandidate(identity, candidates)).toMatchObject({ matched: false,
        reason: 'missing_or_invalid_release_evidence', selection_reason: 'no_dated_eligible_recording', earliest_year: null });
    }
  });

  it('does not inflate tied-year richness with duplicate, unknown, tag, or Vibe evidence', () => {
    const identity = { title: 'Lights', artist: 'Journey' };
    const candidates = [UUID_A, UUID_B].map(mbid => ({ mbid, title: 'Lights', artist: 'Journey', first_release_date: mbid === UUID_A ? '1978-01-01' : '1978-12-31' }));
    const digest = 'c'.repeat(64);
    const lookups = new Map([
      [UUID_A, { status: 'ok', recording: { mbid: UUID_A, genres: [
        { name: 'Rock', association: 'genre', source: 'MusicBrainz', response_digest: digest },
        { name: '  ROCK ', association: 'genre', source: 'MusicBrainz', response_digest: digest },
        { name: 'Uncatalogued', association: 'genre', source: 'MusicBrainz', response_digest: digest },
        { name: 'Country', association: 'genre', source: 'MusicBrainz', response_digest: digest },
        { name: 'Country', association: 'tag', source: 'MusicBrainz', response_digest: digest },
        { name: 'Country', association: 'vibe', source: 'MusicBrainz', response_digest: digest },
      ] } }],
      [UUID_B, { status: 'ok', recording: { mbid: UUID_B, genres: [
        { name: 'Rock', association: 'genre', source: 'MusicBrainz', response_digest: digest },
      ] } }],
    ]);
    const result = resolveRecordingCandidate(identity, candidates, null,
      [{ id: 1, name: 'Rock' }, { id: 2, name: 'Country' }, { id: 3, name: ' country ' }], { lookups });
    expect(result).toMatchObject({ matched: true, mbid: UUID_A, selection_reason: 'canonical_mbid_tiebreak', earliest_year: 1978,
      same_year_candidate_mbids: [UUID_A, UUID_B], matched_genre_counts: [
        { mbid: UUID_A, matched_genre_count: 1 }, { mbid: UUID_B, matched_genre_count: 1 },
      ], richness_comparison_complete: true });
  });

  it('selects by Genre richness when counts differ and by canonical MBID when equal, independent of order', () => {
    const identity = { title: 'Lights', artist: 'Journey' };
    const candidates = [UUID_B, UUID_A].map(mbid => ({ mbid, title: 'Lights', artist: 'Journey', first_release_date: '1978' }));
    const digest = 'd'.repeat(64);
    const definitions = [{ id: 1, name: 'Rock' }, { id: 2, name: 'Country' }];
    const lookup = (mbid, names) => ({ status: 'ok', recording: { mbid, genres: names.map(name => ({
      name, association: 'genre', source: 'MusicBrainz', response_digest: digest,
    })) } });
    const richer = resolveRecordingCandidate(identity, candidates, null, definitions,
      { lookups: new Map([[UUID_A, lookup(UUID_A, ['Rock', 'Country'])], [UUID_B, lookup(UUID_B, ['Rock'])]]) });
    expect(richer).toMatchObject({ matched: true, mbid: UUID_A, selection_reason: 'genre_richness',
      matched_genre_counts: [{ mbid: UUID_A, matched_genre_count: 2 }, { mbid: UUID_B, matched_genre_count: 1 }] });
    const equalLookups = new Map([[UUID_A, lookup(UUID_A, ['Rock'])], [UUID_B, lookup(UUID_B, ['Rock'])]]);
    const forward = resolveRecordingCandidate(identity, candidates, null, definitions, { lookups: equalLookups });
    const reverse = resolveRecordingCandidate(identity, [...candidates].reverse(), null, definitions, { lookups: equalLookups });
    expect(forward).toEqual(reverse);
    expect(forward).toMatchObject({ matched: true, mbid: UUID_A, selection_reason: 'canonical_mbid_tiebreak', tie_break_reason: 'canonical_mbid' });
  });

  it('excludes music-video and Unicode-hyphen re-recorded versions while unknown descriptions do not veto', () => {
    const identity = { title: 'Lights', artist: 'Journey' };
    const result = resolveRecordingCandidate(identity, [
      { mbid: UUID_A, title: 'Lights', artist: 'Journey', first_release_date: '1970', disambiguation: 'Music-video version' },
      { mbid: UUID_B, title: 'Lights', artist: 'Journey', first_release_date: '1977', disambiguation: 'Re‐recorded version' },
      { mbid: UUID_C, title: 'Lights', artist: 'Journey', first_release_date: '1978', disambiguation: 'Unrecognized catalog note' },
      { mbid: 'd4444444-4444-4444-8444-444444444444', title: 'Lights', artist: 'Journey', first_release_date: '1979', disambiguation: '' },
    ]);
    expect(result).toMatchObject({ matched: true, mbid: 'd4444444-4444-4444-8444-444444444444',
      exclusion_counts: { music_video_version: 1, rerecorded_version: 1, unresolved_version_ambiguity: 1 } });
  });

  it('ignores earlier wrong-artist candidates and does not accept incomplete search results', () => {
    const identity = { title: 'Song', artist: 'Artist' };
    const wrongArtist = { mbid: UUID_A, title: 'Song', artist: 'Other', first_release_date: '1950' };
    const valid = { mbid: UUID_B, title: 'Song', artist: 'Artist', first_release_date: '1977' };
    expect(resolveRecordingCandidate(identity, [wrongArtist, valid]))
      .toMatchObject({ matched: true, mbid: UUID_B, selection_reason: 'unique_exact_match' });
    expect(resolveRecordingCandidate(identity, [valid], { status: 'ok', candidates: [valid], pagination: {
      expected_total: 2, collected_count: 1, attempted_pages: 1, validated_pages: 1, complete: false,
    } })).toMatchObject({ matched: false, reason: 'incomplete_search_results', selection_reason: null });
  });

  it('does not use fuzzy aliases, punctuation removal, or artist substitutions', () => {
    expect(resolveRecordingCandidate(
      { title: 'Lights', artist: 'Journey' },
      [{ mbid: UUID_A, title: 'The Lights', artist: 'Journey' }],
    )).toMatchObject({ matched: false, reason: 'no_exact_match' });
    expect(resolveRecordingCandidate(
      { title: 'Lights', artist: 'Journey' },
      [{ mbid: UUID_A, title: 'Lights', artist: 'Journey feat. Someone' }],
    )).toMatchObject({ matched: false, reason: 'no_exact_match' });
  });
});

describe('buildEnrichmentPreview', () => {
  it('preserves the entirely synthetic Lights/Journey manual data and proposes allowed missing tags', () => {
    const song = snapshot({
      release_year: 2001,
      original_key: 'A',
      bpm: 115,
      genres: [{ id: 1, name: 'Classic Rock', is_primary: true }],
      vibes: [{ id: 10, name: 'Upbeat' }],
    });
    const ev = evidence({ genres: [
      tagReference('MusicBrainz', 'digest-hard', 'Hard Rock'),
      tagReference('MusicBrainz', 'digest-post', 'Post-punk'),
    ], vibes: [tagReference('Gemini', 'digest-country-vibe', 'Country')] });
    const reviewed = decision({ genres: ['Hard Rock', 'Post-punk'], vibes: ['Country'] });
    const result = buildEnrichmentPreview(song, ev, reviewed, definitions());

    expect(result.scalar_changes).toEqual([]);
    expect(result.tag_additions.genres).toEqual([
      { id: 2, name: 'Hard Rock', evidence: [{ source: 'MusicBrainz', response_digest: 'digest-hard' }] },
    ]);
    expect(result.tag_additions.vibes).toEqual([
      { id: 11, name: 'Country', evidence: [geminiProvenance('digest-country-vibe')] },
    ]);
    expect(result.diagnostics).toContainEqual({ code: 'unknown_tag_definition', category: 'Genre', field: null, label: 'Post-punk' });
    expect(result.primary_proposal).toBeNull();
    expect(result.has_changes).toBe(true);
  });

  it('proposes only reviewed missing scalars and validates application representations', () => {
    const ev = evidence({ scalars: {
      release_year: [reference('MusicBrainz', 'year-digest', 1977)],
      original_key: [reference('AcousticBrainz', 'key-digest', { key_key: 'F#', key_scale: 'minor' })],
      bpm: [reference('AcousticBrainz', 'bpm-digest', 120.5)],
    } });
    const reviewed = decision({ scalars: {
      release_year: reference('MusicBrainz', 'year-digest', 1977),
      original_key: reference('AcousticBrainz', 'key-digest', { key_key: 'F#', key_scale: 'minor' }),
      bpm: reference('AcousticBrainz', 'bpm-digest', 120.5),
    } });
    expect(buildEnrichmentPreview(snapshot(), ev, reviewed, definitions()).scalar_changes).toEqual([
      { field: 'bpm', before: null, after: 121, evidence: { source: 'AcousticBrainz', response_digest: 'bpm-digest' } },
      { field: 'original_key', before: null, after: 'F#m', evidence: { source: 'AcousticBrainz', response_digest: 'key-digest' } },
      { field: 'release_year', before: null, after: 1977, evidence: { source: 'MusicBrainz', response_digest: 'year-digest' } },
    ]);
  });

  it('rounds positive BPM half-up at .49 and .50 and never halves or doubles', () => {
    const previewBpm = value => {
      const ev = evidence({ scalars: { bpm: [reference('AcousticBrainz', `bpm-${value}`, value)] } });
      const reviewed = decision({ scalars: { bpm: reference('AcousticBrainz', `bpm-${value}`, value) } });
      return buildEnrichmentPreview(snapshot(), ev, reviewed, definitions()).scalar_changes[0]?.after;
    };
    expect(previewBpm(120.49)).toBe(120);
    expect(previewBpm(120.5)).toBe(121);
    expect(previewBpm(240)).toBe(240);
  });

  it('reports malformed candidates and preserves malformed or zero existing numeric values', () => {
    const ev = evidence({ scalars: {
      release_year: [reference('MusicBrainz', 'bad-year', 0)],
      bpm: [reference('AcousticBrainz', 'bad-bpm', 32768)],
    } });
    const reviewed = decision({ scalars: {
      release_year: reference('MusicBrainz', 'bad-year', 0),
      bpm: reference('AcousticBrainz', 'bad-bpm', 32768),
    } });
    const malformedExisting = buildEnrichmentPreview(snapshot({ release_year: 0, bpm: 'unknown' }), ev, reviewed, definitions());
    expect(malformedExisting.scalar_changes).toEqual([]);
    expect(malformedExisting.diagnostics.map(item => item.code)).toContain('malformed_existing_value');
    const invalidCandidates = buildEnrichmentPreview(snapshot(), ev, reviewed, definitions());
    expect(invalidCandidates.scalar_changes).toEqual([]);
    expect(invalidCandidates.diagnostics.map(item => item.code)).toContain('invalid_candidate');
  });

  it('treats undefined required scalar fields and malformed assignment snapshots as problems', () => {
    const missingField = snapshot();
    delete missingField.release_year;
    const result = buildEnrichmentPreview(missingField,
      evidence({ genres: [tagReference('MusicBrainz', 'hard', 'Hard Rock')] }),
      decision({ genres: ['Hard Rock'] }), definitions());
    expect(result.scalar_changes).toEqual([]);
    expect(result.tag_additions.genres).toEqual([{ id: 2, name: 'Hard Rock',
      evidence: [{ source: 'MusicBrainz', response_digest: 'hard' }] }]);
    expect(result.diagnostics).toContainEqual({ code: 'missing_existing_field', category: null, field: 'release_year', label: null });

    const malformedAssignments = buildEnrichmentPreview(snapshot({ genres: undefined }),
      evidence({ genres: [tagReference('MusicBrainz', 'hard', 'Hard Rock')] }),
      decision({ genres: ['Hard Rock'] }), definitions());
    expect(malformedAssignments.tag_additions.genres).toEqual([]);
    expect(malformedAssignments.diagnostics.map(item => item.code)).toContain('invalid_existing_assignments');
  });

  it('leaves conflicting scalar evidence unresolved unless the exact evidence item is selected', () => {
    const candidates = [
      reference('MusicBrainz', 'year-a', 1977),
      reference('MusicBrainz', 'year-b', 1978),
    ];
    const ev = evidence({ scalars: { release_year: candidates } });
    const unresolved = buildEnrichmentPreview(snapshot(), ev, decision(), definitions());
    expect(unresolved.scalar_changes).toEqual([]);
    expect(unresolved.diagnostics).toContainEqual({ code: 'conflicting_evidence_unresolved', category: null, field: 'release_year', label: null });

    const selected = buildEnrichmentPreview(snapshot(), ev, decision({
      scalars: { release_year: reference('MusicBrainz', 'year-b', 1978) },
    }), definitions());
    expect(selected.scalar_changes[0]).toMatchObject({ field: 'release_year', after: 1978 });
  });

  it('does not accept a scalar selection absent from evidence or an unapproved candidate', () => {
    const ev = evidence({ scalars: { bpm: [reference('AcousticBrainz', 'known', 100)] } });
    const noApproval = buildEnrichmentPreview(snapshot(), ev, decision(), definitions());
    expect(noApproval.scalar_changes).toEqual([]);
    expect(noApproval.diagnostics.map(item => item.code)).toContain('candidate_unapproved');
    const foreignSelection = buildEnrichmentPreview(snapshot(), ev, decision({
      scalars: { bpm: reference('AcousticBrainz', 'foreign', 101) },
    }), definitions());
    expect(foreignSelection.scalar_changes).toEqual([]);
    expect(foreignSelection.diagnostics.map(item => item.code)).toContain('selection_not_in_evidence');
  });

  it('keeps Genre and Vibe categories separate and preserves canonical definition spelling', () => {
    const ev = evidence({ genres: [tagReference('MusicBrainz', 'genre-country', ' country ')],
      vibes: [tagReference('Gemini', 'vibe-country', 'COUNTRY')] });
    const result = buildEnrichmentPreview(snapshot(), ev, decision({
      genres: ['COUNTRY'], vibes: [' country '],
    }), definitions({
      genres: [{ id: 31, name: ' Country ' }],
      vibes: [{ id: 41, name: 'Country' }],
    }));
    expect(result.tag_additions.genres).toEqual([{ id: 31, name: ' Country ', evidence: [{ source: 'MusicBrainz', response_digest: 'genre-country' }] }]);
    expect(result.tag_additions.vibes).toEqual([{ id: 41, name: 'Country', evidence: [geminiProvenance('vibe-country')] }]);
  });

  it('reports normalized definition ambiguity and never creates a definition', () => {
    const ev = evidence({ genres: [tagReference('MusicBrainz', 'hard-rock', 'Hard Rock')] });
    const result = buildEnrichmentPreview(snapshot(), ev, decision({ genres: ['Hard Rock'] }), definitions({
      genres: [{ id: 2, name: 'Hard Rock' }, { id: 22, name: ' hard   rock ' }],
    }));
    expect(result.tag_additions.genres).toEqual([]);
    expect(result.diagnostics.map(item => item.code)).toContain('ambiguous_tag_definition');

    const malformedCatalog = buildEnrichmentPreview(snapshot(), ev, decision({ genres: ['Hard Rock'] }), definitions({
      genres: [{ id: 2, name: 'Hard Rock' }, { id: 2, name: 'Alternative' }],
    }));
    expect(malformedCatalog.tag_additions.genres).toEqual([]);
    expect(malformedCatalog.diagnostics.map(item => item.code)).toContain('invalid_definition');

    const unselectedUnknown = buildEnrichmentPreview(snapshot(),
      evidence({ genres: [tagReference('MusicBrainz', 'unknown', 'Post-punk')] }),
      decision(), definitions());
    expect(unselectedUnknown.tag_additions.genres).toEqual([]);
    expect(unselectedUnknown.diagnostics).toContainEqual({
      code: 'unknown_tag_definition', category: 'Genre', field: null, label: 'Post-punk',
    });
  });

  it('skips existing links, preserves a primary, and permits only an explicit valid primary when none exists', () => {
    const ev = evidence({ genres: [
      tagReference('MusicBrainz', 'classic', 'Classic Rock'),
      tagReference('MusicBrainz', 'hard', 'Hard Rock'),
    ] });
    const reviewed = decision({ genres: ['Classic Rock', 'Hard Rock'] });
    const withPrimary = buildEnrichmentPreview(snapshot({
      genres: [{ id: 1, name: 'Classic Rock', is_primary: true }],
    }), ev, reviewed, definitions());
    expect(withPrimary.tag_additions.genres).toMatchObject([{ id: 2, name: 'Hard Rock' }]);
    expect(withPrimary.primary_proposal).toBeNull();

    const withoutPrimary = buildEnrichmentPreview(snapshot({
      genres: [{ id: 1, name: 'Classic Rock', is_primary: false }],
    }), ev, decision({ ...reviewed, primaryGenre: 'Hard Rock' }), definitions());
    expect(withoutPrimary.primary_proposal).toEqual({ genre_id: 2, name: 'Hard Rock' });
    expect(() => buildEnrichmentPreview(snapshot(), ev,
      decision({ ...reviewed, primaryGenre: 'Country' }), definitions()))
      .toThrowError(expect.objectContaining({ code: 'INVALID_PRIMARY_SELECTION' }));
  });

  it('skips populated scalars and assignments on rerun and returns no changes', () => {
    const song = snapshot({ release_year: 1977, original_key: 'G', bpm: 120,
      genres: [{ id: 1, name: 'Classic Rock', is_primary: true }],
      vibes: [{ id: 10, name: 'Upbeat' }] });
    const ev = evidence({ scalars: {
      release_year: [reference('MusicBrainz', 'year', 1977)],
      original_key: [reference('AcousticBrainz', 'key', 'A')],
      bpm: [reference('AcousticBrainz', 'bpm', 140)],
    }, genres: [tagReference('MusicBrainz', 'genre', 'Classic Rock')],
    vibes: [tagReference('Gemini', 'vibe', 'Upbeat')] });
    const reviewed = decision({ scalars: {
      release_year: reference('MusicBrainz', 'year', 1977),
      original_key: reference('AcousticBrainz', 'key', 'A'),
      bpm: reference('AcousticBrainz', 'bpm', 140),
    }, genres: ['Classic Rock'], vibes: ['Upbeat'] });
    const result = buildEnrichmentPreview(song, ev, reviewed, definitions());
    expect(result.scalar_changes).toEqual([]);
    expect(result.tag_additions).toEqual({ genres: [], vibes: [] });
    expect(result.has_changes).toBe(false);
  });

  it('preserves the exact microsecond base version and refuses invalid tokens', () => {
    expect(buildEnrichmentPreview(snapshot(), evidence(), decision(), definitions()).base_version).toBe(VERSION);
    for (const updated_at of ['2026-10-14T18:30:00.123Z', '2026-02-30T18:30:00.123456Z',
      '2026-10-14T18:30:00.123456-04:00']) {
      expect(() => buildEnrichmentPreview(snapshot({ updated_at }), evidence(), decision(), definitions()))
        .toThrowError(expect.objectContaining({ code: 'INVALID_BASE_VERSION' }));
    }
  });

  it('requires exact approved MBID and rejects an invalid primary selection', () => {
    expect(() => buildEnrichmentPreview(snapshot(), evidence(), decision({ mbid: UUID_B }), definitions()))
      .toThrowError(expect.objectContaining({ code: 'EVIDENCE_NOT_APPROVED' }));
    const ev = evidence({ genres: [tagReference('MusicBrainz', 'classic', 'Classic Rock')] });
    expect(() => buildEnrichmentPreview(snapshot(), ev, decision({ genres: ['Classic Rock'], primaryGenre: 'Hard Rock' }), definitions()))
      .toThrowError(expect.objectContaining({ code: 'INVALID_PRIMARY_SELECTION' }));
  });

  it('accepts only an explicit empty decision when recording identity is unresolved', () => {
    const unresolvedEvidence = evidence({ mbid: null, scalars: { release_year: [] }, genres: [], vibes: [] });
    const noOp = buildEnrichmentPreview(snapshot(), unresolvedEvidence,
      { mbid: null, scalars: {}, genres: [], vibes: [] }, definitions());
    expect(noOp).toMatchObject({ approved_mbid: null, scalar_changes: [], tag_additions: { genres: [], vibes: [] },
      primary_proposal: null, has_changes: false });
    expect(() => buildEnrichmentPreview(snapshot(), unresolvedEvidence,
      { mbid: null, scalars: { release_year: reference('MusicBrainz', 'year', 1977) }, genres: [], vibes: [] }, definitions()))
      .toThrowError(expect.objectContaining({ code: 'EVIDENCE_NOT_APPROVED' }));
    expect(() => buildEnrichmentPreview(snapshot(), unresolvedEvidence,
      { mbid: null, scalars: {}, genres: ['Hard Rock'], vibes: [] }, definitions()))
      .toThrowError(expect.objectContaining({ code: 'EVIDENCE_NOT_APPROVED' }));
  });

  it('is deterministic across evidence ordering, leaves inputs unchanged, and cannot leak prohibited fields', () => {
    const song = snapshot({ chart_source: 'SECRET LYRICS', chart_content: { secret: true },
      title: 'Private title', artist: 'Private artist', preferred_key: 'C' });
    const ev = evidence({ raw_body: 'NO RAW PROVIDER BODY', genres: [
      tagReference('MusicBrainz', 'z-digest', 'Hard Rock'),
      tagReference('MusicBrainz', 'a-digest', 'Hard Rock'),
    ] });
    const reviewed = decision({ genres: ['Hard Rock'] });
    const defs = definitions();
    const before = structuredClone({ song, ev, reviewed, defs });
    const first = buildEnrichmentPreview(song, ev, reviewed, defs);
    const second = buildEnrichmentPreview(song, evidence({ ...ev,
      genres: [...ev.genres].reverse() }), reviewed, defs);
    expect(second).toEqual(first);
    expect({ song, ev, reviewed, defs }).toEqual(before);
    expect(Object.keys(first).sort()).toEqual([
      'approved_mbid', 'base_version', 'diagnostics', 'format_version', 'has_changes',
      'primary_proposal', 'scalar_changes', 'song_id', 'tag_additions',
    ].sort());
    expect(JSON.stringify(first)).not.toMatch(/SECRET LYRICS|NO RAW PROVIDER BODY|Private title|Private artist|preferred_key/);
    expect(first.tag_additions.genres[0].evidence.map(item => item.response_digest)).toEqual(['a-digest', 'z-digest']);
  });

  it('accepts Gemini suggestions for each allowed missing scalar and tags while preserving populated scalars', () => {
    for (const [field, value] of [['release_year', 2000], ['original_key', 'C'], ['bpm', 120]]) {
      const item = reference('Gemini', `gemini-${field}`, value);
      const result = buildEnrichmentPreview(snapshot(), evidence({ scalars: { [field]: [item] } }),
        decision({ scalars: { [field]: item } }), definitions());
      expect(result.scalar_changes).toHaveLength(1);
      expect(result.scalar_changes[0]).toMatchObject({ field, after: value,
        evidence: expect.objectContaining({ source: 'Gemini', model_id: 'gemini-3.1-flash-lite',
          prompt_version: 'missing-metadata-fallback-v1', schema_version: 'missing-metadata-schema-v1' }) });
    }
    const populated = snapshot({ release_year: 1999, original_key: 'D', bpm: 90 });
    const selections = {
      release_year: reference('Gemini', 'year', 2000),
      original_key: reference('Gemini', 'key', 'C'),
      bpm: reference('Gemini', 'bpm', 120),
    };
    const preserved = buildEnrichmentPreview(populated, evidence({ scalars: Object.fromEntries(
      Object.entries(selections).map(([field, item]) => [field, [item]])) }),
    decision({ scalars: selections }), definitions());
    expect(preserved.scalar_changes).toEqual([]);
    const tags = buildEnrichmentPreview(snapshot(), evidence({
      genres: [tagReference('Gemini', 'gemini-genre', 'Hard Rock')],
      vibes: [tagReference('Gemini', 'gemini-vibe', 'Upbeat')],
    }), decision({ genres: ['Hard Rock'], vibes: ['Upbeat'] }), definitions());
    expect(tags.tag_additions.genres).toHaveLength(1);
    expect(tags.tag_additions.vibes).toHaveLength(1);
  });

  it('continues to accept historical schema v1 and current schema v2 Gemini evidence', () => {
    for (const schemaVersion of ['missing-metadata-schema-v1', 'missing-metadata-schema-v2']) {
      const item = reference('Gemini', `schema-${schemaVersion}`, 2000);
      item.schema_version = schemaVersion;
      const result = buildEnrichmentPreview(snapshot(), evidence({ scalars: { release_year: [item] } }),
        decision({ scalars: { release_year: { value: item.value, source: item.source, response_digest: item.response_digest } } }), definitions());
      expect(result.scalar_changes).toMatchObject([{ field: 'release_year', after: 2000,
        evidence: { schema_version: schemaVersion } }]);
    }
    const unsupported = reference('Gemini', 'unsupported-schema', 2000);
    unsupported.schema_version = 'missing-metadata-schema-v3';
    const rejected = buildEnrichmentPreview(snapshot(), evidence({ scalars: { release_year: [unsupported] } }),
      decision({ scalars: { release_year: { value: unsupported.value, source: unsupported.source, response_digest: unsupported.response_digest } } }), definitions());
    expect(rejected.scalar_changes).toEqual([]);
    expect(rejected.diagnostics).toContainEqual(expect.objectContaining({ code: 'invalid_candidate', field: 'release_year' }));
  });

  it('allows Gemini-only null MBID evidence but rejects factual evidence without a real approved MBID', () => {
    const item = reference('Gemini', 'gemini-year', 1978);
    const ev = evidence({ mbid: null, scalars: { release_year: [item] } });
    const reviewed = decision({ mbid: null, scalars: { release_year: item } });
    expect(buildEnrichmentPreview(snapshot(), ev, reviewed, definitions()).scalar_changes)
      .toMatchObject([{ field: 'release_year', after: 1978, evidence: { source: 'Gemini' } }]);
    expect(() => buildEnrichmentPreview(snapshot(), { ...ev, scalars: { release_year: [reference('MusicBrainz', 'factual', 1978)] } }, reviewed, definitions()))
      .toThrow(/exact evidence MBID/);
  });

  it('requires explicit selection for conflicting BPM and key evidence and rejects absent selections', () => {
    for (const [field, a, b] of [['bpm', 100, 101], ['original_key', 'C', 'Gm']]) {
      const candidates = [reference('AcousticBrainz', `${field}-a`, a), reference('AcousticBrainz', `${field}-b`, b)];
      const ev = evidence({ scalars: { [field]: candidates } });
      const unresolved = buildEnrichmentPreview(snapshot(), ev, decision(), definitions());
      expect(unresolved.scalar_changes).toEqual([]);
      expect(unresolved.diagnostics).toContainEqual({ code: 'conflicting_evidence_unresolved', category: null, field, label: null });
      const selected = buildEnrichmentPreview(snapshot(), ev, decision({ scalars: { [field]: candidates[1] } }), definitions());
      expect(selected.scalar_changes).toContainEqual(expect.objectContaining({ field, after: b }));
      const absent = reference('AcousticBrainz', `${field}-foreign`, field === 'bpm' ? 102 : 'A');
      const foreign = buildEnrichmentPreview(snapshot(), ev, decision({ scalars: { [field]: absent } }), definitions());
      expect(foreign.scalar_changes).toEqual([]);
      expect(foreign.diagnostics).toContainEqual({ code: 'selection_not_in_evidence', category: null, field, label: null });
    }
  });

  it('maps valid keys only, fills whitespace-only keys, and leaves nonempty keys untouched', () => {
    const valid = reference('AcousticBrainz', 'key-valid', { key_key: 'F#', key_scale: 'minor' });
    const result = buildEnrichmentPreview(snapshot({ original_key: '   ' }), evidence({ scalars: { original_key: [valid] } }),
      decision({ scalars: { original_key: valid } }), definitions());
    expect(result.scalar_changes).toContainEqual(expect.objectContaining({ field: 'original_key', after: 'F#m' }));
    const populated = buildEnrichmentPreview(snapshot({ original_key: 'G' }), evidence({ scalars: { original_key: [valid] } }),
      decision({ scalars: { original_key: valid } }), definitions());
    expect(populated.scalar_changes).toEqual([]);
    for (const value of [{ key_key: 'H', key_scale: 'major' }, { key_key: 'C', key_scale: 'dorian' }, 'C major']) {
      const invalid = reference('AcousticBrainz', 'key-invalid', value);
      const rejected = buildEnrichmentPreview(snapshot(), evidence({ scalars: { original_key: [invalid] } }),
        decision({ scalars: { original_key: invalid } }), definitions());
      expect(rejected.scalar_changes).toEqual([]);
      expect(rejected.diagnostics).toContainEqual({ code: 'invalid_candidate', category: null, field: 'original_key', label: null });
    }
  });

  it('adds multiple reviewed Genre matches and cannot cross Genre evidence into Vibe', () => {
    const ev = evidence({ genres: [tagReference('MusicBrainz', 'hard', 'Hard Rock'), tagReference('MusicBrainz', 'country', 'Country')] });
    const result = buildEnrichmentPreview(snapshot(), ev, decision({ genres: ['Hard Rock', 'Country'], vibes: ['Country'] }), definitions());
    expect(result.tag_additions.genres.map(item => item.name)).toEqual(['Hard Rock', 'Country']);
    expect(result.tag_additions.vibes).toEqual([]);
    expect(result.diagnostics).toContainEqual({ code: 'tag_selection_not_in_evidence', category: 'Vibe', field: null, label: 'Country' });
  });

  it('fails closed on malformed or multiple existing primary flags', () => {
    for (const flag of ['true', 'false', null, undefined]) {
      const row = { id: 1, name: 'Classic Rock' };
      if (flag !== undefined) row.is_primary = flag;
      const result = buildEnrichmentPreview(snapshot({ genres: [row] }), evidence({
        scalars: { release_year: [reference('MusicBrainz', 'year', 1977)] },
        genres: [tagReference('Gemini', 'hard', 'Hard Rock')],
      }), decision({ scalars: { release_year: reference('MusicBrainz', 'year', 1977) }, genres: ['Hard Rock'], primaryGenre: 'Hard Rock' }), definitions());
      expect(result.diagnostics).toContainEqual({ code: 'invalid_existing_primary_state', category: 'Genre', field: null, label: null });
      expect(result.scalar_changes).toEqual([]);
      expect(result.tag_additions).toEqual({ genres: [], vibes: [] });
      expect(result.primary_proposal).toBeNull();
      expect(result.has_changes).toBe(false);
    }
    const multiple = buildEnrichmentPreview(snapshot({ genres: [
      { id: 1, name: 'Classic Rock', is_primary: true }, { id: 2, name: 'Hard Rock', is_primary: true },
    ] }), evidence(), decision(), definitions());
    expect(multiple.diagnostics).toContainEqual({ code: 'invalid_existing_primary_state', category: 'Genre', field: null, label: null });
    expect(multiple.has_changes).toBe(false);
  });

  it('orders complete previews deterministically across Unicode ties and reordered definitions', () => {
    const composed = 'é';
    const decomposed = 'e\u0301';
    const defs = { genres: [{ id: 20, name: '  Hard   Rock  ' }, { id: 21, name: ' Country  Classic ' }], vibes: [] };
    const ev = evidence({ genres: [tagReference('MusicBrainz', 'composed', composed),
      tagReference('MusicBrainz', 'decomposed', decomposed), tagReference('MusicBrainz', 'hard', 'hard rock'),
      tagReference('MusicBrainz', 'country', 'Country Classic')] });
    const reviewed = decision({ genres: ['hard rock', 'Country Classic'] });
    const first = buildEnrichmentPreview(snapshot(), ev, reviewed, defs);
    const reversed = buildEnrichmentPreview(snapshot(), evidence({ ...ev, genres: [...ev.genres].reverse() }), reviewed,
      { genres: [...defs.genres].reverse(), vibes: [] });
    expect(reversed).toEqual(first);
    expect(first.tag_additions.genres.map(item => item.name)).toEqual(['  Hard   Rock  ', ' Country  Classic ']);
    expect(first.diagnostics.filter(item => item.code === 'unknown_tag_definition').map(item => item.label)).toEqual(
      [composed, decomposed].sort((a, b) => a.localeCompare(b, 'en') || (a === b ? 0 : a < b ? -1 : 1)));
  });

  it('chooses a stable diagnostic label for equivalent reviewed Genre and Vibe labels', () => {
    const labels = ['Hard Rock', ' hard   rock '];
    const expectedLabel = [...labels].sort((left, right) =>
      left.localeCompare(right, 'en') || (left === right ? 0 : left < right ? -1 : 1))[0];
    for (const [category, field] of [['Genre', 'genres'], ['Vibe', 'vibes']]) {
      const makePreview = selected => buildEnrichmentPreview(snapshot(), evidence(),
        decision({ [field]: selected }), definitions());
      const first = makePreview(labels);
      const reversed = makePreview([...labels].reverse());
      expect(reversed).toEqual(first);
      expect(first.diagnostics).toContainEqual({
        code: 'tag_selection_not_in_evidence', category, field: null, label: expectedLabel,
      });
      expect(first.has_changes).toBe(false);
    }
  });

  it('preserves nested input objects and arrays deeply', () => {
    const song = snapshot({ nested: { arrays: [{ value: 1, children: ['a', { b: true }] }] } });
    const ev = evidence({ metadata: { nested: [{ response: ['private'] }] }, genres: [tagReference('Gemini', 'hard', 'Hard Rock')] });
    const reviewed = decision({ genres: ['Hard Rock'], metadata: { nested: { selected: [1, 2] } } });
    const defs = definitions({ genres: [{ id: 2, name: '  Hard   Rock ' }] });
    const before = structuredClone({ song, ev, reviewed, defs });
    buildEnrichmentPreview(song, ev, reviewed, defs);
    expect({ song, ev, reviewed, defs }).toEqual(before);
  });
});
