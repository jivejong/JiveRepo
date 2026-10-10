import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { expect, request as playwrightRequest, test } from '@playwright/test';
import { contrastOverLayers } from '../../src/lib/colorContrast.js';

const require = createRequire(import.meta.url);
const { chartToText } = require('../../server/chartParser.js');

const preferenceKey = 'ccm-theme-preference';

async function expectTheme(page, theme) {
  await expect.poll(() => page.locator('html').getAttribute('data-theme')).toBe(theme);
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content',
    theme === 'dark' ? '#1A1A1F' : '#F5F3EE');
}

async function contrastOf(_page, locator, property = 'color') {
  const sample = await locator.evaluate((element, foregroundProperty) => {
    const backgrounds = [];
    for (let node = element; node; node = node.parentElement) {
      backgrounds.push(getComputedStyle(node).backgroundColor);
    }
    return {
      foreground: getComputedStyle(element)[foregroundProperty],
      backgroundsNearestFirst: backgrounds,
      canvas: getComputedStyle(document.documentElement).colorScheme === 'dark' ? '#000000' : '#FFFFFF',
    };
  }, property);
  return contrastOverLayers(sample.foreground, sample.backgroundsNearestFirst, sample.canvas);
}

async function seedTaskOwnedConflict(page, song) {
  await page.evaluate(async conflict => {
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('chart-manager', 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('meta', 'readwrite');
      transaction.objectStore('meta').put({
        key: 'last_sync_conflicts', value: [conflict], updatedAt: Date.now(),
      });
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    db.close();
    window.dispatchEvent(new Event('chart-cache-change'));
  }, { id: song.id, title: song.title, operation: 'update' });
}

async function inspectChart(page) {
  return page.locator('.chart-body').evaluate(body => {
    const rootStyle = getComputedStyle(document.documentElement);
    const rows = [...body.querySelectorAll('.chart-line:has(.chord-line)')];
    const rowHeights = rows.map(row => row.getBoundingClientRect().height);
    const chordLyricGaps = rows.map(row => {
      const chord = row.querySelector('.chord-line');
      const lyric = row.querySelector('.lyric-line-anchored');
      return lyric.getBoundingClientRect().top - chord.getBoundingClientRect().bottom;
    });
    const anchorDeltas = rows.flatMap(row => {
      const chord = row.querySelector('.chord-line');
      const lyric = row.querySelector('.lyric-line-anchored');
      if (!lyric?.firstChild) return [];
      return [...chord.querySelectorAll('.chord-anchor')].map(group => {
        const position = Number(group.dataset.pos);
        const range = document.createRange();
        range.setStart(lyric.firstChild, Math.min(position, lyric.firstChild.length));
        range.setEnd(lyric.firstChild, Math.min(position + 1, lyric.firstChild.length));
        return group.getBoundingClientRect().left - range.getBoundingClientRect().left;
      });
    });
    return {
      anchors: anchorDeltas,
      rowHeights,
      chordLyricGaps,
      spacers: [...body.querySelectorAll('.chart-spacer')].map(node => node.getBoundingClientRect().height),
      symbols: [...body.querySelectorAll('.chord-anchor-symbol')].map(node => node.textContent),
      pipes: [...body.querySelectorAll('.chord-anchor-separator')].map(node => ({
        text: node.textContent, visible: node.checkVisibility(), color: getComputedStyle(node).color,
      })),
      chartText: getComputedStyle(rows[0]?.querySelector('.lyric-line') || body).color,
      chordText: getComputedStyle(rows[0]?.querySelector('.chord-line') || body).color,
      background: rootStyle.getPropertyValue('--bg-base').trim(),
      secondary: rootStyle.getPropertyValue('--text-secondary').trim(),
      muted: rootStyle.getPropertyValue('--text-muted').trim(),
      accent: rootStyle.getPropertyValue('--accent').trim(),
      textOnAccent: rootStyle.getPropertyValue('--text-on-accent').trim(),
      focusRing: rootStyle.getPropertyValue('--focus-ring').trim(),
      danger: rootStyle.getPropertyValue('--danger').trim(),
      success: rootStyle.getPropertyValue('--success').trim(),
      scrollWidth: Math.max(...rows.map(row => row.scrollWidth), 0),
      rowWidth: Math.max(...rows.map(row => row.clientWidth), 0),
      hasHorizontalOverflow: rows.some(row => row.scrollWidth > row.clientWidth + 1),
    };
  });
}

async function expectSettledChart(page) {
  await expect.poll(async () => {
    const result = await inspectChart(page);
    return result.anchors.length > 0 && result.anchors.every(delta => Math.abs(delta) < 1);
  }).toBeTruthy();
  const first = await inspectChart(page);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  const second = await inspectChart(page);
  expect(second.anchors).toEqual(first.anchors);
  expect(second.rowHeights).toEqual(first.rowHeights);
  expect(second.chordLyricGaps).toEqual(first.chordLyricGaps);
  expect(second.spacers).toEqual(first.spacers);
  expect(second.symbols).toEqual(first.symbols);
  expect(second.pipes.map(pipe => pipe.text)).toEqual(first.pipes.map(pipe => pipe.text));
  expect(second.pipes.length).toBeGreaterThan(0);
  expect(second.pipes.every(pipe => pipe.visible)).toBeTruthy();
  expect(second.scrollWidth).toBe(first.scrollWidth);
  expect(second.rowWidth).toBe(first.rowWidth);
  expect(await contrastOf(page, page.locator('.lyric-line').first())).toBeGreaterThanOrEqual(4.5);
  expect(await contrastOf(page, page.locator('.chord-anchor-symbol').first())).toBeGreaterThanOrEqual(4.5);
  expect(await contrastOf(page, page.locator('.chord-anchor-separator').first())).toBeGreaterThanOrEqual(4.5);
  return second;
}

test('System follows device changes, explicit themes persist, and theme switching preserves chart layout', async ({ page, context }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/');
  await expectTheme(page, 'light');
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('radio', { name: 'System' })).toBeChecked();
  await page.getByRole('radio', { name: 'Light' }).focus();
  await page.keyboard.press('Tab');
  const focusStyle = await page.evaluate(() => ({
    visible: document.activeElement.matches(':focus-visible'),
    outlineStyle: getComputedStyle(document.activeElement).outlineStyle,
    outlineWidth: getComputedStyle(document.activeElement).outlineWidth,
  }));
  expect(focusStyle.visible).toBeTruthy();
  expect(focusStyle.outlineStyle).toBe('solid');
  expect(parseFloat(focusStyle.outlineWidth)).toBeGreaterThanOrEqual(2);
  expect(await contrastOf(page, page.locator(':focus-visible'), 'outlineColor')).toBeGreaterThanOrEqual(3);

  await page.getByRole('radio', { name: 'Light' }).check();
  await expectTheme(page, 'light');
  expect(await page.evaluate(key => localStorage.getItem(key), preferenceKey)).toBe('light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expectTheme(page, 'light');

  await page.getByRole('radio', { name: 'System' }).check();
  await expectTheme(page, 'dark');
  await page.getByRole('button', { name: 'Songs' }).click();
  await page.emulateMedia({ colorScheme: 'light' });
  await expectTheme(page, 'light');
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('radio', { name: 'Dark' }).check();
  await expectTheme(page, 'dark');
  await page.getByRole('button', { name: 'Songs' }).click();
  await page.reload();
  await expectTheme(page, 'dark');

  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker?.controller))).toBeTruthy();
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByPlaceholder(/Search songs, artists/)).toBeVisible();
  await expectTheme(page, 'dark');
  expect(await contrastOf(page, page.locator('.offline-banner'))).toBeGreaterThanOrEqual(4.5);
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('radio', { name: 'Light' }).check();
  await expectTheme(page, 'light');
  await page.reload();
  await expectTheme(page, 'light');
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('radio', { name: 'Light' })).toBeChecked();
  await context.setOffline(false);

  await page.getByRole('button', { name: 'Songs' }).click();
  const search = page.getByPlaceholder(/Search songs, artists/);
  await search.fill('Bye Bye Love');
  await page.getByText('Bye Bye Love', { exact: true }).click();
  await expect(page.getByRole('group', { name: 'Chart auto-scroll controls' })).toBeVisible();
  await expectSettledChart(page);
  await page.setViewportSize({ width: 768, height: 1024 });
  const lightTabletChart = await expectSettledChart(page);
  await expect(page.getByRole('button', { name: 'Start auto-scroll' })).toBeVisible();
  await page.getByRole('button', { name: 'Start auto-scroll' }).click();
  await expect.poll(() => page.locator('.chart-scroll').evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Pause auto-scroll' }).click();
  await page.getByRole('button', { name: 'Back' }).click();
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('radio', { name: 'Dark' }).check();
  await page.getByRole('button', { name: 'Songs' }).click();
  await search.fill('Bye Bye Love');
  await page.getByText('Bye Bye Love', { exact: true }).click();
  await expectTheme(page, 'dark');
  const darkChart = await expectSettledChart(page);
  expect(darkChart.anchors.length).toBe(lightTabletChart.anchors.length);
  darkChart.anchors.forEach((delta, index) => {
    expect(Math.abs(delta - lightTabletChart.anchors[index])).toBeLessThan(0.1);
  });
  expect(darkChart.spacers).toEqual(lightTabletChart.spacers);
  expect(darkChart.rowHeights).toEqual(lightTabletChart.rowHeights);
  expect(darkChart.chordLyricGaps).toEqual(lightTabletChart.chordLyricGaps);
  expect(darkChart.symbols).toEqual(lightTabletChart.symbols);
  expect(darkChart.rowWidth).toBe(lightTabletChart.rowWidth);
  expect(darkChart.scrollWidth).toBe(lightTabletChart.scrollWidth);
  expect(darkChart.hasHorizontalOverflow).toBe(lightTabletChart.hasHorizontalOverflow);
  await page.getByRole('button', { name: 'Start auto-scroll' }).click();
  await expect.poll(() => page.locator('.chart-scroll').evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Pause auto-scroll' }).click();
});

test('invalid or unavailable stored preference safely falls back to System', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(key => localStorage.setItem(key, 'sepia'), preferenceKey);
  await page.goto('/');
  await expectTheme(page, 'dark');
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('radio', { name: 'System' })).toBeChecked();

  const blocked = await page.context().newPage();
  await blocked.emulateMedia({ colorScheme: 'light' });
  await blocked.addInitScript(() => Object.defineProperty(window, 'localStorage', {
    configurable: true,
    get() { throw new Error('storage unavailable'); },
  }));
  await blocked.goto('/');
  await expectTheme(blocked, 'light');
  await blocked.getByRole('button', { name: 'Settings' }).click();
  await expect(blocked.getByRole('radio', { name: 'System' })).toBeChecked();
  await blocked.getByRole('radio', { name: 'Dark' }).click();
  await expectTheme(blocked, 'light');
  await expect(blocked.getByRole('radio', { name: 'System' })).toBeChecked();
  await blocked.close();
});

test('theme tokens cover setlist and edit views, with task-owned fixture cleanup', async ({ page, request }) => {
  const name = `T16 Theme view ${randomUUID()}`;
  const created = await request.post('/api/setlists', { data: { name } });
  expect(created.ok(), `setlist fixture creation returned ${created.status()}`).toBeTruthy();
  const setlist = await created.json();

  try {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/');
    await expectTheme(page, 'dark');
    await page.getByRole('button', { name: 'Setlists' }).click();
    await expect(page.getByText(name, { exact: true })).toBeVisible();
    await page.getByText(name, { exact: true }).click();
    await expect(page.locator('.topbar-title')).toHaveText(name);
    await expectTheme(page, 'dark');
    expect(await contrastOf(page, page.locator('.topbar-title'))).toBeGreaterThanOrEqual(4.5);

    await page.getByRole('button', { name: 'Back' }).click();
    await expect(page.getByText(name, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.getByPlaceholder(/Search songs, artists/).fill('Bye Bye Love');
    await page.getByText('Bye Bye Love', { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.locator('.topbar-title')).toHaveText('Edit song');
    await expectTheme(page, 'dark');
    expect(await contrastOf(page, page.locator('.form-input').first())).toBeGreaterThanOrEqual(4.5);
  } finally {
    const deleted = await request.delete(`/api/setlists/${setlist.id}`);
    if (!deleted.ok()) throw new Error(`Could not clean up T16 setlist fixture ${setlist.id}: HTTP ${deleted.status()}`);
    if ((await request.get(`/api/setlists/${setlist.id}`)).status() !== 404) {
      throw new Error(`T16 setlist fixture ${setlist.id} remains after cleanup`);
    }
  }
});

test('danger text meets contrast on effective surfaces in Light and Dark', async ({ page, context, request }) => {
  const suffix = randomUUID();
  const setlistName = `T16 Contrast setlist ${suffix}`;
  const songTitle = `T16 Contrast song ${suffix}`;
  const cleanupRequest = await playwrightRequest.newContext();
  let setlist = null;
  let song = null;
  const measuredContrasts = [];
  const recordContrast = async (theme, state, locator) => {
    const ratio = await contrastOf(page, locator);
    expect(ratio, `${theme} ${state} contrast ratio`).toBeGreaterThanOrEqual(4.5);
    measuredContrasts.push(`${theme}/${state}=${ratio.toFixed(2)}:1`);
  };

  try {
    const setlistResponse = await request.post('/api/setlists', { data: { name: setlistName } });
    expect(setlistResponse.ok(), `setlist fixture creation returned ${setlistResponse.status()}`).toBeTruthy();
    setlist = await setlistResponse.json();
    const songResponse = await request.post('/api/songs', { data: {
      title: songTitle, artist: 'T16 synthetic contrast fixture', chart_written_key: 'C', chart_source: '',
    } });
    expect(songResponse.ok(), `song fixture creation returned ${songResponse.status()}`).toBeTruthy();
    song = await songResponse.json();

    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto('/');
    await page.getByPlaceholder(/Search songs, artists/).fill(songTitle);
    await expect(page.getByText(songTitle, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('radio', { name: 'System' })).toBeChecked();

    for (const theme of ['light', 'dark']) {
      if (theme === 'dark') {
        await page.getByRole('radio', { name: 'Dark' }).check();
      } else {
        await page.getByRole('radio', { name: 'Light' }).check();
      }
      await expectTheme(page, theme);
      await context.setOffline(true);
      await seedTaskOwnedConflict(page, song);

      const offlineStatus = page.getByText('○ Offline', { exact: true });
      const conflictHeading = page.getByText(/tablet change was replaced/);
      await expect(offlineStatus).toBeVisible();
      await expect(conflictHeading).toBeVisible();
      await recordContrast(theme, 'Settings Offline status', offlineStatus);
      await recordContrast(theme, 'Settings conflict heading', conflictHeading);

      await context.setOffline(false);
      await expect(page.getByText('● Online', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Songs' }).click();
      await page.getByPlaceholder(/Search songs, artists/).fill(songTitle);
      await page.getByText(songTitle, { exact: true }).click();
      await page.getByRole('button', { name: 'Edit' }).click();
      await page.locator('.form-input').first().fill('');
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      const editError = page.getByText('Title is required', { exact: true });
      await expect(editError).toBeVisible();
      await recordContrast(theme, 'Edit error', editError);

      await page.getByRole('button', { name: 'Back' }).click();
      await page.getByRole('button', { name: 'Setlists' }).click();
      await page.getByText(setlistName, { exact: true }).click();
      const deleteButton = page.getByRole('button', { name: 'Delete setlist' });
      await expect(deleteButton).toBeVisible();
      await recordContrast(theme, 'setlist Delete control', deleteButton);
      await page.getByRole('button', { name: 'Back' }).click();
      await page.getByRole('button', { name: 'Settings' }).click();
    }
    console.log(`T16 effective foreground/background contrast: ${measuredContrasts.join(', ')}`);
  } finally {
    const cleanupFailures = [];
    if (song) {
      try {
        const currentSong = await cleanupRequest.get(`http://localhost:3000/api/songs/${song.id}`);
        if (currentSong.status() !== 404) {
          if (!currentSong.ok()) throw new Error(`Could not inspect T16 contrast song ${song.id}: HTTP ${currentSong.status()}`);
          const latest = await currentSong.json();
          const deletedSong = await cleanupRequest.delete(`http://localhost:3000/api/songs/${song.id}`, {
            data: { base_updated_at: latest.updated_at },
          });
          if (!deletedSong.ok()) throw new Error(`Could not clean up T16 contrast song ${song.id}: HTTP ${deletedSong.status()}`);
        }
        if ((await cleanupRequest.get(`http://localhost:3000/api/songs/${song.id}`)).status() !== 404) {
          throw new Error(`T16 contrast song ${song.id} remains after cleanup`);
        }
      } catch (error) {
        cleanupFailures.push(error.message);
      }
    }
    if (setlist) {
      try {
        const deletedSetlist = await cleanupRequest.delete(`http://localhost:3000/api/setlists/${setlist.id}`);
        if (!deletedSetlist.ok()) throw new Error(`Could not clean up T16 contrast setlist ${setlist.id}: HTTP ${deletedSetlist.status()}`);
        if ((await cleanupRequest.get(`http://localhost:3000/api/setlists/${setlist.id}`)).status() !== 404) {
          throw new Error(`T16 contrast setlist ${setlist.id} remains after cleanup`);
        }
      } catch (error) {
        cleanupFailures.push(error.message);
      }
    }
    await cleanupRequest.dispose();
    if (cleanupFailures.length) throw new Error(cleanupFailures.join('; '));
  }
});

test('malformed-anchor warnings remain visible and themed in Light and Dark', async ({ page, request }) => {
  const title = `T16 Theme warning ${randomUUID()}`;
  const chart = { sections: [{ label: 'Invented Verse', lines: [{
    type: 'lyric', text: '\u{1F3B8}x invented warning sample',
    chords: [{ sym: 'G', pos: 1 }, { sym: 'C', pos: 2 }],
  }] }] };
  const created = await request.post('/api/songs', { data: {
    title, artist: 'T16 synthetic test', chart_written_key: 'C', chart_source: chartToText(chart),
  } });
  expect(created.ok(), `warning fixture creation returned ${created.status()}`).toBeTruthy();
  const song = await created.json();

  try {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto('/');
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('radio', { name: 'Light' }).check();
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.getByPlaceholder(/Search songs, artists/).fill(title);
    await page.getByText(title, { exact: true }).click();

    const warning = page.locator('.chord-anchor-symbol[data-anchor-warning="inside-surrogate-pair"]');
    await expect(warning).toBeVisible();
    await expect(warning).toHaveAttribute('title', /Stored column splits a surrogate pair/);
    await expectTheme(page, 'light');
    expect(await contrastOf(page, warning)).toBeGreaterThanOrEqual(4.5);

    await page.getByRole('button', { name: 'Back' }).click();
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('radio', { name: 'Dark' }).check();
    await page.getByRole('button', { name: 'Songs' }).click();
    await page.getByPlaceholder(/Search songs, artists/).fill(title);
    await page.getByText(title, { exact: true }).click();
    const darkWarning = page.locator('.chord-anchor-symbol[data-anchor-warning="inside-surrogate-pair"]');
    await expect(darkWarning).toBeVisible();
    await expect(darkWarning).toHaveAttribute('title', /Stored column splits a surrogate pair/);
    await expectTheme(page, 'dark');
    expect(await contrastOf(page, darkWarning)).toBeGreaterThanOrEqual(4.5);
  } finally {
    const current = await request.get(`/api/songs/${song.id}`);
    if (current.status() !== 404) {
      if (!current.ok()) throw new Error(`Could not inspect T16 warning fixture ${song.id}: HTTP ${current.status()}`);
      const latest = await current.json();
      const deleted = await request.delete(`/api/songs/${song.id}`, {
        data: { base_updated_at: latest.updated_at },
      });
      if (!deleted.ok()) throw new Error(`Could not clean up T16 warning fixture ${song.id}: HTTP ${deleted.status()}`);
    }
    if ((await request.get(`/api/songs/${song.id}`)).status() !== 404) {
      throw new Error(`T16 warning fixture ${song.id} remains after cleanup`);
    }
  }
});
