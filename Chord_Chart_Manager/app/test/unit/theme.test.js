import { describe, expect, it } from 'vitest';
import {
  applyTheme, getSystemPrefersDark, readThemePreference, resolveTheme,
  subscribeToSystemTheme, THEME_COLORS, THEME_STORAGE_KEY, writeThemePreference,
} from '../../src/lib/theme.js';
import { compositeColor, contrastOverLayers, contrastRatio } from '../../src/lib/colorContrast.js';

describe('CSS color contrast', () => {
  it('matches known opaque contrast ratios', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 5);
    expect(contrastRatio('#FFFFFF', '#000000')).toBeCloseTo(21, 5);
  });

  it('composites translucent colors and ancestor layers in CSS paint order', () => {
    expect(compositeColor('rgba(255, 0, 0, 0.5)', '#000000'))
      .toEqual([127.5, 0, 0, 1]);
    expect(contrastOverLayers('#FF8585', [
      'rgba(224, 92, 92, 0.12)', '#22222A',
    ], '#FFFFFF')).toBeGreaterThan(4.5);
    expect(contrastOverLayers('#E05C5C', ['#22222A'], '#FFFFFF')).toBeLessThan(4.5);
  });
});

describe('theme preference', () => {
  it('accepts only supported stored values and defaults invalid/missing values to System', () => {
    const values = new Map([[THEME_STORAGE_KEY, 'light']]);
    const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
    expect(readThemePreference(storage)).toBe('light');
    values.set(THEME_STORAGE_KEY, 'sepia');
    expect(readThemePreference(storage)).toBe('system');
    values.delete(THEME_STORAGE_KEY);
    expect(readThemePreference(storage)).toBe('system');
    expect(writeThemePreference('dark', storage)).toBe(true);
    expect(readThemePreference(storage)).toBe('dark');
    expect(writeThemePreference('sepia', storage)).toBe(false);
  });

  it('falls back to System when storage is unavailable or throws', () => {
    const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
    expect(readThemePreference(blocked)).toBe('system');
    expect(writeThemePreference('light', blocked)).toBe(false);
    expect(readThemePreference(null)).toBe('system');
    expect(writeThemePreference('dark', null)).toBe(false);
  });

  it('follows the system preference while System is selected and respects explicit overrides', () => {
    expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
    expect(getSystemPrefersDark(() => ({ matches: true }))).toBe(true);
  });

  it('subscribes to device changes and removes the listener on cleanup', () => {
    let listener;
    let removed;
    const media = {
      matches: false,
      addEventListener(type, callback) { expect(type).toBe('change'); listener = callback; },
      removeEventListener(type, callback) { expect(type).toBe('change'); removed = callback; },
    };
    const changes = [];
    const cleanup = subscribeToSystemTheme(value => changes.push(value), media);
    listener({ matches: true });
    cleanup();
    expect(changes).toEqual([true]);
    expect(removed).toBe(listener);
  });

  it('synchronizes the document palette, browser color scheme, and theme-color metadata', () => {
    const attributes = {};
    const meta = { setAttribute: (name, value) => { meta[name] = value; } };
    const document = {
      documentElement: { dataset: attributes, style: {} },
      querySelector: () => meta,
    };
    applyTheme(document, 'light');
    expect(attributes.theme).toBe('light');
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(meta.content).toBe(THEME_COLORS.light);
  });
});
