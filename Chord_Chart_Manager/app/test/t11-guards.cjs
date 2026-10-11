const assert = require('node:assert/strict');

function requireIsolatedTarget({ isolated, baseUrl, runId }) {
  assert.equal(isolated, '1', 'T11 tests must be launched by the isolated harness.');
  assert.ok(runId, 'T11 harness run identity is required.');
  assert.ok(baseUrl, 'An explicit isolated test server URL is required.');
  const target = new URL(baseUrl);
  assert.ok(['http:', 'https:'].includes(target.protocol), 'T11 target must use HTTP.');
  assert.ok(['127.0.0.1', '::1', 'localhost'].includes(target.hostname),
    'T11 target must be a loopback test server.');
  assert.notEqual(target.port || (target.protocol === 'https:' ? '443' : '80'), '3000',
    'T11 refuses the application port used by the protected main stack.');
  return target;
}

async function verifyHarnessIdentity({ fetchImpl = fetch, baseUrl, runId }) {
  const response = await fetchImpl(new URL('/__t11_identity', baseUrl), {
    headers: { 'x-ccm-t11-run': runId }, signal: AbortSignal.timeout(3000),
  });
  assert.equal(response.status, 200, 'HTTP target is not the active T11 harness server.');
  const identity = await response.json();
  assert.equal(identity.runId, runId, 'HTTP target belongs to a different T11 run.');
  assert.equal(identity.database, 'ccm_test', 'T11 HTTP server is not connected to ccm_test.');
  return identity;
}

module.exports = { requireIsolatedTarget, verifyHarnessIdentity };
