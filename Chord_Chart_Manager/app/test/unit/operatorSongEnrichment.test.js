import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
const require=createRequire(import.meta.url);
const {decide,prepare,preview}=require('../../scripts/operator-song-enrichment.cjs');
const {captureReadOnlyBaseline}=require('../../scripts/preview-existing-song-enrichment.cjs');
const {runSongEnrichmentCohort}=require('../../server/songEnrichmentCohortRunner.js');
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
function makePool({currentSong=song,version=currentSong.updated_at,currentDefinitions=definitions,songs=null}={}){
 const currentSongs=songs||[currentSong];
 const state={ended:false,released:0,queries:[]};
 const pool={state,connect:async()=>({query:async(sql)=>{state.queries.push(sql);if(sql==='BEGIN READ ONLY'||sql==='COMMIT'||sql==='ROLLBACK')return{rows:[],rowCount:0};
  if(sql.includes('FROM public.songs s JOIN public.artists'))return{rows:currentSongs.map(row=>({...row,artist_name:row.artist,updated_at:row.id===currentSong.id?version:row.updated_at})),rowCount:currentSongs.length};
  if(sql.includes('FROM public.song_genres sg'))return{rows:currentSongs.flatMap(row=>row.genres.map(item=>({song_id:row.id,...item}))),rowCount:currentSongs.reduce((count,row)=>count+row.genres.length,0)};
  if(sql.includes('FROM public.song_vibes sv'))return{rows:currentSongs.flatMap(row=>row.vibes.map(item=>({song_id:row.id,...item}))),rowCount:currentSongs.reduce((count,row)=>count+row.vibes.length,0)};
  if(sql==='SELECT id,name FROM public.genres ORDER BY id')return{rows:currentDefinitions.genres,rowCount:currentDefinitions.genres.length};if(sql==='SELECT id,name FROM public.vibes ORDER BY id')return{rows:currentDefinitions.vibes,rowCount:currentDefinitions.vibes.length};
  throw new Error(`unexpected read-only baseline query ${sql}`);},release(){state.released++;}}),end:async()=>{state.ended=true;}};return pool;
}
async function privateDir(){const dir=await fs.mkdtemp(path.join(project,'backups','.operator-workflow-test-'));artifactDirs.push(dir);return dir;}

describe('operator saved-evidence prepare → preview orchestration',()=>{
 it('routes synthetic cohort evidence through prepare, T27 decide, and the existing preview manifest validator',async()=>{
  const root=await privateDir(),baseline=await captureReadOnlyBaseline(makePool(),[727]);
  const manifest={cohort_manifest_version:1,cohort_id:'t28-synthetic',songs:[{song_id:727,title:song.title,artist:song.artist}]};
  const factualReport={report_version:1,report_type:'existing_song_enrichment_read_only_preview',selected_song_ids:[727],
   database_writes:false,approved_apply_manifest:null,songs:[{song_id:727,identity:{title:song.title,artist:song.artist},
    recording:{search_status:'ok',match:{matched:true,mbid},candidates:[{mbid,title:song.title,artist:song.artist,
     disambiguation:'original stereo mix',first_release_date:'1973',response_digest:digest}],
     pagination:{complete:true,expected_total:1,collected_count:1,attempted_pages:1,validated_pages:1}},evidence:{mbid,
     scalars:{release_year:[{value:1973,source:'MusicBrainz',response_digest:digest,submission_reference:`recording:${mbid}`}],original_key:[],bpm:[]},
     genres:[{name:'Blues Rock',source:'MusicBrainz',response_digest:digest}],vibes:[]},
    approved_preview:null,human_review_required:true}]};
  const result=await runSongEnrichmentCohort({manifest,baseline,directory:root,providerClient:{},getCacheHash:async()=>'c'.repeat(64),
   acquireFactual:async({reserveAttempt})=>{await reserveAttempt({provider:'MusicBrainz',operation:'recording_search'});return{factualReport};},
   processBatch:async({rows,evidenceRefs,batchIndex})=>{
    const base=path.join(root,`batch-${batchIndex}`);await fs.mkdir(base,{mode:0o700});
    const inputs=evidenceRefs.map(item=>`${item.id}=${item.path}`),PoolClass=class{constructor(){return makePool();}},
      db={databaseUrl:'postgresql://private@localhost/chords',PoolClass};
    const prepared=await prepare({ids:rows.map(row=>row.id),evidence:inputs,outputDir:path.join(base,'prepare')},db);
    const decisions=await decide({prepared:prepared.preparedPath,outputDir:path.join(base,'decide')},db);
    const previewResult=await preview({prepared:prepared.preparedPath,decisions:decisions.decisionsPath,outputDir:path.join(base,'preview')},
      {...db,operationId:'94c50cb4-8394-4f9a-8eb4-598725fc3959'});
    return{preparedPath:prepared.preparedPath,decisionsPath:decisions.decisionsPath,
     decisionReportPath:decisions.reportPath,manifestPath:previewResult.manifestPath,manifestSha256:previewResult.manifestSha256};
   }});
  const accepted=JSON.parse(await fs.readFile(result.batches[0].manifest_path,'utf8'));
  expect(validateManifest(accepted).map(item=>item.song_id)).toEqual([727]);
  expect(accepted.songs[0].preview.scalar_changes).toEqual([{field:'release_year',before:null,after:1973,
   evidence:{source:'MusicBrainz',response_digest:digest}}]);
  expect(accepted.songs[0].preview.tag_additions.genres).toMatchObject([{id:1,name:'Blues Rock'}]);
  expect(accepted.songs[0].preview.primary_proposal).toBeNull();
  expect(result.factualAttempts).toBe(1);
  expect(result.exceptions).toContainEqual({song_id:727,code:'scalar_original_key_no_candidate'});
  expect(result.exceptions).toContainEqual({song_id:727,code:'vibe_no_candidate'});
 });

 it('creates deterministic private best-effort decisions from prepared evidence and passes them through preview',async()=>{
  const root=await privateDir(),factual=context();
  const b4={report_version:1,report_type:'existing_song_enrichment_read_only_preview',selected_song_ids:[727],
   private_fixture_sentinels:['PRIVATE CHART BODY MUST NOT ESCAPE','PRIVATE LYRICS MUST NOT ESCAPE',
    'PRIVATE RAW RESPONSE MUST NOT ESCAPE','PRIVATE CREDENTIAL MUST NOT ESCAPE'],
   database_writes:false,approved_apply_manifest:null,songs:[{song_id:727,identity:{title:song.title,artist:song.artist},
    recording:{search_status:'ok',match:{matched:true,mbid}},evidence:{mbid,
     scalars:{release_year:[],original_key:[],bpm:[]},
     genres:[{name:'Blues Rock',source:'MusicBrainz',response_digest:'b'.repeat(64)}],
     vibes:[{name:'Rocker',source:'MusicBrainz',response_digest:'c'.repeat(64)}]},
    approved_preview:null,human_review_required:true}]};
  const evidencePaths=[];
  for(const [name,document] of [['factual.json',factual],['b4.json',b4]]) {
   const file=path.join(root,name);await fs.writeFile(file,JSON.stringify(document),{flag:'wx'});evidencePaths.push('727='+file);
  }
  const prepPool=makePool();
  const prepared=await prepare({ids:[727],evidence:evidencePaths,outputDir:path.join(root,'prepare')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return prepPool;}}});
  const makeReadPool=()=>makePool();
  const generated=await decide({prepared:prepared.preparedPath,outputDir:path.join(root,'automatic')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makeReadPool();}}});
  const decisionsBytes=await fs.readFile(generated.decisionsPath);
  const repeated=await decide({prepared:prepared.preparedPath,outputDir:path.join(root,'automatic-repeat')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makeReadPool();}}});
  expect(await fs.readFile(repeated.decisionsPath)).toEqual(decisionsBytes);
  expect(await fs.readFile(repeated.reportPath)).toEqual(await fs.readFile(generated.reportPath));
  const decisions=JSON.parse(decisionsBytes);
  const report=JSON.parse(await fs.readFile(generated.reportPath,'utf8'));
  expect(decisions.targets[0].decision).toMatchObject({mbid,genres:['Blues Rock'],vibes:['Rocker']});
  expect(decisions.targets[0].decision.scalars.release_year).toEqual(decisions.targets[0].evidence.scalars.release_year[0]);
  expect(decisions.targets[0].decision.scalars.original_key).toBeUndefined();
  expect(decisions.targets[0].decision.scalars.bpm).toBeUndefined();
  expect(decisions.targets[0].decision).not.toHaveProperty('primaryGenre');
  expect(report.songs[0].scalars).toMatchObject({release_year:{status:'selected',value:1973},
   original_key:{status:'abstained',reason:'conflicting_evidence'},bpm:{status:'abstained',reason:'conflicting_evidence'}});
  const redacted=decisionsBytes.toString('utf8')+JSON.stringify(report);
  for(const secret of ['PRIVATE CHART BODY MUST NOT ESCAPE','PRIVATE LYRICS MUST NOT ESCAPE',
   'PRIVATE RAW RESPONSE MUST NOT ESCAPE','PRIVATE CREDENTIAL MUST NOT ESCAPE'])expect(redacted).not.toContain(secret);

  const result=await preview({prepared:prepared.preparedPath,decisions:generated.decisionsPath,outputDir:path.join(root,'preview')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makeReadPool();}},
    operationId:'94c50cb4-8394-4f9a-8eb4-598725fc3959'});
  const manifest=JSON.parse(await fs.readFile(result.manifestPath,'utf8'));
  expect(validateManifest(manifest).map(item=>item.song_id)).toEqual([727]);
  expect(manifest.songs[0].preview.scalar_changes).toEqual([{field:'release_year',before:null,after:1973,
   evidence:{source:'MusicBrainz',response_digest:digest,submission_id:'recording:'+mbid}}]);
  expect(manifest.songs[0].preview.tag_additions.genres).toMatchObject([{id:1,name:'Blues Rock'}]);
  expect(manifest.songs[0].preview.tag_additions.vibes).toMatchObject([{id:2,name:'Rocker'}]);
  expect(manifest.songs[0].preview.primary_proposal).toBeNull();
  expect(prepPool.state.queries.some(sql=>/\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b/i.test(sql))).toBe(false);
 });
 it('refuses stale evidence hashes and changed prepared baselines before writing decisions',async()=>{
  const root=await privateDir(),evidencePath=path.join(root,'factual.json');
  await fs.writeFile(evidencePath,JSON.stringify(context()),{flag:'wx'});
  const prepared=await prepare({ids:[727],evidence:['727='+evidencePath],outputDir:path.join(root,'prepare')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makePool();}}});
  await fs.appendFile(evidencePath,' ');
  await expect(decide({prepared:prepared.preparedPath,outputDir:path.join(root,'changed-evidence')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){throw new Error('database must not be opened');}}}))
   .rejects.toMatchObject({code:'EVIDENCE_CHANGED'});
  await expect(fs.stat(path.join(root,'changed-evidence'))).rejects.toThrow();
  await fs.writeFile(evidencePath,JSON.stringify(context()));
  await expect(decide({prepared:prepared.preparedPath,outputDir:path.join(root,'stale-baseline')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makePool({version:'2026-10-13T03:17:35.356315Z'});}}}))
   .rejects.toMatchObject({code:'BASELINE_CHANGED_REPREPARE'});
  await expect(fs.stat(path.join(root,'stale-baseline'))).rejects.toThrow();
  await expect(decide({prepared:prepared.preparedPath,outputDir:path.join(root,'stale-definitions')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return makePool({currentDefinitions:{genres:[{id:9,name:'Changed'}],vibes:definitions.vibes}});}}}))
   .rejects.toMatchObject({code:'BASELINE_CHANGED_REPREPARE'});
  await expect(fs.stat(path.join(root,'stale-definitions'))).rejects.toThrow();
  const malformed=JSON.parse(await fs.readFile(prepared.preparedPath,'utf8'));
  malformed.sources['727'][0].sha256='not-a-hash';
  const malformedPath=path.join(root,'malformed-prepared.json');await fs.writeFile(malformedPath,JSON.stringify(malformed),{flag:'wx'});
  await expect(decide({prepared:malformedPath,outputDir:path.join(root,'malformed')},
   {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){throw new Error('database must not be opened');}}}))
   .rejects.toMatchObject({code:'PREPARED_INVALID'});
  await expect(fs.stat(path.join(root,'malformed'))).rejects.toThrow();
 });
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

  it('routes accepted, unresolved and conflicting saved evidence through prepare → decide → preview',async()=>{
   const root=await privateDir(),rows=[
    {...song,id:736,title:'Accepted Match',artist:'Synthetic Artist A',artist_id:31,genres:[],vibes:[]},
    {...song,id:738,title:'Unresolved Match',artist:'Synthetic Artist B',artist_id:32,genres:[],vibes:[]},
    {...song,id:741,title:'Conflicting Metadata',artist:'Synthetic Artist C',artist_id:33,
     genres:[{id:8,name:'Classic Rock',is_primary:true}],vibes:[]},
   ];
   const catalog={genres:[{id:1,name:'Blues Rock'},{id:8,name:'Classic Rock'}],vibes:[]};
   const pool=()=>makePool({songs:rows,currentDefinitions:catalog});
   const uuidA='73600000-0000-4000-8000-000000000001',uuidC='74100000-0000-4000-8000-000000000001';
   const digestFor=char=>char.repeat(64);
   const reportFor=(row,{matched,mbid,year,conflicts=false,genres=[]}={})=>({report_version:1,
    report_type:'existing_song_enrichment_read_only_preview',selected_song_ids:[row.id],database_writes:false,approved_apply_manifest:null,
    songs:[{song_id:row.id,identity:{title:row.title,artist:row.artist},
     recording:matched?{search_status:'ok',match:{matched:true,mbid},candidates:[{mbid,title:row.title,artist:row.artist,
      disambiguation:'original stereo mix',first_release_date:String(year||1973),response_digest:digestFor('9')}],
      pagination:{complete:true,expected_total:1,collected_count:1,attempted_pages:1,validated_pages:1}}
      :{match:{matched:false,reason:'no_exact_match'}},
     evidence:matched?{mbid,scalars:{release_year:year?[{value:year,source:'MusicBrainz',response_digest:digestFor(row.id===736?'a':'b')}]:[],
      original_key:conflicts?[{value:'Cm',source:'AcousticBrainz',response_digest:digestFor('c'),submission_id:`${mbid}:0`},
       {value:'G',source:'AcousticBrainz',response_digest:digestFor('d'),submission_id:`${mbid}:68`}]:[],
      bpm:conflicts?[{value:100,source:'AcousticBrainz',response_digest:digestFor('e'),submission_id:`${mbid}:0`},
       {value:110,source:'AcousticBrainz',response_digest:digestFor('f'),submission_id:`${mbid}:68`}]:[]},genres,vibes:[]} : null,
     scalar_candidates:Object.fromEntries(['release_year','original_key','bpm'].map(field=>[field,
      {state:conflicts&&field!=='release_year'?'conflicting':year&&field==='release_year'?'resolved':'unresolved',
       candidates:[],conflicting_evidence:conflicts&&field!=='release_year'}])),
     tag_candidates:{genres:{unapproved_additions:[],unknown_labels:[],ambiguous_labels:[]},vibes:{unapproved_additions:[],unknown_labels:[],ambiguous_labels:[]}},
     approved_preview:null,human_review_required:true}]});
   const documents=[
    reportFor(rows[0],{matched:true,mbid:uuidA,year:1973}),
    reportFor(rows[1],{matched:false}),
    reportFor(rows[2],{matched:true,mbid:uuidC,year:1974,conflicts:true,genres:[
     {name:'blues rock',source:'MusicBrainz',response_digest:digestFor('1')},
     {name:'Blues Rock',source:'MusicBrainz',response_digest:digestFor('2')},
     {name:'Unknown Label',source:'MusicBrainz',response_digest:digestFor('3')},
    ]}),
   ];
   const evidenceRefs=[];
   for(let index=0;index<rows.length;index++){
    const file=path.join(root,`saved-${rows[index].id}.json`);await fs.writeFile(file,JSON.stringify(documents[index]),{flag:'wx'});
    evidenceRefs.push(`${rows[index].id}=${file}`);
   }
   const prepared=await prepare({ids:rows.map(row=>row.id),evidence:evidenceRefs,outputDir:path.join(root,'prepare')},
    {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return pool();}}});
   const decided=await decide({prepared:prepared.preparedPath,outputDir:path.join(root,'decide')},
    {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return pool();}}});
   const decisions=JSON.parse(await fs.readFile(decided.decisionsPath,'utf8'));
   const decisionById=Object.fromEntries(decisions.targets.map(target=>[target.song_id,target.decision]));
   expect(decisionById[736].scalars.release_year.value).toBe(1973);
   expect(decisionById[738]).toEqual({mbid:null,scalars:{},genres:[],vibes:[]});
   expect(decisionById[741].scalars.release_year.value).toBe(1974);
   expect(decisionById[741].scalars.original_key).toBeUndefined();expect(decisionById[741].scalars.bpm).toBeUndefined();
   expect(decisionById[741].genres).toEqual(['Blues Rock']);
   const decisionReport=JSON.parse(await fs.readFile(decided.reportPath,'utf8'));
   const reportById=Object.fromEntries(decisionReport.songs.map(item=>[item.song_id,item]));
   expect(reportById[738].recording_match).toMatchObject({status:'abstained',reason:'recording_identity_unresolved'});
   expect(reportById[738].scalars).toMatchObject({release_year:{status:'abstained'},original_key:{status:'abstained'},bpm:{status:'abstained'}});
   expect(reportById[741].genres).toEqual(expect.arrayContaining([
    expect.objectContaining({name:'Blues Rock',status:'selected',reason:'unique_category_definition_duplicate_evidence_collapsed'}),
    expect.objectContaining({name:'Unknown Label',status:'abstained',reason:'unknown_definition'}),
   ]));
   const result=await preview({prepared:prepared.preparedPath,decisions:decided.decisionsPath,outputDir:path.join(root,'preview')},
    {databaseUrl:'postgresql://private@localhost/chords',PoolClass:class{constructor(){return pool();}},operationId:'94c50cb4-8394-4f9a-8eb4-598725fc3959'});
   const manifest=JSON.parse(await fs.readFile(result.manifestPath,'utf8'));
   expect(validateManifest(manifest).map(item=>item.song_id)).toEqual([736,738,741]);
   const previewById=Object.fromEntries(manifest.songs.map(item=>[item.song_id,item.preview]));
   expect(previewById[736].scalar_changes.map(change=>[change.field,change.after])).toEqual([['release_year',1973]]);
   expect(previewById[738]).toMatchObject({has_changes:false,approved_mbid:null,scalar_changes:[],tag_additions:{genres:[],vibes:[]}});
   expect(previewById[741].scalar_changes.map(change=>[change.field,change.after])).toEqual([['release_year',1974]]);
   expect(previewById[741].tag_additions.genres.map(item=>item.name)).toEqual(['Blues Rock']);
   expect(previewById[741].primary_proposal).toBeNull();
  });
});
