import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

const require=createRequire(import.meta.url);
const {main}=require('../../scripts/apply-main-song-enrichment.cjs');
const {buildEnrichmentPreview}=require('../../server/existingSongEnrichment.js');
const {stableJson}=require('../../server/applySongEnrichment.js');
const root=path.resolve(import.meta.dirname,'../../..');
const dirs=[];
afterEach(async()=>{for(const dir of dirs.splice(0))await fs.rm(dir,{recursive:true,force:true});});

function makeManifest(year=1973){
 const song={id:727,updated_at:'2026-10-13T03:17:35.356314Z',release_year:null,original_key:null,bpm:null,genres:[],vibes:[]};
 const candidate={value:year,source:'Gemini',response_digest:'a'.repeat(64),model_id:'gemini-3.1-flash-lite',
  prompt_version:'missing-metadata-fallback-v2',schema_version:'missing-metadata-schema-v2'};
 const evidence={mbid:null,scalars:{release_year:[candidate],original_key:[],bpm:[]},genres:[],vibes:[]};
 const decision={mbid:null,scalars:{release_year:{value:year,source:'Gemini',response_digest:candidate.response_digest}},genres:[],vibes:[]};
 const preview=buildEnrichmentPreview(song,evidence,decision,{genres:[],vibes:[]});
 return{format_version:1,operation_id:'94c50cb4-8394-4f9a-8eb4-598725fc3959',songs:[{song_id:727,base_version:song.updated_at,
  identity:{title:'Beer Drinkers and Hell Raisers',artist_id:19,artist_name:'ZZ Top'},evidence,review_decision:decision,preview}]};
}
function fakePool({receiptHash,writes}){
 const state={queries:[],ended:false};
 class Pool{constructor(){}async connect(){return{async query(sql,params){state.queries.push(sql);
   if(sql.includes('current_database()'))return{rows:[{database_name:'chords'}],rowCount:1};
   if(sql.includes("version='0004'"))return{rows:[{}],rowCount:1};
   if(sql.startsWith('SELECT request_hash,response FROM public.song_enrichment_runs'))return receiptHash
    ?{rows:[{request_hash:receiptHash,response:{operation_id:'94c50cb4-8394-4f9a-8eb4-598725fc3959',songs:[{song_id:727,status:'applied'}]}}],rowCount:1}
    :{rows:[],rowCount:0};
   if(/\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b/i.test(sql))writes.count++;
   return{rows:[],rowCount:0};
  },release(){state.released=true;}};}async end(){state.ended=true;}}
 return{Pool,state};
}
async function setup(){
 const directory=await fs.mkdtemp(path.join(root,'backups','.main-apply-cli-test-'));dirs.push(directory);
 const manifest=makeManifest(),manifestPath=path.join(directory,'manifest.json'),hash=crypto.createHash('sha256').update(stableJson(manifest)).digest('hex');
 await fs.writeFile(manifestPath,JSON.stringify(manifest),{flag:'wx'});
 return{directory,manifest,manifestPath,hash,proofPath:path.join(directory,'proof.json'),missingProofPath:path.join(directory,'proof-absent.json')};
}
function argv(f,proofPath=f.proofPath,hash=f.hash){return['--apply-main','--expected-database','chords','--manifest',f.manifestPath,'--manifest-sha256',hash,'--target-ids','727','--restore-proof',proofPath];}
function proof(f,{expired=false,missingArchive=false,hash=f.hash}={}){
 const runId='11111111-1111-4111-8111-111111111111';
 return{proof_version:1,run_id:runId,target_database:'chords',manifest_sha256:hash,
  backup:{path:missingArchive?path.join(f.directory,'absent.dump'):path.join(f.directory,'archive.dump'),sha256:'b'.repeat(64)},
  live_snapshot_sha256:'c'.repeat(64),restored_snapshot_sha256:'c'.repeat(64),
  restore:{run_id:runId,scratch_database:`ccm_restore_${runId.replaceAll('-','')}`,
   owner_marker:`CCM main apply restore verification run ${runId}`,cleanup_absence:true},
  created_at:expired?'2000-01-01T00:00:00.000Z':new Date().toISOString(),signature:'d'.repeat(64)};
}
async function invoke(f,{receiptHash,proofPath=f.proofPath,writes={count:0}}={}){
 const database=fakePool({receiptHash,writes});
 const result=await main(argv(f,proofPath),{config:{DATABASE_URL:'postgresql://tester:private@127.0.0.1:5432/chords'},Pool:database.Pool});
 return{result,state:database.state,writes};
}

describe('main apply CLI replay does not depend on obsolete restore files',()=>{
 it('allows committed replay when the proof is expired or its archive is missing',async()=>{
  for(const options of [{expired:true},{missingArchive:true}]){
   const f=await setup();await fs.writeFile(f.proofPath,JSON.stringify(proof(f,options)),{flag:'wx'});
   const out=await invoke(f,{receiptHash:f.hash});
   expect(out.result.operation_id).toBe(f.manifest.operation_id);expect(out.writes.count).toBe(0);expect(out.state.ended).toBe(true);
  }
 });
 it('allows committed replay when the proof file is absent',async()=>{
  const f=await setup();const out=await invoke(f,{receiptHash:f.hash,proofPath:f.missingProofPath});
  expect(out.result.operation_id).toBe(f.manifest.operation_id);expect(out.writes.count).toBe(0);expect(out.state.ended).toBe(true);
 });
 it('refuses a new operation with expired, missing-archive or absent proof before writes',async()=>{
  for(const options of [{expired:true},{missingArchive:true}]){
   const f=await setup();await fs.writeFile(f.proofPath,JSON.stringify(proof(f,options)),{flag:'wx'});const writes={count:0};
   await expect(invoke(f,{writes})).rejects.toMatchObject({code:'RESTORE_PROOF_INVALID'});expect(writes.count).toBe(0);
  }
  const f=await setup(),writes={count:0};
  await expect(invoke(f,{proofPath:f.missingProofPath,writes})).rejects.toMatchObject({code:'RESTORE_PROOF_INVALID'});expect(writes.count).toBe(0);
 });
 it('still rejects changed content under a committed operation ID',async()=>{
  const f=await setup(),changed=makeManifest(1974),changedHash=crypto.createHash('sha256').update(stableJson(changed)).digest('hex');
  await fs.writeFile(f.proofPath,JSON.stringify(proof(f,{hash:changedHash,missingArchive:true})),{flag:'wx'});
  const changedPath=path.join(f.directory,'changed.json');await fs.writeFile(changedPath,JSON.stringify(changed),{flag:'wx'});
  const modified={...f,manifestPath:changedPath,hash:changedHash},db=fakePool({receiptHash:f.hash,writes:{count:0}});
  await expect(main(argv(modified),{config:{DATABASE_URL:'postgresql://tester:private@127.0.0.1:5432/chords'},Pool:db.Pool}))
   .rejects.toMatchObject({code:'OPERATION_ID_REUSED'});
  expect(db.state.ended).toBe(true);
 });
});
