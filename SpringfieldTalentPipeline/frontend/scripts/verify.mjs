/**
 * Drives the running dev server in a real Chromium instance and asserts what actually rendered.
 *
 * This exists because "npm run build succeeded" says nothing about whether the app renders or
 * whether it can reach the API. Everything below is read out of the live DOM after React has
 * committed, and browser console errors and failed network requests are captured too - a page can
 * paint and still be quietly broken.
 *
 * Usage: node scripts/verify.mjs [baseUrl]
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE_URL = process.argv[2] ?? 'http://localhost:5173';
const SHOTS = new URL('../screenshots/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');

let failures = 0;
function check(label, condition, detail) {
  const mark = condition ? 'PASS' : 'FAIL';
  if (!condition) failures += 1;
  console.log(`  [${mark}] ${label}${detail ? ` — ${detail}` : ''}`);
}

const browser = await chromium.launch();
const page = await browser.newPage();

const consoleErrors = [];
const failedRequests = [];
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
const abortedRequests = [];
page.on('requestfailed', (req) => {
  const reason = req.failure()?.errorText ?? '';
  // React 19 StrictMode double-invokes effects in dev, so the cleanup's AbortController cancels
  // the first in-flight request by design. That surfaces as ERR_ABORTED and is not a defect.
  if (reason.includes('ERR_ABORTED')) {
    abortedRequests.push(req.url());
    return;
  }
  failedRequests.push(`${req.method()} ${req.url()} — ${reason}`);
});

mkdirSync(SHOTS, { recursive: true });

/**
 * Waits for a settled render.
 *
 * Waiting only for `[results|empty|error]` is not enough after a search: the previous render's
 * result list is still in the DOM at the moment of the click, so the selector matches immediately
 * and the assertions read the *old* list. Waiting for the loading indicator to detach is what
 * actually proves React has committed the new state.
 */
async function settle() {
  await page
    .waitForSelector('[data-testid="loading"]', { state: 'detached', timeout: 20000 })
    .catch(() => {});
  await page.waitForSelector('[data-testid="results"], [data-testid="empty"], [data-testid="error"]', {
    timeout: 20000,
  });
}

/** Types a query, waits for the API call it triggers, and waits for the result to be rendered. */
async function searchFor(term) {
  await page.getByTestId('search-input').fill(term);
  const responded = page.waitForResponse(
    (r) => r.url().includes('/api/candidates') && r.request().method() === 'GET',
    { timeout: 20000 },
  );
  await page.getByTestId('search-button').click();
  await responded;
  await settle();
}

console.log(`\nVerifying ${BASE_URL} in real Chromium\n`);

// 1. Initial load: empty query, which the API answers with the whole pool.
console.log('1. Initial load (empty query -> whole pool)');
const response = await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
check('page responded', response?.ok(), `HTTP ${response?.status()}`);
check('React mounted', (await page.locator('h1').count()) === 1, await page.locator('h1').innerText());
await settle();
const initialCount = await page.getByTestId('count').innerText();
const initialRows = await page.getByTestId('result-row').count();
check('count reported', /candidates/.test(initialCount), initialCount);
check('rows capped at 50, not 1182', initialRows === 50, `${initialRows} rows in DOM`);
await page.screenshot({ path: `${SHOTS}1-initial.png`, fullPage: false });

// 2. A real search against the live dataset.
console.log('\n2. Real search: "bartender"');
await searchFor('bartender');
const rows = await page.getByTestId('result-row').allInnerTexts();
check('results rendered', rows.length > 0, `${rows.length} rows`);
check(
  'Moe Szyslak present',
  rows.some((r) => r.includes('Moe Szyslak')),
  rows.find((r) => r.includes('Moe')) ?? 'not found',
);
check(
  'occupation rendered alongside name',
  rows.some((r) => /Bartender/i.test(r)),
  rows[0]?.replace(/\n/g, ' / '),
);
await page.screenshot({ path: `${SHOTS}2-search-bartender.png` });

// 3. A search that matches nothing must render empty, not crash.
console.log('\n3. No-match query: "zzznotacharacter"');
await searchFor('zzznotacharacter');
check('empty state shown', await page.getByTestId('empty').isVisible());
check('no result rows', (await page.getByTestId('result-row').count()) === 0);
check('app still alive (heading intact)', (await page.locator('h1').count()) === 1);
await page.screenshot({ path: `${SHOTS}3-no-results.png` });

// 4. Recovery: searching again after an empty result must work.
console.log('\n4. Recovery after empty result');
await searchFor('szyslak');
const recovered = await page.getByTestId('result-row').count();
check('results return after an empty search', recovered > 0, `${recovered} rows`);

// 5. Nothing broken behind the scenes.
console.log('\n5. Browser-level health');
check('no console errors', consoleErrors.length === 0, consoleErrors.join(' | ') || 'clean');
check('no failed requests', failedRequests.length === 0, failedRequests.join(' | ') || 'clean');
console.log(
  `  [info] ${abortedRequests.length} request(s) aborted by StrictMode's effect cleanup — expected in dev`,
);

await browser.close();
console.log(`\n${failures === 0 ? 'ALL CHECKS PASSED' : `${failures} CHECK(S) FAILED`}`);
console.log(`screenshots: ${SHOTS}\n`);
process.exit(failures === 0 ? 0 : 1);
