import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { bracedLyric } from './chartText.js';

test('chart metadata stays below title while scrolling and remains usable across themes and widths', async ({ page, request, context }) => {
  test.setTimeout(90000);
  const suffix = randomUUID();
  const title = `T23 metadata ${suffix}`;
  const beatbuddyText = 'Intro - Verse - Chorus - Verse - Chorus - Bridge - Final Chorus - Outro';
  const lyrics = Array.from({ length: 34 }, (_, index) => bracedLyric(
    `Invented metadata scroll line ${index + 1} has no source lyrics`,
    index === 2
      ? [{ sym: 'G', pos: 0 }, { sym: 'D', pos: 0 }, { sym: 'Am', pos: 36 }]
      : [{ sym: 'G', pos: 0 }, { sym: 'D', pos: 20 }, { sym: 'Am', pos: 36 }]));
  // Ordinary text drops a trailing blank line, so the one spacer sits before the last lyric.
  const chartSource = ['[Invented section]', ...lyrics.slice(0, -1), '', lyrics.at(-1)].join('\n');
  const created = await request.post('/api/songs', { data: {
    title, artist: `T23 synthetic ${suffix}`, chart_written_key: 'G', original_key: 'G', bpm: 96,
    beatbuddy_structure: beatbuddyText,
    chart_source: chartSource,
  } });
  expect(created.status()).toBe(201);
  const song = await created.json();
  let primaryError = null;
  const assertBeatBuddyFits = async () => {
    const metrics = await page.locator('.bb-pill').evaluate(element => {
      const box = element.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(element);
      const fragments = [...range.getClientRects()].map(rect => ({
        left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
      }));
      const style = getComputedStyle(element);
      return {
        text: element.textContent,
        box: { left: box.left, right: box.right, top: box.top, bottom: box.bottom },
        fragments,
        clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight, scrollHeight: element.scrollHeight,
        whiteSpace: style.whiteSpace, overflow: style.overflow, textOverflow: style.textOverflow,
      };
    });
    expect(metrics.text).toContain(beatbuddyText);
    expect(metrics.whiteSpace).not.toBe('nowrap');
    expect(metrics.overflow).not.toBe('hidden');
    expect(metrics.textOverflow).not.toBe('ellipsis');
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
    expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);
    expect(metrics.fragments.length).toBeGreaterThan(1);
    expect(metrics.fragments.every(rect => rect.left >= metrics.box.left - 1
      && rect.right <= metrics.box.right + 1
      && rect.top >= metrics.box.top - 1 && rect.bottom <= metrics.box.bottom + 1),
    JSON.stringify(metrics)).toBeTruthy();
    return metrics;
  };
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByLabel('Chart text size').selectOption('-2');
    await page.getByRole('button', { name: 'Songs' }).click();
    for (const theme of ['light', 'dark', 'system']) {
      await page.getByRole('button', { name: 'Settings' }).click();
      await page.getByRole('radio', { name: theme[0].toUpperCase() + theme.slice(1), exact: true }).check();
      await page.getByRole('button', { name: 'Songs' }).click();
      await page.getByPlaceholder(/Search songs/).fill(title);
      await page.getByText(title, { exact: true }).click();
      await expect(page.locator('.chart-spacer')).toHaveCount(1);
      await expect(page.locator('.chord-anchor-separator').first()).toBeVisible();
      for (const viewport of [
        { width: 360, height: 800 }, { width: 390, height: 844 }, { width: 768, height: 1024 },
        { width: 1024, height: 768 },
      ]) {
        await page.setViewportSize(viewport);
        const scroller = page.locator('.chart-scroll');
        const topbar = page.locator('.chart-topbar');
        const meta = page.locator('.chart-meta-bar');
        await expect(meta).toBeVisible();
        await expect(meta.locator('.key-pill')).toBeVisible();
        await expect(meta.locator('.capo-pill', { hasText: '96 BPM' })).toBeVisible();
        await expect(meta.locator('.bb-pill')).toBeVisible();
        const initial = await page.evaluate(() => {
          const title = document.querySelector('.chart-topbar').getBoundingClientRect();
          const meta = document.querySelector('.chart-meta-bar').getBoundingClientRect();
          const chart = document.querySelector('.chart-body').getBoundingClientRect();
          const key = document.querySelector('.key-pill');
          const beatbuddy = document.querySelector('.bb-pill');
          const original = document.querySelector('.transpose-original-key')?.innerText || '';
          const chordSize = parseFloat(getComputedStyle(document.querySelector('.chord-line')).fontSize);
          return { titleBottom: title.bottom, metaTop: meta.top, metaBottom: meta.bottom,
            chartTop: chart.top, keyFont: getComputedStyle(key).fontSize,
            beatbuddyFont: getComputedStyle(beatbuddy).fontSize,
            scrollerHeight: document.querySelector('.chart-scroll').clientHeight,
            scrollHeight: document.querySelector('.chart-scroll').scrollHeight,
            theme: document.documentElement.dataset.theme, original, chordSize };
        });
        const expectedTheme = theme === 'system'
          ? await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
          : theme;
        expect(initial.theme).toBe(expectedTheme);
        expect(initial.original).toContain('Original key');
        expect(initial.original).toContain('G');
        expect(initial.chordSize).toBeCloseTo(12.75, 1);
        expect(Math.abs(initial.metaTop - initial.titleBottom)).toBeLessThan(1);
        expect(initial.chartTop).toBeGreaterThanOrEqual(initial.metaBottom);
        expect(initial.beatbuddyFont).toBe(initial.keyFont);
        expect(initial.scrollHeight).toBeGreaterThan(initial.scrollerHeight);

        await scroller.evaluate(node => { node.scrollTop = 180; });
        await expect.poll(() => scroller.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
        const sticky = await page.evaluate(() => {
          const title = document.querySelector('.chart-topbar').getBoundingClientRect();
          const meta = document.querySelector('.chart-meta-bar').getBoundingClientRect();
          const chart = document.querySelector('.chart-body').getBoundingClientRect();
          return { titleBottom: title.bottom, metaTop: meta.top, metaBottom: meta.bottom,
            chartInitialTop: chart.top, scrollerTop: document.querySelector('.chart-scroll').getBoundingClientRect().top };
        });
        expect(Math.abs(sticky.metaTop - sticky.titleBottom)).toBeLessThan(1);
        expect(sticky.metaTop).toBeGreaterThanOrEqual(sticky.scrollerTop - 1);
        expect(sticky.metaBottom).toBeGreaterThan(sticky.metaTop);
        await page.getByRole('button', { name: 'Up half step' }).click();
        await expect(meta.locator('.key-pill')).not.toHaveText('G');
        await expect(page.locator('.transpose-original-key')).toContainText('G');
        await expect(meta.locator('.bb-pill')).toBeVisible();
        await page.getByRole('button', { name: 'Increase capo' }).click();
        await expect(meta.locator('.key-pill')).toContainText('→');
        await page.getByRole('button', { name: 'Reset' }).click();
      }
      await page.getByRole('button', { name: 'Back' }).click();
    }

    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByLabel('Chart text size').selectOption('2');
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.getByPlaceholder(/Search songs/).fill(title);
    await page.getByText(title, { exact: true }).click();
    for (const viewport of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      const scroller = page.locator('.chart-scroll');
      const meta = page.locator('.chart-meta-bar');
      await expect.poll(() => page.locator('.chord-line').first().getAttribute('data-measurement-revision'))
        .not.toBe('0');
      const beforeScroll = await assertBeatBuddyFits();
      const initialGeometry = await page.evaluate(() => ({
        titleBottom: document.querySelector('.chart-topbar').getBoundingClientRect().bottom,
        metaTop: document.querySelector('.chart-meta-bar').getBoundingClientRect().top,
        metaBottom: document.querySelector('.chart-meta-bar').getBoundingClientRect().bottom,
        chartTop: document.querySelector('.chart-body').getBoundingClientRect().top,
      }));
      expect(Math.abs(initialGeometry.metaTop - initialGeometry.titleBottom)).toBeLessThan(1);
      expect(initialGeometry.chartTop).toBeGreaterThanOrEqual(initialGeometry.metaBottom);
      expect(beforeScroll.fragments.some((rect, index, list) => index > 0
        && Math.abs(rect.top - list[index - 1].top) > 1)).toBeTruthy();

      await page.getByRole('button', { name: 'Up half step' }).click();
      await expect(page.locator('.chart-meta-bar .key-pill')).not.toHaveText('G');
      await page.getByRole('button', { name: 'Increase capo' }).click();
      await expect(page.locator('.chart-meta-bar .key-pill')).toContainText('→');
      await expect(page.locator('.transpose-original-key')).toContainText('G');
      await assertBeatBuddyFits();
      const changedGeometry = await page.evaluate(() => ({
        titleBottom: document.querySelector('.chart-topbar').getBoundingClientRect().bottom,
        metaTop: document.querySelector('.chart-meta-bar').getBoundingClientRect().top,
        metaBottom: document.querySelector('.chart-meta-bar').getBoundingClientRect().bottom,
        chartTop: document.querySelector('.chart-body').getBoundingClientRect().top,
      }));
      expect(Math.abs(changedGeometry.metaTop - changedGeometry.titleBottom)).toBeLessThan(1);
      expect(changedGeometry.chartTop).toBeGreaterThanOrEqual(changedGeometry.metaBottom);

      await scroller.evaluate(node => { node.scrollTop = 180; });
      await expect.poll(() => scroller.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
      const afterScroll = await assertBeatBuddyFits();
      const stickyGeometry = await page.evaluate(() => ({
        titleBottom: document.querySelector('.chart-topbar').getBoundingClientRect().bottom,
        metaTop: document.querySelector('.chart-meta-bar').getBoundingClientRect().top,
        metaBottom: document.querySelector('.chart-meta-bar').getBoundingClientRect().bottom,
      }));
      expect(Math.abs(stickyGeometry.metaTop - stickyGeometry.titleBottom)).toBeLessThan(1);
      expect(stickyGeometry.metaBottom).toBeGreaterThan(stickyGeometry.metaTop);
      expect(afterScroll.fragments.every(rect => rect.top >= afterScroll.box.top - 1
        && rect.bottom <= afterScroll.box.bottom + 1)).toBeTruthy();
      await page.getByRole('button', { name: 'Reset' }).click();
      await scroller.evaluate(node => { node.scrollTop = 0; });
    }
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect.poll(() => page.locator('.chart-scroll').evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    await expect(page.locator('.chart-meta-bar')).toBeVisible();
    await page.getByRole('button', { name: 'Pause auto-scroll' }).click();
    await context.setOffline(true);
    await page.getByRole('button', { name: 'Back' }).click();
    await page.getByPlaceholder(/Search songs/).fill(title);
    await page.getByText(title, { exact: true }).click();
    await expect(page.locator('.chart-meta-bar .bb-pill')).toBeVisible();
    const finalSong = await (await request.get(`/api/songs/${song.id}`)).json();
    expect(finalSong.chart_content).toEqual(song.chart_content);
    expect(finalSong.original_key).toBe('G');
  } catch (error) {
    primaryError = error;
  } finally {
    const errors = [];
    try { await context.setOffline(false); } catch (error) { errors.push(error); }
    try {
      const latest = await request.get(`/api/songs/${song.id}`);
      if (latest.status() !== 404) {
        if (!latest.ok()) throw new Error(`Could not inspect T23 song ${song.id}`);
        const current = await latest.json();
        const deleted = await request.delete(`/api/songs/${song.id}`, { data: { base_updated_at: current.updated_at } });
        if (!deleted.ok()) throw new Error(`Could not delete T23 song ${song.id}: HTTP ${deleted.status()}`);
      }
      if ((await request.get(`/api/songs/${song.id}`)).status() !== 404) throw new Error(`T23 song ${song.id} remains`);
    } catch (error) { errors.push(error); }
    if (primaryError && errors.length) throw new AggregateError([primaryError, ...errors], 'T23 test and cleanup failed');
    if (errors.length) throw new AggregateError(errors, 'T23 fixture cleanup failed');
  }
  if (primaryError) throw primaryError;
});
