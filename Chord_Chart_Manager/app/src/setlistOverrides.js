export function resolveStartingPerformance(song, entry = null) {
  const writtenKey = song?.chart_written_key || song?.performance_key || 'C';
  return {
    key: entry?.transposed_key || song?.preferred_key || writtenKey,
    capo: entry?.capo_fret ?? song?.default_capo ?? song?.capo_fret ?? 0,
  };
}

export function buildPerformanceSaveTarget(context, key, capo) {
  if (context?.setlistId != null && context?.setlistEntry) {
    return {
      kind: 'setlist',
      setlistId: context.setlistId,
      position: context.setlistEntry.position,
      payload: { transposed_key: key, capo_fret: capo },
    };
  }
  return {
    kind: 'song',
    songId: context.song.id,
    payload: { preferred_key: key, default_capo: capo },
  };
}
