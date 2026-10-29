// /chartParser.js is emitted directly from server/chartParser.js and loaded by
// index.html before the React bundle. Node requires that same UMD source.
const parser = globalThis.ChartParser;

if (!parser) {
  throw new Error('Shared chart parser failed to initialize');
}

export const parseChartBody = parser.parseChartBody;
export const isDecoratedChordLine = parser.isDecoratedChordLine;
export const decoratedChordTokenSpans = parser.decoratedChordTokenSpans;
export const decoratedChordAnalysis = parser.decoratedChordAnalysis;
export const transposeDecoratedRawLines = parser.transposeDecoratedRawLines;
