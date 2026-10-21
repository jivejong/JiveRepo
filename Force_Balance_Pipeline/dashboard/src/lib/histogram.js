// Ingest lag histogram data (doc 07 Phase 7, panel 5: proves late-arriving handling). Pure. The view always returns the ten bins.

const BIN_COUNT = 10;

/** The 10 bins in bin_order as {label, normal, replayed, buffered}; normal is rows minus replayed rows. Throws if a bin is missing. */
export function bars(rows) {
  const byOrder = new Map((rows || []).map((r) => [r.bin_order, r]));
  const out = [];
  for (let order = 1; order <= BIN_COUNT; order++) {
    const r = byOrder.get(order);
    if (!r) throw new Error(`ingest lag bin ${order} is missing`);
    out.push({ label: r.label, normal: r.rows - r.replayed_rows, replayed: r.replayed_rows, buffered: r.buffered_rows });
  }
  return out;
}
