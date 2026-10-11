const { verifyHarnessIdentity } = require('./t11-guards.cjs');

module.exports = async () => {
  await verifyHarnessIdentity({ baseUrl: process.env.E2E_BASE_URL, runId: process.env.CCM_T11_RUN_ID });
};
