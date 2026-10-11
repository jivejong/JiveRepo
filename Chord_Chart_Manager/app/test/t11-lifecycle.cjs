async function bounded(promiseOrFactory, label, timeoutMs, onTimeout) {
  let timer;
  try {
    const operation = typeof promiseOrFactory === 'function' ? promiseOrFactory() : promiseOrFactory;
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => {
        try { onTimeout?.(); } catch { /* original timeout is retained */ }
        reject(new Error(`${label} exceeded ${timeoutMs} ms.`));
      }, timeoutMs);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

async function runCleanupStages(stages) {
  const errors = [];
  for (const [label, action] of stages) {
    try { await action(); } catch (error) { errors.push(new Error(`${label}: ${error.message}`, { cause: error })); }
  }
  if (errors.length) throw new AggregateError(errors, 'T11 cleanup had one or more failures.');
}

function startServerBounded(server, listen, timeoutMs = 15_000) {
  return new Promise((resolve, reject) => {
    let timer;
    let settled = false;
    const onListening = () => finish();
    const onError = error => finish(error);
    const removeStartListeners = () => {
      server.removeListener('listening', onListening);
      server.removeListener('error', onError);
    };
    const finish = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      removeStartListeners();
      error ? reject(error) : resolve();
    };

    // Register before listen(): synchronous throws and emitted errors share
    // the same rejection path, and a fast listening event cannot be missed.
    server.once('listening', onListening);
    server.once('error', onError);
    timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      removeStartListeners();

      // listen() has been requested. If a delayed bind completes after this
      // timeout, fence traffic in the caller and close it immediately.
      const closeLateServer = () => {
        server.removeListener('listening', closeLateServer);
        server.removeListener('error', ignoreLateError);
        try { server.close(() => {}); } catch { /* server may not have bound */ }
        try { server.closeAllConnections?.(); } catch { /* preserve timeout */ }
      };
      const ignoreLateError = () => {
        server.removeListener('listening', closeLateServer);
        server.removeListener('error', ignoreLateError);
      };
      server.once('listening', closeLateServer);
      server.once('error', ignoreLateError);
      try {
        if (server.listening) server.close(() => {});
      } catch { /* owned-resource cleanup continues in the runner's finally */ }
      try { server.closeAllConnections?.(); } catch { /* retain startup timeout */ }
      reject(new Error(`HTTP server startup timed out after ${timeoutMs} ms before listening.`));
    }, timeoutMs);

    try { listen(); } catch (error) { finish(error); }
  });
}

async function runWithCleanup(work, cleanup) {
  let result;
  let workError;
  try { result = await work(); } catch (error) { workError = error; }
  let cleanupError;
  try { await cleanup(); } catch (error) { cleanupError = error; }
  if (workError && cleanupError) {
    throw new AggregateError([workError, cleanupError], 'T11 work and cleanup failed.');
  }
  if (workError) throw workError;
  if (cleanupError) throw cleanupError;
  return result;
}

module.exports = { bounded, runCleanupStages, startServerBounded, runWithCleanup };
