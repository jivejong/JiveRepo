import { defineConfig, devices } from '@playwright/test';

if (process.env.CCM_T11_ISOLATED !== '1') {
  throw new Error('Refusing to run browser tests without the isolated T11 harness. Use npm.cmd run test:t11; this guards the live chords database from fixture writes.');
}

export default defineConfig({
  testDir: './test/e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    ...(process.env.E2E_SYSTEM_CHROME === '1' ? { channel: 'chrome' } : {}),
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
