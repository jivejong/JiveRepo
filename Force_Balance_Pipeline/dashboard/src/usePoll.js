// A React hook that loads an API path now and then every POLL_MS, backing off (lib/poll.js) while it fails (doc 07 Phase 7).
// It keeps the last good data on screen and reports the error beside it instead of crashing.
import { useEffect, useState } from "react";

import { getJson } from "./api.js";
import { nextDelay } from "./lib/poll.js";

/** {data, error} for `path`; a null path loads nothing. */
export function usePoll(path) {
  const [state, setState] = useState({ data: null, error: null });

  useEffect(() => {
    if (!path) {
      setState({ data: null, error: null });
      return undefined;
    }
    let cancelled = false;
    let timer = null;
    let failures = 0;
    setState({ data: null, error: null });

    const load = async () => {
      try {
        const data = await getJson(path);
        failures = 0;
        if (!cancelled) setState({ data, error: null });
      } catch (e) {
        failures += 1;
        if (!cancelled) setState((s) => ({ data: s.data, error: e.message }));
      }
      if (!cancelled) timer = setTimeout(load, nextDelay(failures));
    };
    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [path]);

  return state;
}
