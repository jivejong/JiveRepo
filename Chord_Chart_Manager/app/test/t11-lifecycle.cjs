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

module.exports = { bounded, runCleanupStages };
