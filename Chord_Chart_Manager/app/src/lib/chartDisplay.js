export const CHART_SIZE_STORAGE_KEY = 'ccm-chart-size';
export const CHART_SIZES = Object.freeze([
  { value: '-2', label: '-2 Smallest', scale: 0.85 },
  { value: '-1', label: '-1 Smaller', scale: 0.925 },
  { value: '0', label: 'Default', scale: 1 },
  { value: '1', label: '+1 Larger', scale: 1.075 },
  { value: '2', label: '+2 Largest', scale: 1.15 },
]);

export function normalizeChartSize(value) {
  return CHART_SIZES.some(item => item.value === value) ? value : '0';
}

function getStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

export function readChartSize(storage = undefined) {
  const target = storage === undefined ? getStorage() : storage;
  try { return normalizeChartSize(target?.getItem(CHART_SIZE_STORAGE_KEY)); }
  catch { return '0'; }
}

export function writeChartSize(value, storage = undefined) {
  if (normalizeChartSize(value) !== value) return false;
  const target = storage === undefined ? getStorage() : storage;
  try {
    if (!target) return false;
    target.setItem(CHART_SIZE_STORAGE_KEY, value);
    return true;
  } catch { return false; }
}

export function chartScale(value) {
  return CHART_SIZES.find(item => item.value === normalizeChartSize(value)).scale;
}
