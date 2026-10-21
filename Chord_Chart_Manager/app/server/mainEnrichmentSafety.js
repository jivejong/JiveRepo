const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");

const TABLES = [
  ["artists", "public.artists", "id::text"], ["genres", "public.genres", "id::text"],
  ["vibes", "public.vibes", "id::text"], ["songs", "public.songs", "id::text"],
  ["song_genres", "public.song_genres", "song_id::text || ':' || genre_id::text"],
  ["song_vibes", "public.song_vibes", "song_id::text || ':' || vibe_id::text"],
  ["setlists", "public.setlists", "id::text"], ["setlist_songs", "public.setlist_songs", "id::text"],
  ["mutation_receipts", "public.mutation_receipts", "operation_id::text"],
  ["schema_migrations", "public.schema_migrations", "version::text"],
  ["song_enrichment_runs", "public.song_enrichment_runs", "operation_id::text"],
];
const PRIVATE_BACKUPS = path.resolve(__dirname, "../../backups");
const KEY_FILE = path.join(PRIVATE_BACKUPS, ".main-enrichment-proof-key");
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${stableJson(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}
function sha(value) { return crypto.createHash("sha256").update(value).digest("hex"); }
function canonicalSnapshotSql() {
  const expected = TABLES.map(([name]) => `('${name}'::text)`).join(",");
  return `WITH expected(table_name) AS (VALUES ${expected}),
    all_rows AS (${TABLES.map(([name, table, key])=>`SELECT '${name}'::text table_name, (${key}) row_key, to_jsonb(t) row_data FROM ${table} t`).join(" UNION ALL ")}),
    per_table AS (SELECT e.table_name,COUNT(r.table_name)::text row_count,
      md5(COALESCE(string_agg(r.row_key || ':' || md5(r.row_data::text), E'\\n' ORDER BY r.row_key),'')) digest
      FROM expected e LEFT JOIN all_rows r USING(table_name) GROUP BY e.table_name)
    SELECT table_name,row_count,digest FROM per_table ORDER BY table_name`;
}
async function protectedSnapshotSha256(client) {
  const rows=(await client.query(canonicalSnapshotSql())).rows;
  const expected=TABLES.map(([name])=>name).sort();
  if(rows.length!==expected.length || rows.some((row,index)=>row.table_name!==expected[index] ||
      !/^(?:0|[1-9][0-9]*)$/.test(String(row.row_count)) || !/^[a-f0-9]{32}$/.test(row.digest))) throw new Error("SNAPSHOT_INCOMPLETE");
  // PostgreSQL hashes canonical jsonb row text and includes counts before values cross the driver boundary.
  return sha(rows.map(row=>`${row.table_name}:${row.row_count}:${row.digest}`).join("\n"));
}
async function getProofKey({filesystem=fs}={}) {
  try { const key=await filesystem.readFile(KEY_FILE); if(key.length!==32)throw new Error(); return key; }
  catch {
    const key=crypto.randomBytes(32); await filesystem.mkdir(path.dirname(KEY_FILE),{recursive:true});
    await filesystem.writeFile(KEY_FILE,key,{flag:"wx",mode:0o600}); return key;
  }
}
async function signRestoreProof(payload,options={}) {
  const key=await getProofKey(options), signature=crypto.createHmac("sha256",key).update(stableJson(payload)).digest("hex");
  return {...payload,signature};
}
async function verifyRestoreProof(proof,{filesystem=fs,now=Date.now(),maxAgeMs=24*60*60*1000}={}) {
  if(!proof || proof.proof_version!==1 || typeof proof.target_database!=="string" || !UUID.test(proof.run_id||"") ||
      !UUID.test(proof.restore?.run_id||"") || proof.restore.run_id!==proof.run_id || proof.restore.scratch_database!==`ccm_restore_${proof.run_id.replaceAll("-","")}` ||
      proof.restore.owner_marker!==`CCM main apply restore verification run ${proof.run_id}` || proof.restore.cleanup_absence!==true ||
      typeof proof.backup?.path!=="string" || !/^[a-f0-9]{64}$/.test(proof.backup?.sha256||"") || !/^[a-f0-9]{64}$/.test(proof.manifest_sha256||"") ||
      !/^[a-f0-9]{64}$/.test(proof.live_snapshot_sha256||"") || proof.live_snapshot_sha256!==proof.restored_snapshot_sha256 ||
      typeof proof.created_at!=="string" || !Number.isFinite(Date.parse(proof.created_at)) ||
      now-Date.parse(proof.created_at)<0 || now-Date.parse(proof.created_at)>maxAgeMs ||
      typeof proof.signature!=="string" || !/^[a-f0-9]{64}$/.test(proof.signature)) throw new Error("RESTORE_PROOF_INVALID");
  const full=path.resolve(proof.backup.path);
  if(!full.startsWith(`${PRIVATE_BACKUPS}${path.sep}`))throw new Error("BACKUP_PATH_INVALID");
  const bytes=await filesystem.readFile(full);
  if(sha(bytes)!==proof.backup.sha256)throw new Error("BACKUP_HASH_MISMATCH");
  const key=await filesystem.readFile(KEY_FILE);
  if(key.length!==32)throw new Error("RESTORE_PROOF_INVALID");
  const payload={...proof}; delete payload.signature;
  const expected=crypto.createHmac("sha256",key).update(stableJson(payload)).digest();
  const actual=Buffer.from(proof.signature,"hex");
  if(actual.length!==expected.length || !crypto.timingSafeEqual(actual,expected))throw new Error("RESTORE_PROOF_SIGNATURE_INVALID");
  return true;
}
module.exports={KEY_FILE,PRIVATE_BACKUPS,TABLES,canonicalSnapshotSql,protectedSnapshotSha256,signRestoreProof,stableJson,verifyRestoreProof};
