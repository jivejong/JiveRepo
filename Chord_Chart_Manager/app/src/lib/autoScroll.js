export const AUTO_SCROLL_MIN_SPEED = 5;
export const AUTO_SCROLL_MAX_SPEED = 40;
export const AUTO_SCROLL_STEP = 5;
export const AUTO_SCROLL_DEFAULT_SPEED = 10;
// Treat longer frame stalls as suspended time instead of catching up in one jump.
export const AUTO_SCROLL_FRAME_GAP_RESET_MS = 1000;

export function clampAutoScrollSpeed(speed) {
  const numeric = Number(speed);
  if (!Number.isFinite(numeric)) return AUTO_SCROLL_DEFAULT_SPEED;
  return Math.max(AUTO_SCROLL_MIN_SPEED, Math.min(AUTO_SCROLL_MAX_SPEED,
    Math.round(numeric / AUTO_SCROLL_STEP) * AUTO_SCROLL_STEP));
}

export function isVerticalScrollKey(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
  return ['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(event.key);
}

export function createAutoScrollController({
  requestFrame = callback => requestAnimationFrame(callback),
  cancelFrame = handle => cancelAnimationFrame(handle),
  onRunningChange = () => {},
  onBottom = () => {},
} = {}) {
  let element = null;
  let frame = null;
  let previousTime = null;
  let position = 0;
  let speed = AUTO_SCROLL_DEFAULT_SPEED;
  let running = false;

  const pause = () => {
    if (!running) return;
    running = false;
    previousTime = null;
    if (frame !== null) cancelFrame(frame);
    frame = null;
    onRunningChange(false);
  };

  const tick = timestamp => {
    frame = null;
    if (!running || !element) return;
    if (previousTime !== null) {
      const elapsedMilliseconds = timestamp - previousTime;
      if (elapsedMilliseconds > AUTO_SCROLL_FRAME_GAP_RESET_MS) {
        // Keep the run active, but discard suspended time. Movement resumes
        // from this fresh baseline on the next ordinary animation frame.
        previousTime = timestamp;
        frame = requestFrame(tick);
        return;
      }
      const elapsedSeconds = Math.max(0, elapsedMilliseconds) / 1000;
      const bottom = Math.max(0, element.scrollHeight - element.clientHeight);
      position = Math.min(bottom, position + speed * elapsedSeconds);
      element.scrollTop = position;
      if (position >= bottom) {
        element.scrollTop = bottom;
        pause();
        onBottom(true);
        return;
      }
    }
    previousTime = timestamp;
    frame = requestFrame(tick);
  };

  return {
    start(target, nextSpeed = speed) {
      if (running) return true;
      element = target;
      position = element?.scrollTop ?? 0;
      speed = clampAutoScrollSpeed(nextSpeed);
      if (!element || element.scrollHeight <= element.clientHeight
          || element.scrollTop >= element.scrollHeight - element.clientHeight) {
        onBottom(Boolean(element && element.scrollTop >= element.scrollHeight - element.clientHeight));
        return false;
      }
      onBottom(false);
      running = true;
      previousTime = null;
      onRunningChange(true);
      frame = requestFrame(tick);
      return true;
    },
    pause,
    stop() {
      pause();
      element = null;
      previousTime = null;
    },
    setSpeed(value) { speed = clampAutoScrollSpeed(value); },
    get running() { return running; },
  };
}
