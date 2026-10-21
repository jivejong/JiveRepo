import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';
const require = createRequire(import.meta.url);
const { buildDecisionTemplate, buildManifest, hashBytes, normalizeReports, stableJson } = require('../../server/operatorEnrichmentWorkflow.js');
const genreOnlyReport = require('../fixtures/gemini-genre-only-preview-v1.json');

const mbid="62ec7207-aa84-43b6-949e-8066b6be2cad", digest="a".repeat(64), gd="b".repeat(64);
const song={id:727,title:"Beer Drinkers and Hell Raisers",artist:"ZZ Top",artist_id:19,updated_at:"2026-10-13T03:17:35.356314Z",
 release_year:null,original_key:null,bpm:null,genres:[],vibes:[]};
const definitions={genres:[{id:1,name:"Blues Rock"}],vibes:[{id:2,name:"Rocker"}]};
const context={factual_context_version:1,target:{song_id:727,title:song.title,artist:song.artist},
 recording:{mbid,title:"Beer Drinkers & Hell Raisers",artist:"ZZ Top",disambiguation:"original stereo mix",validated:true,response_digest:digest},
 scalar_candidates:{release_year:{status:"resolved",candidates:[{source:"MusicBrainz",value:1973,response_digest:digest,submission_reference:`recording:${mbid}`}]},
 original_key:{status:"conflicting",candidates:[{source:"AcousticBrainz",value:{key_key:"C",key_scale:"minor"},response_digest:digest,submission_reference:`${mbid}:0`},{source:"AcousticBrainz",value:{key_key:"G",key_scale:"major"},response_digest:gd,submission_reference:`${mbid}:2`}]},
 bpm:{status:"unresolved",candidates:[]}},submission_coverage:{source:"AcousticBrainz",total_available:138,sampled_offsets:[0,2],completeness:"partial"},conflicts:{release_year:false,original_key:true,bpm:false}};
const fallback={format_version:1,mode:"explicit_gemini_missing_metadata_fallback",database_writes:false,selected_song_id:727,
 baseline:{id:727,title:song.title,artist:song.artist},approved_apply_manifest:null,
 factual_evidence:{context_version:1,target:context.target,recording:context.recording,scalar_candidates:Object.fromEntries(Object.entries(context.scalar_candidates).map(([k,v])=>[k,{state:"unapproved_candidates",evidence_status:"ok",conflicting_evidence:v.status==="conflicting",candidates:v.candidates}])),submission_coverage:context.submission_coverage,conflicts:context.conflicts},
 gemini_suggestions:{status:"ok",response_digest:gd,model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2",
 scalars:{release_year:[],original_key:[{value:{key_key:"C",key_scale:"minor"},source:"Gemini",response_digest:gd,model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}],bpm:[]},genres:[],vibes:[]}};
function source(document){const bytes=Buffer.from(JSON.stringify(document));return{path:"saved-report.json",bytes,document};}

describe("saved-evidence operator consolidation",()=>{
 it("normalizes factual context plus Gemini fallback without invented approvals and preserves provenance",()=>{
  const normalized=normalizeReports(song,[source(context),source(fallback)]);
  expect(normalized.evidence.mbid).toBe(mbid);
  expect(normalized.evidence.scalars.release_year[0]).toMatchObject({value:1973,source:"MusicBrainz",response_digest:digest,submission_id:`recording:${mbid}`});
  expect(normalized.evidence.scalars.original_key).toHaveLength(3);
  expect(normalized.evidence.scalars.original_key[2]).toMatchObject({source:"Gemini",model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"});
  expect(normalized.sources[0].sha256).toBe(hashBytes(source(context).bytes));
  const reports={727:normalized};
  const template=buildDecisionTemplate({baseline:{songs:[song],definitions},sourceReports:reports,definitions});
  expect(template.targets[0].decision).toEqual({mbid:null,scalars:{},genres:[],vibes:[]});
  const decision=structuredClone(template); decision.targets[0].decision.mbid=mbid;
  decision.targets[0].decision.scalars.release_year=normalized.evidence.scalars.release_year[0];
  decision.targets[0].decision.scalars.original_key=normalized.evidence.scalars.original_key[2];
  const manifest=buildManifest({baseline:{songs:[song]},decisions:decision,sourceReports:reports,operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions});
  expect(manifest.songs[0].preview.scalar_changes.map(x=>[x.field,x.after])).toEqual([["original_key","Cm"],["release_year",1973]]);
  expect(stableJson(manifest)).toContain("missing-metadata-schema-v2");
 });
 it("retains historical v1 Gemini provenance without rewriting it",()=>{
  const legacy=structuredClone(fallback);legacy.gemini_suggestions.prompt_version="missing-metadata-fallback-v1";
  legacy.gemini_suggestions.schema_version="missing-metadata-schema-v1";
  const candidate=legacy.gemini_suggestions.scalars.original_key[0];
  candidate.prompt_version="missing-metadata-fallback-v1";candidate.schema_version="missing-metadata-schema-v1";
  const normalized=normalizeReports(song,[source(legacy)]);
  expect(normalized.evidence.scalars.original_key[2]).toMatchObject({prompt_version:"missing-metadata-fallback-v1",schema_version:"missing-metadata-schema-v1"});
 });
 it("normalizes the producer-shaped Genre-only report into explicit Genre evidence only",()=>{
  const normalized=normalizeReports(song,[source(genreOnlyReport)]);
  expect(normalized.evidence.genres).toEqual(genreOnlyReport.result.genres.map(item=>({name:item.name,source:"Gemini",
    response_digest:item.response_digest,model_id:item.model_id,prompt_version:item.prompt_version,schema_version:item.schema_version})));
  expect(normalized.evidence.scalars).toEqual({release_year:[],original_key:[],bpm:[]});
  expect(normalized.evidence.vibes).toEqual([]);
  const report={727:normalized},baseline={songs:[song],definitions:{genres:[{id:1,name:"Blues Rock"},{id:2,name:"Hard Rock"},{id:3,name:"Southern Rock"}],vibes:[]}};
  const decisions=buildDecisionTemplate({baseline,sourceReports:report,definitions:baseline.definitions});
  decisions.targets[0].decision.genres=["Blues Rock","Hard Rock","Southern Rock"];
  const manifest=buildManifest({baseline:{songs:[song]},decisions,sourceReports:report,
    operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:baseline.definitions});
  expect(manifest.songs[0].review_decision).toMatchObject({genres:["Blues Rock","Hard Rock","Southern Rock"],vibes:[]});
  expect(manifest.songs[0].review_decision.primaryGenre).toBeUndefined();
  expect(manifest.songs[0].preview.scalar_changes).toEqual([]);
  expect(manifest.songs[0].preview.tag_additions.genres.map(item=>[item.id,item.name])).toEqual([[1,"Blues Rock"],[2,"Hard Rock"],[3,"Southern Rock"]]);
  expect(manifest.songs[0].preview.tag_additions.vibes).toEqual([]);
  expect(manifest.songs[0].evidence.genres).toEqual(normalized.evidence.genres);
  expect(stableJson(manifest)).toContain("missing-metadata-schema-v2");
  const historical=structuredClone(genreOnlyReport);
  for(const item of historical.result.genres){item.prompt_version="missing-metadata-fallback-v1";item.schema_version="missing-metadata-schema-v1";}
  expect(normalizeReports(song,[source(historical)]).evidence.genres[0]).toMatchObject({
    prompt_version:"missing-metadata-fallback-v1",schema_version:"missing-metadata-schema-v1"});
 });
 it("rejects mismatched, malformed, failed or unsupported Genre-only reports without candidates",()=>{
  expect(()=>normalizeReports(song,[source({...genreOnlyReport,song:{...genreOnlyReport.song,id:728}})])).toThrow();
  const malformed=structuredClone(genreOnlyReport);malformed.result.genres[0].response_digest="bad";
  expect(()=>normalizeReports(song,[source(malformed)])).toThrow();
  const outsideSubset=structuredClone(genreOnlyReport);outsideSubset.result.genres[0].name="blues rock";
  expect(()=>normalizeReports(song,[source(outsideSubset)])).toThrow();
  const wrongSource=structuredClone(genreOnlyReport);wrongSource.result.genres[0].source="MusicBrainz";
  expect(()=>normalizeReports(song,[source(wrongSource)])).toThrow();
  const unsupportedProvenance=structuredClone(genreOnlyReport);unsupportedProvenance.result.genres[0].schema_version="missing-metadata-schema-v3";
  expect(()=>normalizeReports(song,[source(unsupportedProvenance)])).toThrow();
  const failed=structuredClone(genreOnlyReport);failed.result.status="invalid_response";
  expect(()=>normalizeReports(song,[source(failed)])).toThrow();
  const abstention=structuredClone(genreOnlyReport);abstention.result.genres=[];
  expect(normalizeReports(song,[source(abstention)]).evidence.genres).toEqual([]);
  const unexpectedVibe=structuredClone(genreOnlyReport);unexpectedVibe.result.vibes=[{name:"Rocker",source:"Gemini",response_digest:genreOnlyReport.result.response_digest,
    model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
  expect(()=>normalizeReports(song,[source(unexpectedVibe)])).toThrow();
  const wrongType=structuredClone(genreOnlyReport);wrongType.report_type="future_genre_preview";
  expect(()=>normalizeReports(song,[source(wrongType)])).toThrow();
 });
 it("rejects a target mismatch, malformed evidence, and an edited unevidenced value",()=>{
  expect(()=>normalizeReports(song,[source({...context,target:{...context.target,title:"Other"}})])).toThrow();
  expect(()=>normalizeReports(song,[source({...context,recording:{...context.recording,mbid:"bad"}})])).toThrow();
  expect(()=>normalizeReports(song,[source({...context,recording:{...context.recording,title:"Different recording"}})])).toThrow();
  const normalized=normalizeReports(song,[source(context)]); const reports={727:normalized};
  const template=buildDecisionTemplate({baseline:{songs:[song],definitions},sourceReports:reports,definitions});
  template.targets[0].decision.scalars.release_year={value:1974,source:"MusicBrainz",response_digest:digest};
  expect(()=>buildManifest({baseline:{songs:[song]},decisions:template,sourceReports:reports,operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions})).toThrow();
 });
 const b4Envelope=(songs)=>({report_version:1,report_type:"existing_song_enrichment_read_only_preview",selected_song_ids:songs.map(row=>row.song_id),
   database_writes:false,approved_apply_manifest:null,baseline:{captured_at:"2026-10-19T12:00:00.000Z"},songs});
 it("accepts the actual B4 report_version contract and preserves a genuine abstention without inventing evidence",()=>{
  const abstaining={song_id:727,identity:{title:song.title,artist:song.artist},recording:{search_status:"invalid_response",match:{matched:false,reason:"incomplete_search_results"},candidates:[]},
    acousticbrainz:{status:"skipped",submission_enumeration:"not_requested"},evidence:null,
    scalar_candidates:Object.fromEntries(["release_year","original_key","bpm"].map(field=>[field,{current_value:null,state:"not_attempted",candidates:[],conflicting_evidence:false}])),
    tag_candidates:{genres:{unapproved_additions:[],unknown_labels:[],ambiguous_labels:[]},vibes:{unapproved_additions:[],unknown_labels:[],ambiguous_labels:[]}},approved_preview:null,human_review_required:true};
  const normalized=normalizeReports(song,[source(b4Envelope([abstaining]))]);
  expect(normalized.evidence).toEqual({mbid:null,scalars:{release_year:[],original_key:[],bpm:[]},genres:[],vibes:[]});
  expect(()=>normalizeReports(song,[source({format_version:1,report_type:"existing_song_enrichment_read_only_preview"})])).toThrow();
  expect(()=>normalizeReports(song,[source({report_version:99,report_type:"existing_song_enrichment_read_only_preview"})])).toThrow();
 });
 it("accepts successful Lights B4 evidence using the producer's real shape",()=>{
  const recording="a2632d2c-5862-44f8-8235-1a35539ffbdb";
  const b4=b4Envelope([{song_id:733,identity:{title:"Lights",artist:"Journey"},recording:{search_status:"ok",match:{matched:true,mbid:recording}},
   acousticbrainz:{status:"ok",submission_count:226,sampled_offsets:[0,112,225],submission_enumeration:"partial"},
   evidence:{mbid:recording,scalars:{release_year:[{value:1978,source:"MusicBrainz",response_digest:"d99b7ef54d2dd4005b96e77a418a1a515fc616043ac2fb19dff1cb9f5f937b42"}],
    original_key:[{value:{key_key:"F#",key_scale:"minor"},source:"AcousticBrainz",response_digest:"279966ed3f142eb298b6c8b3ea14139576fbb63770c0c368dad99118069bb374",submission_id:`${recording}:0`}],
    bpm:[{value:103.80191803,source:"AcousticBrainz",response_digest:"b139e3e6b3e8f47db5a6e52d18bde29ff89a4652d42a1e9dea8fa486195ef2f5",submission_id:`${recording}:112`}]},
    genres:[{name:"arena rock",source:"MusicBrainz",response_digest:"d99b7ef54d2dd4005b96e77a418a1a515fc616043ac2fb19dff1cb9f5f937b42"}],vibes:[]},
   scalar_candidates:{},tag_candidates:{},approved_preview:null,human_review_required:true}]);
  const lights={...song,id:733,title:"Lights",artist:"Journey"};
  const normalized=normalizeReports(lights,[source(b4)]);
  expect(normalized.evidence.mbid).toBe(recording);
  expect(normalized.evidence.scalars.bpm[0]).toMatchObject({value:103.80191803,submission_id:`${recording}:112`});
 });
 it("rejects missing/false B4 structure and does not treat abstention display candidates as evidence",()=>{
  const item={song_id:727,identity:{title:song.title,artist:song.artist},evidence:null,human_review_required:true,approved_preview:null};
  expect(()=>normalizeReports(song,[source({...b4Envelope([item]),database_writes:true})])).toThrow();
  expect(()=>normalizeReports(song,[source(b4Envelope([{...item,identity:{...item.identity,title:"Other"}}]))])).toThrow();
 });
 it("accepts actual B4 version and canonical successful evidence",()=>{
  const b4=b4Envelope([{song_id:727,identity:{title:song.title,artist:song.artist},recording:{match:{matched:true,mbid}},human_review_required:true,approved_preview:null,
   evidence:{mbid,scalars:{release_year:[{value:1973,source:"MusicBrainz",response_digest:digest}],original_key:[],bpm:[]},genres:[],vibes:[]}}]);
  expect(normalizeReports(song,[source(b4)]).evidence.scalars.release_year[0].value).toBe(1973);
  expect(()=>normalizeReports(song,[source({report_version:1,report_type:"other"})])).toThrow();
 });
 it("carries the saved Lights demonstration's year, sampled BPM/key, and category evidence into the operator sequence",()=>{
  const lights={id:733,title:"Lights",artist:"Journey",artist_id:25,updated_at:"2026-10-15T01:47:38.332302Z",
    release_year:null,original_key:"D",bpm:null,genres:[{id:229,name:"Classic Rock",is_primary:true},{id:230,name:"Pop",is_primary:false}],vibes:[{id:161,name:"Mellow"},{id:163,name:"Emotional"}]};
  const mbDigest="d99b7ef54d2dd4005b96e77a418a1a515fc616043ac2fb19dff1cb9f5f937b42";
  const bpmDigest="b139e3e6b3e8f47db5a6e52d18bde29ff89a4652d42a1e9dea8fa486195ef2f5";
  const abDigest="279966ed3f142eb298b6c8b3ea14139576fbb63770c0c368dad99118069bb374";
  const recording="a2632d2c-5862-44f8-8235-1a35539ffbdb";
  const b4=b4Envelope([{song_id:733,
    identity:{title:"Lights",artist:"Journey"},recording:{match:{matched:true,mbid:recording}},approved_preview:null,human_review_required:true,evidence:{mbid:recording,
      scalars:{release_year:[{value:1978,source:"MusicBrainz",response_digest:mbDigest}],
        original_key:[{value:{key_key:"F#",key_scale:"minor"},source:"AcousticBrainz",response_digest:abDigest,submission_id:`${recording}:0`}],
        bpm:[{value:103.80191803,source:"AcousticBrainz",response_digest:bpmDigest,submission_id:`${recording}:112`}]},
      genres:[{name:"arena rock",source:"MusicBrainz",response_digest:mbDigest}],vibes:[]}}]);
  const normalized=normalizeReports(lights,[source(b4)]);
  const baseline={songs:[lights],definitions:{genres:[{id:1,name:"Arena Rock"}],vibes:[]}};
  const reports={733:normalized},template=buildDecisionTemplate({baseline,sourceReports:reports,definitions:baseline.definitions});
  const selected=structuredClone(template);selected.targets[0].decision.mbid=recording;
  selected.targets[0].decision.scalars.release_year=normalized.evidence.scalars.release_year[0];
  const manifest=buildManifest({baseline:{songs:[lights]},decisions:selected,sourceReports:reports,
    operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:baseline.definitions});
  expect(normalized.evidence.mbid).toBe(recording);
  expect(manifest.songs[0].preview.has_changes).toBe(true);
  expect(manifest.songs[0].preview.scalar_changes).toEqual([{field:"release_year",before:null,after:1978,evidence:{source:"MusicBrainz",response_digest:mbDigest}}]);
  expect(manifest.songs[0].evidence.scalars.bpm[0]).toMatchObject({value:103.80191803,submission_id:`${recording}:112`});
  expect(manifest.songs[0].evidence.genres[0]).toMatchObject({name:"arena rock",source:"MusicBrainz",response_digest:mbDigest});
  selected.targets[0].decision.genres=["arena rock"];
  const tagManifest=buildManifest({baseline:{songs:[lights]},decisions:selected,sourceReports:reports,
    operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:baseline.definitions});
  expect(tagManifest.songs[0].review_decision.genres).toEqual(["arena rock"]);
  expect(tagManifest.songs[0].preview.tag_additions.genres.map(item=>item.name)).toEqual(["Arena Rock"]);
  selected.targets[0].decision.genres=[{name:"arena rock",source:"MusicBrainz",response_digest:mbDigest}];
  expect(()=>buildManifest({baseline:{songs:[lights]},decisions:selected,sourceReports:reports,
    operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:baseline.definitions})).toThrow();
  selected.targets[0].decision.genres=["Rocker"];
  expect(()=>buildManifest({baseline:{songs:[lights]},decisions:selected,sourceReports:reports,
    operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:baseline.definitions})).toThrow(expect.objectContaining({code:"DECISIONS_UNEVIDENCED"}));
  selected.targets[0].decision.genres=["arena rock"];
  const duplicateCatalog={genres:[{id:1,name:"Arena Rock"},{id:2,name:" arena   rock "}],vibes:[]};
  const duplicateDecision=structuredClone(template);duplicateDecision.targets[0].decision.mbid=recording;
  duplicateDecision.targets[0].decision.scalars.release_year=normalized.evidence.scalars.release_year[0];
  duplicateDecision.targets[0].decision.genres=["arena rock"];
  duplicateDecision.targets[0].definitions_sha256=hashBytes(Buffer.from(stableJson(duplicateCatalog)));
  expect(()=>buildManifest({baseline:{songs:[lights]},decisions:duplicateDecision,sourceReports:reports,
    operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:duplicateCatalog})).toThrow();
 });
});
