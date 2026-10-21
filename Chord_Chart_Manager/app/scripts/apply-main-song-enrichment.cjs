#!/usr/bin/env node
const fs=require("node:fs/promises"),path=require("node:path"),{execFileSync}=require("node:child_process");
const {Pool}=require("../server/node_modules/pg");
const {parsePrivateEnv}=require("./preview-gemini-fallback.cjs");
const {validateDatabaseUrl}=require("./preview-existing-song-enrichment.cjs");
const {applyMainSongEnrichment,stableJson}=require("../server/applySongEnrichment.js");
const APP=path.resolve(__dirname,".."),ROOT=path.resolve(APP,".."),BACKUPS=path.join(ROOT,"backups");
function fail(code){const e=new Error(code);e.code=code;return e;}
function ignored(file){const rel=path.relative(ROOT,path.resolve(file));if(!rel||rel.startsWith(`..${path.sep}`)||path.isAbsolute(rel))return false;try{execFileSync("git",["check-ignore","--quiet","--no-index","--",rel],{cwd:ROOT,stdio:"ignore"});return true;}catch{return false;}}
function parse(argv){const values=new Map([["--expected-database","database"],["--manifest","manifest"],["--manifest-sha256","hash"],["--target-ids","targetIds"],["--restore-proof","proof"]]);const out={optIn:false};for(let i=0;i<argv.length;i++){const a=argv[i];if(a==="--apply-main"){if(out.optIn)throw fail("USAGE");out.optIn=true;}else if(values.has(a)){const key=values.get(a);if(out[key]||!argv[i+1]||argv[i+1].startsWith("--"))throw fail("USAGE");out[key]=argv[++i];}else throw fail("USAGE");}if(!out.optIn||!out.database||!out.manifest||!out.hash||!out.targetIds||!out.proof||!/^[a-f0-9]{64}$/.test(out.hash))throw fail("EXPLICIT_MAIN_APPLY_GATES_REQUIRED");out.ids=out.targetIds.split(",").map(Number);if(!out.ids.length||out.ids.length>25||out.ids.some(n=>!Number.isSafeInteger(n)||n<1)||new Set(out.ids).size!==out.ids.length)throw fail("TARGET_SET_INVALID");return out;}
async function main(argv=process.argv.slice(2),deps={}){
 const filesystem=deps.filesystem||fs;
 const a=parse(argv);const manifestPath=path.resolve(a.manifest),proofPath=path.resolve(a.proof);
 if(!manifestPath.startsWith(`${BACKUPS}${path.sep}`)||!proofPath.startsWith(`${BACKUPS}${path.sep}`)||!ignored(manifestPath)||!ignored(proofPath))throw fail("ARTIFACT_NOT_PRIVATE");
 const manifest=JSON.parse(await filesystem.readFile(manifestPath,"utf8"));
 let proof=null;
 try{proof=JSON.parse(await filesystem.readFile(proofPath,"utf8"));}
 catch(error){if(error?.code!=="ENOENT")throw fail("RESTORE_PROOF_FILE_INVALID");}
 if(require("node:crypto").createHash("sha256").update(stableJson(manifest)).digest("hex")!==a.hash)throw fail("MANIFEST_HASH_MISMATCH");
 const ids=manifest.songs?.map(item=>item.song_id).sort((x,y)=>x-y);if(JSON.stringify(ids)!==JSON.stringify([...a.ids].sort((x,y)=>x-y)))throw fail("TARGET_SET_MISMATCH");
 const env=deps.config||parsePrivateEnv(await filesystem.readFile(deps.envPath||path.join(APP,".env"),"utf8"));
 const databaseUrl=validateDatabaseUrl(process.env.DATABASE_URL||env.DATABASE_URL||
   `postgresql://${encodeURIComponent(env.POSTGRES_USER||"")}:${encodeURIComponent(env.POSTGRES_PASSWORD||"")}@127.0.0.1:${encodeURIComponent(env.DB_PORT||"15432")}/${encodeURIComponent(env.POSTGRES_DB||"")}`);
 const PoolClass=deps.Pool||Pool,pool=new PoolClass({connectionString:databaseUrl,connectionTimeoutMillis:5000});
 try{return await applyMainSongEnrichment(pool,manifest,{apply_main:true,expected_database:a.database,manifest_sha256:a.hash,target_ids:a.ids,
   manifest_path:manifestPath,restore_proof:proof});}finally{await pool.end();}
}
if(require.main===module)main().then(result=>process.stdout.write(`${JSON.stringify(result,null,2)}\n`)).catch(e=>{process.stderr.write(`Main enrichment apply refused: ${e.code||"FAILED"}\n`);process.exitCode=1;});
module.exports={main,parse};
