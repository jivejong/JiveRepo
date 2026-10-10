import { createRequire } from 'node:module';
import { expect, test } from '@playwright/test';

const require = createRequire(import.meta.url);
const { chartToText } = require('../../server/chartParser.js');

async function inspectLabels(row) {
  return row.evaluate(node => {
    const chordLine = node.querySelector('.chord-line');
    const lyricLine = node.querySelector('.lyric-line-anchored');
    const lyricText = lyricLine?.firstChild?.textContent || '';
    const graphemes = typeof Intl.Segmenter === 'function'
      ? [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(lyricText)] : [];
    const chordRect = chordLine.getBoundingClientRect();
    const rowRect = node.getBoundingClientRect();
    const rowScaleX = node.clientWidth ? rowRect.width / node.clientWidth : 1;
    const scrollContentLeft = rowRect.left - node.scrollLeft * rowScaleX;
    const labels = [...chordLine.querySelectorAll('.chord-anchor')].map(label => {
      const rect = label.getBoundingClientRect();
      const style = getComputedStyle(label);
      const position = Number(label.dataset.pos);
      const segment = graphemes.find(item => position >= item.index
        && position < item.index + item.segment.length);
      let anchorDelta = null;
      if (segment) {
        const range = document.createRange();
        range.setStart(lyricLine.firstChild, segment.index);
        range.setEnd(lyricLine.firstChild, segment.index + segment.segment.length);
        anchorDelta = rect.left - range.getBoundingClientRect().left;
      }
      return {
        symbol: label.textContent,
        position,
        anchorDelta,
        lane: Number(label.dataset.lane),
        visible: label.checkVisibility() && style.visibility === 'visible' && style.opacity !== '0',
        color: style.color,
        background: style.backgroundColor,
        rect: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
          width: rect.width, height: rect.height },
        insideChordLine: rect.top >= chordRect.top && rect.bottom <= chordRect.bottom,
        insideRow: rect.top >= rowRect.top && rect.bottom <= rowRect.bottom,
        insideScrollContent: rect.left >= scrollContentLeft - 1
          && rect.right <= scrollContentLeft + node.scrollWidth * rowScaleX + 1,
        insideViewport: rect.left >= rowRect.left - 1 && rect.right <= rowRect.right + 1,
      };
    });
    return { labels, rowWidth: node.clientWidth, rowScrollWidth: node.scrollWidth,
      measurementRevision: Number(chordLine.dataset.measurementRevision || 0) };
  });
}

function expectLabelsDistinct(result, symbols) {
  expect(result.labels.map(label => label.symbol)).toEqual(symbols);
  for (const label of result.labels) {
    expect(label.visible).toBeTruthy();
    expect(label.rect.width).toBeGreaterThan(0);
    expect(label.rect.height).toBeGreaterThan(0);
    expect(label.insideChordLine, JSON.stringify(label)).toBeTruthy();
    expect(label.insideRow, JSON.stringify(label)).toBeTruthy();
    expect(label.insideScrollContent, JSON.stringify(label)).toBeTruthy();
    if (label.anchorDelta !== null) expect(Math.abs(label.anchorDelta)).toBeLessThan(1);
    expect(label.color).not.toBe('rgba(0, 0, 0, 0)');
    if (label.background !== 'rgba(0, 0, 0, 0)') {
      expect(label.background).not.toBe('transparent');
      const luminance = color => {
        const channels = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
          const channel = value / 255;
          return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
        });
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
      };
      const values = [luminance(label.color), luminance(label.background)].sort((a, b) => b - a);
      expect((values[0] + 0.05) / (values[1] + 0.05)).toBeGreaterThanOrEqual(4.5);
    }
  }
  for (let i = 0; i < result.labels.length; i += 1) {
    for (let j = i + 1; j < result.labels.length; j += 1) {
      const a = result.labels[i].rect;
      const b = result.labels[j].rect;
      const intersectionWidth = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const intersectionHeight = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      expect(intersectionWidth > 0 && intersectionHeight > 0,
        `${result.labels[i].symbol} and ${result.labels[j].symbol} overpaint`).toBeFalsy();
    }
  }
}

async function rowMeasurementRevisions(rows) {
  return Promise.all(rows.map(async row => Number(
    await row.locator('.chord-line').getAttribute('data-measurement-revision') || 0)));
}

async function expectRowsRemeasured(rows, previous) {
  await Promise.all(rows.map((row, index) => expect.poll(async () => Number(
    await row.locator('.chord-line').getAttribute('data-measurement-revision') || 0))
    .toBeGreaterThan(previous[index])));
}

async function waitForStableFrames(page) {
  await page.evaluate(() => new Promise(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

test('generated charts round-trip and keep anchors and colliding labels visible', async ({ page, request, context }) => {
  const suffix = crypto.randomUUID();
  const title = `T13a invented alignment ${suffix}`;
  const songsResponse = await request.get('/api/songs?q=Already%20Gone');
  expect(songsResponse.ok()).toBeTruthy();
  const artist = (await songsResponse.json()).songs.find(song => song.title === 'Already Gone')?.artist;
  expect(artist).toBeTruthy();

  const longLyric = 'Invented alignment words continue across this deliberately long tablet test line with extra harmless filler';
  const overlapLyric = 'Invented overlapping anchors remain attached to their exact character positions';
  const transposeLyric = 'Invented transpose collision keeps both horizontal anchor columns';
  const unicodeLyric = '\u{1F3B8}x\u0301y';
  const structured = { sections: [{ label: 'Invented Verse', lines: [
    { type: 'lyric', text: longLyric, confidence: 'low',
      chords: [{ sym: 'G', pos: 8 }, { sym: 'C', pos: 26 }, { sym: 'D', pos: 43 }] },
    { type: 'spacer' },
    { type: 'lyric', text: overlapLyric, confidence: 'low', chords: [
      { sym: 'C', pos: 5 }, { sym: 'G', pos: 11 }, { sym: 'Am', pos: 21 },
      { sym: 'F', pos: 22 }, { sym: 'Dm', pos: 22 }, { sym: 'E7', pos: 22 },
    ] },
    { type: 'lyric', text: transposeLyric, confidence: 'high',
      chords: [{ sym: 'C', pos: 5 }, { sym: 'D', pos: 6 }] },
    { type: 'lyric', text: 'end', confidence: 'high',
      chords: [{ sym: 'Am', pos: longLyric.length + 5 }] },
    { type: 'progression', chords: ['C', 'G'], repeat: 2, note: 'invented progression note' },
    { type: 'repeat', ref: 'Verse 1', times: 2 },
    { type: 'raw', text: 'tab|--invented-text' },
  ] }, { label: 'Invented Chorus', lines: [
    { type: 'spacer' },
    { type: 'lyric', text: 'Invented second section line', chords: [], confidence: 'high' },
  ] }, { label: 'Invented Verse 2', lines: [
    { type: 'lyric', text: 'Invented third section line', chords: [], confidence: 'high' },
    { type: 'lyric', text: unicodeLyric, confidence: 'high', chords: [
      { sym: 'C', pos: 1 }, { sym: 'D', pos: 2 }, { sym: 'E', pos: 3 }, { sym: 'F', pos: 10 },
    ] },
  ] }] };
  const source = chartToText(structured);
  let songId;
  try {
    const created = await request.post('/api/songs', {
      data: { title, artist, chart_written_key: 'C', chart_source: source },
    });
    expect(created.ok()).toBeTruthy();
    songId = (await created.json()).id;
    const persistedUnicode = await (await request.get(`/api/songs/${songId}`)).json();
    expect(persistedUnicode.chart_content.sections[2].lines[1].chords.map(chord => chord.pos))
      .toEqual([1, 2, 3, 10]);

    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('/');
    const search = page.getByPlaceholder('Search songs, artists…');
    await search.fill(title);
    await page.getByText(title, { exact: true }).click();
    await expect(page.locator('.chart-line')).toHaveCount(7);
    await expect(page.locator('.chart-spacer')).toHaveCount(2);
    const spacing = await page.locator('.chart-body').evaluate(body => {
      const sections = [...body.querySelectorAll('.chart-section')];
      const spacers = [...body.querySelectorAll('.chart-spacer')];
      return { spacerHeights: spacers.map(spacer => spacer.getBoundingClientRect().height),
        explicitBoundaryGap: sections[1].getBoundingClientRect().top - sections[0].getBoundingClientRect().bottom,
        automaticSectionGap: sections[2].getBoundingClientRect().top - sections[1].getBoundingClientRect().bottom,
        scrollHeight: body.scrollHeight, contentBottom: sections.at(-1).getBoundingClientRect().bottom - body.getBoundingClientRect().top };
    });
    expect(spacing.spacerHeights.every(height => height > 10)).toBeTruthy();
    expect(Math.abs(spacing.explicitBoundaryGap)).toBeLessThan(1);
    expect(spacing.automaticSectionGap).toBeGreaterThanOrEqual(20);
    expect(spacing.scrollHeight).toBeGreaterThanOrEqual(spacing.contentBottom);
    await page.evaluate(async () => { await document.fonts.ready; });

    const measureFirstLine = async () => page.locator('.chart-line').first().evaluate(row => {
      const chordRow = row.querySelector('.chord-line');
      const lyric = row.querySelector('.lyric-line-anchored');
      return [...chordRow.querySelectorAll('.chord-anchor')].map(anchor => {
        const position = Number(anchor.dataset.pos);
        const range = document.createRange();
        range.setStart(lyric.firstChild, position);
        range.setEnd(lyric.firstChild, position + 1);
        return { position, delta: anchor.getBoundingClientRect().left - range.getBoundingClientRect().left,
          chord: anchor.textContent };
      });
    });
    const expectAnchorsAligned = measured => {
      expect(measured.map(anchor => anchor.position)).toEqual([8, 26, 43]);
      for (const anchor of measured) expect(Math.abs(anchor.delta)).toBeLessThan(1);
    };
    const measureUnicodeAnchors = async () => page.locator('.chart-line').nth(6).evaluate(row => {
      const lyric = row.querySelector('.lyric-line-anchored');
      const text = lyric.firstChild.textContent;
      const segments = [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)];
      const anchors = [...row.querySelectorAll('.chord-anchor')];
      return anchors.map(anchor => {
        const position = Number(anchor.dataset.pos);
        const segment = segments.find(item => position >= item.index
          && position < item.index + item.segment.length);
        if (!segment) return { position, warning: anchor.dataset.anchorWarning || null,
          visible: anchor.checkVisibility(), left: anchor.getBoundingClientRect().left,
          chord: anchor.textContent };
        const range = document.createRange();
        range.setStart(lyric.firstChild, segment.index);
        range.setEnd(lyric.firstChild, segment.index + segment.segment.length);
        const rect = range.getBoundingClientRect();
        return { position, warning: anchor.dataset.anchorWarning || null,
          delta: anchor.getBoundingClientRect().left - rect.left,
          visible: anchor.checkVisibility(), chord: anchor.textContent };
      });
    });
    const expectUnicodeAnchorsAligned = measured => {
      expect(measured.map(anchor => anchor.position)).toEqual([1, 2, 3, 10]);
      expect(measured[0].warning).toBe('inside-surrogate-pair');
      expect(measured[2].warning).toBe('inside-grapheme');
      for (const anchor of measured.slice(0, 3)) expect(Math.abs(anchor.delta)).toBeLessThan(1);
      expect(measured.every(anchor => anchor.visible)).toBeTruthy();
    };
    const measuredRows = [0, 1, 2, 3, 6].map(index => page.locator('.chart-line').nth(index));
    const assertStableAnchors = async () => {
      const read = async () => Promise.all(measuredRows.map(inspectLabels));
      await expect.poll(async () => (await read()).every(result =>
        result.labels.every(label => label.anchorDelta === null || Math.abs(label.anchorDelta) < 1)))
        .toBeTruthy();
      const first = await read();
      await waitForStableFrames(page);
      const second = await read();
      await waitForStableFrames(page);
      const third = await read();
      for (let rowIndex = 0; rowIndex < first.length; rowIndex += 1) {
        for (let labelIndex = 0; labelIndex < first[rowIndex].labels.length; labelIndex += 1) {
          for (const result of [second, third]) {
            expect(Math.abs(first[rowIndex].labels[labelIndex].rect.left
              - result[rowIndex].labels[labelIndex].rect.left)).toBeLessThan(0.1);
            expect(Math.abs(first[rowIndex].labels[labelIndex].rect.top
              - result[rowIndex].labels[labelIndex].rect.top)).toBeLessThan(0.1);
          }
        }
      }
      return third;
    };
    const verifyLayout = async () => {
      expectAnchorsAligned(await measureFirstLine());
      expectUnicodeAnchorsAligned(await measureUnicodeAnchors());
      const collision = await inspectLabels(measuredRows[1]);
      expectLabelsDistinct(collision, collision.labels.map(label => label.symbol));
      const introduced = await inspectLabels(measuredRows[2]);
      expectLabelsDistinct(introduced, introduced.labels.map(label => label.symbol));
      const beyond = await inspectLabels(measuredRows[3]);
      expectLabelsDistinct(beyond, beyond.labels.map(label => label.symbol));
      expect(beyond.rowScrollWidth).toBeGreaterThan(beyond.rowWidth);
    };
    const changeAndVerify = async action => {
      const revisions = await rowMeasurementRevisions(measuredRows);
      await action();
      await expectRowsRemeasured(measuredRows, revisions);
      await assertStableAnchors();
      await verifyLayout();
    };

    const firstBefore = await measureFirstLine();
    expectAnchorsAligned(firstBefore);
    await page.evaluate(async () => { await document.fonts.ready; });
    await expect.poll(async () => measureUnicodeAnchors()).toMatchObject([
      { position: 1, warning: 'inside-surrogate-pair' },
      { position: 2, warning: null },
      { position: 3, warning: 'inside-grapheme' },
      { position: 10, warning: null },
    ]);
    const unicodeFontLoaded = await measureUnicodeAnchors();
    expectUnicodeAnchorsAligned(unicodeFontLoaded);
    await page.evaluate(() => {
      document.documentElement.style.setProperty('--font-chord', 'Arial, sans-serif');
      document.fonts.dispatchEvent(new Event('loadingdone'));
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const unicodeAlternateFont = await measureUnicodeAnchors();
    expectUnicodeAnchorsAligned(unicodeAlternateFont);
    await page.evaluate(() => {
      document.documentElement.style.removeProperty('--font-chord');
      document.fonts.dispatchEvent(new Event('loadingdone'));
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const unicodeFontRestored = await measureUnicodeAnchors();
    expectUnicodeAnchorsAligned(unicodeFontRestored);
    const desktopBefore = await inspectLabels(page.locator('.chart-line').nth(1));
    expectLabelsDistinct(desktopBefore, ['C', 'G', 'Am', 'F', 'Dm', 'E7']);
    expect(desktopBefore.labels.map(label => label.position)).toEqual([5, 11, 21, 22, 22, 22]);

    await page.getByRole('button', { name: 'Up half step' }).click();
    const desktopAfterTranspose = await measureFirstLine();
    expectAnchorsAligned(desktopAfterTranspose);
    expect(desktopAfterTranspose.map(anchor => anchor.chord)).not.toEqual(firstBefore.map(anchor => anchor.chord));
    const collisionsAfterTranspose = await inspectLabels(page.locator('.chart-line').nth(1));
    expectLabelsDistinct(collisionsAfterTranspose, ['Db', 'Ab', 'Bbm', 'Gb', 'Ebm', 'F7']);

    const introducedCollision = await inspectLabels(page.locator('.chart-line').nth(2));
    expect(introducedCollision.labels.map(label => label.position)).toEqual([5, 6]);
    expectLabelsDistinct(introducedCollision, ['Db', 'Eb']);
    expect(introducedCollision.labels[0].lane).not.toBe(introducedCollision.labels[1].lane);
    const unicodeAfterTranspose = await measureUnicodeAnchors();
    expectUnicodeAnchorsAligned(unicodeAfterTranspose);
    expect(unicodeAfterTranspose.map(anchor => anchor.chord)).toEqual(['Db', 'Eb', 'F', 'Gb']);
    expectLabelsDistinct(await inspectLabels(page.locator('.chart-line').nth(6)), ['Db', 'Eb', 'F', 'Gb']);

    await changeAndVerify(async () => {
      await page.evaluate(() => {
        document.body.style.zoom = '125%';
        window.dispatchEvent(new Event('resize'));
      });
    });
    const desktopZoomCollision = await inspectLabels(measuredRows[1]);
    expectLabelsDistinct(desktopZoomCollision,
      ['Db', 'Ab', 'Bbm', 'Gb', 'Ebm', 'F7']);
    const desktopZoomBeyond = await measuredRows[3].evaluate(node => {
      node.scrollLeft = node.scrollWidth;
      const label = node.querySelector('.chord-anchor');
      const rect = label.getBoundingClientRect();
      const row = node.getBoundingClientRect();
      return { left: rect.left, right: rect.right, rowLeft: row.left, rowRight: row.right };
    });
    expect(desktopZoomBeyond.left).toBeGreaterThanOrEqual(desktopZoomBeyond.rowLeft - 1);
    expect(desktopZoomBeyond.right).toBeLessThanOrEqual(desktopZoomBeyond.rowRight + 1);

    await changeAndVerify(async () => {
      await page.getByRole('button', { name: 'Up half step' }).click();
    });
    const desktopZoomCollisionAfterTranspose = await inspectLabels(measuredRows[1]);
    expectLabelsDistinct(desktopZoomCollisionAfterTranspose,
      ['D', 'A', 'Bm', 'G', 'Em', 'F#7']);

    await changeAndVerify(async () => {
      await page.evaluate(() => {
        document.body.style.zoom = '100%';
        window.dispatchEvent(new Event('resize'));
      });
    });
    await changeAndVerify(async () => {
      await page.setViewportSize({ width: 768, height: 1024 });
    });
    const tabletBeforeTranspose = await inspectLabels(measuredRows[1]);
    expectLabelsDistinct(tabletBeforeTranspose, tabletBeforeTranspose.labels.map(label => label.symbol));
    await changeAndVerify(async () => {
      await page.getByRole('button', { name: 'Up half step' }).click();
    });
    await changeAndVerify(async () => {
      await page.evaluate(() => {
        document.body.style.zoom = '125%';
        window.dispatchEvent(new Event('resize'));
      });
    });
    const tabletZoomCollision = await inspectLabels(measuredRows[1]);
    expectLabelsDistinct(tabletZoomCollision, tabletZoomCollision.labels.map(label => label.symbol));
    await changeAndVerify(async () => {
      await page.getByRole('button', { name: 'Up half step' }).click();
    });

    await measuredRows[0].evaluate(node => { node.scrollLeft = Math.floor(node.scrollWidth / 2); });
    await measuredRows[3].evaluate(node => { node.scrollLeft = node.scrollWidth; });
    await assertStableAnchors();
    await verifyLayout();
    const tabletZoomBeyond = await inspectLabels(measuredRows[3]);
    expect(tabletZoomBeyond.labels[0].insideViewport).toBeTruthy();
    expectLabelsDistinct(tabletZoomBeyond, tabletZoomBeyond.labels.map(label => label.symbol));

    await changeAndVerify(async () => {
      await page.evaluate(() => {
        document.body.style.zoom = '100%';
        window.dispatchEvent(new Event('resize'));
      });
    });
    const tabletBeyond = await inspectLabels(measuredRows[3]);
    expect(tabletBeyond.labels[0].position).toBe(longLyric.length + 5);
    expect(tabletBeyond.rowScrollWidth).toBeGreaterThan(tabletBeyond.rowWidth);
    await measuredRows[3].evaluate(node => { node.scrollLeft = node.scrollWidth; });
    const scrolledBeyond = await inspectLabels(measuredRows[3]);
    expectLabelsDistinct(scrolledBeyond, scrolledBeyond.labels.map(label => label.symbol));
    expect(scrolledBeyond.labels[0].insideViewport).toBeTruthy();

    const tabletLabels = await inspectLabels(measuredRows[1]);
    const tabletScroll = await inspectLabels(measuredRows[0]);
    expectLabelsDistinct(tabletLabels, tabletLabels.labels.map(label => label.symbol));
    expect(tabletScroll.rowScrollWidth).toBeGreaterThan(tabletScroll.rowWidth);
    expectAnchorsAligned(await measureFirstLine());
    const tabletSpacing = await page.locator('.chart-body').evaluate(body => {
      const sections = [...body.querySelectorAll('.chart-section')];
      return { spacerHeights: [...body.querySelectorAll('.chart-spacer')].map(spacer => spacer.getBoundingClientRect().height),
        explicitBoundaryGap: sections[1].getBoundingClientRect().top - sections[0].getBoundingClientRect().bottom,
        automaticSectionGap: sections[2].getBoundingClientRect().top - sections[1].getBoundingClientRect().bottom,
        scrollHeight: body.scrollHeight };
    });
    expect(tabletSpacing.spacerHeights.every(height => height > 10)).toBeTruthy();
    expect(Math.abs(tabletSpacing.explicitBoundaryGap)).toBeLessThan(1);
    expect(tabletSpacing.automaticSectionGap).toBeGreaterThanOrEqual(20);
    expect(tabletSpacing.scrollHeight).toBeGreaterThan(0);
    console.log('T13a anchor measurements:', JSON.stringify({
      desktopBefore: firstBefore.map(({ position, delta }) => ({ position, delta: Number(delta.toFixed(2)) })),
      desktopAfterTranspose: desktopAfterTranspose.map(({ position, delta }) => ({ position, delta: Number(delta.toFixed(2)) })),
    }));
    const compactUnicode = measurements => measurements.map(({ position, delta, warning }) => ({
      position, delta: Number.isFinite(delta) ? Number(delta.toFixed(2)) : null, warning,
    }));
    console.log('T13a Unicode boundary measurements:', JSON.stringify({
      desktopFontLoaded: compactUnicode(unicodeFontLoaded),
      desktopAlternateFont: compactUnicode(unicodeAlternateFont),
      desktopFontRestored: compactUnicode(unicodeFontRestored),
      desktopAfterTranspose: compactUnicode(unicodeAfterTranspose),
      tabletAfterTranspose: compactUnicode(await measureUnicodeAnchors()),
    }));

    const initial = await (await request.get(`/api/songs/${songId}`)).json();
    const lossy = await request.put(`/api/songs/${songId}`, {
      data: { chart_source: source.replace('[[CCM-CHART:2]]\n', ''),
        base_updated_at: initial.updated_at },
    });
    expect(lossy.status()).toBe(400);
    const afterRejected = await (await request.get(`/api/songs/${songId}`)).json();
    expect(afterRejected.chart_content).toEqual(initial.chart_content);
    expect(afterRejected.updated_at).toBe(initial.updated_at);

    await page.getByRole('button', { name: 'Edit' }).click();
    const editor = page.locator('.form-textarea');
    await expect(editor).toHaveValue(source);
    const unchangedResponse = page.waitForResponse(response =>
      response.url().endsWith(`/api/songs/${songId}`)
        && response.request().method() === 'PUT');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await unchangedResponse).ok()).toBeTruthy();
    const onlineUnchanged = await (await request.get(`/api/songs/${songId}`)).json();
    expect(onlineUnchanged.chart_content).toEqual(initial.chart_content);

    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(editor).toHaveValue(source);
    const editedSource = source.replace('harmless', 'gentle');
    await editor.fill(editedSource);
    const editResponse = page.waitForResponse(response =>
      response.url().endsWith(`/api/songs/${songId}`)
        && response.request().method() === 'PUT');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await editResponse).ok()).toBeTruthy();
    const savedResponse = await request.get(`/api/songs/${songId}`);
    const saved = await savedResponse.json();
    expect(saved.chart_content.sections[0].lines.map(line => line.type)).toEqual(
      ['lyric', 'spacer', 'lyric', 'lyric', 'lyric', 'progression', 'repeat', 'raw']);
    expect(saved.chart_content.sections[0].lines[0].text).toBe(longLyric.replace('harmless', 'gentle'));
    expect(saved.chart_content.sections[0].lines.slice(1)).toEqual(structured.sections[0].lines.slice(1));
    const cacheAfterOnline = await page.evaluate(async id => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open('chart-manager', 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const song = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').get(id);
        get.onsuccess = () => resolve(get.result);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return { chart_content: song?.chart_content, chart_source: song?.chart_source };
    }, songId);
    expect(cacheAfterOnline.chart_content).toEqual(saved.chart_content);
    expect(cacheAfterOnline.chart_source).toBe(saved.chart_source);

    await page.getByRole('button', { name: 'Back' }).click();
    await search.fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    const offlineSource = saved.chart_source.replace('overlapping', 'stacked');
    const offlineExpected = structuredClone(saved.chart_content);
    offlineExpected.sections[0].lines[2].text = overlapLyric.replace('overlapping', 'stacked');
    await context.setOffline(true);
    await expect(page.getByText(/changes saved locally/i)).toBeVisible();
    const offlineEditor = page.locator('.form-textarea');
    await offlineEditor.fill(offlineSource);
    await expect(offlineEditor).toHaveValue(offlineSource);
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    const cachedEdit = await page.evaluate(async id => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open('chart-manager', 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const song = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').get(id);
        get.onsuccess = () => resolve(get.result);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return { chart_content: song?.chart_content, chart_source: song?.chart_source,
        pending: !!song?._sync };
    }, songId);
    expect(cachedEdit.chart_source).toBe(offlineSource);
    expect(cachedEdit.chart_content).toEqual(offlineExpected);
    expect(cachedEdit.pending).toBeTruthy();

    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByText(/changes saved locally/i)).toBeVisible();
    await page.locator('.form-group').filter({ hasText: 'BPM' }).locator('input').fill('121');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
    const cached = await page.evaluate(async id => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open('chart-manager', 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const song = await new Promise((resolve, reject) => {
        const get = db.transaction('songs').objectStore('songs').get(id);
        get.onsuccess = () => resolve(get.result);
        get.onerror = () => reject(get.error);
      });
      db.close();
      return { chart_content: song?.chart_content, chart_source: song?.chart_source,
        pending: !!song?._sync };
    }, songId);
    expect(cached.chart_content).toEqual(offlineExpected);
    expect(cached.chart_source).toBe(offlineSource);
    expect(cached.pending).toBeTruthy();
    await context.setOffline(false);
    await expect.poll(async () => (await (await request.get(`/api/songs/${songId}`)).json()).bpm)
      .toBe(121);
    const synced = await (await request.get(`/api/songs/${songId}`)).json();
    expect(synced.chart_content).toEqual(offlineExpected);
    expect(synced.chart_source).toBe(offlineSource);

    await page.getByRole('button', { name: 'Back' }).click();
    await search.fill(title);
    await page.getByText(title, { exact: true }).click();
    await expect(page.locator('.chart-line')).toHaveCount(7);
    const reopened = await (await request.get(`/api/songs/${songId}`)).json();
    expect(reopened.chart_content).toEqual(offlineExpected);
  } finally {
    if (songId) {
      const currentResponse = await request.get(`/api/songs/${songId}`);
      expect(currentResponse.ok()).toBeTruthy();
      const current = await currentResponse.json();
      const deleted = await request.delete(`/api/songs/${songId}`, {
        data: { base_updated_at: current.updated_at },
      });
      expect(deleted.ok()).toBeTruthy();
      expect((await request.get(`/api/songs/${songId}`)).status()).toBe(404);
    }
    await context.setOffline(false);
  }
});

test('ordinary chart blanks survive online and offline save, sync, and reopen', async ({ page, request, context }) => {
  const title = `T13a invented ordinary spacing ${crypto.randomUUID()}`;
  const source = '[Verse]\nInvented first line.\n\n\nInvented second line.\n\n[Chorus]\n\nInvented refrain line.\n\n';
  let songId;
  try {
    const created = await request.post('/api/songs', {
      data: { title, artist: 'Already Gone', chart_written_key: 'C', chart_source: source },
    });
    expect(created.ok()).toBeTruthy();
    songId = (await created.json()).id;
    const initial = await (await request.get(`/api/songs/${songId}`)).json();
    expect(initial.chart_content.sections.map(section => section.lines.map(line => line.type))).toEqual([
      ['lyric', 'spacer', 'lyric'], ['spacer', 'lyric'],
    ]);

    await page.goto('/');
    await page.getByPlaceholder('Search songs, artists…').fill(title);
    await page.getByText(title, { exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await page.locator('.form-group').filter({ hasText: 'BPM' }).locator('input').fill('97');
    const onlineSave = page.waitForResponse(response => response.url().endsWith(`/api/songs/${songId}`)
      && response.request().method() === 'PUT');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await onlineSave).ok()).toBeTruthy();
    const online = await (await request.get(`/api/songs/${songId}`)).json();
    expect(online.chart_content).toEqual(initial.chart_content);

    await page.getByRole('button', { name: 'Edit' }).click();
    await context.setOffline(true);
    await expect(page.getByText(/changes saved locally/i)).toBeVisible();
    const editor = page.locator('.form-textarea');
    await editor.fill(source.replace('Invented second line.', 'Invented revised line.'));
    await page.getByRole('button', { name: 'Save changes' }).click();
    const cached = await page.evaluate(async id => {
      const opened = await new Promise((resolve, reject) => {
        const request = indexedDB.open('chart-manager', 1);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const song = await new Promise((resolve, reject) => {
        const request = opened.transaction('songs').objectStore('songs').get(id);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      opened.close();
      return song;
    }, songId);
    expect(cached.chart_content.sections.map(section => section.lines.map(line => line.type))).toEqual([
      ['lyric', 'spacer', 'lyric'], ['spacer', 'lyric'],
    ]);
    expect(cached.chart_content.sections[0].lines[2].text).toBe('Invented revised line.');
    expect(cached._sync).toBeTruthy();

    await context.setOffline(false);
    await expect.poll(async () => (await (await request.get(`/api/songs/${songId}`)).json())
      .chart_content.sections[0].lines[2].text).toBe('Invented revised line.');
    const synced = await (await request.get(`/api/songs/${songId}`)).json();
    expect(synced.chart_content).toEqual(cached.chart_content);
    await page.getByRole('button', { name: 'Back' }).click();
    await page.getByPlaceholder('Search songs, artists…').fill(title);
    await page.getByText(title, { exact: true }).click();
    await expect(page.locator('.chart-spacer')).toHaveCount(2);
    expect((await (await request.get(`/api/songs/${songId}`)).json()).chart_content)
      .toEqual(cached.chart_content);
  } finally {
    await context.setOffline(false);
    if (songId) {
      const current = await (await request.get(`/api/songs/${songId}`)).json();
      const deleted = await request.delete(`/api/songs/${songId}`, {
        data: { base_updated_at: current.updated_at },
      });
      expect(deleted.ok()).toBeTruthy();
      expect((await request.get(`/api/songs/${songId}`)).status()).toBe(404);
    }
  }
});
