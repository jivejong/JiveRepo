#!/usr/bin/env node
const fs = require("node:fs/promises");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { Pool } = require("../server/node_modules/pg");
const { captureReadOnlyBaseline, validateDatabaseUrl } = require("./preview-existing-song-enrichment.cjs");
const { buildDecisionTemplate, buildManifest, hashBytes, normalizeReports, stableJson } = require("../server/operatorEnrichmentWorkflow.js");

const APP_ROOT = path.resolve(__dirname, "..");
const ROOT = path.resolve(APP_ROOT, "..");
const SCALARS = ["release_year", "original_key", "bpm"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function error(code) { const e = new Error(code); e.code = code; return e; }
function ignored(file) {
  const relative = path.relative(ROOT, path.resolve(file));
  if (!relative || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return false;
  try { execFileSync("git", ["check-ignore", "--quiet", "--no-index", "--", relative], { cwd: ROOT, stdio: "ignore" }); return true; }
  catch { return false; }
}
function parseArgs(argv) {
  const [stage, ...args] = argv;
  if (!new Set(["prepare", "preview"]).has(stage)) throw error("USAGE");
  const options = { stage, evidence: [] };
  const values = new Set(["--song-ids", "--evidence", "--output-dir", "--prepared", "--decisions"]);
  for (let i=0;i<args.length;i++) {
    const key=args[i];
    if (!values.has(key) || !args[i+1] || args[i+1].startsWith("--")) throw error("USAGE");
    const value=args[++i];
    if (key === "--evidence") options.evidence.push(value);
    else { const name = ({"--song-ids":"songIds","--output-dir":"outputDir","--prepared":"prepared","--decisions":"decisions"})[key];
      if (options[name] !== undefined) throw error("USAGE"); options[name]=value; }
  }
  if (!options.outputDir) throw error("USAGE");
  if (stage === "prepare") {
    options.ids=(options.songIds||"").split(",").map(Number);
    if (options.ids.length<1 || options.ids.length>25 || options.ids.some(n=>!Number.isSafeInteger(n)||n<1)||new Set(options.ids).size!==options.ids.length || !options.evidence.length) throw error("USAGE");
  } else if (!options.prepared || !options.decisions) throw error("USAGE");
  return options;
}
async function readReport(pathname) {
  const full=path.resolve(pathname);
  if (!full.startsWith(`${ROOT}${path.sep}`)) throw error("EVIDENCE_PATH_INVALID");
  const bytes=await fs.readFile(full);
  return { path:full, bytes, document:JSON.parse(bytes.toString("utf8")) };
}
async function createPrivateDir(input) {
  const target=path.resolve(input), backups=path.join(ROOT,"backups");
  if (!target.startsWith(`${backups}${path.sep}`) || !ignored(target)) throw error("OUTPUT_NOT_PRIVATE");
  const parent=await fs.realpath(path.dirname(target));
  if (!ignored(path.join(parent,path.basename(target)))) throw error("OUTPUT_NOT_PRIVATE");
  await fs.mkdir(target,{recursive:false,mode:0o700});
  return target;
}
async function writeExclusive(dir,name,content) {
  const handle=await fs.open(path.join(dir,name),"wx",0o600);
  try { await handle.writeFile(content); await handle.sync(); } finally { await handle.close(); }
}
function targetsFromEvidence(optionValues) {
  return optionValues.map(value=>{
    const match=/^(\d+)=(.+)$/.exec(value); if(!match) throw error("USAGE");
    return {id:Number(match[1]),path:match[2]};
  });
}
async function withReadOnlyPool(databaseUrl, fn, PoolClass=Pool) {
  const pool=new PoolClass({connectionString:databaseUrl, options:"-c default_transaction_read_only=on"});
  try { return await fn(pool); } finally { await pool.end(); }
}
function digestDefinitions(definitions) { return hashBytes(Buffer.from(stableJson(definitions))); }
async function loadDatabaseUrl(databaseUrl=process.env.DATABASE_URL) {
  const values={};
  if(!databaseUrl){
    const text=await fs.readFile(path.join(APP_ROOT,".env"),"utf8");
    for(const line of text.split(/\r?\n/)){const match=/^\s*(DATABASE_URL|POSTGRES_USER|POSTGRES_PASSWORD|POSTGRES_DB|DB_PORT)\s*=\s*(.*?)\s*$/.exec(line);
      if(match){let value=match[2];if((value.startsWith('"')&&value.endsWith('"'))||(value.startsWith("'")&&value.endsWith("'")))value=value.slice(1,-1);values[match[1]]=value;}}
    databaseUrl=values.DATABASE_URL;
    if(!databaseUrl&&values.POSTGRES_USER&&values.POSTGRES_PASSWORD&&values.POSTGRES_DB){
      databaseUrl=`postgresql://${encodeURIComponent(values.POSTGRES_USER)}:${encodeURIComponent(values.POSTGRES_PASSWORD)}@127.0.0.1:${encodeURIComponent(values.DB_PORT||"15432")}/${encodeURIComponent(values.POSTGRES_DB)}`;
    }
  }
  return validateDatabaseUrl(databaseUrl);
}
function candidateLine(candidate) {
  return JSON.stringify({ value: candidate.value, name: candidate.name, source: candidate.source,
    response_digest: candidate.response_digest,
    ...(candidate.submission_id ? { submission_reference: candidate.submission_id } : {}),
    ...(candidate.model_id ? { model_id: candidate.model_id, prompt_version: candidate.prompt_version, schema_version: candidate.schema_version } : {}) });
}
function summary(targets,stage) {
  return `${stage.toUpperCase()} - read-only song enrichment operator workflow\n`+targets.map(t=>{
    const artist=t.identity.artist_name||t.identity.artist;
    const lines=[`\nSong ${t.song_id}: ${t.identity.title} / ${artist}; exact version ${t.base_version}`,
      `Evidence files (${t.evidence_files.length}):`,...t.evidence_files.map(item=>`  ${item.path}  sha256=${item.sha256}`)];
    for(const context of t.source_context||[])lines.push(`Source context: ${JSON.stringify(context)}`);
    for(const field of SCALARS){const candidates=t.evidence.scalars[field]||[];lines.push(`${field}: ${candidates.length?candidates.map(candidateLine).join("; "):"no candidate"}`);}
    for(const category of ["genres","vibes"]){const candidates=t.evidence[category]||[];lines.push(`${category}: ${candidates.length?candidates.map(candidateLine).join("; "):"no candidate"}`);}
    const decision=t.review_decision||t.decision;
    if(!decision) lines.push("Decision selections: none. Review each candidate and edit the decision file explicitly.");
    else {
      const selected=[];
      if(decision.mbid)selected.push(`recording MBID ${decision.mbid}`);
      for(const [field,candidate] of Object.entries(decision.scalars||{}))selected.push(`${field} ${JSON.stringify(candidate.value)}`);
      for(const category of ["genres","vibes"])for(const label of decision[category]||[])selected.push(`${category.slice(0,-1)} ${label}`);
      if(decision.primaryGenre)selected.push(`primary Genre ${decision.primaryGenre}`);
      lines.push(`Decision selections: ${selected.length?selected.join("; "):"none"}.`);
    }
    return lines.join("\n");
  }).join("\n")+"\n";
}
function renderChanges(manifest) {
  return manifest.songs.map(song=>{
    const p=song.preview, lines=[`\nSong ${song.song_id}: ${song.identity.title} / ${song.identity.artist_name}; base ${song.base_version}`];
    for(const change of p.scalar_changes)lines.push(`  ${change.field}: ${JSON.stringify(change.before)} -> ${JSON.stringify(change.after)} (${change.evidence.source}, ${change.evidence.response_digest})`);
    for(const category of ["genres","vibes"])for(const item of p.tag_additions[category])lines.push(`  add ${category.slice(0,-1)}: ${item.name} [${item.id}]`);
    if(p.primary_proposal)lines.push(`  primary Genre: ${p.primary_proposal.name} [${p.primary_proposal.genre_id}]`);
    if(!p.has_changes)lines.push("  no changes");
    lines.push(`  unresolved/skipped: ${JSON.stringify(p.diagnostics)}`);
    return lines.join("\n");
  }).join("\n");
}
async function prepare(options,{databaseUrl,PoolClass=Pool}={}) {
  const url=await loadDatabaseUrl(databaseUrl);
  const inputs=options.evidence.map(value=>{const m=/^(\d+)=(.+)$/.exec(value);if(!m)throw error("USAGE");return{id:Number(m[1]),path:m[2]};});
  if(inputs.some(x=>!options.ids.includes(x.id)))throw error("EVIDENCE_TARGET_UNDECLARED");
  const docs=await Promise.all(inputs.map(async item=>({id:item.id,...await readReport(item.path)})));
  let baseline;
  baseline=await withReadOnlyPool(url,p=>captureReadOnlyBaseline(p,options.ids),PoolClass);
  const sourceReports={};
  for(const song of baseline.songs){
    const songDocs=docs.filter(d=>d.id===song.id);
    if(!songDocs.length)throw error("EVIDENCE_REQUIRED_PER_SONG");
    const normalized=normalizeReports(song,songDocs);
    sourceReports[song.id]={...normalized};
  }
  const prepared={operator_prepare_version:1,baseline,definitions_sha256:digestDefinitions(baseline.definitions),
    sources:Object.fromEntries(Object.entries(sourceReports).map(([id,v])=>[id,v.sources])),
    source_context:Object.fromEntries(Object.entries(sourceReports).map(([id,v])=>[id,v.source_context])),
    evidence:Object.fromEntries(Object.entries(sourceReports).map(([id,v])=>[id,v.evidence]))};
  const template=buildDecisionTemplate({baseline,sourceReports,definitions:baseline.definitions});
  const out=await createPrivateDir(options.outputDir);
  try { await writeExclusive(out,"prepared-v1.json",`${JSON.stringify(prepared,null,2)}\n`);
    await writeExclusive(out,"decisions-v1.json",`${JSON.stringify(template,null,2)}\n`);
    await writeExclusive(out,"review.txt",summary(template.targets.map(x=>({song_id:x.song_id,identity:x,base_version:x.base_version,
      evidence_files:sourceReports[x.song_id].sources,source_context:sourceReports[x.song_id].source_context,evidence:x.evidence})),"prepare"));
  } catch(e){for(const n of ["prepared-v1.json","decisions-v1.json","review.txt"])await fs.unlink(path.join(out,n)).catch(()=>{});await fs.rmdir(out).catch(()=>{});throw e;}
  return {outputDir:out,preparedPath:path.join(out,"prepared-v1.json"),decisionsPath:path.join(out,"decisions-v1.json"),reportPath:path.join(out,"review.txt")};
}
async function preview(options,{databaseUrl,PoolClass=Pool,operationId=require("node:crypto").randomUUID()}={}) {
  const url=await loadDatabaseUrl(databaseUrl), preparedDoc=JSON.parse(await fs.readFile(options.prepared,"utf8")), decisions=JSON.parse(await fs.readFile(options.decisions,"utf8"));
  if(preparedDoc.operator_prepare_version!==1||!Array.isArray(preparedDoc.baseline?.songs))throw error("PREPARED_INVALID");
  const ids=preparedDoc.baseline.songs.map(s=>s.id), reports=[];
  for(const [id,sources] of Object.entries(preparedDoc.sources))for(const source of sources){const read=await readReport(source.path);if(hashBytes(read.bytes)!==source.sha256)throw error("EVIDENCE_CHANGED");reports.push({id:Number(id),...read});}
  let current;
  current=await withReadOnlyPool(url,p=>captureReadOnlyBaseline(p,ids),PoolClass);
  if(stableJson(current)!==stableJson(preparedDoc.baseline)||digestDefinitions(current.definitions)!==preparedDoc.definitions_sha256)throw error("BASELINE_CHANGED_REPREPARE");
  const sourceReports={};
  for(const song of current.songs){const docs=reports.filter(r=>r.id===song.id);sourceReports[song.id]=normalizeReports(song,docs);}
  const manifest=buildManifest({baseline:current,decisions,sourceReports,operationId,definitions:current.definitions});
  const canonical=stableJson(manifest), sha256=hashBytes(Buffer.from(canonical));
  const deltas=manifest.songs.map(s=>({song_id:s.song_id,identity:s.identity,base_version:s.base_version,
    preview:s.preview, unresolved:s.preview.diagnostics}));
  const out=await createPrivateDir(options.outputDir);
  const receipt={operator_preview_version:1,created_at:new Date().toISOString(),manifest_path:path.join(out,"apply-manifest-v1.json"),
    manifest_sha256:sha256,manifest_canonical_sha256:sha256,target_ids:ids,definitions_sha256:preparedDoc.definitions_sha256,
    source_context:preparedDoc.source_context,
    evidence_sha256:Object.fromEntries(Object.entries(preparedDoc.sources).map(([id,items])=>[id,items.map(x=>x.sha256).sort()])),deltas};
  try {await writeExclusive(out,"apply-manifest-v1.json",`${JSON.stringify(manifest,null,2)}\n`);
    await writeExclusive(out,"preview-v1.json",`${JSON.stringify(receipt,null,2)}\n`);
    await writeExclusive(out,"changes.txt",summary(manifest.songs.map(s=>({song_id:s.song_id,identity:s.identity,base_version:s.base_version,
      review_decision:s.review_decision,evidence_files:sourceReports[s.song_id].sources,source_context:sourceReports[s.song_id].source_context,evidence:s.evidence})),"preview")+renderChanges(manifest));
  }catch(e){for(const n of ["apply-manifest-v1.json","preview-v1.json","changes.txt"])await fs.unlink(path.join(out,n)).catch(()=>{});await fs.rmdir(out).catch(()=>{});throw e;}
  return {outputDir:out,manifestPath:receipt.manifest_path,manifestSha256:sha256,receiptPath:path.join(out,"preview-v1.json"),changesPath:path.join(out,"changes.txt")};
}
async function main(argv=process.argv.slice(2),deps={}){const options=parseArgs(argv);return options.stage==="prepare"?prepare(options,deps):preview(options,deps);}
if(require.main===module)main().then(result=>process.stdout.write(`${JSON.stringify(result,null,2)}\n`)).catch(e=>{process.stderr.write(`Operator workflow refused: ${e.code||"FAILED"}\n`);process.exitCode=1;});
module.exports={createPrivateDir,loadDatabaseUrl,main,parseArgs,prepare,preview,targetsFromEvidence,withReadOnlyPool};
