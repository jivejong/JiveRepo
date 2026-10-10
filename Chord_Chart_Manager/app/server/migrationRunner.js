const fs = require("node:fs");
const path = require("node:path");
const { validateBaseline } = require("./baselineValidation");
const { validateMigrationSql } = require("./migrationSql");

const LOCK_KEY = [1128480077, 6]; // Application-specific namespace plus runner version.

function listMigrations(directory) {
  const entries = fs.readdirSync(directory, { withFileTypes: true });
  const sqlCandidates = entries.filter((entry) => /\.sql$/i.test(entry.name));
  const files = sqlCandidates
    .filter((entry) => {
      if (!entry.isFile() || !/^\d{4}_[a-z0-9]+(?:_[a-z0-9]+)*\.sql$/.test(entry.name)) {
        throw new Error(`Invalid migration filename or file type: ${entry.name}. Use a regular NNNN_name.sql file.`);
      }
      return true;
    })
    .map((entry) => {
      const match = entry.name.match(/^(\d{4})_(.+)\.sql$/);
      return { version: match[1], name: match[2], file: path.join(directory, entry.name) };
    })
    .sort((a, b) => a.version.localeCompare(b.version));
  for (let i = 1; i < files.length; i += 1) {
    if (files[i - 1].version === files[i].version) throw new Error(`Duplicate migration version ${files[i].version}.`);
    if (Number(files[i].version) !== Number(files[i - 1].version) + 1) {
      throw new Error(`Migration sequence has a gap before ${files[i].version}.`);
    }
  }
  if (!files.length || files[0].version !== "0001" || files[0].name !== "baseline") {
    throw new Error("Migration directory must start with 0001_baseline.sql.");
  }
  return files;
}

async function hasApplicationObjects(client) {
  const result = await client.query(
    `SELECT count(*)::int AS count FROM pg_class c
     JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relname <> 'schema_migrations'
       AND c.relkind IN ('r','p','v','m','f','S')
     UNION ALL
     SELECT count(*)::int AS count FROM pg_proc p
     JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='public' AND p.proname <> 'schema_migrations'`
  );
  return result.rows.some((row) => row.count > 0);
}

async function validateMigrationBookkeeping(client) {
  const relation = await client.query(`
    SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='schema_migrations'`);
  if (!relation.rowCount) return false;
  if (relation.rowCount !== 1 || relation.rows[0].relkind !== "r") {
    throw new Error("public.schema_migrations exists but is not a regular table.");
  }
  const columns = (await client.query(`
    SELECT a.attname, format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull,
           pg_get_expr(d.adbin,d.adrelid) AS default_expr
    FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid
    JOIN pg_namespace n ON n.oid=t.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid=t.oid AND d.adnum=a.attnum
    WHERE n.nspname='public' AND t.relname='schema_migrations'
      AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum`)).rows;
  const actual = columns.map((c) => `${c.attname}|${c.type}|${c.attnotnull}|${c.default_expr || ""}`);
  const expected = [
    "version|character varying(4)|true|", "name|text|true|",
    "applied_at|timestamp with time zone|true|now()",
  ];
  const primaryKey = (await client.query(`
    SELECT array_to_json(array_agg(a.attname ORDER BY k.ordinality)) AS columns
    FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum,ordinality)
    JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
    WHERE c.conrelid='public.schema_migrations'::regclass AND c.contype='p'
    GROUP BY c.oid`)).rows;
  const constraintCount = (await client.query(
    "SELECT count(*)::int AS count FROM pg_constraint WHERE conrelid='public.schema_migrations'::regclass"
  )).rows[0].count;
  if (JSON.stringify(actual) !== JSON.stringify(expected) || constraintCount !== 1 || primaryKey.length !== 1 ||
      JSON.stringify(primaryKey[0].columns) !== JSON.stringify(["version"])) {
    throw new Error("public.schema_migrations has an incompatible structure; expected version varchar(4) PRIMARY KEY, name text NOT NULL, and applied_at timestamptz NOT NULL DEFAULT now().");
  }
  return true;
}

async function startAfterMigrations(migrate, listen) {
  await migrate();
  return listen();
}

async function runMigrationsWithClient(client, { migrationsDir } = {}) {
  const directory = migrationsDir || path.resolve(__dirname, "..", "db", "migrations");
  const migrations = listMigrations(directory);
  let locked = false;
  let inTransaction = false;
  try {
    await client.query("SELECT pg_advisory_lock($1, $2)", LOCK_KEY);
    locked = true;
    // Keep public as the DDL target; PostgreSQL implicitly searches pg_catalog first.
    await client.query("SET search_path TO public, pg_temp");

    await client.query("BEGIN");
    inTransaction = true;
    await client.query(`
      CREATE TABLE IF NOT EXISTS public.schema_migrations (
        version varchar(4) PRIMARY KEY,
        name text NOT NULL,
        applied_at timestamptz NOT NULL DEFAULT now()
      )`);
    await client.query("COMMIT");
    inTransaction = false;
    await validateMigrationBookkeeping(client);

    const applied = (await client.query(
      "SELECT version,name FROM public.schema_migrations ORDER BY version"
    )).rows;
    const appliedVersions = applied.map((row) => row.version);
    const knownVersions = new Set(migrations.map((migration) => migration.version));
    for (const version of appliedVersions) {
      if (!knownVersions.has(version)) throw new Error(`Database records unknown migration ${version}.`);
      const recorded = applied.find((row) => row.version === version);
      const expected = migrations.find((migration) => migration.version === version);
      if (recorded.name !== expected.name) throw new Error(`Recorded migration name does not match version ${version}.`);
    }
    for (let i = 0; i < appliedVersions.length; i += 1) {
      if (appliedVersions[i] !== migrations[i]?.version) {
        throw new Error("Recorded migrations are not a prefix of the available migration sequence.");
      }
    }

    if (appliedVersions.length === 0 && await hasApplicationObjects(client)) {
      await validateBaseline(client);
      await client.query("BEGIN");
      inTransaction = true;
      await client.query(
        "INSERT INTO public.schema_migrations(version,name) VALUES ($1,$2)",
        [migrations[0].version, migrations[0].name]
      );
      await client.query("COMMIT");
      inTransaction = false;
      appliedVersions.push(migrations[0].version);
      console.log("Adopted existing schema as migration 0001_baseline.");
    }

    for (const migration of migrations) {
      if (appliedVersions.includes(migration.version)) continue;
      const sql = fs.readFileSync(migration.file, "utf8");
      if (!sql.trim()) throw new Error(`Migration ${path.basename(migration.file)} is empty.`);
      validateMigrationSql(sql, path.basename(migration.file));
      await client.query("BEGIN");
      inTransaction = true;
      await client.query(sql);
      await client.query(
        "INSERT INTO public.schema_migrations(version,name) VALUES ($1,$2)",
        [migration.version, migration.name]
      );
      await client.query("COMMIT");
      inTransaction = false;
      appliedVersions.push(migration.version);
      console.log(`Applied migration ${migration.version}_${migration.name}.`);
    }
    return appliedVersions;
  } catch (error) {
    if (inTransaction) {
      try { await client.query("ROLLBACK"); } catch { /* Keep the original failure. */ }
    }
    throw error;
  } finally {
    if (locked) await client.query("SELECT pg_advisory_unlock($1, $2)", LOCK_KEY);
  }
}

module.exports = { listMigrations, hasApplicationObjects, validateMigrationBookkeeping, runMigrationsWithClient, startAfterMigrations };
