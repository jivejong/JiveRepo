import { describe, expect, it, vi } from 'vitest';
import { shouldIgnoreChartRowSwipe } from '../../src/lib/chartSwipe';

describe('shouldIgnoreChartRowSwipe', () => {
  it('ignores navigation gestures originating in horizontally scrollable paired rows', () => {
    const closest = vi.fn(() => ({ scrollWidth: 420, clientWidth: 320 }));
    expect(shouldIgnoreChartRowSwipe({ closest })).toBe(true);
    expect(closest).toHaveBeenCalledWith('.chart-line, .paired-literal-scroll');
  });

  it('keeps the existing chart-line guard and permits rows without horizontal overflow', () => {
    expect(shouldIgnoreChartRowSwipe({ closest: () => ({ scrollWidth: 321, clientWidth: 320 }) })).toBe(false);
    expect(shouldIgnoreChartRowSwipe({ closest: () => null })).toBe(false);
  });
});
