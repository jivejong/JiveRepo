import { describe, expect, it } from 'vitest';
import {
  AUTO_SCROLL_DEFAULT_SPEED, AUTO_SCROLL_FRAME_GAP_RESET_MS, AUTO_SCROLL_MAX_SPEED, AUTO_SCROLL_MIN_SPEED,
  AUTO_SCROLL_STEP, clampAutoScrollSpeed, createAutoScrollController, isVerticalScrollKey,
} from '../../src/lib/autoScroll.js';

function makeFrames() {
  let nextId = 1;
  const pending = new Map();
  return {
    requestFrame(callback) { const id = nextId++; pending.set(id, callback); return id; },
    cancelFrame(id) { pending.delete(id); },
    run(timestamp) {
      const current = [...pending.entries()];
      pending.clear();
      current.forEach(([, callback]) => callback(timestamp));
    },
    get count() { return pending.size; },
  };
}

function makeScroller({ top = 0, height = 100, viewport = 20 } = {}) {
  return { scrollTop: top, scrollHeight: height, clientHeight: viewport };
}

describe('auto-scroll timing', () => {
  it('keeps the documented speed range, step, and default', () => {
    expect([AUTO_SCROLL_MIN_SPEED, AUTO_SCROLL_MAX_SPEED, AUTO_SCROLL_STEP,
      AUTO_SCROLL_DEFAULT_SPEED]).toEqual([5, 40, 5, 10]);
    expect(clampAutoScrollSpeed(3)).toBe(5);
    expect(clampAutoScrollSpeed(41)).toBe(40);
    expect(clampAutoScrollSpeed(17)).toBe(15);
  });

  it('moves by elapsed seconds, changes rate, and resumes at the current position', () => {
    const frames = makeFrames();
    const target = makeScroller();
    const controller = createAutoScrollController(frames);
    expect(controller.start(target, 10)).toBe(true);
    expect(target.scrollTop).toBe(0);
    frames.run(1000); // establish the timestamp; no initial jump
    frames.run(2000);
    expect(target.scrollTop).toBe(10);
    controller.pause();
    expect(frames.count).toBe(0);
    controller.setSpeed(20);
    expect(controller.start(target)).toBe(true);
    frames.run(9000); // resume resets elapsed time, despite the long pause
    frames.run(9500);
    expect(target.scrollTop).toBe(20);
  });

  it('treats an active 10-second frame gap as suspended time, then resumes normally', () => {
    const frames = makeFrames();
    const target = makeScroller();
    const controller = createAutoScrollController(frames);
    controller.start(target, 10);
    frames.run(0);
    frames.run(100);
    expect(target.scrollTop).toBe(1);
    frames.run(10_100);
    expect(target.scrollTop).toBe(1);
    expect(controller.running).toBe(true);
    expect(frames.count).toBe(1);
    frames.run(10_200);
    expect(target.scrollTop).toBe(2);
    expect(AUTO_SCROLL_FRAME_GAP_RESET_MS).toBe(1000);
  });

  it('clamps at the bottom, reports end, and stops scheduling', () => {
    const frames = makeFrames();
    const bottomValues = [];
    const target = makeScroller({ top: 75, height: 100, viewport: 20 });
    const controller = createAutoScrollController({
      requestFrame: frames.requestFrame, cancelFrame: frames.cancelFrame,
      onBottom: value => bottomValues.push(value),
    });
    controller.start(target, 40);
    frames.run(0);
    frames.run(500);
    expect(target.scrollTop).toBe(80);
    expect(controller.running).toBe(false);
    expect(frames.count).toBe(0);
    expect(bottomValues.at(-1)).toBe(true);
  });

  it('retains fractional distance when a browser rounds scrollTop writes', () => {
    const frames = makeFrames();
    let roundedTop = 0;
    const target = {
      scrollHeight: 1000,
      clientHeight: 100,
      get scrollTop() { return roundedTop; },
      set scrollTop(value) { roundedTop = Math.trunc(value); },
    };
    const controller = createAutoScrollController(frames);
    controller.start(target, 10);
    frames.run(0);
    for (let frame = 1; frame <= 100; frame += 1) frames.run(frame * 16);
    expect(target.scrollTop).toBeGreaterThan(10);
  });

  it('keeps elapsed movement consistent across frame rates with rounded scrollTop writes', () => {
    for (const framesPerSecond of [24, 30, 60, 120]) {
      const frames = makeFrames();
      let roundedTop = 0;
      const target = {
        scrollHeight: 10_000,
        clientHeight: 100,
        get scrollTop() { return roundedTop; },
        set scrollTop(value) { roundedTop = Math.trunc(value); },
      };
      const controller = createAutoScrollController(frames);
      controller.start(target, 10);
      frames.run(0);
      for (let frame = 1; frame <= framesPerSecond; frame += 1) {
        frames.run(frame * 1000 / framesPerSecond);
      }
      expect(target.scrollTop, `${framesPerSecond} fps`).toBe(10);
      controller.stop();
    }
  });

  it('resets the baseline on hidden-page pause and only resumes after explicit Start', () => {
    const frames = makeFrames();
    const target = makeScroller();
    const controller = createAutoScrollController(frames);
    controller.start(target, 10);
    frames.run(0);
    frames.run(100);
    controller.pause(); // visibilitychange handler uses the same pause operation
    expect(target.scrollTop).toBe(1);
    expect(frames.count).toBe(0);
    controller.start(target, 10);
    frames.run(20_000); // first frame establishes a fresh baseline
    expect(target.scrollTop).toBe(1);
    frames.run(20_100);
    expect(target.scrollTop).toBe(2);
  });

  it('uses current chart height while running and stops at a newly shortened bottom', () => {
    const frames = makeFrames();
    const target = makeScroller({ top: 20, height: 1000, viewport: 100 });
    const controller = createAutoScrollController(frames);
    controller.start(target, 10);
    frames.run(0);
    frames.run(100);
    expect(target.scrollTop).toBe(21);
    target.scrollHeight = 500; // chart content shrank while the controller was running
    frames.run(200);
    expect(target.scrollTop).toBe(22);
    expect(controller.running).toBe(true);
    target.scrollHeight = 123; // new bottom is 23; next step must clamp and stop
    frames.run(300);
    expect(target.scrollTop).toBe(23);
    expect(controller.running).toBe(false);
    expect(frames.count).toBe(0);
  });

  it('does not duplicate frames and cleans up on stop or short charts', () => {
    const frames = makeFrames();
    const target = makeScroller();
    const controller = createAutoScrollController(frames);
    controller.start(target);
    controller.start(target);
    expect(frames.count).toBe(1);
    controller.stop();
    expect(frames.count).toBe(0);
    expect(controller.running).toBe(false);
    expect(controller.start(makeScroller({ height: 20, viewport: 20 }))).toBe(false);
  });

  it('classifies vertical keys without intercepting modified shortcuts', () => {
    expect(isVerticalScrollKey({ key: 'PageDown' })).toBe(true);
    expect(isVerticalScrollKey({ key: ' ', shiftKey: false })).toBe(true);
    expect(isVerticalScrollKey({ key: 'ArrowDown', ctrlKey: true })).toBe(false);
    expect(isVerticalScrollKey({ key: 'ArrowRight' })).toBe(false);
  });
});
