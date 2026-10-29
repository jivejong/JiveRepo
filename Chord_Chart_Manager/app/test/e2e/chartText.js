// Helpers for writing test charts as ordinary chart text.

// One lyric line with each chord as an inline {symbol} at its column.
// Chords sharing a column keep their order.
export function bracedLyric(text, chords) {
  let line = '';
  let last = 0;
  for (const { sym, pos } of [...chords].sort((a, b) => a.pos - b.pos)) {
    line += `${text.slice(last, pos)}{${sym}}`;
    last = pos;
  }
  return line + text.slice(last);
}

// A chord row placing each symbol at its column, for the lyric line below it.
export function chordRow(chords) {
  return chords.reduce((row, { sym, pos }) => `${row.padEnd(pos)}${sym}`, '');
}
