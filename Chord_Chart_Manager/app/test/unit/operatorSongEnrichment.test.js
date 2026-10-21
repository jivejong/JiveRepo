import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
const require=createRequire(import.meta.url);
const {prepare,preview}=require('../../scripts/operator-song-enrichment.cjs');
const {validateManifest}=require('../../server/applySongEnrichment.js');
const genreOnlyReport=require('../fixtures/gemini-genre-only-preview-v1.json');

const project=path.resolve(import.meta.dirname,'../../..');
const artifactDirs=[];
afterEach(async()=>{for(const dir of artifactDirs.splice(0))await fs.rm(dir,{recursive:true,force:true});});
const mbid='62ec7207-aa84-43b6-949e-8066b6be2cad',digest='a'.repeat(64);
const song={id:727,artist_id:19,title:'Beer Drinkers and Hell Raisers',artist:'ZZ Top',updated_at:'2026-10-13T03:17:35.356314Z',
 release_year:null,original_key:null,bpm:null,genres:[],vibes:[]};
const definitions={genres:[{id:1,name:'Blues Rock'}],vibes:[{id:2,name:'Rocker'},{id:3,name:'Gritty'},{id:4,name:'Dive Bar'}]};
function context(){return{factual_context_version:1,target:{song_id:727,title:song.title,artist:song.artist},
 recording:{mbid,title:'Beer Drinkers & Hell Raisers',artist:'ZZ Top',disambiguation:'original stereo mix',validated:true,response_digest:digest},
 scalar_candidates:{release_year:{status:'resolved',candidates:[{source:'MusicBrainz',value:1973,response_digest:digest,submission_reference:`recording:${mbid}`}]},
 original_key:{status:'conflicting',candidates:[{source:'AcousticBrainz',value:{key_key:'C',key_scale:'minor'},response_digest:digest,submission_reference:`${mbid}:0`},
  {source:'AcousticBrainz',value:{key_key:'G',key_scale:'major'},response_digest:'b'.repeat(64),submission_reference:`${mbid}:68`},
  {source:'AcousticBrainz',value:{key_key:'C',key_scale:'minor'},response_digest:'c'.repeat(64),submission_reference:`${mbid}:137`}]},
 bpm:{status:'conflicting',candidates:[{source:'AcousticBrainz',value:134.480422974,response_digest:digest,submission_reference:`${mbid}:0`},
  {source:'AcousticBrainz',value:134.510665894,response_digest:'b'.repeat(64),submission_reference:`${mbid}:68`},
  {source:'AcousticBrainz',value:134.438598633,response_digest:'c'.repeat(64),submission_reference:`${mbid}:137`}] }},
 submission_coverage:{source:'AcousticBrainz',total_available:138,sampled_offsets:[0,68,137],completeness:'partial'},conflicts:{release_year:false,original_key:true,bpm:true}};}
function makePool({currentSong=song,version=currentSong.updated_at,currentDefinitions=definitions}={}){
 const state={ended:false,released:0,queries:[]};
 const pool={state,connect:async()=>({query:async(sql)=>{state.queries.push(sql);if(sql==='BEGIN READ ONLY'||sql==='COMMIT'||sql==='ROLLBACK')return{rows:[],rowCount:0};
  if(sql.includes('FROM public.songs s JOIN public.artists'))return{rows:[{...currentSong,artist_name:currentSong.artist,updated_at:version}],rowCount:1};
  if(sql.includes('FROM public.song_genres sg'))return{rows:currentSong.genres.map(row=>({song_id:currentSong.id,...row})),rowCount:currentSong.genres.length};
  if(sql.includes('FROM public.song_vibes sv'))return{rows:currentSong.vibes.map(row=>({song_id:currentSong.id,...row})),rowCount:currentSong.vibes.length};
  if(sql==='SELECT id,name FROM public.genres ORDER BY id')return{rows:currentDefinitions.genres,rowCount:currentDefinitions.genres.length};if(sql==='SELECT id,name FROM public.vibes ORDER BY id')return{rows:currentDefinitions.vibes,rowCount:currentDefinitions.vibes.length};
  throw new Error(`unexpected read-only baseline query ${sql}`);},release(){state.released++;}}),end:async()=>{state.ended=true;}};return pool;
}
async function privateDir(){const dir=await fs.mkdtemp(path.join(project,'backups','.operator-workflow-test-'));artifactDirs.push(dir);return dir;}

describe('operator saved-evidence prepare → preview orchestration',()=>{
 it('imports the saved Genre-only producer shape through prepare, edited string decisions and preview',async()=>{
  const root=await privateDir(),current={...song,updated_at:'2026-10-20T04:11:45.892061Z',release_year:1973,original_key:'Cm',bpm:134,
    genres:[],vibes:[{id:2,name:'Rocker'},{id:3,name:'Gritty'},{id:4,name:'Dive Bar'}]};
  const catalog={genres:[{id:101,name:'Blues Rock'},{id:102,name:'Hard Rock'},{id:103,name:'Southern Rock'}],vibes:definitions.vibes};
  const evidencePath=path.join(root,'gemini-genre-only-preview.json');
  await fs.writeFile(evidencePath,JSON.stringify(genreOnlyReport),{flag:'wx'});
  const prepPool=makePool({currentSong:current,currentDefinitions:catalog});
  const prepared=await prepare({ids:[727],evidence:[`727=${evidencePath}`],outputDir:path.join(root,'prepare')},
    {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return prepPool;}}});
  expect(prepPool.state.ended).toBe(true);expect(prepPool.state.released).toBe(1);
  const preparedDoc=JSON.parse(await fs.readFile(prepared.preparedPath,'utf8'));
  const decisions=JSON.parse(await fs.readFile(prepared.decisionsPath,'utf8'));
  expect(preparedDoc.evidence['727'].genres).toHaveLength(3);
  expect(preparedDoc.evidence['727'].genres.map(item=>item.name)).toEqual(['Blues Rock','Hard Rock','Southern Rock']);
  expect(preparedDoc.evidence['727'].genres.every(item=>item.response_digest===genreOnlyReport.result.response_digest&&
    item.model_id==='gemini-3.1-flash-lite'&&item.prompt_version==='missing-metadata-fallback-v2'&&
    item.schema_version==='missing-metadata-schema-v2')).toBe(true);
  expect(preparedDoc.evidence['727'].scalars).toEqual({release_year:[],original_key:[],bpm:[]});
  expect(preparedDoc.evidence['727'].vibes).toEqual([]);
  expect(preparedDoc.baseline.songs[0]).toMatchObject({release_year:1973,original_key:'Cm',bpm:134,
    genres:[],vibes:[{id:2,name:'Rocker'},{id:3,name:'Gritty'},{id:4,name:'Dive Bar'}]});
  expect(decisions.targets[0].decision).toEqual({mbid:null,scalars:{},genres:[],vibes:[]});
  decisions.targets[0].decision.genres=['Blues Rock','Hard Rock','Southern Rock'];
  await fs.writeFile(prepared.decisionsPath,`${JSON.stringify(decisions,null,2)}\n`);
  const previewPool=makePool({currentSong:current,currentDefinitions:catalog});
  const result=await preview({prepared:prepared.preparedPath,decisions:prepared.decisionsPath,outputDir:path.join(root,'preview')},
    {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return previewPool;}},operationId:'94c50cb4-8394-4f9a-8eb4-598725fc3959'});
  expect(previewPool.state.ended).toBe(true);expect(previewPool.state.released).toBe(1);
  const manifest=JSON.parse(await fs.readFile(result.manifestPath,'utf8'));
  expect(validateManifest(manifest).map(item=>item.song_id)).toEqual([727]);
  expect(manifest.songs[0].base_version).toBe(current.updated_at);
  expect(manifest.songs[0].preview.scalar_changes).toEqual([]);
  expect(manifest.songs[0].review_decision.genres).toEqual(['Blues Rock','Hard Rock','Southern Rock']);
  expect(manifest.songs[0].review_decision.vibes).toEqual([]);
  expect(manifest.songs[0].review_decision.primaryGenre).toBeUndefined();
  expect(manifest.songs[0].preview.primary_proposal).toBeNull();
  expect(manifest.songs[0].preview.tag_additions.genres.map(item=>[item.id,item.name])).toEqual([
    [101,'Blues Rock'],[102,'Hard Rock'],[103,'Southern Rock']]);
  expect(manifest.songs[0].preview.tag_additions.vibes).toEqual([]);
  expect(manifest.songs[0].evidence.genres.map(item=>item.response_digest)).toEqual(Array(3).fill(genreOnlyReport.result.response_digest));
  expect(manifest.songs[0].evidence.genres.every(item=>item.source==='Gemini'&&item.model_id==='gemini-3.1-flash-lite'&&
    item.prompt_version==='missing-metadata-fallback-v2'&&item.schema_version==='missing-metadata-schema-v2')).toBe(true);
 });
 it('runs all three saved song-727 evidence forms through edited scalar, Genre and Gemini Vibe decisions to B1/B3 preview',async()=>{
  const root=await privateDir(),factual=context(),fallback={format_version:1,mode:'explicit_gemini_missing_metadata_fallback',database_writes:false,selected_song_id:727,
   baseline:{id:727,title:song.title,artist:song.artist},approved_apply_manifest:null,
   factual_evidence:{context_version:1,target:factual.target,recording:factual.recording,scalar_candidates:Object.fromEntries(Object.entries(factual.scalar_candidates).map(([field,value])=>[field,{state:value.status,candidates:value.candidates,conflicting_evidence:value.status==='conflicting'}])),submission_coverage:factual.submission_coverage,conflicts:factual.conflicts},
   gemini_suggestions:{status:'ok',response_digest:'d'.repeat(64),model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v1',schema_version:'missing-metadata-schema-v1',
    scalars:{release_year:[],original_key:[{value:{key_key:'C',key_scale:'minor'},source:'Gemini',response_digest:'d'.repeat(64),model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v1',schema_version:'missing-metadata-schema-v1'}],
     bpm:[{value:134.48,source:'Gemini',response_digest:'d'.repeat(64),model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v1',schema_version:'missing-metadata-schema-v1'}]},genres:[],vibes:[]}},
   vibe={report_version:1,report_type:'gemini_vibe_only_preview',database_writes:false,approval_or_apply_manifest:null,song:{id:727,title:song.title,artist:song.artist},limits:{model:'gemini-3.1-flash-lite'},result:{status:'ok',vibes:['Rocker','Gritty','Dive Bar'].map(name=>({name,source:'Gemini',response_digest:'e'.repeat(64),model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v2',schema_version:'missing-metadata-schema-v2'}))}},
   b4={report_version:1,report_type:'existing_song_enrichment_read_only_preview',selected_song_ids:[727],database_writes:false,approved_apply_manifest:null,songs:[{song_id:727,identity:{title:song.title,artist:song.artist},recording:{match:{matched:true,mbid}},
    evidence:{mbid,scalars:{release_year:[],original_key:[],bpm:[]},genres:[{name:'Blues Rock',source:'MusicBrainz',response_digest:'f'.repeat(64)}],vibes:[]},approved_preview:null,human_review_required:true}]};
  const inputs=[['factual-context.json',factual],['gemini-scalars.json',fallback],['vibes.json',vibe],['b4-genre.json',b4]];
  const evidence=[];for(const [name,doc] of inputs){const file=path.join(root,name);await fs.writeFile(file,JSON.stringify(doc),{flag:'wx'});evidence.push(`727=${file}`);}
  const prepPool=makePool(),prepared=await prepare({ids:[727],evidence,outputDir:path.join(root,'prepare')},{databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return prepPool;}}});
  expect(prepPool.state.ended).toBe(true);expect(prepPool.state.released).toBe(1);expect(prepPool.state.queries[0]).toBe('BEGIN READ ONLY');
  const preparedDoc=JSON.parse(await fs.readFile(prepared.preparedPath,'utf8')),decisions=JSON.parse(await fs.readFile(prepared.decisionsPath,'utf8'));
  expect(decisions.targets[0].decision).toEqual({mbid:null,scalars:{},genres:[],vibes:[]});
  expect(decisions.targets[0].source_context[0].submission_coverage).toEqual(context().submission_coverage);
  const review=await fs.readFile(prepared.reportPath,'utf8');
  expect(review).toContain('total_available');expect(review).toContain('sampled_offsets');expect(review).toContain(digest);
  const expectedEvidenceHashes=await Promise.all(evidence.map(async entry=>crypto.createHash('sha256').update(await fs.readFile(entry.slice(entry.indexOf('=')+1))).digest('hex')));
  expect(decisions.targets[0].evidence_sha256).toEqual(expectedEvidenceHashes.sort());
  expect(JSON.stringify(preparedDoc)).not.toContain('chart_content');
  decisions.targets[0].decision.mbid=mbid;
  decisions.targets[0].decision.scalars.release_year=decisions.targets[0].evidence.scalars.release_year[0];
  decisions.targets[0].decision.scalars.original_key=decisions.targets[0].evidence.scalars.original_key.find(item=>item.source==='Gemini');
  decisions.targets[0].decision.scalars.bpm=decisions.targets[0].evidence.scalars.bpm.find(item=>item.source==='Gemini');
  decisions.targets[0].decision.genres=['Blues Rock'];decisions.targets[0].decision.vibes=['Rocker','Gritty','Dive Bar'];
  await fs.writeFile(prepared.decisionsPath,`${JSON.stringify(decisions,null,2)}\n`);
  const previewPool=makePool(),result=await preview({prepared:prepared.preparedPath,decisions:prepared.decisionsPath,outputDir:path.join(root,'preview')},
    {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return previewPool;}},operationId:'94c50cb4-8394-4f9a-8eb4-598725fc3959'});
  expect(previewPool.state.ended).toBe(true);expect(previewPool.state.released).toBe(1);
  const manifest=JSON.parse(await fs.readFile(result.manifestPath,'utf8'));expect(validateManifest(manifest).map(x=>x.song_id)).toEqual([727]);
  expect(manifest.songs[0].review_decision.genres).toEqual(['Blues Rock']);
  expect(manifest.songs[0].review_decision.vibes).toEqual(['Rocker','Gritty','Dive Bar']);
  expect(manifest.songs[0].preview.scalar_changes.map(change=>[change.field,change.after])).toEqual([['bpm',134],['original_key','Cm'],['release_year',1973]]);
  expect(manifest.songs[0].preview.tag_additions.genres.map(tag=>tag.name)).toEqual(['Blues Rock']);
  expect(manifest.songs[0].preview.tag_additions.vibes.map(tag=>tag.name)).toEqual(['Rocker','Gritty','Dive Bar']);
  expect(result.manifestSha256).toMatch(/^[a-f0-9]{64}$/);expect(await fs.stat(result.manifestPath)).toBeTruthy();
  const changes=await fs.readFile(path.join(result.outputDir,'changes.txt'),'utf8');expect(changes).toContain('1973');expect(changes).toContain('Blues Rock');expect(changes).toContain('Rocker');
  expect(changes).toContain('Beer Drinkers and Hell Raisers / ZZ Top');expect(changes).toContain('Decision selections:');
 });
 it('detects source changes before preview and does not create a preview artifact',async()=>{
  const root=await privateDir(),evidencePath=path.join(root,'factual-context.json');await fs.writeFile(evidencePath,JSON.stringify(context()),{flag:'wx'});
  const prep=await prepare({ids:[727],evidence:[`727=${evidencePath}`],outputDir:path.join(root,'prepare')},{databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makePool();}}});
  await fs.appendFile(evidencePath,' ');
  await expect(preview({prepared:prep.preparedPath,decisions:prep.decisionsPath,outputDir:path.join(root,'blocked')},{databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){throw new Error('database should not be reached');}}})).rejects.toMatchObject({code:'EVIDENCE_CHANGED'});
  await expect(fs.stat(path.join(root,'blocked'))).rejects.toThrow();
 });
 it('prints an explicit no-op state when the prepared decision remains empty',async()=>{
  const root=await privateDir(),evidencePath=path.join(root,'abstaining-b4.json');
  const abstention={report_version:1,report_type:'existing_song_enrichment_read_only_preview',selected_song_ids:[727],
   database_writes:false,approved_apply_manifest:null,songs:[{song_id:727,identity:{title:song.title,artist:song.artist},
    recording:{match:{matched:false,reason:'incomplete_search_results'}},evidence:null,approved_preview:null,human_review_required:true}]};
  const suggestionDigest='f'.repeat(64),suggestions={format_version:1,mode:'explicit_gemini_missing_metadata_fallback',database_writes:false,
   selected_song_id:727,baseline:{id:727,title:song.title,artist:song.artist},approved_apply_manifest:null,
   gemini_suggestions:{status:'ok',response_digest:suggestionDigest,model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v2',
    schema_version:'missing-metadata-schema-v2',scalars:{release_year:[],original_key:[{value:{key_key:'C',key_scale:'minor'},source:'Gemini',
     response_digest:suggestionDigest,model_id:'gemini-3.1-flash-lite',prompt_version:'missing-metadata-fallback-v2',schema_version:'missing-metadata-schema-v2'}],bpm:[]},genres:[],vibes:[]}};
  await fs.writeFile(evidencePath,JSON.stringify(abstention),{flag:'wx'});
  const suggestionPath=path.join(root,'unselected-suggestion.json');await fs.writeFile(suggestionPath,JSON.stringify(suggestions),{flag:'wx'});
  const prepared=await prepare({ids:[727],evidence:[`727=${evidencePath}`,`727=${suggestionPath}`],outputDir:path.join(root,'prepare')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makePool();}}});
  const result=await preview({prepared:prepared.preparedPath,decisions:prepared.decisionsPath,outputDir:path.join(root,'preview')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makePool();}},operationId:'94c50cb4-8394-4f9a-8eb4-598725fc3959'});
  const text=await fs.readFile(result.changesPath,'utf8');
  expect(text).toContain('Decision selections: none.');expect(text).toContain('no changes');
 });
});
