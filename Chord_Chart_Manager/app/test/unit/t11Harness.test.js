import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { requireIsolatedTarget, verifyHarnessIdentity } = require('../t11-guards.cjs');
const { bounded, runCleanupStages } = require('../t11-lifecycle.cjs');

describe('T11 harness safety', () => {
  it('rejects missing URL, stale isolation flag, and main-stack URL before writes', async () => {
    expect(() => requireIsolatedTarget({ runId: 'current' })).toThrow(/isolated harness/);
    expect(() => requireIsolatedTarget({ isolated: '1', runId: 'current' })).toThrow(/explicit isolated test server URL/);
    expect(() => requireIsolatedTarget({ isolated: '1', runId: 'current', baseUrl: 'http://localhost:3000' })).toThrow(/application port/);
    expect(() => requireIsolatedTarget({ isolated: '1', runId: 'stale', baseUrl: 'http://127.0.0.1:45671' })).not.toThrow();
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ runId: 'new-run', database: 'ccm_test' }), { status: 200 }));
    await expect(verifyHarnessIdentity({ fetchImpl, baseUrl: 'http://127.0.0.1:45671', runId: 'stale' })).rejects.toThrow(/different T11 run/);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it('requires the server identity to confirm the scratch database', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ runId: 'run-1', database: 'chords' }), { status: 200 }));
    await expect(verifyHarnessIdentity({ fetchImpl, baseUrl: 'http://127.0.0.1:45671', runId: 'run-1' })).rejects.toThrow(/not connected to ccm_test/);
  });

  it('bounds a shutdown timeout and proceeds through later safe cleanup stages', async () => {
    const order = [];
    const error = await runCleanupStages([
      ['server close', () => bounded(() => new Promise(() => {}), 'server close', 10)],
      ['stop writes', async () => { order.push('fence'); }],
      ['drop owned scratch', async () => { order.push('drop'); }],
      ['preservation check', async () => { order.push('preserve'); }],
    ]).catch(value => value);
    expect(order).toEqual(['fence', 'drop', 'preserve']);
    expect(error).toBeInstanceOf(AggregateError);
    expect(error.errors[0].message).toMatch(/server close.*exceeded 10 ms/);
  });

  it('continues cleanup when close rejects and retains every failure', async () => {
    const order = [];
    const error = await runCleanupStages([
      ['pool close', async () => { throw new Error('rejected'); }],
      ['scratch absence', async () => { order.push('absence'); throw new Error('still exists'); }],
      ['main preservation', async () => { order.push('preservation'); }],
    ]).catch(value => value);
    expect(order).toEqual(['absence', 'preservation']);
    expect(error.errors.map(item => item.message)).toEqual([
      'pool close: rejected', 'scratch absence: still exists',
    ]);
  });
});
