import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';
const require=createRequire(import.meta.url);
const {main}=require('../../scripts/verify-main-enrichment-backup.cjs');
const safety=require('../../server/mainEnrichmentSafety.js');
const {stableJson}=safety;

function fixture(failure=null){
 const manifest={format_version:1,operation_id:'11111111-1111-4111-8111-111111111111',songs:[{song_id:1}]};
 const manifestBytes=Buffer.from(JSON.stringify(manifest));
 const manifestPath=require('node:path').join(safety.PRIVATE_BACKUPS,'synthetic-main-proof-manifest.json');
 const backupPath=require('node:path').join(safety.PRIVATE_BACKUPS,'ccm_test-20261019-123000.dump');
 const backup=Buffer.from('synthetic scratch backup bytes');
 const scriptPath=require.resolve('../../scripts/verify-main-enrichment-backup.cjs');
 const envPath=require('node:path').join(require('node:path').dirname(scriptPath),'..','.env');
 const writes=new Map([[manifestPath,manifestBytes],[envPath,Buffer.from('POSTGRES_USER=test\nPOSTGRES_PASSWORD=test\nPOSTGRES_DB=ccm_test\nDB_PORT=15432')],[backupPath,backup]]);
 const fs={async readFile(file){if(!writes.has(file))throw Object.assign(new Error('ENOENT'),{code:'ENOENT'});return writes.get(file);},
  async stat(){return{isFile:()=>true,size:backup.length,mtimeMs:Date.now()+1000};},async writeFile(file,value){writes.set(file,Buffer.from(value));}};
 const state={poolEnds:[],scratchConnects:0,scratchEnds:0,scratchReleases:0,dropAttempts:0,ownerExists:false,queries:[],proofWritten:false};
 class Pool {constructor(config){this.config=config;this.kind=config.connectionString.includes('/postgres')?'admin':config.connectionString.split('/').at(-1);}
  async connect(){if(this.kind==='ccm_test')return{async query(sql){state.queries.push(sql);if(sql==='BEGIN READ ONLY'||sql==='COMMIT'||sql==='ROLLBACK')return{rows:[],rowCount:0};
    if(sql.includes('current_database()'))return{rows:[{database_name:'ccm_test',role_name:'tester'}],rowCount:1};if(sql.includes("version='0004'"))return{rows:[{}],rowCount:1};throw new Error(`unexpected main query ${sql}`);},release(){state.mainReleased=true;}};
   if(this.kind.startsWith('ccm_restore_')){state.scratchConnects++;return{async query(){return{rows:[],rowCount:0};},release(){state.scratchReleases++;}};}
   throw new Error('admin pool should use query');}
  async query(sql,params){state.queries.push(sql);if(sql.startsWith('SELECT 1 FROM pg_database'))return{rows:[],rowCount:0};
   if(sql.startsWith('CREATE DATABASE')){state.ownerExists=true;return{rows:[],rowCount:0};}
   if(sql.startsWith('COMMENT ON DATABASE')){state.marker=/IS '([^']+)'/.exec(sql)?.[1];return{rows:[],rowCount:0};}
   if(sql.includes('shobj_description'))return{rows:[{owner:'tester',marker:state.marker}],rowCount:1};
   if(sql.startsWith('SELECT pg_terminate_backend'))return{rows:[],rowCount:0};
   if(sql.startsWith('SELECT count(*)'))return{rows:[{n:0}],rowCount:1};
   if(sql.startsWith('DROP DATABASE')){state.dropAttempts++;if(failure==='cleanup')throw new Error('simulated DROP failure');state.ownerExists=false;return{rows:[],rowCount:0};}
   return{rows:[],rowCount:0};}
  async end(){state.poolEnds.push(this.kind);if(this.kind.startsWith('ccm_restore_'))state.scratchEnds++;}
 }
 const execFileSync=(exe,args)=>{
  if(exe==='powershell.exe')return `${backupPath}\n`;
  if(exe==='docker'&&args.includes('ps'))return 'test-container';
  if(exe==='docker'&&args.includes('pg_restore')&&failure==='restore')throw new Error('simulated restore failure');
  return '';
 };
 let snapshots=0;
 const snapshot=async()=>{snapshots++;if(failure==='compare'&&snapshots===2)return'c'.repeat(64);return'a'.repeat(64);};
 return{manifest,manifestPath,manifestHash:require('node:crypto').createHash('sha256').update(stableJson(manifest)).digest('hex'),state,
  deps:{Pool,fs,execFileSync,protectedSnapshotSha256:snapshot,signRestoreProof:async payload=>({...payload,signature:'test-signature'})}};
}
function run(f){return main(['--expected-database','ccm_test','--manifest',f.manifestPath,'--manifest-sha256',f.manifestHash],f.deps);}

describe('main restore proof orchestration cleanup',()=>{
 it('closes the scratch pool even when scratch client acquisition fails',async()=>{
  const f=fixture();f.deps.Pool=class extends f.deps.Pool {async connect(){if(this.kind.startsWith('ccm_restore_'))throw new Error('synthetic connection failure');return super.connect();}};
  await expect(run(f)).rejects.toThrow('synthetic connection failure');expect(f.state.scratchEnds).toBe(1);expect(f.state.mainReleased).toBe(true);
 });
 it('cleans owned scratch after restore and comparison failures while preserving the original error',async()=>{
  for(const failure of ['restore','compare']){const f=fixture(failure);await expect(run(f)).rejects.toThrow(failure==='restore'?'simulated restore failure':'RESTORE_SNAPSHOT_MISMATCH');
   expect(f.state.scratchEnds).toBe(failure==='restore'?0:1);expect(f.state.ownerExists).toBe(false);expect(f.state.poolEnds).toContain('admin');}
 });
 it('reports cleanup failure without replacing successful-path completion state or hiding cleanup attempts',async()=>{
  const f=fixture('cleanup');await expect(run(f)).rejects.toMatchObject({name:'AggregateError'});expect(f.state.dropAttempts).toBeGreaterThanOrEqual(1);
  expect(f.state.poolEnds).toContain('admin');expect(f.state.ownerExists).toBe(true);
 });
});
