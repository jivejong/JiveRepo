const { Pool } = require("pg");
const defaultPool = require("./db").pool;
const { runMigrationsWithClient } = require("./migrationRunner");

function poolForDatabase(database) {
  if (!database) return defaultPool;
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(database)) {
    throw new Error("Database name must be a simple PostgreSQL identifier.");
  }
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required.");
  const url = new URL(process.env.DATABASE_URL);
  url.pathname = `/${database}`;
  return new Pool({ connectionString: url.toString() });
}

async function runMigrations(options = {}) {
  const pool = options.pool || poolForDatabase(options.database);
  const ownsPool = !options.pool && Boolean(options.database);
  let client;
  try {
    client = await pool.connect();
    return await runMigrationsWithClient(client, options);
  } finally {
    if (client) client.release();
    if (ownsPool) await pool.end();
  }
}

async function main() {
  const args = process.argv.slice(2);
  let database;
  while (args.length) {
    const flag = args.shift();
    if (flag !== "--database" || !args.length || database) {
      throw new Error("Usage: npm run migrate [-- --database DATABASE]");
    }
    database = args.shift();
  }
  await runMigrations({ database });
}

if (require.main === module) {
  (async () => {
    let failure;
    try { await main(); } catch (error) { failure = error; }
    try { await defaultPool.end(); } catch (error) { failure ||= error; }
    if (failure) {
      console.error(`Migration failed: ${failure.message}`);
      process.exitCode = 1;
    }
  })();
}

module.exports = { runMigrations };
