/**
 * Drives the whole Stage B flow in real Chromium against the live backend.
 *
 * Every wait is on a committed render, never on a selector alone - Stage A's failure was a
 * `waitForSelector` matching the *previous* render's list, so assertions read stale DOM. Here each
 * step waits for the API response that step triggers and then for its loading indicator to detach.
 *
 * The AI steps get long timeouts on purpose: they are real Gemini generations, not mocked.
 *
 * Usage: node scripts/verify-stage-b.mjs [baseUrl]
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE_URL = process.argv[2] ?? 'http://localhost:5173';
const SHOTS = new URL('../screenshots/stage-b/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const AI_TIMEOUT = 120000;

let failures = 0;
function check(label, condition, detail) {
  if (!condition) failures += 1;
  console.log(`  [${condition ? 'PASS' : 'FAIL'}] ${label}${detail ? ` — ${detail}` : ''}`);
}

const browser = await chromium.launch();
const page = await browser.newPage();
const consoleErrors = [];
const failedRequests = [];
const expectedConflicts = [];
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  // Chromium logs every non-2xx fetch as a console error. The 409 probe is commented out of the
  // UI for now, so none is expected - but if the probe comes back, this is where its console
  // entry is separated from real defects. Health check 8 asserts the count.
  if (/status of 409/.test(m.text())) {
    expectedConflicts.push(m.text());
    return;
  }
  consoleErrors.push(m.text());
});
page.on('requestfailed', (r) => {
  const reason = r.failure()?.errorText ?? '';
  if (!reason.includes('ERR_ABORTED')) failedRequests.push(`${r.url()} — ${reason}`);
});
/**
 * Every POST to /ai-profile, so the suite can assert the auto-trigger fires exactly once.
 * React StrictMode double-invokes effects in dev; without a ref guard this fired twice for one
 * application and the two generations raced to insert the single row the schema allows, surfacing
 * as a 500. Asserting on the rendered score alone would not have caught it - the UI shows a number
 * either way, because one of the two requests still succeeds.
 */
const aiProfilePosts = [];
const interviewPosts = [];
page.on('request', (r) => {
  if (r.url().includes('/ai-profile') && r.method() === 'POST') aiProfilePosts.push(r.url());
  if (r.url().includes('/mock-interview') && r.method() === 'POST') interviewPosts.push(r.url());
});

/** Which of the four pages is showing. Hidden pages stay in the DOM, so this reads visibility. */
async function visiblePages() {
  const shown = [];
  for (const name of ['applications', 'fit', 'interview', 'decision']) {
    if (await page.getByTestId(`page-${name}`).isVisible()) shown.push(name);
  }
  return shown.join(',');
}

mkdirSync(SHOTS, { recursive: true });

/** Waits for a panel's loading indicator to detach, proving React committed the result. */
async function settled(testId, timeout = 30000) {
  await page
    .waitForSelector(`[data-testid="${testId}-loading"]`, { state: 'detached', timeout })
    .catch(() => {});
}

const TITLE = `Bartender demo ${Date.now()}`;
console.log(`\nStage B flow against ${BASE_URL}\n`);

// --- 1. Create a requisition -------------------------------------------------------------
console.log('1. Create requisition');
await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
check('app mounted', (await page.locator('h1').count()) === 1);
check(
  'title is the Springfield image, centred',
  await page.locator('h1 img[alt="Springfield Talent Pipeline"]').evaluate((img) => {
    const box = img.getBoundingClientRect();
    return img.naturalWidth > 0 && Math.abs(box.left + box.width / 2 - window.innerWidth / 2) < 12;
  }),
);
check('no New requisition button before a requisition exists', (await page.getByTestId('restart').count()) === 0);
await page.getByTestId('req-title').fill(TITLE);
await page.getByTestId('req-keywords').fill('bartender tavern bar drinks');
const created = page.waitForResponse((r) => r.url().endsWith('/api/requisitions') && r.request().method() === 'POST');
await page.getByTestId('req-submit').click();
const createdRes = await created;
check('POST /api/requisitions -> 201', createdRes.status() === 201, `HTTP ${createdRes.status()}`);
await page.waitForSelector('[data-testid="requisition-summary"]', { timeout: 20000 });
check('requisition rendered', (await page.getByTestId('requisition-title').innerText()) === TITLE);
await page.screenshot({ path: `${SHOTS}1-requisition.png`, fullPage: true });

// --- 2. Ranked matches -------------------------------------------------------------------
console.log('\n2. Ranked matches');
await settled('matches');
await page.waitForSelector('[data-testid="matches-list"]', { timeout: 20000 });
const matchRows = await page.getByTestId('match-row').allInnerTexts();
const ranks = (await page.locator('.rank').allInnerTexts()).map(Number);
check('multiple candidates matched', matchRows.length > 1, `${matchRows.length} matches`);
check(
  'Moe Szyslak in the ranked list',
  matchRows.some((r) => r.includes('Moe Szyslak')),
);
check('every row carries a ts_rank score', ranks.length === matchRows.length && ranks.every((r) => r > 0));
check(
  'order is exactly the API order (descending rank, unmodified)',
  ranks.every((r, i) => i === 0 || ranks[i - 1] >= r),
  ranks.map((r) => r.toFixed(5)).join(' ≥ '),
);
await page.screenshot({ path: `${SHOTS}2-matches.png`, fullPage: true });

// --- 3. Apply ----------------------------------------------------------------------------
console.log('\n3. Apply a matched candidate');
const moeRow = page.getByTestId('match-row').filter({ hasText: 'Moe Szyslak' });
const applied = page.waitForResponse((r) => r.url().endsWith('/api/applications') && r.request().method() === 'POST');
await moeRow.getByRole('button', { name: 'Apply' }).click();
const appliedRes = await applied;
check('POST /api/applications -> 201', appliedRes.status() === 201, `HTTP ${appliedRes.status()}`);
await page.waitForSelector('[data-testid="page-fit"]', { state: 'visible', timeout: 15000 });
check('applying moves to the fit score page, alone', (await visiblePages()) === 'fit', await visiblePages());

// --- 4. Fit score (real Gemini call) -------------------------------------------------------
console.log('\n4. Fit score (live Gemini generation)');
const generating = await page
  .waitForSelector('[data-testid="fit-loading"]', { timeout: 15000 })
  .then((el) => el.innerText())
  .catch(() => '');
check('loading copy reads as "generating", not a bare spinner', /generat/i.test(generating), generating.trim());
await settled('fit', AI_TIMEOUT);
await page.waitForSelector('[data-testid="fit-result"]', { timeout: AI_TIMEOUT });
const score = Number(await page.getByTestId('fit-score').innerText());
const rationale = await page.getByTestId('fit-rationale').innerText();
check('fitScore rendered and in range', Number.isInteger(score) && score >= 0 && score <= 100, `${score}/100`);
check('fitRationale rendered', rationale.length > 20, `${rationale.slice(0, 80)}…`);
check(
  'exactly one POST /ai-profile fired (no StrictMode double-fire)',
  aiProfilePosts.length === 1,
  `${aiProfilePosts.length} request(s)`,
);
await page.screenshot({ path: `${SHOTS}3-fit-score.png`, fullPage: true });

// --- 5. Mock interview (real Gemini call) --------------------------------------------------
console.log('\n5. Mock interview (live Gemini generation)');
check(
  'fit page offers Back to applications, Generate interview and New requisition',
  (await page.getByTestId('nav-back').innerText()).includes('Back to applications') &&
    (await page.getByTestId('nav-forward').innerText()).includes('Generate interview') &&
    (await page.getByTestId('restart').count()) === 1,
);
await page.getByTestId('nav-forward').click();
check('Generate interview moves to the interview page, alone', (await visiblePages()) === 'interview', await visiblePages());
const interviewLoading = await page
  .waitForSelector('[data-testid="interview-loading"]', { timeout: 15000 })
  .then((el) => el.innerText())
  .catch(() => '');
check('interview loading names the model and warns it is slow', /several seconds/i.test(interviewLoading));
await settled('interview', AI_TIMEOUT);
await page.waitForSelector('[data-testid="interview-result"]', { timeout: AI_TIMEOUT });
const turns = await page.getByTestId('interview-turn').count();
const assessment = await page.getByTestId('interview-assessment').innerText();
check('5-8 Q&A turns rendered', turns >= 5 && turns <= 8, `${turns} turns`);
check('overallAssessment rendered', assessment.length > 20, `${assessment.slice(0, 80)}…`);
await page.screenshot({ path: `${SHOTS}4-interview.png`, fullPage: true });

// --- 5b. Navigation keeps state, and never re-pays for a Gemini call --------------------------
console.log('\n5b. Back / forward keep the results');
await page.getByTestId('nav-back').click();
check('Back from interview lands on the fit page', (await visiblePages()) === 'fit', await visiblePages());
check('fit score survived the round trip', (await page.getByTestId('fit-score').innerText()) === String(score));
await page.getByTestId('nav-forward').click();
check('Generate interview again just navigates', (await visiblePages()) === 'interview', await visiblePages());
check('transcript survived the round trip', (await page.getByTestId('interview-turn').count()) === turns);
check(
  'still exactly one POST /ai-profile and one POST /mock-interview',
  aiProfilePosts.length === 1 && interviewPosts.length === 1,
  `${aiProfilePosts.length} ai-profile, ${interviewPosts.length} mock-interview`,
);

// --- 6. Extend offer (walks SOURCED → SCREENING → INTERVIEWING → OFFER, then offers) ----------
console.log('\n6. Extend offer (one click walks to OFFER, then submits the offer)');
await page.getByTestId('nav-forward').click();
check('Go to decision moves to the decision page, alone', (await visiblePages()) === 'decision', await visiblePages());
check(
  'decision page offers Back to interview and New requisition, and no forward',
  (await page.getByTestId('nav-back').innerText()).includes('Back to interview') &&
    (await page.getByTestId('nav-forward').count()) === 0 &&
    (await page.getByTestId('restart').count()) === 1,
);
// HIRED is reachable only from OFFER, so the offer is preceded by three legal transitions.
const transitionStatuses = [];
const offerStatuses = [];
const collect = (r) => {
  if (r.url().includes('/transition') && r.request().method() === 'POST') transitionStatuses.push(r.status());
  if (r.url().includes('/offer') && r.request().method() === 'POST') offerStatuses.push(r.status());
};
page.on('response', collect);
await page.getByTestId('offer-amount').fill('45000');
await page.getByTestId('offer-submit').click();
await page.waitForSelector('[data-testid="offer-outcome"]', { timeout: 60000 });
page.off('response', collect);
check(
  'three sequential transitions, every one 200',
  transitionStatuses.length === 3 && transitionStatuses.every((s) => s === 200),
  transitionStatuses.join(', ') || 'none observed',
);
check('POST /offer -> 201', offerStatuses.length === 1 && offerStatuses[0] === 201, offerStatuses.join(', '));
check(
  'walked path rendered',
  (await page.getByTestId('walked-path').innerText()).includes('SOURCED → SCREENING → INTERVIEWING → OFFER'),
);
await settled('transition');
check('offer accepted and stage is HIRED', (await page.getByTestId('current-stage').innerText()) === 'HIRED');
check('offer form gone after submission (one-shot)', (await page.getByTestId('offer-form').count()) === 0);
check('Reject button gone once terminal', (await page.getByTestId('reject').count()) === 0);
check('terminal probe section is not rendered', (await page.getByTestId('probe-409').count()) === 0);
await page.screenshot({ path: `${SHOTS}5-decision.png`, fullPage: true });

// --- 6b. New requisition returns to step 1 ----------------------------------------------------
console.log('\n6b. New requisition');
await page.getByTestId('restart').click();
check('back on the applications page with an empty form', (await visiblePages()) === 'applications');
check('requisition form is showing again', await page.getByTestId('req-title').isVisible());

/*
 * --- 7. The 409 - the most important assertion in the earlier version of this stage -----------
 * Commented out with the "HIRED is terminal" probe in PipelineActions.jsx. If that block is
 * restored, restore this step (it needs to run before 6b, while the decision page is still up)
 * and change the health check below back to expecting exactly one 409 in the console.
 *
console.log('\n7. Second transition must be refused (409)');
const refused = page.waitForResponse((r) => r.url().includes('/transition') && r.request().method() === 'POST');
await page.getByTestId('probe-409').click();
const refusedRes = await refused;
check('second transition -> HTTP 409', refusedRes.status() === 409, `HTTP ${refusedRes.status()}`);
await page.waitForSelector('[data-testid="rejection-409"]', { timeout: 20000 });
const current = await page.getByTestId('rejection-current').innerText();
const requested = await page.getByTestId('rejection-requested').innerText();
const allowedNext = await page.getByTestId('rejection-allowed').innerText();
const errText = await page.getByTestId('rejection-error').innerText();
check('409 body rendered: currentStage', current === 'HIRED', current);
check('409 body rendered: requestedStage', requested === 'REJECTED', requested);
check('409 body rendered: allowedNextStages is empty', allowedNext === '(none)', allowedNext);
check('409 message explains it is terminal', /terminal/i.test(errText), errText);
check('no "unexpected" branch fired', (await page.getByTestId('rejection-unexpected').count()) === 0);
await page.screenshot({ path: `${SHOTS}5-409-refused.png`, fullPage: true });
*/

// --- 8. Browser health -------------------------------------------------------------------
console.log('\n8. Browser-level health');
check('no unexpected console errors', consoleErrors.length === 0, consoleErrors.join(' | ') || 'clean');
check(
  'no 409 was logged (the probe that provoked one is commented out)',
  expectedConflicts.length === 0,
  `${expectedConflicts.length} occurrence(s)`,
);
check('no failed requests', failedRequests.length === 0, failedRequests.join(' | ') || 'clean');

await browser.close();
console.log(`\n${failures === 0 ? 'ALL CHECKS PASSED' : `${failures} CHECK(S) FAILED`}`);
console.log(`screenshots: ${SHOTS}\n`);
process.exit(failures === 0 ? 0 : 1);
