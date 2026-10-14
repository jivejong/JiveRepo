// Reject results from an asynchronous list read after a newer projection starts.
export function createLatestRequestGate() {
  let generation = 0;
  return {
    begin: () => ++generation,
    invalidate: () => { generation += 1; },
    isCurrent: ticket => ticket === generation,
    publishIfCurrent(ticket, cancelled, publish) {
      if (cancelled || ticket !== generation) return false;
      publish();
      return true;
    },
  };
}
