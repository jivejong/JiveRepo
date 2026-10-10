function parseCssColor(value) {
  if (Array.isArray(value)) return value;
  const input = value.trim();
  if (input.startsWith('#')) {
    let hex = input.slice(1);
    if (hex.length === 3 || hex.length === 4) {
      hex = [...hex].map(channel => channel + channel).join('');
    }
    if (hex.length === 6) hex += 'ff';
    if (hex.length !== 8) throw new TypeError(`Unsupported CSS color: ${value}`);
    return [0, 2, 4, 6].map((offset, index) => {
      const channel = parseInt(hex.slice(offset, offset + 2), 16);
      return index === 3 ? channel / 255 : channel;
    });
  }

  const channels = input.match(/[\d.]+/g)?.map(Number);
  if (!channels || channels.length < 3 || channels.length > 4) {
    throw new TypeError(`Unsupported CSS color: ${value}`);
  }
  return [channels[0], channels[1], channels[2], channels[3] ?? 1];
}

function compositeColor(foreground, background) {
  const fg = parseCssColor(foreground);
  const bg = parseCssColor(background);
  const alpha = fg[3] + bg[3] * (1 - fg[3]);
  if (alpha === 0) return [0, 0, 0, 0];
  return [0, 1, 2].map(index =>
    (fg[index] * fg[3] + bg[index] * bg[3] * (1 - fg[3])) / alpha,
  ).concat(alpha);
}

function luminance(color) {
  const channels = color.slice(0, 3).map(channel => channel / 255).map(channel =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

export function contrastRatio(foreground, background) {
  const fg = parseCssColor(foreground);
  const opaqueBackground = compositeColor(background, '#FFFFFF');
  const opaqueForeground = compositeColor(foreground, opaqueBackground);
  const values = [luminance(opaqueForeground), luminance(opaqueBackground)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

/**
 * Compute text contrast over CSS background colors ordered nearest-to-farthest.
 * The outermost opaque document surface normally replaces the canvas fallback.
 */
export function contrastOverLayers(foreground, backgroundsNearestFirst, canvas = '#FFFFFF') {
  const effectiveBackground = [...backgroundsNearestFirst].reverse()
    .reduce((background, layer) => compositeColor(layer, background), canvas);
  return contrastRatio(foreground, colorString(effectiveBackground));
}

function colorString([red, green, blue, alpha]) {
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

export { compositeColor, parseCssColor };
