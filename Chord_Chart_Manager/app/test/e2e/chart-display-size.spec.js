import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

const require = createRequire(import.meta.url);
const { chartToText } = require('../../server/chartParser.js');

test('five chart sizes persist offline and retain measured alignment across tablet layouts and themes', async ({ page, request, context }) => {
  test.setTimeout(90000);
  const suffix = randomUUID();
  const title = `T22 size ${suffix}`;
  const content = { sections: [{ label: 'Invented verse', lines: [
    ...Array.from({ length: 28 }, (_, index) => ({
      type: 'lyric', text: `Synthetic size line ${index + 1} keeps invented text for alignment checks`, confidence: 'high',
      chords: index === 1
        ? [{ sym: 'C', pos: 2 }, { sym: 'D', pos: 2 }, { sym: 'F#maj7', pos: 30 }]
        : [{ sym: 'G', pos: 0 }, { sym: 'D', pos: 18 }, { sym: 'Am', pos: 34 }],
    })),
    { type: 'spacer' },
    { type: 'progression', chords: ['G', 'D', 'Em', 'C'], repeat: 2 },
  ] }] };
  const created = await request.post('/api/songs', { data: {
    title, artist: `T22 synthetic ${suffix}`, chart_written_key: 'G', chart_source: chartToText(content),
  } });
  expect(created.status()).toBe(201);
  const song = await created.json();
  let primaryError = null;
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Settings' }).click();
    const select = page.getByLabel('Chart text size');
    const choices = [
      ['-2', 12.75], ['-1', 13.875], ['0', 15], ['1', 16.125], ['2', 17.25],
    ];
    for (const [value, expectedPixels] of choices) {
      await select.selectOption(value);
      await expect(select).toHaveValue(value);
      await expect.poll(() => page.evaluate(() => localStorage.getItem('ccm-chart-size'))).toBe(value);
      await page.getByRole('button', { name: 'Songs' }).click();
      await page.getByPlaceholder(/Search songs/).fill(title);
      await page.getByText(title, { exact: true }).click();
      const row = page.locator('.chart-line:has(.chord-line)').first();
      await expect(row).toBeVisible();
      await expect.poll(() => row.locator('.chord-line').evaluate(node =>
        parseFloat(getComputedStyle(node).fontSize))).toBeCloseTo(expectedPixels, 1);
      await expect.poll(() => row.locator('.lyric-line').evaluate(node =>
        parseFloat(getComputedStyle(node).fontSize))).toBeCloseTo(expectedPixels, 1);
      await expect.poll(() => row.locator('.chord-line').getAttribute('data-measurement-revision'))
        .not.toBe('0');
      const layout = await row.evaluate(node => {
        const chord = node.querySelector('.chord-line');
        const lyric = node.querySelector('.lyric-line');
        const groups = [...chord.querySelectorAll('.chord-anchor')];
        return { chordHeight: chord.getBoundingClientRect().height,
          lyricTop: lyric.getBoundingClientRect().top,
          chordBottom: chord.getBoundingClientRect().bottom,
          groups: groups.map(group => ({ text: group.textContent, left: group.getBoundingClientRect().left,
            visible: group.checkVisibility() && getComputedStyle(group).visibility === 'visible' })) };
      });
      expect(layout.chordBottom).toBeLessThanOrEqual(layout.lyricTop);
      expect(layout.groups.every(group => group.visible && group.left >= 0)).toBeTruthy();
      if (value === '2') {
        const separators = page.locator('.chord-anchor-separator');
        await expect(separators.first()).toBeVisible();
        await expect(separators.first()).toContainText('|');
      }
      await page.getByRole('button', { name: 'Back' }).click();
      await page.getByRole('button', { name: 'Settings' }).click();
    }

    const viewportCases = [{ width: 768, height: 1024 }, { width: 1024, height: 768 }];
    for (const theme of ['light', 'dark']) {
      for (const [value, expectedPixels] of choices) {
        for (const viewport of viewportCases) {
          await page.setViewportSize(viewport);
          await page.getByRole('button', { name: 'Settings' }).click();
          await page.getByRole('radio', { name: theme[0].toUpperCase() + theme.slice(1), exact: true }).check();
          await page.getByLabel('Chart text size').selectOption(value);
          await page.getByRole('button', { name: 'Songs' }).click();
          await page.getByPlaceholder(/Search songs/).fill(title);
          await page.getByText(title, { exact: true }).click();
          const row = page.locator('.chart-line:has(.chord-line)').first();
          await expect(row).toBeVisible();
          await expect.poll(async () => Number(await row.locator('.chord-line')
            .getAttribute('data-measurement-revision') || 0)).toBeGreaterThan(0);
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const metrics = await page.evaluate(() => {
            const chart = document.querySelector('.chart-scroll').getBoundingClientRect();
            const bodyElement = document.querySelector('.chart-body');
            const body = bodyElement.getBoundingClientRect();
            const chord = document.querySelector('.chord-line');
            const lyric = document.querySelector('.lyric-line');
            const anchorDeltas = [...chord.querySelectorAll('.chord-anchor')].map(group => {
              const position = Number(group.dataset.pos);
              const range = document.createRange();
              range.setStart(lyric.firstChild, position);
              range.setEnd(lyric.firstChild, position + 1);
              return group.getBoundingClientRect().left - range.getBoundingClientRect().left;
            });
            return { app: document.querySelector('.app').getBoundingClientRect(), chart, body,
              wideClass: document.querySelector('.app').classList.contains('app--chart-wide'),
              theme: document.documentElement.dataset.theme,
              chartPadding: getComputedStyle(bodyElement).paddingLeft,
              chord: parseFloat(getComputedStyle(chord).fontSize),
              lyric: parseFloat(getComputedStyle(lyric).fontSize), anchorDeltas };
          });
          expect(metrics.theme).toBe(theme);
          expect(metrics.app.width).toBe(viewport.width);
          expect(metrics.wideClass).toBeTruthy();
          expect(metrics.chord).toBeCloseTo(expectedPixels, 1);
          expect(metrics.lyric).toBeCloseTo(expectedPixels, 1);
          expect(metrics.chartPadding).toBe('12px');
          expect(metrics.body.right).toBeLessThanOrEqual(metrics.chart.right + 1);
          expect(metrics.anchorDeltas.every(delta => Math.abs(delta) < 1), JSON.stringify(metrics.anchorDeltas)).toBeTruthy();
          await page.getByRole('button', { name: 'Back' }).click();
        }
      }
    }

    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByLabel('Chart text size').selectOption('1');
    await page.reload();
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByLabel('Chart text size')).toHaveValue('1');
    await context.setOffline(true);
    await page.getByLabel('Chart text size').selectOption('-1');
    await expect(page.getByLabel('Chart text size')).toHaveValue('-1');
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.getByPlaceholder(/Search songs/).fill(title);
    await page.getByText(title, { exact: true }).click();
    await expect.poll(() => page.locator('.chord-line').first().evaluate(node =>
      parseFloat(getComputedStyle(node).fontSize))).toBeCloseTo(13.875, 1);
    await expect(page.locator('.chart-spacer')).toHaveCount(1);
    await page.setViewportSize({ width: 390, height: 844 });
    const phoneLayout = await page.evaluate(() => ({
      appWidth: document.querySelector('.app').getBoundingClientRect().width,
      padding: getComputedStyle(document.querySelector('.chart-body')).paddingLeft,
      pageFits: document.querySelector('.app').scrollWidth <= document.querySelector('.app').clientWidth,
    }));
    expect(phoneLayout.appWidth).toBe(390);
    expect(phoneLayout.padding).toBe('16px');
    expect(phoneLayout.pageFits).toBeTruthy();
    await page.setViewportSize({ width: 1280, height: 800 });
    const desktopLayout = await page.evaluate(() => ({
      appWidth: document.querySelector('.app').getBoundingClientRect().width,
      padding: getComputedStyle(document.querySelector('.chart-body')).paddingLeft,
    }));
    expect(desktopLayout.appWidth).toBe(768);
    expect(desktopLayout.padding).toBe('16px');
    const scroller = page.locator('.chart-scroll');
    await expect.poll(() => scroller.evaluate(node => node.scrollHeight > node.clientHeight)).toBeTruthy();
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect.poll(() => scroller.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    await page.getByRole('button', { name: 'Pause auto-scroll' }).click();
    const stored = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(stored.chart_content).toEqual(song.chart_content);
  } catch (error) {
    primaryError = error;
  } finally {
    const errors = [];
    try { await context.setOffline(false); } catch (error) { errors.push(error); }
    try {
      const latest = await request.get(`/api/songs/${song.id}`);
      if (latest.status() !== 404) {
        if (!latest.ok()) throw new Error(`Could not inspect T22 song ${song.id}`);
        const current = await latest.json();
        const deleted = await request.delete(`/api/songs/${song.id}`, { data: { base_updated_at: current.updated_at } });
        if (!deleted.ok()) throw new Error(`Could not delete T22 song ${song.id}: HTTP ${deleted.status()}`);
      }
      if ((await request.get(`/api/songs/${song.id}`)).status() !== 404) throw new Error(`T22 song ${song.id} remains`);
    } catch (error) { errors.push(error); }
    if (primaryError && errors.length) throw new AggregateError([primaryError, ...errors], 'T22 test and cleanup failed');
    if (errors.length) throw new AggregateError(errors, 'T22 fixture cleanup failed');
  }
  if (primaryError) throw primaryError;
});
