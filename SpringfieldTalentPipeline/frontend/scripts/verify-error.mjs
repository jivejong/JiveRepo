import { chromium } from 'playwright';
const SHOTS = new URL('../screenshots/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const browser = await chromium.launch();

// Case A: backend unreachable (connection refused at the proxy)
let page = await browser.newPage();
await page.route('**/api/**', (route) => route.abort('connectionrefused'));
await page.goto('http://localhost:5173', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-testid="error"]', { timeout: 20000 });
console.log('  [A] backend unreachable ->', (await page.getByTestId('error').innerText()).replace(/\s+/g, ' '));
await page.screenshot({ path: `${SHOTS}4-error-unreachable.png` });
await page.close();

// Case B: backend returns 500
page = await browser.newPage();
await page.route('**/api/**', (route) => route.fulfill({ status: 500, body: 'boom' }));
await page.goto('http://localhost:5173', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-testid="error"]', { timeout: 20000 });
console.log('  [B] backend 500        ->', (await page.getByTestId('error').innerText()).replace(/\s+/g, ' '));
await page.screenshot({ path: `${SHOTS}5-error-500.png` });

// and it must recover once the backend comes back
await page.unroute('**/api/**');
await page.getByTestId('search-input').fill('szyslak');
await page.getByTestId('search-button').click();
await page.waitForSelector('[data-testid="results"]', { timeout: 20000 });
const rows = await page.getByTestId('result-row').count();
console.log(`  [C] recovers after error -> ${rows} rows`);
await browser.close();
console.log(rows > 0 ? '\nERROR-PATH CHECKS PASSED' : '\nRECOVERY FAILED');
