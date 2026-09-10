/**
 * Offer stage, driven in real Chromium against the live backend.
 *
 * Four cases: an in-range offer accepted through to HIRED, a below-range offer declined to
 * WITHDRAWN, a candidate whose occupation resolves to a real SOC code, and one whose occupation
 * cannot be mapped at all and must fall back to the aggregate row without crashing.
 *
 * Waits follow the same rule as the earlier suites - the API response the step triggered, then the
 * loading indicator detaching - never a selector alone.
 *
 * Usage: node scripts/verify-offer.mjs [baseUrl]
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE_URL = process.argv[2] ?? 'http://localhost:5173';
const SHOTS = new URL('../screenshots/offer/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const AI_TIMEOUT = 120000;

let failures = 0;
function check(label, condition, detail) {
  if (!condition) failures += 1;
  console.log(`  [${condition ? 'PASS' : 'FAIL'}] ${label}${detail ? ` — ${detail}` : ''}`);
}

const browser = await chromium.launch();
mkdirSync(SHOTS, { recursive: true });

/**
 * Runs the flow up to the offer form for one candidate, submits an amount, and returns the
 * rendered outcome.
 */
async function runOffer({ keywords, candidateName, amount, label }) {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && !/status of 409/.test(m.text())) consoleErrors.push(m.text());
  });

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('req-title').fill(`${label} ${Date.now()}`);
  await page.getByTestId('req-keywords').fill(keywords);
  await page.getByTestId('req-submit').click();
  await page.waitForSelector('[data-testid="matches-list"]', { timeout: 20000 });

  const row = page.getByTestId('match-row').filter({ hasText: candidateName });
  if ((await row.count()) === 0) {
    await page.close();
    return { missing: true, consoleErrors };
  }
  await row.first().getByRole('button', { name: 'Apply' }).click();

  // The fit score fires automatically and is a real Groq call; wait it out before continuing.
  await page.waitForSelector('[data-testid="fit-result"], [data-testid="fit-error"]', { timeout: AI_TIMEOUT });

  await page.getByTestId('hire').click();
  await page.waitForSelector('[data-testid="offer-form"]', { timeout: 60000 });
  const stageAtPause = await page.getByTestId('current-stage').innerText();

  await page.getByTestId('offer-amount').fill(String(amount));
  const responded = page.waitForResponse((r) => r.url().includes('/offer') && r.request().method() === 'POST', {
    timeout: 60000,
  });
  await page.getByTestId('offer-submit').click();
  const response = await responded;
  await page.waitForSelector('[data-testid="offer-outcome"]', { timeout: 30000 });

  const outcome = {
    status: response.status(),
    stageAtPause,
    decision: await page.getByTestId('offer-decision').innerText(),
    rationale: await page.getByTestId('offer-rationale').innerText(),
    occupation: await page.getByTestId('offer-occupation').innerText(),
    range: await page.getByTestId('offer-range').innerText(),
    finalStage: await page.getByTestId('current-stage').innerText(),
    formGone: (await page.getByTestId('offer-form').count()) === 0,
    consoleErrors,
    page,
  };
  return outcome;
}

console.log(`\nOffer stage against ${BASE_URL}\n`);

// --- 1. In range -> accepted -> HIRED ----------------------------------------------------
console.log('1. Moe Szyslak, $45,000 (inside the Bartenders band)');
const accepted = await runOffer({
  keywords: 'bartender tavern bar drinks',
  candidateName: 'Moe Szyslak',
  amount: 45000,
  label: 'Offer accept',
});
check('walk paused at OFFER rather than auto-completing', accepted.stageAtPause === 'OFFER', accepted.stageAtPause);
check('POST /offer -> 201', accepted.status === 201, `HTTP ${accepted.status}`);
check('decision is ACCEPTED', /ACCEPTED/.test(accepted.decision), accepted.decision);
check('landed on HIRED', accepted.finalStage === 'HIRED', accepted.finalStage);
check('resolved a real SOC code, not the fallback', /35-3011/.test(accepted.occupation) && !/FALLBACK|fallback/.test(accepted.occupation), accepted.occupation);
check('rationale names the occupation and the band', /Bartenders/.test(accepted.rationale) && /within/.test(accepted.rationale), accepted.rationale);
check('form is gone after submission (one-shot)', accepted.formGone);
await accepted.page.screenshot({ path: `${SHOTS}1-accepted.png`, fullPage: true });
await accepted.page.close();

// --- 2. Below range -> declined -> WITHDRAWN ---------------------------------------------
console.log('\n2. Moe Szyslak, $15,000 (below the band)');
const declined = await runOffer({
  keywords: 'bartender tavern bar drinks',
  candidateName: 'Moe Szyslak',
  amount: 15000,
  label: 'Offer decline',
});
check('decision is DECLINED', /DECLINED/.test(declined.decision), declined.decision);
check('landed on WITHDRAWN, not REJECTED', declined.finalStage === 'WITHDRAWN', declined.finalStage);
check('rationale says the offer was below the band', /below/.test(declined.rationale), declined.rationale);
check('same wage range as the accepted case', declined.range === accepted.range, declined.range);
await declined.page.screenshot({ path: `${SHOTS}2-declined.png`, fullPage: true });
await declined.page.close();

// --- 3. Unmappable occupation -> aggregate fallback --------------------------------------
console.log('\n3. Marge Simpson, occupation "Unemployed" (no occupation title can match)');
const fallback = await runOffer({
  keywords: 'unemployed',
  candidateName: 'Marge Simpson',
  amount: 60000,
  label: 'Offer fallback',
});
if (fallback.missing) {
  check('Marge appeared in the match list', false, 'not matched by those keywords');
} else {
  check('POST /offer -> 201 (did not crash on an unmappable occupation)', fallback.status === 201, `HTTP ${fallback.status}`);
  check('fell back to the aggregate row', /00-0000/.test(fallback.occupation), fallback.occupation);
  check('fallback is labelled in the UI', /fallback/i.test(fallback.occupation), fallback.occupation);
  check('rationale says "all occupations"', /all occupations/i.test(fallback.rationale), fallback.rationale);
  check('still reached a terminal stage', ['HIRED', 'WITHDRAWN'].includes(fallback.finalStage), fallback.finalStage);
  await fallback.page.screenshot({ path: `${SHOTS}3-fallback.png`, fullPage: true });
  await fallback.page.close();
}

// --- 4. Browser health across all runs ----------------------------------------------------
console.log('\n4. Browser-level health');
const allErrors = [...accepted.consoleErrors, ...declined.consoleErrors, ...(fallback.consoleErrors ?? [])];
check('no unexpected console errors', allErrors.length === 0, allErrors.join(' | ') || 'clean');

await browser.close();
console.log(`\n${failures === 0 ? 'ALL CHECKS PASSED' : `${failures} CHECK(S) FAILED`}`);
console.log(`screenshots: ${SHOTS}\n`);
process.exit(failures === 0 ? 0 : 1);
