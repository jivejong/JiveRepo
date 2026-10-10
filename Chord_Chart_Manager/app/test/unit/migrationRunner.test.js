import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { listMigrations, validateMigrationBookkeeping, runMigrationsWithClient, startAfterMigrations } = require('../../server/migrationRunner.js');
const { validateMigrationSql } = require('../../server/migrationSql.js');
const temporaryDirectories = [];

function migrationDirectory(files) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ccm-migration-test-'));
  temporaryDirectories.push(directory);
  for (const [name, contents] of Object.entries(files)) fs.writeFileSync(path.join(directory, name), contents);
  return directory;
}

function fakeClient() {
  const queries = [];
  const applied = [];
  const tables = [];
  let pendingApplied = [];
  let pendingTables = [];
  return {
    queries,
    applied,
    tables,
    async query(sql, values = []) {
      const statement = String(sql).trim();
      queries.push(statement);
      if (/^BEGIN$/i.test(statement)) { pendingApplied = []; pendingTables = []; return { rows: [] }; }
      if (/^COMMIT$/i.test(statement)) {
        applied.push(...pendingApplied); tables.push(...pendingTables);
        pendingApplied = []; pendingTables = []; return { rows: [] };
      }
      if (/^ROLLBACK$/i.test(statement)) { pendingApplied = []; pendingTables = []; return { rows: [] }; }
      if (/pg_advisory_(lock|unlock)/i.test(statement)) return { rows: [{ ok: true }] };
      if (/CREATE TABLE IF NOT EXISTS public\.schema_migrations/i.test(statement)) return { rows: [] };
      if (/SELECT version,name FROM public\.schema_migrations/i.test(statement)) {
        return { rows: [...applied].sort((a, b) => a.version.localeCompare(b.version)) };
      }
      if (/SELECT count\(\*\)::int AS count FROM pg_class/i.test(statement)) return { rows: [{ count: 0 }] };
      if (/FAIL_MIGRATION/.test(statement)) {
        if (/CREATE TABLE migration_should_rollback/i.test(statement)) pendingTables.push('migration_should_rollback');
        throw new Error('fixture migration failure');
      }
      if (/CREATE TABLE migration_marker/i.test(statement)) pendingTables.push('migration_marker');
      if (/INSERT INTO public\.schema_migrations/i.test(statement)) {
        pendingApplied.push({ version: values[0], name: values[1] });
      }
      return { rows: [] };
    },
  };
}

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

describe('migration runner', () => {
  it('does not begin listening when migration startup fails', async () => {
    const listen = vi.fn();
    await expect(startAfterMigrations(
      async () => { throw new Error('migration blocked startup'); },
      listen,
    )).rejects.toThrow('migration blocked startup');
    expect(listen).not.toHaveBeenCalled();
  });

  it('sorts numbered SQL files and rejects malformed names or sequence gaps', () => {
    const directory = migrationDirectory({
      '0001_baseline.sql': 'SELECT 1;',
      '0002_add_example.sql': 'SELECT 2;',
    });
    expect(listMigrations(directory).map((migration) => migration.version)).toEqual(['0001', '0002']);
    fs.writeFileSync(path.join(directory, '0004_gap.sql'), 'SELECT 4;');
    expect(() => listMigrations(directory)).toThrow(/gap/);
    fs.writeFileSync(path.join(directory, 'oops.sql'), 'SELECT 0;');
    expect(() => listMigrations(directory)).toThrow(/Invalid migration filename/);
  });

  it('rejects uppercase SQL candidates and duplicate migration numbers', () => {
    const directory = migrationDirectory({ '0001_baseline.sql': 'SELECT 1;' });
    fs.writeFileSync(path.join(directory, '0002_add.SQL'), 'SELECT 2;');
    expect(() => listMigrations(directory)).toThrow(/Invalid migration filename/);
    fs.rmSync(path.join(directory, '0002_add.SQL'));
    fs.writeFileSync(path.join(directory, '0002_bad__name.sql'), 'SELECT 2;');
    expect(() => listMigrations(directory)).toThrow(/Invalid migration filename/);
    fs.rmSync(path.join(directory, '0002_bad__name.sql'));
    fs.writeFileSync(path.join(directory, '0001_other.sql'), 'SELECT 2;');
    expect(() => listMigrations(directory)).toThrow(/Duplicate migration version/);
  });

  it('rejects transaction commands but permits transaction words in comments, literals, identifiers, and function bodies', () => {
    for (const sql of [
      'BEGIN;', 'START TRANSACTION;', 'COMMIT;', 'END;', 'ROLLBACK;', 'ABORT;',
      'SAVEPOINT x;', 'RELEASE SAVEPOINT x;', 'PREPARE TRANSACTION \'x\';',
    ]) expect(() => validateMigrationSql(sql, 'fixture.sql')).toThrow(/transaction control/);
    expect(() => validateMigrationSql(`
      -- COMMIT
      /* ROLLBACK /* SAVEPOINT */ still comment */
      SELECT 'BEGIN', E'COMMIT', "ROLLBACK";
      CREATE FUNCTION public.test() RETURNS void LANGUAGE plpgsql AS $body$
      BEGIN RAISE NOTICE 'COMMIT'; END; $body$;
    `)).not.toThrow();
    expect(() => validateMigrationSql(String.raw`SELECT E'quote\' and COMMIT';`)).not.toThrow();
    expect(() => validateMigrationSql(String.raw`SELECT 'backslash \'; COMMIT;`)).toThrow(/transaction control/);
    expect(() => validateMigrationSql(`CREATE PROCEDURE public.bad() LANGUAGE plpgsql AS $body$ BEGIN COMMIT; END; $body$;`)).toThrow(/transaction control/);
    expect(() => validateMigrationSql(`CREATE PROCEDURE public.dynamic() LANGUAGE plpgsql AS $body$ BEGIN EXECUTE 'COMMIT'; END; $body$;`)).toThrow(/dynamic SQL/);
    expect(() => validateMigrationSql(`CALL public.transaction_boundary();`)).toThrow(/procedure call/);
    expect(() => validateMigrationSql(`SELECT $body$ COMMIT; $body$;`)).not.toThrow();
  });

  it('rejects incompatible migration bookkeeping before trusting its rows', async () => {
    const client = {
      async query(sql) {
        if (/SELECT c\.relkind/.test(sql)) return { rowCount: 1, rows: [{ relkind: 'r' }] };
        if (/SELECT a\.attname/.test(sql)) return { rows: [
          { attname: 'version', type: 'text', attnotnull: true, default_expr: null },
          { attname: 'name', type: 'text', attnotnull: true, default_expr: null },
          { attname: 'applied_at', type: 'timestamp with time zone', attnotnull: true, default_expr: 'now()' },
        ] };
        if (/SELECT array_to_json/.test(sql)) return { rows: [{ columns: ['version'] }] };
        if (/SELECT count\(\*\)::int AS count FROM pg_constraint/.test(sql)) return { rows: [{ count: 1 }] };
        throw new Error('unexpected bookkeeping query');
      },
    };
    await expect(validateMigrationBookkeeping(client)).rejects.toThrow(/incompatible structure/);
  });

  it('locks, applies pending files in separate transactions, and skips applied files on repeat', async () => {
    const directory = migrationDirectory({
      '0001_baseline.sql': 'CREATE TABLE migration_marker; ',
      '0002_example.sql': 'SELECT 2;',
    });
    const client = fakeClient();
    await runMigrationsWithClient(client, { migrationsDir: directory });
    const lockIndex = client.queries.findIndex((query) => /pg_advisory_lock/.test(query));
    const pathIndex = client.queries.findIndex((query) => /SET search_path TO public, pg_temp/.test(query));
    const stateIndex = client.queries.findIndex((query) => /SELECT version,name FROM public\.schema_migrations/.test(query));
    expect(lockIndex).toBeGreaterThanOrEqual(0);
    expect(pathIndex).toBeGreaterThan(lockIndex);
    expect(stateIndex).toBeGreaterThan(pathIndex);
    const commitCount = client.queries.filter((query) => query === 'COMMIT').length;
    expect(client.applied.map((row) => row.version)).toEqual(['0001', '0002']);
    expect(client.tables).toEqual(['migration_marker']);
    await runMigrationsWithClient(client, { migrationsDir: directory });
    expect(client.queries.filter((query) => query === 'COMMIT')).toHaveLength(commitCount + 1); // bookkeeping table transaction only
    expect(client.applied.map((row) => row.version)).toEqual(['0001', '0002']);
    expect(client.queries.filter((query) => /pg_advisory_unlock/.test(query))).toHaveLength(2);
  });

  it('rolls back a failed migration without recording its version', async () => {
    const directory = migrationDirectory({
      '0001_baseline.sql': 'SELECT 1;',
      '0002_failing.sql': 'CREATE TABLE migration_should_rollback; SELECT FAIL_MIGRATION;',
    });
    const client = fakeClient();
    await expect(runMigrationsWithClient(client, { migrationsDir: directory })).rejects.toThrow('fixture migration failure');
    expect(client.applied.map((row) => row.version)).toEqual(['0001']);
    expect(client.tables).not.toContain('migration_should_rollback');
    expect(client.queries).toContain('ROLLBACK');
    expect(client.queries.filter((query) => /pg_advisory_unlock/.test(query))).toHaveLength(1);
  });
});
