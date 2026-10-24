const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('@playwright/test');

const originalRow = '  C - G/D  (4x)  ';
const movedRow = '  C# - G#/D#  (4x)  ';
const backRow = originalRow;
const lyricText = '  abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN  ';

async function main() {
  const appRoot = path.resolve(__dirname, '../..');
  const { createServer } = await import('vite');
  const vite = await createServer({
    configFile: path.join(appRoot, 'vite.config.js'),
    server: { host: '127.0.0.1', port: 0, strictPort: false },
    logLevel: 'error',
  });
  let browser;
  try {
    await vite.listen();
    const address = vite.httpServer.address();
    assert.ok(address && typeof address === 'object');
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${address.port}/test/browser/ordered-row-preview.html`);
    await page.waitForFunction(() => window.previewReady === true || window.previewError);
    const previewError = await page.evaluate(() => window.previewError || null);
    assert.equal(previewError, null, previewError || 'browser preview failed to initialize');
    await page.addStyleTag({ path: path.join(appRoot, 'src/index.css') });

    async function verifyPair(expectedRow, expectedChords, expectedSecondRow = false) {
      const pair = page.locator('[data-paired-literal-row="true"]').first();
      const chordRow = pair.locator('.paired-literal-chords');
      const lyricRow = pair.locator('.paired-literal-lyric');
      await chordRow.waitFor({ state: 'visible' });
      assert.equal(await chordRow.evaluate(element => element.textContent), expectedRow);
      assert.equal(await lyricRow.textContent(), lyricText);
      assert.equal(await chordRow.evaluate(element => getComputedStyle(element).whiteSpace), 'pre');
      assert.equal(await lyricRow.evaluate(element => getComputedStyle(element).whiteSpace), 'pre');
      assert.deepEqual(await chordRow.locator('[data-chord-symbol]').evaluateAll(
        elements => elements.map(element => element.getAttribute('data-chord-symbol'))), expectedChords);
      const actualNonChord = await chordRow.evaluate(element => Array.from(element.childNodes)
        .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join(''));
      assert.equal(actualNonChord, '   -   (4x)  ');

      for (const expectedIndex of [2, 6]) {
        const chord = chordRow.locator('[data-chord-symbol]').nth(expectedIndex === 2 ? 0 : 1);
        assert.equal(Number(await chord.getAttribute('data-anchor-column')), expectedIndex);
        const geometry = await page.evaluate(({ chordElement, lyricElement, charIndex }) => {
          const range = document.createRange();
          range.setStart(lyricElement.firstChild, charIndex);
          range.setEnd(lyricElement.firstChild, charIndex + 1);
          return {
            chordLeft: chordElement.getBoundingClientRect().left,
            lyricLeft: range.getBoundingClientRect().left,
          };
        }, { chordElement: await chord.elementHandle(), lyricElement: await lyricRow.elementHandle(), charIndex: expectedIndex });
        assert.ok(Math.abs(geometry.chordLeft - geometry.lyricLeft) < 1.5,
          `chord at source column ${expectedIndex} missed lyric anchor: ${JSON.stringify(geometry)}`);
      }

      const scroll = await pair.evaluate(element => ({
        width: element.clientWidth,
        contentWidth: element.scrollWidth,
      }));
      assert.ok(scroll.contentWidth > scroll.width, `fixture must require horizontal scrolling: ${JSON.stringify(scroll)}`);
      await pair.evaluate(element => { element.scrollLeft = 55; });
      assert.ok(await pair.evaluate(element => element.scrollLeft > 0), 'paired rows must share a working horizontal scroller');
      for (const expectedIndex of [2, 6]) {
        const chord = chordRow.locator('[data-chord-symbol]').nth(expectedIndex === 2 ? 0 : 1);
        const geometry = await page.evaluate(({ chordElement, lyricElement, charIndex }) => {
          const range = document.createRange();
          range.setStart(lyricElement.firstChild, charIndex);
          range.setEnd(lyricElement.firstChild, charIndex + 1);
          return { chordLeft: chordElement.getBoundingClientRect().left,
            lyricLeft: range.getBoundingClientRect().left };
        }, { chordElement: await chord.elementHandle(), lyricElement: await lyricRow.elementHandle(), charIndex: expectedIndex });
        assert.ok(Math.abs(geometry.chordLeft - geometry.lyricLeft) < 1.5,
          `scrolled chord at source column ${expectedIndex} missed lyric anchor: ${JSON.stringify(geometry)}`);
      }

      if (expectedSecondRow) {
        const secondRow = page.locator('.chord-only-line[data-transposition-status="partial"]');
        assert.equal(await secondRow.evaluate(element => element.textContent), ' C# - G# - D/G5x');
        assert.equal(await secondRow.getAttribute('data-transposition-status'), 'partial');
        assert.equal(await secondRow.locator('.raw-transposition-warning')
          .getAttribute('aria-label'), 'Partial transposition; unsupported token preserved');
        assert.deepEqual(await secondRow.locator('[data-chord-symbol]').evaluateAll(
          elements => elements.map(element => element.getAttribute('data-chord-symbol'))), ['C#', 'G#']);
      }
    }

    for (const viewport of [
      { width: 320, height: 800, scale: '1.4' },
      { width: 768, height: 1024, scale: '1' },
      { width: 768, height: 1024, scale: '1.4' },
    ]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.locator('.chart-body').evaluateAll((elements, scale) => elements.forEach(element =>
        element.style.setProperty('--chart-font-scale', scale)), viewport.scale);
      await page.evaluate(() => window.previewState.renderSwipeChart(
        window.previewState.chart, window.previewState.chart));
      await verifyPair(originalRow, ['C', 'G/D']);

      const navigationBeforeScroll = await page.evaluate(() => {
        const target = document.querySelector('#chart [data-chord-symbol]');
        const start = new Event('touchstart', { bubbles: true, cancelable: true });
        Object.defineProperty(start, 'touches', { value: [{ clientX: 240, clientY: 20 }] });
        target.dispatchEvent(start);
        const end = new Event('touchend', { bubbles: true, cancelable: true });
        Object.defineProperty(end, 'changedTouches', { value: [{ clientX: 120, clientY: 22 }] });
        target.dispatchEvent(end);
        return window.swipeNavigationCount || 0;
      });
      assert.equal(navigationBeforeScroll, 0,
        'horizontal touch gesture on overflowing paired row must not navigate songs');

      await page.evaluate(() => {
        const state = window.previewState;
        const moved = state.transposeDecoratedRawLines(state.chart, symbol => ({
          C: 'C#', 'G/D': 'G#/D#', G: 'G#', D: 'D#',
        })[symbol] || symbol);
        state.renderSwipeChart(moved, state.chart);
      });
      await verifyPair(movedRow, ['C#', 'G#/D#'], true);

      await page.evaluate(() => {
        const state = window.previewState;
        const moved = state.transposeDecoratedRawLines(state.chart, symbol => ({
          C: 'C#', 'G/D': 'G#/D#', G: 'G#7', D: 'D#',
        })[symbol] || symbol);
        const back = state.transposeDecoratedRawLines(moved, symbol => ({
          C: 'C#', 'G/D': 'G#/D#', G: 'G#7', D: 'D#',
          'C#': 'C', 'G#/D#': 'G/D', 'G#7': 'G', 'D#': 'D',
        })[symbol] || symbol);
        state.renderSwipeChart(back, state.chart);
        const adjacent = state.transposeDecoratedRawLines(state.adjacentChart,
          symbol => ({ C: 'C#7', G: 'G#7' })[symbol] || symbol);
        state.adjacentRoot.render(state.renderChart(adjacent, state.adjacentChart));
      });
      await verifyPair(backRow, ['C', 'G/D']);

      const adjacent = page.locator('#adjacent-chart [data-paired-literal-row="true"]');
      const adjacentChords = adjacent.locator('.paired-literal-chords');
      const adjacentLyric = adjacent.locator('.paired-literal-lyric');
      assert.equal(await adjacentChords.textContent(), 'C#7 G#7');
      assert.equal(await adjacentLyric.textContent(), lyricText);
      const adjacentAnchors = await adjacentChords.locator('[data-chord-symbol]').evaluateAll(
        elements => elements.map(element => Number(element.getAttribute('data-anchor-column'))));
      assert.deepEqual(adjacentAnchors, [0, 2]);
      const adjacentGeometry = await adjacentChords.locator('[data-chord-symbol]').evaluateAll(elements =>
        elements.map((element, index) => {
          const lyric = element.closest('[data-paired-literal-row="true"]')
            .querySelector('.paired-literal-lyric');
          const range = document.createRange();
          range.setStart(lyric.firstChild, index * 2);
          range.setEnd(lyric.firstChild, index * 2 + 1);
          const symbolRange = document.createRange();
          symbolRange.selectNodeContents(element);
          return { anchorLeft: element.getBoundingClientRect().left,
            lyricLeft: range.getBoundingClientRect().left,
            labelRight: symbolRange.getBoundingClientRect().right };
        }));
      assert.ok(adjacentGeometry.every(item => Math.abs(item.anchorLeft - item.lyricLeft) < 1.5),
        `adjacent labels must keep their original lyric anchors: ${JSON.stringify(adjacentGeometry)}`);
      assert.ok(adjacentGeometry[0].labelRight > adjacentGeometry[1].anchorLeft,
        `wider adjacent chord labels should expose, not hide, their collision: ${JSON.stringify(adjacentGeometry)}`);
    }
    console.log('PASS: paired literal rows retain original lyric columns through wider transposition and transpose-back, at phone/tablet widths and both font sizes, including while horizontally scrolled. Horizontal touch on an overflowing pair does not trigger song navigation. Adjacent C#7/G#7 labels overlap at fixed anchors; no anchor was shifted to avoid the collision.');
  } finally {
    if (browser) await browser.close();
    await vite.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
