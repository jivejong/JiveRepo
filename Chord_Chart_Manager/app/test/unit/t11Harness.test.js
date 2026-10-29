import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { requireIsolatedTarget, verifyHarnessIdentity } = require('../t11-guards.cjs');

describe('T11 harness safety', () => {
  it('NFR-010 rejects missing URL, stale isolation flag, and main-stack URL before writes', async () => {
    expect(() => requireIsolatedTarget({ runId: 'current' })).toThrow(/isolated harness/);
    expect(() => requireIsolatedTarget({ isolated: '1', runId: 'current' })).toThrow(/explicit isolated test server URL/);
    expect(() => requireIsolatedTarget({ isolated: '1', runId: 'current', baseUrl: 'http://localhost:3000' })).toThrow(/application port/);
    expect(() => requireIsolatedTarget({ isolated: '1', runId: 'stale', baseUrl: 'http://127.0.0.1:45671' })).not.toThrow();
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ runId: 'new-run', database: 'ccm_test' }), { status: 200 }));
    await expect(verifyHarnessIdentity({ fetchImpl, baseUrl: 'http://127.0.0.1:45671', runId: 'stale' })).rejects.toThrow(/different T11 run/);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it('NFR-010 requires the server identity to confirm the scratch database', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ runId: 'run-1', database: 'chords' }), { status: 200 }));
    await expect(verifyHarnessIdentity({ fetchImpl, baseUrl: 'http://127.0.0.1:45671', runId: 'run-1' })).rejects.toThrow(/not connected to ccm_test/);
  });
});
