import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const safety = require('../../server/mainEnrichmentSafety.js');
const { applyMainSongEnrichment, authorizeMainConnection, stableJson } = require('../../server/applySongEnrichment.js');
const crypto=require('node:crypto');
const {buildEnrichmentPreview}=require('../../server/existingSongEnrichment.js');

function memoryFs() {
  const files = new Map();
  return {
    files,
    async readFile(file) { if (!files.has(file)) throw new Error('ENOENT'); return files.get(file); },
    async writeFile(file, data, options) { if (options?.flag === 'wx' && files.has(file)) throw new Error('EEXIST'); files.set(file, Buffer.from(data)); },
    async mkdir() {},
  };
}

const runId = '94c50cb4-8394-4f9a-8eb4-598725fc3959';
const backupPath = require('node:path').join(safety.PRIVATE_BACKUPS, 'synthetic-backup.dump');
function proofPayload() {
  const snapshot = 'a'.repeat(64);
  return { proof_version: 1, run_id: runId, target_database: 'chords', manifest_sha256: 'b'.repeat(64),
    backup: { path: backupPath, sha256: 'BACKUP_HASH' }, live_snapshot_sha256: snapshot,
    restored_snapshot_sha256: snapshot,
    restore: { run_id: runId, scratch_database: `ccm_restore_${runId.replaceAll('-', '')}`,
      owner_marker: `CCM main apply restore verification run ${runId}`, cleanup_absence: true },
    created_at: new Date('2026-10-19T12:00:00Z').toISOString() };
}
function approvedManifest(){
 const snapshot={id:727,updated_at:'2026-10-13T03:17:35.356314Z',release_year:null,original_key:null,bpm:null,genres:[],vibes:[]};
 const candidate={value:1973,source:'Gemini',response_digest:'a'.repeat(64),model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v2',schema_version:'missing-metadata-schema-v2'};
 const evidence={mbid:null,scalars:{release_year:[candidate],original_key:[],bpm:[]},genres:[],vibes:[]};
 const review_decision={mbid:null,scalars:{release_year:{value:1973,source:'Gemini',response_digest:candidate.response_digest}},genres:[],vibes:[]};
 const preview=buildEnrichmentPreview(snapshot,evidence,review_decision,{genres:[],vibes:[]});
 return{format_version:1,operation_id:runId,songs:[{song_id:727,base_version:snapshot.updated_at,identity:{title:'Fixture',artist_id:1,artist_name:'Fixture Artist'},evidence,review_decision,preview}]};
}

describe('main apply restore proof safeguards', () => {
  it('signs and verifies backup bytes and complete snapshot evidence without trusting operator text', async () => {
    const filesystem = memoryFs();
    filesystem.files.set(backupPath, Buffer.from('synthetic archive bytes'));
    const payload = proofPayload();
    payload.backup.sha256 = require('node:crypto').createHash('sha256').update(filesystem.files.get(backupPath)).digest('hex');
    const signed = await safety.signRestoreProof(payload, { filesystem });
    await expect(safety.verifyRestoreProof(signed, { filesystem, now: Date.parse('2026-10-19T13:00:00Z') })).resolves.toBe(true);
    await expect(safety.verifyRestoreProof({ ...signed, live_snapshot_sha256: 'c'.repeat(64) }, { filesystem })).rejects.toThrow('RESTORE_PROOF_INVALID');
    filesystem.files.set(backupPath, Buffer.from('tampered archive'));
    await expect(safety.verifyRestoreProof(signed, { filesystem, now: Date.parse('2026-10-19T13:00:00Z') }))
      .rejects.toThrow('BACKUP_HASH_MISMATCH');
  });

  it('refuses missing main opt-in before connecting to any database client', async () => {
    let connects = 0;
    const pool = { connect: async () => { connects += 1; throw new Error('must not connect'); } };
    await expect(applyMainSongEnrichment(pool, {}, {})).rejects.toMatchObject({ code: 'MAIN_APPLY_GATE_REQUIRED' });
    expect(connects).toBe(0);
  });

  it('checks actual database identity and migration 0004 before receipt lookup', async () => {
    const gate = { expected_database: 'chords', restore_proof: { live_snapshot_sha256: 'a'.repeat(64) } };
    const queries = [];
    const client = { query: async sql => {
      queries.push(sql);
      if (sql.includes('current_database()')) return { rows: [{ database_name: 'chords' }] };
      if (sql.includes("version='0004'")) return { rowCount: 1 };
      throw new Error('snapshot gate belongs only to a new operation');
    } };
    await expect(authorizeMainConnection(client, gate)).resolves.toBeUndefined();
    expect(queries[0]).toContain('current_database()');
    expect(queries[1]).toContain("version='0004'");
    expect(queries).toHaveLength(2);

    const missingMigration = { query: async sql => sql.includes('current_database()')
      ? { rows: [{ database_name: 'chords' }] } : { rowCount: 0 } };
    await expect(authorizeMainConnection(missingMigration, gate)).rejects.toMatchObject({ code: 'MIGRATION_REQUIRED' });
    const wrongDatabase = { query: async () => ({ rows: [{ database_name: 'other' }] }) };
    await expect(authorizeMainConnection(wrongDatabase, gate)).rejects.toMatchObject({ code: 'DATABASE_IDENTITY_MISMATCH' });
  });

  it('uses database-side hashes for every application table and excludes no table rows from the hash', async () => {
    let sql;
    const client = { query: async statement => {
      sql = statement;
      return { rows: safety.TABLES.map(([table_name]) => ({ table_name, row_count: '0', digest: 'd'.repeat(32) })).sort((a,b)=>a.table_name.localeCompare(b.table_name)) };
    } };
    await expect(safety.protectedSnapshotSha256(client)).resolves.toMatch(/^[a-f0-9]{64}$/);
    for (const [name] of safety.TABLES) expect(sql).toContain(`'${name}'::text`);
    expect(sql).toContain('public.song_enrichment_runs');
    expect(sql).toContain('to_jsonb(t)');
    expect(sql).toContain('LEFT JOIN all_rows');
    expect(sql).toContain('COUNT(r.table_name)::text row_count');
  });

  it('hashes all-empty protected tables and mixed empty/nonempty states with row counts', async () => {
    let rows=safety.TABLES.map(([table_name])=>({table_name,row_count:'0',digest:'e'.repeat(32)})).sort((a,b)=>a.table_name.localeCompare(b.table_name));
    const client={query:async()=>({rows})};
    const allEmpty=await safety.protectedSnapshotSha256(client);
    rows=rows.map(row=>row.table_name==='song_enrichment_runs'?{...row,row_count:'1',digest:'f'.repeat(32)}:row);
    const mixed=await safety.protectedSnapshotSha256(client);
    expect(allEmpty).not.toBe(mixed);
    expect(safety.TABLES.map(([name])=>name)).toEqual(expect.arrayContaining(['song_enrichment_runs','setlists','setlist_songs','song_genres','song_vibes']));
    rows=rows.slice(1);
    await expect(safety.protectedSnapshotSha256(client)).rejects.toThrow('SNAPSHOT_INCOMPLETE');
  });

  it('copies manifest, nested deltas, gate hash and restore proof before the first await, then replays the original receipt', async () => {
    const manifest=approvedManifest(),original=structuredClone(manifest),hash=crypto.createHash('sha256').update(stableJson(original)).digest('hex');
    const gate={apply_main:true,expected_database:'chords',manifest_sha256:hash,manifest_path:'reviewed.json',target_ids:[727],
      restore_proof:{target_database:'chords',manifest_sha256:hash,live_snapshot_sha256:'a'.repeat(64),proof_version:1}};
    let resolveConnect,operationIdSeen=null;const queries=[];
    const client={query:async(sql,params)=>{queries.push(sql);
      if(sql.includes('current_database()'))return{rows:[{database_name:'chords'}]};
      if(sql.includes("version='0004'"))return{rowCount:1,rows:[]};
      if(sql.includes('SELECT request_hash,response FROM public.song_enrichment_runs')){operationIdSeen=params[0];return{rowCount:1,rows:[{request_hash:hash,response:{operation_id:runId,songs:[{song_id:727,status:'applied'}]}}]};}
      return{rowCount:0,rows:[]};
    },release(){}};
    const pool={connect:()=>new Promise(resolve=>{resolveConnect=()=>resolve(client);})};
    const pending=applyMainSongEnrichment(pool,manifest,gate);
    manifest.operation_id='22222222-2222-4222-8222-222222222222';manifest.songs[0].preview.scalar_changes[0].after=1999;
    gate.manifest_sha256='c'.repeat(64);gate.target_ids[0]=999;gate.restore_proof.manifest_sha256='d'.repeat(64);
    resolveConnect();
    await expect(pending).resolves.toMatchObject({operation_id:runId});
    expect(operationIdSeen).toBe(runId);
    expect(queries.some(sql=>sql.includes('WITH expected(table_name)'))).toBe(false);
    expect(queries).toContain('COMMIT');
    let connects=0;
    await expect(applyMainSongEnrichment({connect:async()=>{connects++;}},original,{...structuredClone(gate),manifest_sha256:'0'.repeat(64)}))
      .rejects.toMatchObject({code:'MAIN_APPLY_MANIFEST_MISMATCH'});
    expect(connects).toBe(0);
  });
});
