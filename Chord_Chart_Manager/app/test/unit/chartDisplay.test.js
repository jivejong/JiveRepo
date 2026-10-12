import { describe, expect, it } from 'vitest';
import { CHART_SIZE_STORAGE_KEY, CHART_SIZES, chartScale, normalizeChartSize,
  readChartSize, writeChartSize } from '../../src/lib/chartDisplay.js';

describe('chart display size preference', () => {
  it('uses five bounded, equally stepped choices with Default at 1x', () => {
    expect(CHART_SIZES.map(size => size.value)).toEqual(['-2', '-1', '0', '1', '2']);
    expect(CHART_SIZES.map(size => chartScale(size.value))).toEqual([0.85, 0.925, 1, 1.075, 1.15]);
  });

  it('defaults safely and persists only valid enum choices', () => {
    const values = new Map();
    const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
    expect(readChartSize(storage)).toBe('0');
    expect(writeChartSize('2', storage)).toBe(true);
    expect(values.get(CHART_SIZE_STORAGE_KEY)).toBe('2');
    expect(readChartSize(storage)).toBe('2');
    values.set(CHART_SIZE_STORAGE_KEY, 'large');
    expect(readChartSize(storage)).toBe('0');
    expect(normalizeChartSize(null)).toBe('0');
    expect(writeChartSize('large', storage)).toBe(false);
  });

  it('handles missing, inaccessible, and throwing storage', () => {
    expect(readChartSize(null)).toBe('0');
    expect(writeChartSize('1', null)).toBe(false);
    const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
    expect(readChartSize(blocked)).toBe('0');
    expect(writeChartSize('1', blocked)).toBe(false);
  });
});
