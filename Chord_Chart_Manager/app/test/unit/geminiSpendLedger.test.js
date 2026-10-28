import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const crypto = require('node:crypto');
const { MAX_REQUEST_MICRO_USD, MAX_SPEND_MICRO_USD, MODEL_ID, RECOVERY_SONG_IDS, createGeminiSpendLedger, verifyProviderLimits } =
  require('../../server/geminiSpendLedger.js');
const { createSongEnrichmentProviderClient } = require('../../server/songEnrichmentProviders.js');
const { reserveBeforeProviderAttempt } = require('../../scripts/acquire-song-enrichment-cohort.cjs');
const project = path.resolve(import.meta.dirname, '../../..');
const roots = [];
const config = { model: MODEL_ID, maxGeminiAttempts: 10, timeoutMs: 10_000, maxResponseBytes: 1_048_576,
  geminiMaxInputBytes: 16_384, geminiMaxOutputTokens: 1_024 };

afterEach(async () => { for (const root of roots.splice(0)) await fs.rm(root, { recursive: true, force: true }); });

async function ledgerRoot() {
  const root = await fs.mkdtemp(path.join(project, 'backups', '.gemini-spend-test-'));
  roots.push(root);
  return { root, ledgerPath: path.join(root, 'ledger-v1.json') };
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function digest(value) { return crypto.createHash('sha256').update(value).digest('hex'); }
async function writePriorCheckpoint(file, cohort, ids) {
  const checkpoint = { checkpoint_version: 1, state_machine_version: 3, binding: { cohort_id: cohort },
    songs: ids.map(song_id => ({ song_id, gemini_attempts: 1 })), gemini_attempts: ids.length };
  await fs.mkdir(path.dirname(file), { recursive: true });
  const bytes = JSON.stringify({ checkpoint, sha256: digest(canonical(checkpoint)) });
  await fs.writeFile(file, bytes);
  return digest(Buffer.from(bytes));
}
async function recoveryFixture(ledger) {
  const { root } = await ledgerRoot();
  const legacy = new Map([[736, 'legacy/736/checkpoint-v1.json'], [755, 'legacy/755/checkpoint-v1.json']]);
  const explicit = new Map([[735, 'mirrors/735/checkpoint-v1.json'], [737, 'mirrors/737/checkpoint-v1.json'],
    [738, 'mirrors/738/checkpoint-v1.json']]);
  for (const [id, file] of legacy) await writePriorCheckpoint(path.join(root, file), `legacy-${id}`, [id]);
  const spend = createGeminiSpendLedger({ ledgerPath: path.join(root, 'ledger-v1.json'), backupsRoot: root });
  await spend.initialize(config);
  const priorIds = {};
  for (const id of explicit.keys()) priorIds[id] = (await spend.reserve({ providerConfig: config,
    cohortId: `prior-${id}`, songId: id })).ledgerSha256;
  const savedLedger = JSON.parse(await fs.readFile(path.join(root, 'ledger-v1.json'), 'utf8'));
  for (const id of explicit.keys()) await writePriorCheckpoint(path.join(root, explicit.get(id)), `mirror-${id}`, [id]);
  const songs = RECOVERY_SONG_IDS.map(song_id => {
    const file = legacy.get(song_id) || explicit.get(song_id);
    const ledgerReservation = savedLedger.reservations.find(item => item.song_id === song_id);
    return { song_id, prior_reservation_id: ledgerReservation?.reservation_id || null,
      prior_sources: [{ path: file, sha256: null, attempts: 1,
        ...(ledgerReservation ? { mirror_reservation_id: ledgerReservation.reservation_id } : {}) }] };
  });
  for (const item of songs) item.prior_sources[0].sha256 = digest(await fs.readFile(path.join(root, item.prior_sources[0].path)));
  const authorization = { version: 1, cohort_id: 'lite-recovery-campaign-2026-10-27', songs };
  const authorizationSha256 = digest(Buffer.from(JSON.stringify(authorization)));
  const recoveryRunId = digest(Buffer.from('active-run'));
  return { root, spend, authorization, authorizationSha256, recoveryRunId, ledgerPath: path.join(root, 'ledger-v1.json') };
}

describe('durable Gemini spend ledger', () => {
  it('seeds from retained report reservations and charges each new maximum before dispatch', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const reportPath = path.join(root, 'retained', 'report-v1.json');
    await fs.mkdir(path.dirname(reportPath));
    await fs.writeFile(reportPath, JSON.stringify({ mode: 'explicit_gemini_missing_metadata_fallback',
      attempts: { total: 2, gemini: 2 } }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    const initialized = await ledger.initialize(config);
    expect(initialized).toMatchObject({ seededReservations: 2, reservedMicroUsd: 2 * MAX_REQUEST_MICRO_USD,
      remainingMicroUsd: MAX_SPEND_MICRO_USD - 2 * MAX_REQUEST_MICRO_USD });
    const reservation = await ledger.reserve({ providerConfig: config, cohortId: 'lite-batch-a', songId: 756 });
    expect(reservation).toMatchObject({ reservedMicroUsd: MAX_REQUEST_MICRO_USD,
      totalReservedMicroUsd: 3 * MAX_REQUEST_MICRO_USD });
    const saved = JSON.parse(await fs.readFile(ledgerPath, 'utf8'));
    expect(saved.reservations).toMatchObject([{ cohort_id: 'lite-batch-a', song_id: 756,
      maximum_cost_micro_usd: MAX_REQUEST_MICRO_USD, state: 'reserved_uncertain' }]);
    expect(JSON.stringify(saved)).not.toMatch(/api.?key|credential|raw_response|provider_response|lyrics|chart/i);
  });

  it('keeps an interrupted reservation charged when a new process reopens the ledger', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const first = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await first.initialize(config);
    await first.reserve({ providerConfig: config, cohortId: 'lite-interrupted', songId: 757 });
    // Simulate process interruption after the durable reservation and before dispatch acknowledgement.
    const resumed = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    const state = await resumed.initialize(config);
    expect(state).toMatchObject({ seededReservations: 0, reservedMicroUsd: MAX_REQUEST_MICRO_USD,
      remainingMicroUsd: MAX_SPEND_MICRO_USD - MAX_REQUEST_MICRO_USD });
    expect(JSON.parse(await fs.readFile(ledgerPath, 'utf8')).reservations[0].state).toBe('reserved_uncertain');
  });

  it('exposes retained song reservations and refuses a second reservation for the same song', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const reportPath = path.join(root, 'retained', 'report-v1.json');
    await fs.mkdir(path.dirname(reportPath));
    await fs.writeFile(reportPath, JSON.stringify({ mode: 'explicit_gemini_missing_metadata_fallback',
      selected_song_id: 755, attempts: { total: 1, gemini: 1 } }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await ledger.initialize(config);
    expect(await ledger.reservedSongIds()).toMatchObject({ songIds: [755], unattributedReservations: 0 });
    await expect(ledger.reserve({ providerConfig: config, cohortId: 'lite-replacement', songId: 755 }))
      .rejects.toMatchObject({ code: 'GEMINI_PRIOR_RESERVATION' });
    expect(JSON.parse(await fs.readFile(ledgerPath, 'utf8')).reservations).toHaveLength(0);
  });

  it('permits one hash-bound recovery reservation only for the five authorized historical reservations', async () => {
    const fixture = await recoveryFixture();
    const cohortId = fixture.authorization.cohort_id;
    expect(RECOVERY_SONG_IDS).toEqual([735, 736, 737, 738, 755]);
    await fixture.spend.validateRecoveryAuthorization({ authorization: fixture.authorization,
      authorizationSha256: fixture.authorizationSha256, recoveryRunId: fixture.recoveryRunId,
      cohortId, songIds: [...RECOVERY_SONG_IDS, 745, 746, 747, 748, 749, 750, 751, 752, 753, 754] });
    const activeCheckpoint = path.join(fixture.root, 'active-run', 'checkpoint-v1.json');
    const dispatched = [];
    for (const songId of RECOVERY_SONG_IDS) {
      await fixture.spend.reserve({ providerConfig: config, cohortId, songId,
        recoveryAuthorization: fixture.authorization, recoveryAuthorizationSha256: fixture.authorizationSha256,
        recoveryRunId: fixture.recoveryRunId });
      dispatched.push(songId);
      const checkpoint = { checkpoint_version: 1, state_machine_version: 3,
        binding: { cohort_id: cohortId, recovery_authorization_sha256: fixture.authorizationSha256,
          recovery_run_id: fixture.recoveryRunId },
        songs: dispatched.map(id => ({ song_id: id, gemini_attempts: 1 })), gemini_attempts: dispatched.length };
      await fs.mkdir(path.dirname(activeCheckpoint), { recursive: true });
      await fs.writeFile(activeCheckpoint, JSON.stringify({ checkpoint, sha256: digest(canonical(checkpoint)) }));
    }
    const saved = JSON.parse(await fs.readFile(fixture.ledgerPath, 'utf8'));
    expect(saved.reservations.filter(item => item.recovery_parent)).toHaveLength(5);
    expect(saved.reserved_micro_usd).toBe(10 * MAX_REQUEST_MICRO_USD);
    expect(saved.reservations.filter(item => item.recovery_parent).every(item =>
      item.recovery_authorization_sha256 === fixture.authorizationSha256 && item.recovery_run_id === fixture.recoveryRunId)).toBe(true);
    await expect(fixture.spend.reserve({ providerConfig: config, cohortId, songId: 735,
      recoveryAuthorization: fixture.authorization, recoveryAuthorizationSha256: fixture.authorizationSha256,
      recoveryRunId: fixture.recoveryRunId }))
      .rejects.toMatchObject({ code: 'GEMINI_PRIOR_RESERVATION' });
    await fixture.spend.reserve({ providerConfig: config, cohortId, songId: 745,
      recoveryAuthorization: fixture.authorization, recoveryAuthorizationSha256: fixture.authorizationSha256,
      recoveryRunId: fixture.recoveryRunId });
    await expect(fixture.spend.reserve({ providerConfig: config, cohortId, songId: 745,
      recoveryAuthorization: fixture.authorization, recoveryAuthorizationSha256: fixture.authorizationSha256,
      recoveryRunId: fixture.recoveryRunId }))
      .rejects.toMatchObject({ code: 'GEMINI_PRIOR_RESERVATION' });
  });

  it('refuses a broadened, stale, or multiply-spent recovery authorization', async () => {
    const fixture = await recoveryFixture();
    const cohortId = fixture.authorization.cohort_id;
    const broad = structuredClone(fixture.authorization);
    broad.songs[0].song_id = 756;
    await expect(fixture.spend.validateRecoveryAuthorization({ authorization: broad,
      authorizationSha256: digest(Buffer.from(JSON.stringify(broad))), recoveryRunId: fixture.recoveryRunId,
      cohortId, songIds: [...RECOVERY_SONG_IDS, 756] }))
      .rejects.toMatchObject({ code: 'GEMINI_RECOVERY_AUTHORIZATION_INVALID' });
    const stale = structuredClone(fixture.authorization);
    stale.songs[0].prior_sources[0].sha256 = 'f'.repeat(64);
    await expect(fixture.spend.validateRecoveryAuthorization({ authorization: stale,
      authorizationSha256: digest(Buffer.from(JSON.stringify(stale))), recoveryRunId: fixture.recoveryRunId,
      cohortId, songIds: RECOVERY_SONG_IDS }))
      .rejects.toMatchObject({ code: 'GEMINI_RECOVERY_AUTHORIZATION_INVALID' });
    const ledger = JSON.parse(await fs.readFile(fixture.ledgerPath, 'utf8'));
    const first = ledger.reservations.find(item => item.song_id === 735);
    const second = { ...first, reservation_id: '00000000-0000-4000-8000-000000000099' };
    ledger.reservations.push(second);
    ledger.reserved_micro_usd += MAX_REQUEST_MICRO_USD;
    delete ledger.sha256;
    ledger.sha256 = digest(canonical(ledger));
    await fs.writeFile(fixture.ledgerPath, JSON.stringify(ledger));
    await expect(fixture.spend.validateRecoveryAuthorization({ authorization: fixture.authorization,
      authorizationSha256: fixture.authorizationSha256, recoveryRunId: fixture.recoveryRunId,
      cohortId, songIds: RECOVERY_SONG_IDS }))
      .rejects.toMatchObject({ code: 'GEMINI_RECOVERY_AUTHORIZATION_INVALID' });
  });

  it('marks seeded spend without a verifiable song identity as unattributed', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const reportPath = path.join(root, 'retained', 'report-v1.json');
    await fs.mkdir(path.dirname(reportPath));
    await fs.writeFile(reportPath, JSON.stringify({ mode: 'explicit_gemini_missing_metadata_fallback',
      attempts: { total: 1, gemini: 1 } }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await ledger.initialize(config);
    expect(await ledger.reservedSongIds()).toMatchObject({ songIds: [], unattributedReservations: 1 });
  });

  it('reserves spend before checkpoint attempt, then dispatches without self-matching as prior', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await ledger.initialize(config);
    const events = [];
    const checkpointPath = path.join(root, 'active', 'checkpoint-v1.json');
    const client = createSongEnrichmentProviderClient({
      cache: { get: async () => null, set: async () => true },
      config: { ...config, maxAttempts: 1, maxGeminiAttempts: 1, maxRetryAfterMs: 5_000, retryDelayMs: 1_000,
        musicBrainzMinimumIntervalMs: 1_000, musicBrainzUserAgent: 'UnitTest/1.0 (offline)' },
      beforeAttempt: intent => reserveBeforeProviderAttempt({ intent, songId: 755, cohortId: 'lite-ordering',
        providerConfig: config, spendLedger: ledger, reserveRunnerAttempt: async () => {
          events.push('checkpoint-reserved');
          const checkpoint = { checkpoint_version: 1, state_machine_version: 3,
            binding: { cohort_id: 'lite-ordering' }, songs: [{ song_id: 755, gemini_attempts: 1 }], gemini_attempts: 1 };
          const envelope = { checkpoint, sha256: crypto.createHash('sha256').update(canonical(checkpoint)).digest('hex') };
          await fs.mkdir(path.dirname(checkpointPath), { recursive: true });
          await fs.writeFile(checkpointPath, JSON.stringify(envelope));
          return true;
        } }),
      transport: async () => { events.push('transport'); throw new Error('synthetic network-disabled transport'); },
    });
    await client.requestGeminiTags({ title: 'Synthetic Song', artist: 'Synthetic Artist', context: {}, requestedFields: ['release_year'],
      definitions: { genres: [], vibes: [] }, apiKey: 'synthetic-key' });
    expect(events).toEqual(['checkpoint-reserved', 'transport']);
    expect(JSON.parse(await fs.readFile(ledgerPath, 'utf8')).reservations).toMatchObject([
      { cohort_id: 'lite-ordering', song_id: 755, state: 'reserved_uncertain' },
    ]);
    await expect(ledger.reserve({ providerConfig: config, cohortId: 'lite-replacement', songId: 755 }))
      .rejects.toMatchObject({ code: 'GEMINI_PRIOR_RESERVATION' });
  });

  it('migrates the prior policy shape and reconciles legacy reports without resetting reservations', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const checkpointPath = path.join(root, 'retained', 'checkpoint-v1.json');
    await fs.mkdir(path.dirname(checkpointPath), { recursive: true });
    const state = { checkpoint_version: 1, state_machine_version: 3, binding: { cohort_id: 'retained-cohort' },
      songs: Array.from({ length: 16 }, (_, index) => ({ song_id: 800 + index, gemini_attempts: 1 })), gemini_attempts: 16 };
    await fs.writeFile(checkpointPath, JSON.stringify({ checkpoint: state,
      sha256: crypto.createHash('sha256').update(canonical(state)).digest('hex') }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    expect(await ledger.initialize(config)).toMatchObject({ seededReservations: 16 });
    await ledger.reserve({ providerConfig: config, cohortId: 'already-reserved', songId: 756 });
    const beforeMigration = JSON.parse(await fs.readFile(ledgerPath, 'utf8'));
    delete beforeMigration.policy.pricing_valid_through;
    const beforePayload = { ...beforeMigration };
    delete beforePayload.sha256;
    beforeMigration.sha256 = crypto.createHash('sha256').update(canonical(beforePayload)).digest('hex');
    await fs.writeFile(ledgerPath, JSON.stringify(beforeMigration));
    const report = (type, gemini, id) => ({ report_version: 1, report_type: type, song: { id },
      limits: { model: MODEL_ID, attempts_allowed: 1, retries_allowed: 0, cache: false,
        max_input_bytes: 16_384, max_output_tokens: 1_024, timeout_ms: 10_000, max_response_bytes: 1_048_576 },
      result: { status: 'invalid_response', attempts: { total: gemini, gemini } } });
    const paths = [
      ['legacy/vibe-a/report-v1.json', report('gemini_vibe_only_preview', 1, 900)],
      ['legacy/vibe-b/report-v1.json', report('gemini_vibe_only_preview', 1, 901)],
      ['legacy/vibe-c/report-v1.json', report('gemini_vibe_only_preview', 1, 902)],
      ['legacy/genre/report-v1.json', report('gemini_genre_only_preview', 1, 903)],
    ];
    for (const [relative, value] of paths) {
      const file = path.join(root, relative);
      await fs.mkdir(path.dirname(file), { recursive: true });
      await fs.writeFile(file, JSON.stringify(value));
    }

    const migrated = await createGeminiSpendLedger({ ledgerPath, backupsRoot: root }).initialize(config);
    expect(migrated).toMatchObject({ seededReservations: 20,
      reservedMicroUsd: 21 * MAX_REQUEST_MICRO_USD });
    const saved = JSON.parse(await fs.readFile(ledgerPath, 'utf8'));
    expect(saved.seed_sources).toHaveLength(5);
    expect(saved.seed_sources.some(source => source.path === 'retained/checkpoint-v1.json' && source.reservations === 16)).toBe(true);
    expect(saved.seed_sources.filter(source => source.path.startsWith('legacy/'))).toHaveLength(4);
    expect(saved.reservations).toHaveLength(1);
    expect(saved.reservations[0].cohort_id).toBe('already-reserved');
    expect(saved.policy.pricing_valid_through).toBe('2026-10-28');
    expect(await createGeminiSpendLedger({ ledgerPath, backupsRoot: root }).initialize(config))
      .toMatchObject({ seededReservations: 20, reservedMicroUsd: 21 * MAX_REQUEST_MICRO_USD });
    expect(await createGeminiSpendLedger({ ledgerPath, backupsRoot: root }).reservedSongIds())
      .toMatchObject({ songIds: [756, ...Array.from({ length: 16 }, (_, index) => 800 + index), 900, 901, 902, 903],
        unattributedReservations: 0 });
  });

  it('fails closed on malformed legacy nested attempt accounting', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const reportPath = path.join(root, 'legacy', 'report-v1.json');
    await fs.mkdir(path.dirname(reportPath));
    await fs.writeFile(reportPath, JSON.stringify({ report_version: 1, report_type: 'gemini_vibe_only_preview',
      limits: { model: MODEL_ID, attempts_allowed: 1, retries_allowed: 0, cache: false,
        max_input_bytes: 16_384, max_output_tokens: 1_024, timeout_ms: 10_000, max_response_bytes: 1_048_576 },
      result: { status: 'ok', attempts: { total: 1, gemini: 0 } } }));
    await expect(createGeminiSpendLedger({ ledgerPath, backupsRoot: root }).initialize(config))
      .rejects.toMatchObject({ code: 'GEMINI_SPEND_LEDGER_INVALID' });
  });

  it('refuses a reservation that would cross the two dollar ceiling', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const calls = Math.floor(MAX_SPEND_MICRO_USD / MAX_REQUEST_MICRO_USD) - 1;
    const source = path.join(root, 'prior', 'report-v1.json');
    await fs.mkdir(path.dirname(source));
    await fs.writeFile(source, JSON.stringify({ mode: 'explicit_gemini_missing_metadata_fallback',
      attempts: { total: calls, gemini: calls } }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    const seeded = await ledger.initialize(config);
    expect(seeded.reservedMicroUsd).toBe(calls * MAX_REQUEST_MICRO_USD);
    await ledger.reserve({ providerConfig: config, cohortId: 'lite-ceiling', songId: 758 });
    await expect(ledger.reserve({ providerConfig: config, cohortId: 'lite-ceiling', songId: 759 }))
      .rejects.toMatchObject({ code: 'GEMINI_SPEND_LIMIT' });
  });

  it('fails closed for unverified provider limits, a busy lock, and altered ledger bytes', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await expect(ledger.initialize({ ...config, model: 'unverified-model' }))
      .rejects.toMatchObject({ code: 'GEMINI_SPEND_POLICY_UNVERIFIED' });
    expect(() => verifyProviderLimits(config, { today: '2026-10-26' }))
      .toThrowError('GEMINI_SPEND_POLICY_UNVERIFIED');
    expect(() => verifyProviderLimits(config, { today: '2026-10-27' })).not.toThrow();
    expect(() => verifyProviderLimits(config, { today: '2026-10-28' })).not.toThrow();
    expect(() => verifyProviderLimits(config, { today: '2026-10-29' }))
      .toThrowError('GEMINI_SPEND_POLICY_UNVERIFIED');
    await ledger.initialize(config);
    await fs.writeFile(`${ledgerPath}.lock`, '{}', { flag: 'wx' });
    await expect(ledger.reserve({ providerConfig: config, cohortId: 'lite-locked', songId: 760 }))
      .rejects.toMatchObject({ code: 'GEMINI_SPEND_LEDGER_LOCKED' });
    await fs.unlink(`${ledgerPath}.lock`);
    const altered = JSON.parse(await fs.readFile(ledgerPath, 'utf8'));
    altered.reserved_micro_usd = 1;
    await fs.writeFile(ledgerPath, JSON.stringify(altered));
    await expect(ledger.reserve({ providerConfig: config, cohortId: 'lite-tampered', songId: 761 }))
      .rejects.toMatchObject({ code: 'GEMINI_SPEND_LEDGER_INVALID' });
  });

  it('renews only the verified pricing window while preserving all historical reservations and totals', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const source = path.join(root, 'retained', 'report-v1.json');
    await fs.mkdir(path.dirname(source));
    await fs.writeFile(source, JSON.stringify({ mode: 'explicit_gemini_missing_metadata_fallback',
      selected_song_id: 755, attempts: { total: 2, gemini: 2 } }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await ledger.initialize(config);
    await ledger.reserve({ providerConfig: config, cohortId: 'historical', songId: 756 });
    const before = JSON.parse(await fs.readFile(ledgerPath, 'utf8'));
    const historical = { seed_sources: before.seed_sources, seeded_reservations: before.seeded_reservations,
      reservations: before.reservations, reserved_micro_usd: before.reserved_micro_usd };
    before.policy.pricing_checked_on = '2026-10-26';
    before.policy.pricing_valid_through = '2026-10-26';
    const priorPayload = { ...before };
    delete priorPayload.sha256;
    before.sha256 = crypto.createHash('sha256').update(canonical(priorPayload)).digest('hex');
    await fs.writeFile(ledgerPath, JSON.stringify(before));

    const renewed = await createGeminiSpendLedger({ ledgerPath, backupsRoot: root }).initialize(config);
    const after = JSON.parse(await fs.readFile(ledgerPath, 'utf8'));
    expect(MAX_REQUEST_MICRO_USD).toBe(5_632);
    expect(renewed).toMatchObject({ seededReservations: 2, reservedMicroUsd: 3 * MAX_REQUEST_MICRO_USD,
      remainingMicroUsd: MAX_SPEND_MICRO_USD - 3 * MAX_REQUEST_MICRO_USD });
    expect(after.policy).toMatchObject({ pricing_source: 'https://ai.google.dev/gemini-api/docs/pricing',
      pricing_checked_on: '2026-10-27', pricing_valid_through: '2026-10-28',
      input_price_micro_usd_per_million_tokens: 250_000, output_price_micro_usd_per_million_tokens: 1_500_000,
      max_input_bytes: 16_384, reserved_input_token_ceiling: 16_384, max_output_tokens: 1_024,
      max_response_bytes: 1_048_576, timeout_ms: 10_000, maximum_spend_micro_usd: 2_000_000,
      maximum_request_micro_usd: 5_632 });
    expect({ seed_sources: after.seed_sources, seeded_reservations: after.seeded_reservations,
      reservations: after.reservations, reserved_micro_usd: after.reserved_micro_usd }).toEqual(historical);
    expect(after.sha256).not.toBe(before.sha256);
  });

  it('refuses a retained source whose hash changed after ledger initialization', async () => {
    const { root, ledgerPath } = await ledgerRoot();
    const source = path.join(root, 'retained', 'report-v1.json');
    await fs.mkdir(path.dirname(source));
    await fs.writeFile(source, JSON.stringify({ mode: 'explicit_gemini_missing_metadata_fallback',
      attempts: { total: 1, gemini: 1 } }));
    const ledger = createGeminiSpendLedger({ ledgerPath, backupsRoot: root });
    await ledger.initialize(config);
    await fs.appendFile(source, ' ');
    await expect(ledger.reserve({ providerConfig: config, cohortId: 'lite-drift', songId: 762 }))
      .rejects.toMatchObject({ code: 'GEMINI_SPEND_LEDGER_INVALID' });
  });
});
