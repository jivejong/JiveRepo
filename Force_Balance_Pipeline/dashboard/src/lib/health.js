// Source health data shaping (doc 07 Phase 7, panel 4). Pure. Buffer depth is never reported by the probe, so it is always "n/a".

/** [{scan_id, t, complete, disconnected}] in ascending time. */
export function completenessStrip(scans) {
  return (scans || [])
    .map((s) => ({ scan_id: s.scan_id, t: s.scan_event_time, complete: Boolean(s.complete), disconnected: Boolean(s.any_disconnected) }))
    .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
}

/** {mode, lastSeen, lastScanComplete, bufferDepth: "n/a"} for the first source of the /api/source-health answer. */
export function summary(health) {
  const source = (health && health.sources && health.sources[0]) || null;
  return {
    mode: source ? source.current_mode : null,
    lastSeen: source ? source.last_seen_at : null,
    lastScanComplete: source ? source.last_scan_complete : null,
    bufferDepth: "n/a",
  };
}
