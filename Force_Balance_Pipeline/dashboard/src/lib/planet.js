// Planet detail data shaping (doc 07 Phase 7, panel 2). Pure.

/** The reading columns of each channel and the channel name used by the baseline bands. */
export const CHANNELS = [
  { channel: "midichlorian", column: "midichlorian_ppm", z: "z_midi" },
  { channel: "kyber", column: "kyber_resonance", z: "z_kyber" },
  { channel: "dark_side", column: "dark_side_activity", z: "z_dark" },
];

function bandsFor(bands, channel) {
  const row = (bands || []).find((b) => b.channel === channel);
  if (!row) return { mean: null, lower1: null, upper1: null, lower2: null, upper2: null };
  return { mean: row.mean_90d, lower1: row.lower_1, upper1: row.upper_1, lower2: row.lower_2, upper2: row.upper_2 };
}

/** For each channel: {channel, points: [{t, value, z, replayed}], bands: {mean, lower1, upper1, lower2, upper2}}. Points keep the order of
 *  the readings (ascending time from the server); the bands are null when the planet has no baseline row for the channel. */
export function channelSeries(readings, bands) {
  return CHANNELS.map(({ channel, column, z }) => ({
    channel,
    points: (readings || []).map((r) => ({ t: r.event_time, value: r[column], z: r[z], replayed: Boolean(r.is_replayed) })),
    bands: bandsFor(bands, channel),
  }));
}
