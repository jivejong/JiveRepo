export function shouldIgnoreChartRowSwipe(target) {
  const row = target?.closest?.('.chart-line, .paired-literal-scroll');
  return Boolean(row && row.scrollWidth > row.clientWidth + 1);
}
