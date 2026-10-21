#!/usr/bin/env node
const fs=require("node:fs/promises"),path=require("node:path"),crypto=require("node:crypto"),{execFileSync}=require("node:child_process");
const {Pool}=require("../server/node_modules/pg");
const {parsePrivateEnv}=require("./preview-gemini-fallback.cjs");
const {validateDatabaseUrl}=require("./preview-existing-song-enrichment.cjs");
const {protectedSnapshotSha256,signRestoreProof,stableJson}=require("../server/mainEnrichmentSafety.js");
const APP=path.resolve(__dirname,".."),ROOT=path.resolve(APP,".."),BACKUPS=path.join(ROOT,"backups");
function fail(code){const e=new Error(code);e.code=code;return e;}
function args(argv){const out={};const fields=new Set(["--expected-database","--manifest","--manifest-sha256"]);for(let i=0;i<argv.length;i++){const k=argv[i];if(!fields.has(k)||!argv[i+1]||out[k])throw fail("USAGE");out[k]=argv[++i];}if(fields.size!==Object.keys(out).length||!/^[-a-f0-9]{64}$/.test(out["--manifest-sha256"]))throw fail("USAGE");return out;}
function docker(values,execute=execFileSync){return execute("docker",values,{cwd:ROOT,encoding:"utf8",stdio:["ignore","pipe","pipe"]}).trim();}
function compose(){return["compose","--project-directory",APP,"-f",path.join(APP,"docker-compose.yml")];}
function sqlName(value){if(!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(value))throw fail("DATABASE_IDENTITY_INVALID");return `"${value}"`;}
async function main(argv=process.argv.slice(2),deps={}){
 const filesystem=deps.fs||fs,execute=deps.execFileSync||execFileSync;
 const snapshot=deps.protectedSnapshotSha256||protectedSnapshotSha256,signProof=deps.signRestoreProof||signRestoreProof;
 const a=args(argv),manifestPath=path.resolve(a["--manifest"]);
 if(!manifestPath.startsWith(`${BACKUPS}${path.sep}`))throw fail("MANIFEST_PATH_INVALID");
 const manifestBytes=await filesystem.readFile(manifestPath),manifest=JSON.parse(manifestBytes);
 if(crypto.createHash("sha256").update(stableJson(manifest)).digest("hex")!==a["--manifest-sha256"]||!Array.isArray(manifest.songs)||manifest.songs.length>25)throw fail("MANIFEST_HASH_MISMATCH");
 const env=parsePrivateEnv(await filesystem.readFile(path.join(APP,".env"),"utf8"));
 const databaseUrl=validateDatabaseUrl(process.env.DATABASE_URL||env.DATABASE_URL||`postgresql://${encodeURIComponent(env.POSTGRES_USER||"")}:${encodeURIComponent(env.POSTGRES_PASSWORD||"")}@127.0.0.1:${encodeURIComponent(env.DB_PORT||"15432")}/${encodeURIComponent(env.POSTGRES_DB||"")}`);
 const MainPool=deps.Pool||Pool,mainPool=new MainPool({connectionString:databaseUrl,options:"-c default_transaction_read_only=on"});
 let adminPool,ownsScratch=false,backupPath=null,containerId=null,tempDump=null;
 const runId=crypto.randomUUID(),scratch=`ccm_restore_${runId.replaceAll("-","")}`,marker=`CCM main apply restore verification run ${runId}`;
 let mainSnapshot,dbName,roleName,primaryError=null,mainPoolEnded=false;
 try{
  const client=await mainPool.connect();try{await client.query("BEGIN READ ONLY");const id=(await client.query("SELECT current_database() database_name,current_user role_name")).rows[0];
    dbName=id.database_name;roleName=id.role_name;if(dbName!==a["--expected-database"])throw fail("DATABASE_IDENTITY_MISMATCH");
    if((await client.query("SELECT 1 FROM public.schema_migrations WHERE version='0004'")).rowCount!==1)throw fail("MIGRATION_REQUIRED");
    mainSnapshot=await snapshot(client);await client.query("COMMIT");
  }catch(e){try{await client.query("ROLLBACK");}catch{}throw e;}finally{client.release();}
  const adminUrl=new URL(databaseUrl);adminUrl.pathname="/postgres";adminPool=new MainPool({connectionString:adminUrl.toString()});
  const found=await adminPool.query("SELECT 1 FROM pg_database WHERE datname=$1",[scratch]);if(found.rowCount)throw fail("RESTORE_DATABASE_EXISTS");
  const backupScript=path.join(ROOT,"scripts","backup.ps1"),backupStartedAt=Date.now();
  backupPath=execute("powershell.exe",["-NoProfile","-ExecutionPolicy","Bypass","-File",backupScript,"-Database",dbName,"-Keep","2147483647"],{cwd:ROOT,encoding:"utf8",stdio:["ignore","pipe","pipe"]}).trim().split(/\r?\n/).at(-1);
  if(!backupPath)throw fail("BACKUP_FAILED");backupPath=path.resolve(backupPath);
  if(!backupPath.startsWith(`${BACKUPS}${path.sep}`)||!new RegExp(`^${dbName}-\\d{8}-\\d{6}\\.dump$`).test(path.basename(backupPath)))throw fail("BACKUP_PATH_INVALID");
  const backupStat=await filesystem.stat(backupPath);if(!backupStat.isFile()||backupStat.size===0||backupStat.mtimeMs<backupStartedAt-5000)throw fail("BACKUP_NOT_FRESH");
  const backupBytes=await filesystem.readFile(backupPath),backupSha=crypto.createHash("sha256").update(backupBytes).digest("hex");
  containerId=docker([...compose(),"ps","-q","db"],execute);if(!containerId)throw fail("DATABASE_CONTAINER_UNAVAILABLE");
  tempDump=`/tmp/ccm-main-restore-${runId}.dump`;docker(["cp",backupPath,`${containerId}:${tempDump}`],execute);
  await adminPool.query(`CREATE DATABASE ${sqlName(scratch)} OWNER ${sqlName(roleName)}`);ownsScratch=true;
  await adminPool.query(`COMMENT ON DATABASE ${sqlName(scratch)} IS '${marker}'`);
  const owner=await adminPool.query(`SELECT r.rolname owner,shobj_description(d.oid,'pg_database') marker FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname=$1`,[scratch]);
  if(owner.rows[0]?.owner!==roleName||owner.rows[0]?.marker!==marker)throw fail("RESTORE_OWNERSHIP_MISMATCH");
  docker([...compose(),"exec","-T","-u","postgres","db","pg_restore","--exit-on-error","-U",roleName,"-d",scratch,tempDump],execute);
  const scratchUrl=new URL(databaseUrl);scratchUrl.pathname=`/${scratch}`;const scratchPool=new MainPool({connectionString:scratchUrl.toString(),options:"-c default_transaction_read_only=on"});
  let restored,scratchClient,scratchFailure=null;const scratchCleanup=[];
  try{scratchClient=await scratchPool.connect();restored=await snapshot(scratchClient);}catch(error){scratchFailure=error;}
  finally{if(scratchClient){try{scratchClient.release();}catch(error){scratchCleanup.push(error);}}
   try{await scratchPool.end();}catch(error){scratchCleanup.push(error);}}
  if(scratchFailure&&scratchCleanup.length)throw new AggregateError([scratchFailure,...scratchCleanup],"Scratch restore read and cleanup failed.",{cause:scratchFailure});
  if(scratchFailure)throw scratchFailure;if(scratchCleanup.length)throw new AggregateError(scratchCleanup,"Scratch restore cleanup failed.");
  if(restored!==mainSnapshot)throw fail("RESTORE_SNAPSHOT_MISMATCH");
  await mainPool.end();mainPoolEnded=true;
  const ownerCheck=await adminPool.query(`SELECT r.rolname owner,shobj_description(d.oid,'pg_database') marker FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname=$1`,[scratch]);
  if(ownerCheck.rows[0]?.owner!==roleName||ownerCheck.rows[0]?.marker!==marker)throw fail("RESTORE_OWNERSHIP_MISMATCH");
  await adminPool.query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",[scratch]);
  if(Number((await adminPool.query("SELECT count(*)::int n FROM pg_stat_activity WHERE datname=$1",[scratch])).rows[0].n)!==0)throw fail("RESTORE_SESSIONS_REMAIN");
  await adminPool.query(`DROP DATABASE ${sqlName(scratch)}`);ownsScratch=false;
  if((await adminPool.query("SELECT 1 FROM pg_database WHERE datname=$1",[scratch])).rowCount)throw fail("RESTORE_CLEANUP_FAILED");
  const unsigned={proof_version:1,run_id:runId,target_database:dbName,manifest_sha256:a["--manifest-sha256"],
   backup:{path:backupPath,sha256:backupSha},live_snapshot_sha256:mainSnapshot,restored_snapshot_sha256:restored,
   restore:{run_id:runId,scratch_database:scratch,owner_marker:marker,cleanup_absence:true},created_at:new Date().toISOString()};
  const proof=await signProof(unsigned);const proofPath=path.join(BACKUPS,`main-enrichment-restore-proof-${runId}.json`);
  await filesystem.writeFile(proofPath,`${JSON.stringify(proof,null,2)}\n`,{flag:"wx",mode:0o600});return{proofPath,backupPath,targetDatabase:dbName,manifestSha256:a["--manifest-sha256"],liveSnapshotSha256:mainSnapshot};
 }catch(error){primaryError=error;throw error;}finally{
  const cleanupErrors=[];
  if(mainPool&&!mainPoolEnded)try{await mainPool.end();}catch(error){cleanupErrors.push(error);}
  if(adminPool){
   if(ownsScratch){
    let check;
    try{check=await adminPool.query(`SELECT r.rolname owner,shobj_description(d.oid,'pg_database') marker FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname=$1`,[scratch]);}
    catch(error){cleanupErrors.push(error);}
    if(check?.rows[0]?.owner===roleName&&check.rows[0]?.marker===marker){
     try{await adminPool.query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",[scratch]);
      await adminPool.query(`DROP DATABASE ${sqlName(scratch)}`);ownsScratch=false;}
     catch(error){cleanupErrors.push(error);}
    }else if(check)cleanupErrors.push(fail("RESTORE_CLEANUP_OWNERSHIP_REFUSED"));
   }
   if(containerId&&tempDump)try{docker([...compose(),"exec","-T","db","rm","-f",tempDump],execute);}catch(error){cleanupErrors.push(error);}
   try{await adminPool.end();}catch(error){cleanupErrors.push(error);}
  }
  if(cleanupErrors.length){
   const details=primaryError?[primaryError,...cleanupErrors]:cleanupErrors;
   const combined=new AggregateError(details,primaryError?"Restore verification failed and cleanup also reported errors.":"Restore verification cleanup failed.",{cause:primaryError||cleanupErrors[0]});
   combined.code=primaryError?.code||"RESTORE_CLEANUP_FAILED";
   throw combined;
  }
 }
}
if(require.main===module)main().then(result=>process.stdout.write(`${JSON.stringify(result,null,2)}\n`)).catch(e=>{process.stderr.write(`Restore proof stopped: ${e.code||"FAILED"}\n`);process.exitCode=1;});
module.exports={args,main};
