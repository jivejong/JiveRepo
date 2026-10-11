import { defineConfig, devices } from '@playwright/test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
require('./test/t11-guards.cjs').requireIsolatedTarget({
  isolated: process.env.CCM_T11_ISOLATED,
  baseUrl: process.env.E2E_BASE_URL,
  runId: process.env.CCM_T11_RUN_ID,
});

export default defineConfig({
  globalSetup: './test/t11-global-setup.cjs',
  testDir: './test/e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL,
    ...(process.env.E2E_SYSTEM_CHROME === '1' ? { channel: 'chrome' } : {}),
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
