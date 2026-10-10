export const THEME_STORAGE_KEY = 'ccm-theme-preference';
export const THEME_PREFERENCES = Object.freeze(['light', 'dark', 'system']);
export const THEME_COLORS = Object.freeze({ light: '#F5F3EE', dark: '#1A1A1F' });

export function isThemePreference(value) {
  return THEME_PREFERENCES.includes(value);
}

function getStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

export function readThemePreference(storage = undefined) {
  const target = storage === undefined ? getStorage() : storage;
  try {
    const stored = target?.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : 'system';
  } catch {
    return 'system';
  }
}

export function writeThemePreference(preference, storage = undefined) {
  if (!isThemePreference(preference)) return false;
  const target = storage === undefined ? getStorage() : storage;
  try {
    if (!target) return false;
    target.setItem(THEME_STORAGE_KEY, preference);
    return true;
  } catch {
    return false;
  }
}

export function getSystemPrefersDark(matchMedia = undefined) {
  try {
    const matcher = matchMedia === undefined ? globalThis.matchMedia : matchMedia;
    return Boolean(matcher?.('(prefers-color-scheme: dark)').matches);
  } catch {
    return false;
  }
}

export function resolveTheme(preference, prefersDark) {
  if (preference === 'light' || preference === 'dark') return preference;
  return prefersDark ? 'dark' : 'light';
}

export function applyTheme(document, theme) {
  if (theme !== 'light' && theme !== 'dark') return;
  const root = document?.documentElement;
  if (!root) return;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  const color = THEME_COLORS[theme];
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.setAttribute('content', color);
}

export function subscribeToSystemTheme(onChange, mediaQuery = undefined) {
  let query = mediaQuery;
  if (query === undefined) {
    try { query = globalThis.matchMedia?.('(prefers-color-scheme: dark)'); } catch { query = null; }
  }
  if (!query) return () => {};
  const listener = event => onChange(Boolean(event.matches));
  if (query.addEventListener) query.addEventListener('change', listener);
  else query.addListener?.(listener);
  return () => {
    if (query.removeEventListener) query.removeEventListener('change', listener);
    else query.removeListener?.(listener);
  };
}
