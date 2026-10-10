import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { expect, test as base } from '@playwright/test';

const require = createRequire(import.meta.url);
const { chartToText } = require('../../server/chartParser.js');

const test = base.extend({
  t15Fixtures: [async ({ request, context }, use) => {
    const ids = new Set();
    await use({ record: id => ids.add(id) });
    const errors = [];
    try { await context.setOffline(false); } catch (error) { errors.push(error); }
    for (const id of ids) {
      try {
        const current = await request.get(`/api/songs/${id}`);
        if (current.status() === 404) continue;
        if (!current.ok()) throw new Error(`Could not inspect owned T15 fixture ${id}: HTTP ${current.status()}`);
        const song = await current.json();
        const deleted = await request.delete(`/api/songs/${id}`, {
          data: { base_updated_at: song.updated_at },
        });
        if (!deleted.ok()) throw new Error(`Could not delete owned T15 fixture ${id}: HTTP ${deleted.status()}`);
        if ((await request.get(`/api/songs/${id}`)).status() !== 404) {
          throw new Error(`Owned T15 fixture ${id} remains after cleanup`);
        }
      } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, 'T15 synthetic fixture cleanup failed');
  }, { timeout: 60_000 }],
});

test('auto-scroll is manual, timed, offline, input-aware, and cleaned up', async ({ page, request, context, t15Fixtures }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const suffix = randomUUID();
  const titles = [`T15 Auto Scroll ${suffix} A`, `T15 Auto Scroll ${suffix} B`];
  const longLine = `Invented lyric line ${'carefully '.repeat(18)}`;
  const chart = { sections: [{ label: 'Invented Verse', lines: [
    ...Array.from({ length: 34 }, (_, index) => ({ type: 'lyric',
      text: `Invented ${index + 1} ${longLine}`, chords: [] })),
    { type: 'lyric', text: longLine, chords: [
      { sym: 'C', pos: 0 }, { sym: 'G', pos: 0 }, { sym: 'F', pos: 70 }, { sym: 'D', pos: 95 },
    ] },
    { type: 'spacer' },
    ...Array.from({ length: 12 }, (_, index) => ({ type: 'lyric',
      text: `After separator ${index + 1}`, chords: [] })),
  ] }] };
  for (const title of titles) {
    const created = await request.post('/api/songs', { data: {
      title, artist: 'T15 invented test artist', chart_written_key: 'C',
      chart_source: chartToText(chart),
    } });
    expect(created.ok(), `fixture creation returned ${created.status()}`).toBeTruthy();
    t15Fixtures.record((await created.json()).id);
  }

    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    const query = `T15 Auto Scroll ${suffix}`;
    const searchResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/songs' && url.searchParams.get('q') === query;
    });
    await search.fill(query);
    const response = await searchResponse;
    const apiResult = await response.json();
    const observedTitles = apiResult.songs.map(song => song.title).sort();
    expect(observedTitles).toEqual([...titles].sort());
    const fixtureRow = page.locator('.song-row').filter({ hasText: titles[0] });
    await expect(fixtureRow).toBeVisible();
    await fixtureRow.click();
    await expect(page.getByRole('group', { name: 'Chart auto-scroll controls' })).toBeVisible();
    const scroller = page.locator('.chart-scroll');
    const controls = page.locator('.auto-scroll-controls');
    await expect(page.locator('.chart-spacer')).toHaveCount(1);
    const pipe = page.locator('.chord-anchor-separator').first();
    await expect(pipe).toHaveText(' | ');
    await expect(pipe).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.getByRole('button', { name: 'Up half step' }).click();
    await expect(page.locator('.chord-anchor').first()).toContainText('Db');
    await expect(page.locator('.chord-anchor-separator').first()).toHaveText(' | ');
    await page.getByRole('button', { name: 'Reset' }).click();
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();
    await expect(page.locator('.auto-scroll-speed')).toHaveText('10 CSS px/s');
    const initial = await scroller.evaluate(async element => {
      const before = element.scrollTop;
      for (let i = 0; i < 5; i += 1) await new Promise(requestAnimationFrame);
      return { before, after: element.scrollTop,
        contentBottom: element.scrollHeight, viewport: element.clientHeight,
        controlTop: document.querySelector('.auto-scroll-controls').getBoundingClientRect().top,
        scrollBottom: element.getBoundingClientRect().bottom };
    });
    expect(initial.before).toBe(initial.after); // no automatic start
    expect(initial.contentBottom).toBeGreaterThan(initial.viewport);
    expect(initial.controlTop).toBeGreaterThanOrEqual(initial.scrollBottom - 1);

    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect(page.getByRole('button', { name: 'Pause auto-scroll' })).toBeVisible();
    const measuredRate = await scroller.evaluate(async element => {
      const start = await new Promise(resolve => requestAnimationFrame(resolve));
      const startTop = element.scrollTop;
      let last;
      do { last = await new Promise(resolve => requestAnimationFrame(resolve)); }
      while (last - start < 550);
      return { rate: (element.scrollTop - startTop) / ((last - start) / 1000),
        startTop, endTop: element.scrollTop, clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight, atBottom: element.scrollTop >= element.scrollHeight - element.clientHeight,
        overflowY: getComputedStyle(element).overflowY, scrollBehavior: getComputedStyle(element).scrollBehavior,
        hidden: document.hidden,
        control: document.querySelector('.auto-scroll-controls button').getAttribute('aria-label') };
    });
    expect(measuredRate.rate, `${JSON.stringify(measuredRate)} errors=${JSON.stringify(pageErrors)}`).toBeGreaterThan(6);
    expect(measuredRate.rate, JSON.stringify(measuredRate)).toBeLessThan(14);
    await page.getByRole('button', { name: 'Increase auto-scroll speed' }).click();
    await expect(page.locator('.auto-scroll-speed')).toHaveText('15 CSS px/s');
    const fasterRate = await scroller.evaluate(async element => {
      const start = await new Promise(resolve => requestAnimationFrame(resolve));
      const startTop = element.scrollTop;
      let last;
      do { last = await new Promise(resolve => requestAnimationFrame(resolve)); }
      while (last - start < 500);
      return (element.scrollTop - startTop) / ((last - start) / 1000);
    });
    expect(fasterRate).toBeGreaterThan(11);
    expect(fasterRate).toBeLessThan(19);

    // Horizontal chart-row wheel/pan stays independent from vertical motion.
    const horizontalRow = page.locator('.chart-line').filter({ has: page.locator('.chord-line') }).first();
    await expect.poll(() => horizontalRow.evaluate(node => node.scrollWidth - node.clientWidth)).toBeGreaterThan(2);
    const beforePan = await scroller.evaluate(node => node.scrollTop);
    await horizontalRow.evaluate(node => {
      node.scrollLeft = Math.min(140, node.scrollWidth - node.clientWidth);
      node.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaX: 140, deltaY: 0 }));
    });
    await expect.poll(() => horizontalRow.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
    await expect(page.getByRole('button', { name: 'Pause auto-scroll' })).toBeVisible();
    expect(await scroller.evaluate(node => node.scrollTop)).toBeGreaterThanOrEqual(beforePan);
    await expect(page.getByText(titles[0], { exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Pause auto-scroll' }).click();
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();
    await context.setOffline(true);
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect(page.getByRole('button', { name: 'Pause auto-scroll' })).toBeVisible();
    const offlineStart = await scroller.evaluate(node => node.scrollTop);
    await expect.poll(() => scroller.evaluate(node => node.scrollTop), { timeout: 3000 })
      .toBeGreaterThan(offlineStart);

    // Intentional vertical wheel input pauses without resetting position.
    await scroller.hover();
    await page.mouse.wheel(0, 140);
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();
    const pausedAt = await scroller.evaluate(node => node.scrollTop);
    await scroller.press('PageDown');
    expect(await scroller.evaluate(node => node.scrollTop)).toBeGreaterThanOrEqual(pausedAt);
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    const nativeScrollbar = await scroller.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const width = node.offsetWidth - node.clientWidth;
      return { width, x: rect.right - Math.max(2, width / 2), y: rect.top + 40 };
    });
    console.log(nativeScrollbar.width > 0
      ? `T15 native scrollbar probe: dragging ${nativeScrollbar.width}px scrollbar.`
      : 'T15 native scrollbar probe: no native scrollbar exposed; synthetic classification check follows.');
    test.info().annotations.push({
      type: 'native-scrollbar',
      description: nativeScrollbar.width > 0
        ? `Attempted native scrollbar drag; detected ${nativeScrollbar.width}px scrollbar.`
        : 'Native scrollbar unavailable in this browser; synthetic pointer classification checked below.',
    });
    if (nativeScrollbar.width > 0) {
      await page.mouse.move(nativeScrollbar.x, nativeScrollbar.y);
      await page.mouse.down();
      await page.mouse.move(nativeScrollbar.x, nativeScrollbar.y - 30, { steps: 3 });
      await page.mouse.up();
      await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();
    }
    await scroller.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const x = rect.right - 1;
      const y = rect.top + 40;
      node.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse',
        pointerId: 77, clientX: x, clientY: y }));
      node.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType: 'mouse',
        pointerId: 77, clientX: x, clientY: y - 12 }));
      node.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerType: 'mouse',
        pointerId: 77, clientX: x, clientY: y - 12 }));
    });
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();

    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect(page.getByRole('button', { name: 'Pause auto-scroll' })).toBeVisible();
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
      delete document.hidden;
    });
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();

    // Resume from current position, then stop exactly at the bottom.
    await scroller.evaluate(node => { node.scrollTop = node.scrollHeight - node.clientHeight - 2; });
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'At bottom' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();

    // At tablet width, controls remain in-flow and usable. Offline remains active.
    await page.setViewportSize({ width: 768, height: 1024 });
    await expect(controls).toBeVisible();
    const tabletLayout = await page.evaluate(() => {
      const bar = document.querySelector('.auto-scroll-controls').getBoundingClientRect();
      const viewport = document.querySelector('.chart-scroll').getBoundingClientRect();
      return { barTop: bar.top, barBottom: bar.bottom, viewBottom: viewport.bottom,
        barWidth: bar.width, innerWidth: innerWidth };
    });
    expect(tabletLayout.barTop).toBeGreaterThanOrEqual(tabletLayout.viewBottom - 1);
    expect(tabletLayout.barBottom).toBeLessThanOrEqual(1024);
    expect(tabletLayout.barWidth).toBeLessThanOrEqual(tabletLayout.innerWidth);

    // The chart-row swipe is claimed by its own horizontal scroller, not song navigation.
    const row = horizontalRow;
    await row.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const x = Math.min(rect.right - 20, rect.left + 90);
      const y = rect.top + Math.min(10, rect.height / 2);
      const touch = (type, clientX) => {
        const point = new Touch({ identifier: 1, target: node, clientX, clientY: y });
        node.dispatchEvent(new TouchEvent(type, { bubbles: true, changedTouches: [point],
          touches: type === 'touchend' ? [] : [point] }));
      };
      touch('touchstart', x); touch('touchend', x - 100);
    });
    await expect(page.getByText(titles[0], { exact: true })).toBeVisible();
    const titleNode = page.locator('.chart-topbar-title');
    await titleNode.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const fire = (type, clientX) => {
        const point = new Touch({ identifier: 2, target: node, clientX, clientY: y });
        node.dispatchEvent(new TouchEvent(type, { bubbles: true, changedTouches: [point],
          touches: type === 'touchend' ? [] : [point] }));
      };
      fire('touchstart', x); fire('touchend', x - 100);
    });
    await expect(page.getByText(titles[1], { exact: true })).toBeVisible();
    await page.locator('.chart-topbar-title').evaluate(node => {
      const rect = node.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const fire = (type, clientX) => {
        const point = new Touch({ identifier: 3, target: node, clientX, clientY: y });
        node.dispatchEvent(new TouchEvent(type, { bubbles: true, changedTouches: [point],
          touches: type === 'touchend' ? [] : [point] }));
      };
      fire('touchstart', x); fire('touchend', x + 100);
    });
    await expect(page.getByText(titles[0], { exact: true })).toBeVisible();
    await scroller.evaluate(node => { node.scrollTop = 0; });
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect(page.getByRole('button', { name: 'Pause auto-scroll' })).toBeVisible();
    await page.locator('.chart-topbar-title').evaluate(node => {
      const rect = node.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const fire = (type, clientX) => {
        const point = new Touch({ identifier: 4, target: node, clientX, clientY: y });
        node.dispatchEvent(new TouchEvent(type, { bubbles: true, changedTouches: [point],
          touches: type === 'touchend' ? [] : [point] }));
      };
      fire('touchstart', x); fire('touchend', x - 100);
    });
    await expect(page.getByText(titles[1], { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();
    await page.getByRole('button', { name: 'Start auto-scroll' }).click();
    await expect(page.getByRole('button', { name: 'Pause auto-scroll' })).toBeVisible();
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('group', { name: 'Chart auto-scroll controls' })).toHaveCount(0);
});
