import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';
const require = createRequire(import.meta.url);
const { buildBestEffortDecisions, buildDecisionTemplate, buildManifest, hashBytes, normalizeReports, stableJson } = require('../../server/operatorEnrichmentWorkflow.js');
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
 baseline:{id:727,title:song.title,artist:song.artist},requested_missing_fields:["original_key"],approved_apply_manifest:null,
 factual_evidence:{context_version:1,target:context.target,recording:context.recording,scalar_candidates:Object.fromEntries(Object.entries(context.scalar_candidates).map(([k,v])=>[k,{state:"unapproved_candidates",evidence_status:"ok",conflicting_evidence:v.status==="conflicting",candidates:v.candidates}])),submission_coverage:context.submission_coverage,conflicts:context.conflicts},
 gemini_suggestions:{status:"ok",response_digest:gd,model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2",
 scalars:{release_year:[],original_key:[{value:{key_key:"C",key_scale:"minor"},source:"Gemini",response_digest:gd,model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}],bpm:[]},genres:[],vibes:[]}};
function source(document){const bytes=Buffer.from(JSON.stringify(document));return{path:"saved-report.json",bytes,document};}

describe("saved-evidence operator consolidation",()=>{
 it("accepts the factual-not-attempted status with exact-song Gemini provenance and no MBID",()=>{
  const status={report_version:1,report_type:"factual_acquisition_status",database_writes:false,selected_song_ids:[727],
   approved_apply_manifest:null,songs:[{song_id:727,identity:{title:song.title,artist:song.artist},
    acquisition:{status:"not_attempted_due_to_provider_failure",reason:"factual_provider_failure_guard"},
    human_review_required:true,approved_preview:null}]};
  const geminiOnly={...fallback,factual_evidence:null,requested_missing_fields:["release_year"],
   gemini_suggestions:{...fallback.gemini_suggestions,scalars:{...fallback.gemini_suggestions.scalars,
    original_key:[],bpm:[],release_year:[{value:1978,source:"Gemini",response_digest:gd,model_id:"gemini-3.1-flash-lite",
     prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}]}}};
  const normalized=normalizeReports(song,[source(status),source(geminiOnly)]);
  expect(normalized.evidence.mbid).toBeNull();
  expect(normalized.evidence.scalars.release_year).toHaveLength(1);
  expect(normalized.evidence.scalars.release_year[0]).toMatchObject({value:1978,source:"Gemini",response_digest:gd,
   song_identity:{song_id:727,title:song.title,artist:song.artist}});
  expect(normalized.evidence.scalars.original_key).toEqual([]);
  const decisions=buildBestEffortDecisions({baseline:{songs:[song]},sourceReports:{727:normalized},definitions});
  expect(decisions.decisions.targets[0].decision).toMatchObject({mbid:null,scalars:{release_year:{value:1978,source:"Gemini"}},genres:[],vibes:[]});
  const forged={...status,songs:[{...status.songs[0],identity:{...status.songs[0].identity,title:"Different Song"}}]};
  expect(()=>normalizeReports(song,[source(forged),source(geminiOnly)])).toThrow(expect.objectContaining({code:"EVIDENCE_IDENTITY_MISMATCH"}));
 });
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
    response_digest:item.response_digest,model_id:item.model_id,prompt_version:item.prompt_version,schema_version:item.schema_version,
    song_identity:{song_id:song.id,title:song.title,artist:song.artist}})));
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
 it("revalidates saved Gemini response hashes and requested scalar fields",()=>{
  const digestMismatch=structuredClone(fallback);
  digestMismatch.gemini_suggestions.scalars.original_key[0].response_digest="c".repeat(64);
  expect(()=>normalizeReports(song,[source(digestMismatch)])).toThrow(expect.objectContaining({code:"EVIDENCE_INVALID"}));
  const unrequested=structuredClone(fallback);
  unrequested.gemini_suggestions.scalars.release_year=[{value:1973,source:"Gemini",response_digest:gd,
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
  expect(()=>normalizeReports(song,[source(unrequested)])).toThrow(expect.objectContaining({code:"EVIDENCE_INVALID"}));
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
 it("keeps a direct B4 match without complete candidates out of automatic decisions but allows manual selection",()=>{
  const recording="a1111111-1111-4111-8111-111111111111";
  const year={value:1973,source:"MusicBrainz",response_digest:digest};
  const b4=b4Envelope([{song_id:727,identity:{title:song.title,artist:song.artist},
   recording:{match:{matched:true,mbid:recording,disambiguation:""}},human_review_required:true,approved_preview:null,
   evidence:{mbid:recording,scalars:{release_year:[year],original_key:[],bpm:[]},
    genres:[{name:"Blues Rock",source:"MusicBrainz",response_digest:digest}],vibes:[]}}]);
  const normalized=normalizeReports(song,[source(b4)]),sourceReports={727:normalized};
  expect(normalized.recording_identity).toMatchObject({accepted:false,reason:"recording_identity_not_accepted"});
  const baseline={songs:[song],definitions};
  const automatic=buildBestEffortDecisions({baseline,sourceReports,definitions});
  expect(automatic.decisions.targets[0].decision).toEqual({mbid:null,scalars:{},genres:[],vibes:[]});
  expect(automatic.report.songs[0]).toMatchObject({recording_match:{status:"abstained",reason:"recording_identity_not_accepted"},
   scalars:{release_year:{status:"abstained",reason:"recording_identity_not_accepted"}},
   genres:[{status:"abstained",reason:"recording_identity_not_accepted"}]});

  const manual=buildDecisionTemplate({baseline,sourceReports,definitions});
  manual.targets[0].decision.mbid=recording;
  manual.targets[0].decision.scalars.release_year=normalized.evidence.scalars.release_year[0];
  const manifest=buildManifest({baseline:{songs:[song]},decisions:manual,sourceReports,
   operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions});
  expect(manifest.songs[0].review_decision.mbid).toBe(recording);
  expect(manifest.songs[0].preview.scalar_changes).toEqual([{field:"release_year",before:null,after:1973,
   evidence:{source:"MusicBrainz",response_digest:digest}}]);
 });
 it("revalidates saved B1 candidates and carries only the newly selected recording's year",()=>{
  const target={...song,id:736,title:"And It Stoned Me",artist:"Van Morrison"};
  const live="a1111111-1111-4111-8111-111111111111",original="b2222222-2222-4222-8222-222222222222",later="c3333333-3333-4333-8333-333333333333";
  const candidate=(mbid,disambiguation,date,d)=>({mbid,title:target.title,artist:target.artist,disambiguation,
   ...(date?{first_release_date:date}:{}),response_digest:d.repeat(64)});
  const b4=b4Envelope([{song_id:736,identity:{title:target.title,artist:target.artist},human_review_required:true,approved_preview:null,
   recording:{search_status:"ok",match:{matched:true,mbid:later},candidates:[
    candidate(live,"live at the Roxy","1989","a"),candidate(original,"original stereo mix","1970-03","b"),candidate(later,"","1978-11-26","c")],
    pagination:{expected_total:3,attempted_pages:1,validated_pages:1,collected_count:3,complete:true},
    release_date_comparison:{lookup_year_discrepancy:false}},
   evidence:{mbid:later,scalars:{release_year:[{value:1978,source:"MusicBrainz",response_digest:digest}],
    original_key:[{value:"D",source:"AcousticBrainz",response_digest:gd}],bpm:[{value:120,source:"AcousticBrainz",response_digest:gd}]},
    genres:[{name:"Blues Rock",source:"MusicBrainz",response_digest:digest}],vibes:[]}}]);
  const normalized=normalizeReports(target,[source(b4)]);
  expect(normalized.evidence.mbid).toBe(original);
  expect(normalized.recording_identity).toMatchObject({accepted:true,mbid:original});
  expect(normalized.evidence.scalars).toEqual({release_year:[{value:1970,source:"MusicBrainz",response_digest:"b".repeat(64)}],original_key:[],bpm:[]});
  expect(normalized.evidence.genres).toEqual([]);
  const sourceReports={736:normalized},defs={genres:[],vibes:[]};
  const decided=buildBestEffortDecisions({baseline:{songs:[target],definitions:defs},sourceReports,definitions:defs});
  expect(decided.decisions.targets[0].decision).toMatchObject({mbid:original,scalars:{release_year:{value:1970}},genres:[],vibes:[]});
  const manifest=buildManifest({baseline:{songs:[target]},decisions:decided.decisions,sourceReports,
   operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions:defs});
  expect(manifest.songs[0].preview.scalar_changes).toEqual([{field:"release_year",before:null,after:1970,
   evidence:{source:"MusicBrainz",response_digest:"b".repeat(64)}}]);
 });
 it("does not let saved B4 with an unmarked recording bypass release-type validation",()=>{
  const target={...song,id:736,title:"And It Stoned Me",artist:"Van Morrison"};
  const recording="a1111111-1111-4111-8111-111111111111",releaseId="b2222222-2222-4222-8222-222222222222";
  const candidate={mbid:recording,title:target.title,artist:target.artist,disambiguation:"",
   response_digest:"a".repeat(64)};
  const b4=b4Envelope([{song_id:736,identity:{title:target.title,artist:target.artist},human_review_required:true,approved_preview:null,
   recording:{search_status:"ok",match:{matched:true,mbid:recording},candidates:[candidate],
    pagination:{expected_total:1,attempted_pages:1,validated_pages:1,collected_count:1,complete:true}},
   evidence:{mbid:recording,scalars:{release_year:[{value:1970,source:"MusicBrainz",response_digest:digest}],original_key:[],bpm:[]},genres:[],vibes:[]}}]);
  const unresolved=normalizeReports(target,[source(b4)]);
  expect(unresolved.recording_identity).toMatchObject({accepted:false,reason:"recording_identity_unresolved"});
  expect(unresolved.evidence).toMatchObject({mbid:null,scalars:{release_year:[]},genres:[],vibes:[]});

  b4.songs[0].recording.release_type_evidence=[{mbid:recording,response_digest:"c".repeat(64),first_release_date:"1970-03-01",complete:true,release_count:1,
   releases:[{id:releaseId,title:"Studio Album",date:"1970-03-01",status:"Official",disambiguation:"",
    release_group:{id:"c3333333-3333-4333-8333-333333333333",title:"Studio Album",primary_type:"Album",secondary_types:[]}}]}];
  const accepted=normalizeReports(target,[source(b4)]);
  expect(accepted.recording_identity).toMatchObject({accepted:true,mbid:recording});
  expect(accepted.evidence.scalars.release_year).toEqual([{value:1970,source:"MusicBrainz",response_digest:"c".repeat(64)}]);
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
 it("selects one unconflicted missing scalar, preserves populated values, and reports conflicts without choosing them",()=>{
  const normalized=normalizeReports(song,[source(context)]),sourceReports={727:normalized};
  const result=buildBestEffortDecisions({baseline:{songs:[song],definitions},sourceReports,definitions});
  expect(result.decisions.targets[0].decision).toEqual({mbid,scalars:{release_year:normalized.evidence.scalars.release_year[0]},genres:[],vibes:[]});
  expect(result.report.songs[0].scalars).toMatchObject({
   release_year:{status:"selected",reason:"one_unconflicted_canonical_value",value:1973,source:"MusicBrainz"},
   original_key:{status:"abstained",reason:"conflicting_evidence"},
   bpm:{status:"abstained",reason:"no_candidate"},
  });
  const disagreement=structuredClone(normalized);
  disagreement.evidence.scalars.release_year.push({...disagreement.evidence.scalars.release_year[0],
   value:1974,source:"Gemini",response_digest:gd,model_id:"gemini-3.1-flash-lite",
   prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"});
  const conflicted=buildBestEffortDecisions({baseline:{songs:[song],definitions},sourceReports:{727:disagreement},definitions});
  expect(conflicted.report.songs[0].scalars.release_year).toMatchObject({status:"abstained",reason:"conflicting_evidence"});
  const invalid=structuredClone(normalized);
  invalid.evidence.scalars.release_year[0].value=0;
  const rejected=buildBestEffortDecisions({baseline:{songs:[song],definitions},sourceReports:{727:invalid},definitions});
  expect(rejected.report.songs[0].scalars.release_year).toMatchObject({status:"abstained",reason:"invalid_candidate"});
  const populated={...song,release_year:1978,original_key:"Cm",bpm:134};
  const preserved=buildBestEffortDecisions({baseline:{songs:[populated],definitions},sourceReports,definitions});
  expect(preserved.decisions.targets[0].decision.scalars).toEqual({});
  expect(preserved.report.songs[0].scalars).toMatchObject({
   release_year:{status:"preserved",reason:"already_populated"},
   original_key:{status:"preserved",reason:"already_populated"},
   bpm:{status:"preserved",reason:"already_populated"},
  });
 });
 it("selects category-correct missing labels once, preserves assignments and primary, and never infers a primary",()=>{
  const recording="62ec7207-aa84-43b6-949e-8066b6be2cad",tagDigest="c".repeat(64);
  const b4=b4Envelope([{song_id:727,identity:{title:song.title,artist:song.artist},
   recording:{search_status:"ok",match:{matched:true,mbid:recording},candidates:[{mbid:recording,title:song.title,artist:song.artist,
    disambiguation:"original stereo mix",first_release_date:"1973",response_digest:digest}],
    pagination:{complete:true,expected_total:1,collected_count:1,attempted_pages:1,validated_pages:1}},approved_preview:null,human_review_required:true,
   evidence:{mbid:recording,scalars:{release_year:[],original_key:[],bpm:[]},
    genres:["Rock","Country","country"].map(name=>({name,source:"MusicBrainz",response_digest:tagDigest})),
    vibes:["Country","Upbeat"].map(name=>({name,source:"MusicBrainz",response_digest:tagDigest}))},
   scalar_candidates:{},tag_candidates:{}}]);
  const assigned={...song,genres:[{id:10,name:"Rock",is_primary:true}],vibes:[{id:20,name:"Country"}]};
  const catalog={genres:[{id:10,name:"Rock"},{id:11,name:"Country"}],vibes:[{id:20,name:"Country"},{id:21,name:"Upbeat"}]};
  const normalized=normalizeReports(assigned,[source(b4)]),sourceReports={727:normalized};
  const result=buildBestEffortDecisions({baseline:{songs:[assigned],definitions:catalog},sourceReports,definitions:catalog});
  expect(result.decisions.targets[0].decision).toMatchObject({mbid:recording,genres:["Country"],vibes:["Upbeat"]});
  expect(result.decisions.targets[0].decision.primaryGenre).toBeUndefined();
  expect(result.report.songs[0].genres).toContainEqual({name:"Rock",definition_id:10,status:"preserved",reason:"already_assigned"});
  expect(result.report.songs[0].genres).toContainEqual({name:"Country",definition_id:11,status:"selected",
   reason:"unique_category_definition_duplicate_evidence_collapsed"});
  expect(result.report.songs[0].vibes).toContainEqual({name:"Country",definition_id:20,status:"preserved",reason:"already_assigned"});
  expect(result.report.songs[0].vibes).toContainEqual({name:"Upbeat",definition_id:21,status:"selected",reason:"unique_category_definition"});

  const primaryless={...assigned,genres:[]};
  const noPrimary=buildBestEffortDecisions({baseline:{songs:[primaryless],definitions:catalog},sourceReports,definitions:catalog});
  expect(noPrimary.decisions.targets[0].decision.genres).toEqual(["Country","Rock"]);
  expect(noPrimary.decisions.targets[0].decision).not.toHaveProperty("primaryGenre");
  expect(noPrimary.report.songs[0].primary_genre).toEqual({status:"unchanged",reason:"automatic_primary_inference_disabled"});
 });
 it("abstains on unresolved recording identity and unknown or ambiguous category definitions",()=>{
  const uncertain={report_version:1,report_type:"gemini_genre_only_preview",database_writes:false,approval_or_apply_manifest:null,
   song:{id:727,title:song.title,artist:song.artist},request:{requested_scalars:[],allowed_genres:["Blues Rock"],allowed_vibes:[]},
   limits:{model:"gemini-3.1-flash-lite"},result:{status:"ok",response_digest:gd,
    genres:[{name:"Blues Rock",source:"Gemini",response_digest:gd,model_id:"gemini-3.1-flash-lite",
     prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}],vibes:[]}};
  const normalized=normalizeReports(song,[source(uncertain)]),sourceReports={727:normalized};
  const unresolved=buildBestEffortDecisions({baseline:{songs:[song],definitions},sourceReports,definitions});
  expect(unresolved.decisions.targets[0].decision).toEqual({mbid:null,scalars:{},genres:["Blues Rock"],vibes:[]});
  expect(unresolved.report.songs[0].recording_match.reason).toBe("recording_identity_unresolved");
  expect(unresolved.report.songs[0].genres[0].reason).toBe("unique_category_definition");

  const recording="62ec7207-aa84-43b6-949e-8066b6be2cad";
  const b4=b4Envelope([{song_id:727,identity:{title:song.title,artist:song.artist},
   recording:{search_status:"ok",match:{matched:true,mbid:recording},candidates:[{mbid:recording,title:song.title,artist:song.artist,
    disambiguation:"original stereo mix",first_release_date:"1973",response_digest:digest}],
    pagination:{complete:true,expected_total:1,collected_count:1,attempted_pages:1,validated_pages:1}},
   evidence:{mbid:recording,scalars:{release_year:[],original_key:[],bpm:[]},
    genres:[{name:"Blues Rock",source:"MusicBrainz",response_digest:digest}],vibes:[]},
   approved_preview:null,human_review_required:true}]);
  const matched=normalizeReports(song,[source(b4)]),reports={727:matched};
  const mixed={genres:[{id:1,name:"Blues Rock"},{id:2,name:" blues   rock "}],vibes:[]};
  const diagnosis=buildBestEffortDecisions({baseline:{songs:[song],definitions:mixed},sourceReports:reports,definitions:mixed});
  expect(diagnosis.decisions.targets[0].decision.genres).toEqual([]);
  expect(diagnosis.report.songs[0].genres[0].reason).toBe("ambiguous_definition");
  expect(()=>buildBestEffortDecisions({baseline:{songs:[song],definitions},sourceReports:{},definitions})).toThrow();
  expect(()=>buildBestEffortDecisions({baseline:{songs:[song],definitions},sourceReports:{727:{sources:[],source_context:[],evidence:{scalars:{},genres:[],vibes:[]}}},definitions}))
   .toThrow(expect.objectContaining({code:"BEST_EFFORT_INPUT_INVALID"}));
 });
 it("selects validated Gemini values for unresolved identity and binds each candidate to the exact song",()=>{
  const target={...song};
  const report=structuredClone(fallback);
  report.requested_missing_fields=["release_year","original_key","bpm"];
  report.selected_song_id=target.id;
  report.baseline={id:target.id,title:target.title,artist:target.artist};
  report.recording={match:{matched:false,reason:"ambiguous_recording"}};
  report.factual_evidence={context_version:"b4-v1",scalar_candidates:Object.fromEntries(
   ["release_year","original_key","bpm"].map(field=>[field,{candidates:[]} ])),genre_evidence:[],vibe_evidence:[]};
  report.gemini_suggestions.scalars.release_year=[{value:1978,source:"Gemini",response_digest:gd,...{
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}}];
  report.gemini_suggestions.scalars.original_key=[{value:{key_key:"C",key_scale:"minor"},source:"Gemini",response_digest:gd,
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
  report.gemini_suggestions.scalars.bpm=[{value:96,source:"Gemini",response_digest:gd,
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
  report.gemini_suggestions.genres=[{name:"Blues Rock",source:"Gemini",response_digest:gd,
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
  report.gemini_suggestions.vibes=[{name:"Rocker",source:"Gemini",response_digest:gd,
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
  const normalized=normalizeReports(target,[source(report)]),sourceReports={[target.id]:normalized};
  expect(normalized.evidence.mbid).toBeNull();
  const decided=buildBestEffortDecisions({baseline:{songs:[target],definitions},sourceReports,definitions});
  const decision=decided.decisions.targets[0].decision;
  expect(decision).toMatchObject({mbid:null,genres:["Blues Rock"],vibes:["Rocker"]});
  expect(decision.scalars).toMatchObject({release_year:{value:1978,source:"Gemini"},
   original_key:{value:{key_key:"C",key_scale:"minor"},source:"Gemini"},bpm:{value:96,source:"Gemini"}});
  expect(decision.scalars.release_year.song_identity).toEqual({song_id:target.id,title:target.title,artist:target.artist});
  const manifest=buildManifest({baseline:{songs:[target]},decisions:decided.decisions,sourceReports,
   operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions});
  expect(manifest.songs[0].preview.has_changes).toBe(true);
  expect(manifest.songs[0].preview.approved_mbid).toBeNull();
  expect(manifest.songs[0].preview.primary_proposal).toBeNull();
  expect(manifest.songs[0].preview.scalar_changes.every(change=>change.evidence.song_identity.song_id===target.id)).toBe(true);
  expect(manifest.songs[0].preview.tag_additions.genres[0].evidence[0].song_identity.artist).toBe(target.artist);
 });
 it("abstains on conflicting or malformed Gemini evidence and never selects factual values without an accepted MBID",()=>{
  const target={...song};
  const report=structuredClone(fallback);
  report.requested_missing_fields=["release_year"];
  report.selected_song_id=target.id;report.baseline={id:target.id,title:target.title,artist:target.artist};
  report.recording={match:{matched:false,reason:"unresolved"}};
  report.factual_evidence={context_version:"b4-v1",scalar_candidates:Object.fromEntries(
   ["release_year","original_key","bpm"].map(field=>[field,{candidates:[]} ])),genre_evidence:[],vibe_evidence:[]};
  report.gemini_suggestions.scalars.release_year=[1978,1979].map(value=>({value,source:"Gemini",response_digest:gd,
   model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}));
  report.gemini_suggestions.scalars.original_key=[];
  const normalized=normalizeReports(target,[source(report)]);
  const decided=buildBestEffortDecisions({baseline:{songs:[target],definitions},sourceReports:{[target.id]:normalized},definitions});
  expect(decided.decisions.targets[0].decision.scalars).toEqual({});
  expect(decided.report.songs[0].scalars.release_year.reason).toBe("conflicting_evidence");
  const malformed=structuredClone(report);malformed.gemini_suggestions.scalars.release_year[0].schema_version="bad";
  expect(()=>normalizeReports(target,[source(malformed)])).toThrow();

  const forged={...normalized,evidence:{mbid:null,scalars:{release_year:[{value:1978,source:"MusicBrainz",response_digest:digest,
    submission_id:`recording:${mbid}`}],original_key:[],bpm:[]},genres:[],vibes:[]}};
  const factualOnly=buildBestEffortDecisions({baseline:{songs:[target],definitions},sourceReports:{[target.id]:forged},definitions});
  expect(factualOnly.decisions.targets[0].decision).toEqual({mbid:null,scalars:{},genres:[],vibes:[]});
 });
 it("requires accepted recording identity at the normalization-to-automatic-decision boundary",()=>{
  const makeLegacyFallback=(target,recording,preview=null)=>{
   const report=structuredClone(fallback);
   report.selected_song_id=target.id;
   report.baseline={id:target.id,title:target.title,artist:target.artist};
   report.factual_evidence={context_version:"b4-v1",scalar_candidates:Object.fromEntries(
    ["release_year","original_key","bpm"].map(field=>[field,{candidates:[]} ])),genre_evidence:[],vibe_evidence:[]};
   report.recording=recording;
   report.b1_validation_preview=preview;
   report.gemini_suggestions.scalars.release_year=[{value:1973,source:"Gemini",response_digest:gd,
    model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
   report.requested_missing_fields=["release_year"];
   report.gemini_suggestions.scalars.original_key=[];
   report.gemini_suggestions.genres=[{name:"Blues Rock",source:"Gemini",response_digest:gd,
    model_id:"gemini-3.1-flash-lite",prompt_version:"missing-metadata-fallback-v2",schema_version:"missing-metadata-schema-v2"}];
   return report;
  };
  const cases=[
   {id:727,reason:"ambiguous",recording:{match:{matched:false,reason:"ambiguous_recording",mbid}}},
   {id:728,reason:"mismatch",recording:{title:"Different Song",artist:"Different Artist",match:{matched:true,mbid}}},
   {id:729,reason:"conflicting",recording:{match:{matched:false,reason:"conflicting_recording_candidates",mbid}},
    preview:{approved_mbid:"a2632d2c-5862-44f8-8235-1a35539ffbdb"}},
  ];
  const rejectedSongs=cases.map(item=>({...song,id:item.id,title:`Song ${item.id}`}));
  const rejectedReports=Object.fromEntries(cases.map((item,index)=>{
   const report=makeLegacyFallback(rejectedSongs[index],item.recording,item.preview);
   const normalized=normalizeReports(rejectedSongs[index],[source(report)]);
   expect(normalized.evidence.mbid).toBe(report.b1_validation_preview?.approved_mbid||report.recording?.match?.mbid||null);
   expect(normalized.recording_identity).toMatchObject({accepted:false,reason:"recording_identity_not_accepted"});
   return [item.id,normalized];
  }));

  const acceptedSong={...song,id:730,title:"Accepted Match"};
  const acceptedB4=b4Envelope([{song_id:730,identity:{title:acceptedSong.title,artist:acceptedSong.artist},
   recording:{search_status:"ok",match:{matched:true,mbid},candidates:[{mbid,title:acceptedSong.title,artist:acceptedSong.artist,
    disambiguation:"original stereo mix",first_release_date:"1973",response_digest:digest}],
    pagination:{complete:true,expected_total:1,collected_count:1,attempted_pages:1,validated_pages:1}},
   evidence:{mbid,scalars:{release_year:[{value:1973,source:"MusicBrainz",response_digest:digest}],original_key:[],bpm:[]},
    genres:[{name:"Blues Rock",source:"MusicBrainz",response_digest:digest}],vibes:[]},
   approved_preview:null,human_review_required:true}]);
  const acceptedNormalized=normalizeReports(acceptedSong,[source(acceptedB4)]);
  expect(acceptedNormalized.recording_identity).toMatchObject({accepted:true,mbid,reason:"accepted_saved_recording_identity"});
  const baselineSongs=[...rejectedSongs,acceptedSong];
  const sourceReports={...rejectedReports,730:acceptedNormalized};
  const result=buildBestEffortDecisions({baseline:{songs:baselineSongs,definitions},sourceReports,definitions});
  for(const item of cases){
   const songDecision=result.decisions.targets.find(target=>target.song_id===item.id).decision;
   expect(songDecision.mbid).toBeNull();
   expect(songDecision.scalars.release_year).toMatchObject({value:1973,source:"Gemini"});
   expect(songDecision.genres).toEqual(["Blues Rock"]);
   expect(result.report.songs.find(entry=>entry.song_id===item.id)).toMatchObject({
    recording_match:{status:"abstained",reason:"recording_identity_not_accepted"},
    scalars:{release_year:{status:"selected",reason:"one_unconflicted_canonical_value",source:"Gemini"}},
    genres:[{status:"selected",reason:"unique_category_definition"}],
   });
  }
  const acceptedDecision=result.decisions.targets.find(target=>target.song_id===730).decision;
  expect(acceptedDecision).toMatchObject({mbid,scalars:{release_year:acceptedNormalized.evidence.scalars.release_year[0]},genres:["Blues Rock"]});
  const manifest=buildManifest({baseline:{songs:baselineSongs},decisions:result.decisions,sourceReports,
   operationId:"94c50cb4-8394-4f9a-8eb4-598725fc3959",definitions});
  expect(manifest.songs.slice(0,3).every(entry=>entry.evidence.mbid===null&&entry.review_decision.mbid===null)).toBe(true);
 });
});
