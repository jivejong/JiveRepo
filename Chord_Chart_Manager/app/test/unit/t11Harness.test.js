import { createRequire } from 'node:module';
import { EventEmitter } from 'node:events';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { requireIsolatedTarget, verifyHarnessIdentity } = require('../t11-guards.cjs');
const { bounded, runCleanupStages, startServerBounded, runWithCleanup } = require('../t11-lifecycle.cjs');

function fakeServer() {
  const server = new EventEmitter();
  server.listening = false;
  server.closeCalls = 0;
  server.close = callback => {
    server.closeCalls++;
    server.listening = false;
    callback?.();
  };
  server.closeAllConnections = vi.fn();
  return server;
}

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

  it('starts successfully when listening fires immediately and always runs final cleanup', async () => {
    const server = fakeServer();
    let cleanupCount = 0;
    await runWithCleanup(async () => {
      await startServerBounded(server, () => {
        server.listening = true;
        server.emit('listening');
      }, 100);
      expect(server.listenerCount('listening')).toBe(0);
      expect(server.listenerCount('error')).toBe(0);
    }, async () => { cleanupCount++; });
    expect(cleanupCount).toBe(1);
  });

  it('routes emitted and synchronous startup errors through owned cleanup', async () => {
    for (const start of [
      server => queueMicrotask(() => server.emit('error', new Error('bind rejected'))),
      () => { throw new Error('listen threw'); },
    ]) {
      const server = fakeServer();
      let ownedScratch = true;
      const error = await runWithCleanup(
        () => startServerBounded(server, () => start(server), 100),
        async () => { ownedScratch = false; },
      ).catch(value => value);
      expect(error.message).toMatch(/bind rejected|listen threw/);
      expect(ownedScratch).toBe(false);
      expect(server.listenerCount('listening')).toBe(0);
      expect(server.listenerCount('error')).toBe(0);
    }
  });

  it('times out a missing startup event, cleans owned state, and closes a late server before it can write', async () => {
    const server = fakeServer();
    let acceptingRequests = false;
    let ownedScratch = true;
    let absenceVerified = false;
    const cleanupOrder = [];
    const error = await runWithCleanup(
      () => startServerBounded(server, () => {}, 15),
      async () => {
        acceptingRequests = false;
        await runCleanupStages([
          ['server shutdown', async () => { cleanupOrder.push('server'); }],
          ['owned scratch drop', async () => { ownedScratch = false; cleanupOrder.push('drop'); }],
          ['owned scratch absence', async () => {
            absenceVerified = !ownedScratch;
            expect(absenceVerified).toBe(true);
            cleanupOrder.push('absence');
          }],
        ]);
      },
    ).catch(value => value);

    expect(error.message).toMatch(/startup timed out after 15 ms/);
    expect(cleanupOrder).toEqual(['server', 'drop', 'absence']);
    expect(ownedScratch).toBe(false);
    expect(absenceVerified).toBe(true);
    expect(acceptingRequests).toBe(false);

    server.listening = true;
    server.emit('listening');
    expect(server.closeCalls).toBe(1);
    expect(server.listening).toBe(false);
    expect(acceptingRequests).toBe(false);
    expect(server.listenerCount('listening')).toBe(0);
    expect(server.listenerCount('error')).toBe(0);
  });

  it('reports startup failure together with owned cleanup failure', async () => {
    const server = fakeServer();
    const error = await runWithCleanup(
      () => startServerBounded(server, () => { throw new Error('startup fault'); }, 100),
      async () => { throw new Error('scratch absence check failed'); },
    ).catch(value => value);
    expect(error).toBeInstanceOf(AggregateError);
    expect(error.errors.map(item => item.message)).toEqual([
      'startup fault', 'scratch absence check failed',
    ]);
  });
});
