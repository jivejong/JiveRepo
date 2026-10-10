const TABLES = [
  "artists", "genres", "vibes", "songs", "song_vibes", "song_genres",
  "setlists", "setlist_songs",
];

const COLUMNS = {
  artists: [
    ["id", "integer", false, "nextval('artists_id_seq'::regclass)"], ["name", "text", false, null],
  ],
  genres: [
    ["id", "integer", false, "nextval('genres_id_seq'::regclass)"], ["name", "text", false, null],
  ],
  vibes: [
    ["id", "integer", false, "nextval('vibes_id_seq'::regclass)"], ["name", "text", false, null],
  ],
  songs: [
    ["id", "integer", false, "nextval('songs_id_seq'::regclass)"], ["title", "text", false, null],
    ["artist_id", "integer", false, null], ["original_key", "text", true, null],
    ["performance_key", "text", true, null], ["preferred_key", "text", true, null],
    ["alt_key", "text", true, null], ["capo_fret", "smallint", true, "0"],
    ["bpm", "smallint", true, null], ["release_year", "smallint", true, null],
    ["bb_structure", "text", true, null], ["chart_content", "jsonb", false, null],
    ["chart_source", "text", true, null], ["source_document", "text", true, null],
    ["created_at", "timestamp with time zone", false, "now()"],
    ["updated_at", "timestamp with time zone", false, "now()"],
  ],
  song_vibes: [["song_id", "integer", false, null], ["vibe_id", "integer", false, null]],
  song_genres: [
    ["song_id", "integer", false, null], ["genre_id", "integer", false, null],
    ["is_primary", "boolean", false, "false"],
  ],
  setlists: [
    ["id", "integer", false, "nextval('setlists_id_seq'::regclass)"], ["name", "text", false, null],
    ["gig_date", "date", true, null], ["notes", "text", true, null],
    ["created_at", "timestamp with time zone", false, "now()"],
    ["updated_at", "timestamp with time zone", false, "now()"],
  ],
  setlist_songs: [
    ["setlist_id", "integer", false, null], ["song_id", "integer", false, null],
    ["position", "integer", false, null], ["transposed_key", "text", true, null],
    ["capo_fret", "smallint", true, null], ["notes", "text", true, null],
  ],
};

const CONSTRAINTS = [
  "artists|artists_pkey|p|primarykey(id)",
  "genres|genres_pkey|p|primarykey(id)",
  "vibes|vibes_pkey|p|primarykey(id)",
  "songs|songs_pkey|p|primarykey(id)",
  "songs|songs_artist_id_fkey|f|foreignkey(artist_id)referencesartists(id)",
  "songs|songs_bpm_check|c|check(((bpmisnull)or(bpm>0)))",
  "songs|songs_capo_fret_check|c|check(((capo_fret>=0)and(capo_fret<=11)))",
  "song_vibes|song_vibes_pkey|p|primarykey(song_id,vibe_id)",
  "song_vibes|song_vibes_song_id_fkey|f|foreignkey(song_id)referencessongs(id)ondeletecascade",
  "song_vibes|song_vibes_vibe_id_fkey|f|foreignkey(vibe_id)referencesvibes(id)ondeletecascade",
  "song_genres|song_genres_pkey|p|primarykey(song_id,genre_id)",
  "song_genres|song_genres_genre_id_fkey|f|foreignkey(genre_id)referencesgenres(id)ondeletecascade",
  "song_genres|song_genres_song_id_fkey|f|foreignkey(song_id)referencessongs(id)ondeletecascade",
  "setlists|setlists_pkey|p|primarykey(id)",
  "setlist_songs|setlist_songs_pkey|p|primarykey(setlist_id,position)",
  "setlist_songs|setlist_songs_capo_fret_check|c|check(((capo_fretisnull)or((capo_fret>=0)and(capo_fret<=11))))",
  "setlist_songs|setlist_songs_setlist_id_fkey|f|foreignkey(setlist_id)referencessetlists(id)ondeletecascade",
  "setlist_songs|setlist_songs_song_id_fkey|f|foreignkey(song_id)referencessongs(id)ondeletecascade",
].sort();

const INDEXES = [
  "artists|artists_name_lower_idx|createuniqueindexartists_name_lower_idxonpublic.artistsusingbtree(lower(name))",
  "artists|artists_pkey|createuniqueindexartists_pkeyonpublic.artistsusingbtree(id)",
  "genres|genres_name_lower_idx|createuniqueindexgenres_name_lower_idxonpublic.genresusingbtree(lower(name))",
  "genres|genres_pkey|createuniqueindexgenres_pkeyonpublic.genresusingbtree(id)",
  "vibes|vibes_name_lower_idx|createuniqueindexvibes_name_lower_idxonpublic.vibesusingbtree(lower(name))",
  "vibes|vibes_pkey|createuniqueindexvibes_pkeyonpublic.vibesusingbtree(id)",
  "songs|songs_pkey|createuniqueindexsongs_pkeyonpublic.songsusingbtree(id)",
  "songs|songs_artist_idx|createindexsongs_artist_idxonpublic.songsusingbtree(artist_id)",
  "songs|songs_bpm_idx|createindexsongs_bpm_idxonpublic.songsusingbtree(bpm)",
  "songs|songs_year_idx|createindexsongs_year_idxonpublic.songsusingbtree(release_year)",
  "songs|songs_title_idx|createindexsongs_title_idxonpublic.songsusingbtree(lower(title))",
  "song_vibes|song_vibes_pkey|createuniqueindexsong_vibes_pkeyonpublic.song_vibesusingbtree(song_id,vibe_id)",
  "song_genres|song_genres_pkey|createuniqueindexsong_genres_pkeyonpublic.song_genresusingbtree(song_id,genre_id)",
  "song_genres|song_genres_genre_idx|createindexsong_genres_genre_idxonpublic.song_genresusingbtree(genre_id)",
  "song_genres|song_genres_one_primary_idx|createuniqueindexsong_genres_one_primary_idxonpublic.song_genresusingbtree(song_id)whereis_primary",
  "setlists|setlists_pkey|createuniqueindexsetlists_pkeyonpublic.setlistsusingbtree(id)",
  "setlist_songs|setlist_songs_pkey|createuniqueindexsetlist_songs_pkeyonpublic.setlist_songsusingbtree(setlist_id,position)",
  "setlist_songs|setlist_songs_song_idx|createindexsetlist_songs_song_idxonpublic.setlist_songsusingbtree(song_id)",
].sort();

const TRIGGERS = ["setlists|setlists_set_updated_at|O|19|true||0||public|set_updated_at|plpgsql|0|trigger|false|v||beginnew.updated_at=now();returnnew;end;",
  "songs|songs_set_updated_at|O|19|true||0||public|set_updated_at|plpgsql|0|trigger|false|v||beginnew.updated_at=now();returnnew;end;"].sort();

function normalize(value) {
  return value === null ? "" : String(value).replaceAll('"', "").replace(/\s+/g, "").toLowerCase();
}

function expectedColumns() {
  return Object.entries(COLUMNS).flatMap(([table, columns]) => columns.map(([name, type, nullable, def]) =>
    `${table}|${name}|${type}|${nullable ? "YES" : "NO"}|${normalize(def)}`)).sort();
}

async function validateBaseline(client) {
  const problems = [];
  const tables = (await client.query(
    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations' ORDER BY tablename"
  )).rows.map((row) => row.tablename);
  if (JSON.stringify(tables) !== JSON.stringify([...TABLES].sort())) problems.push("application tables");

  const columns = (await client.query(`
    SELECT c.table_name, c.column_name, format_type(a.atttypid,a.atttypmod) AS data_type,
           c.is_nullable, c.column_default
    FROM information_schema.columns c
    JOIN pg_namespace n ON n.nspname=c.table_schema
    JOIN pg_class t ON t.relnamespace=n.oid AND t.relname=c.table_name
    JOIN pg_attribute a ON a.attrelid=t.oid AND a.attname=c.column_name
    WHERE c.table_schema='public' AND c.table_name=ANY($1::text[])
    ORDER BY c.table_name,c.ordinal_position`, [TABLES])).rows.map((row) =>
    `${row.table_name}|${row.column_name}|${row.data_type}|${row.is_nullable}|${normalize(row.column_default)}`).sort();
  if (JSON.stringify(columns) !== JSON.stringify(expectedColumns())) problems.push("columns, types, nullability, or defaults");

  const constraints = (await client.query(`
    SELECT t.relname AS table_name, c.conname, c.contype, pg_get_constraintdef(c.oid) AS definition
    FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
    WHERE t.relnamespace='public'::regnamespace AND t.relname=ANY($1::text[])
    ORDER BY t.relname,c.conname`, [TABLES])).rows.map((row) =>
    `${row.table_name}|${row.conname}|${row.contype}|${normalize(row.definition)}`).sort();
  if (JSON.stringify(constraints) !== JSON.stringify(CONSTRAINTS)) problems.push("primary, foreign, or check constraints");

  const indexes = (await client.query(`
    SELECT tablename,indexname,indexdef FROM pg_indexes
    WHERE schemaname='public' AND tablename=ANY($1::text[]) ORDER BY tablename,indexname`, [TABLES])).rows.map((row) =>
    `${row.tablename}|${row.indexname}|${normalize(row.indexdef)}`).sort();
  if (JSON.stringify(indexes) !== JSON.stringify(INDEXES)) problems.push("indexes");

  const functions = (await client.query(`
    SELECT p.proname, p.prokind, p.pronargs
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' ORDER BY p.proname,p.prokind,p.pronargs`)).rows
    .map((row) => `${row.proname}|${row.prokind}|${row.pronargs}`);
  if (JSON.stringify(functions) !== JSON.stringify(["set_updated_at|f|0"])) problems.push("application functions");

  const triggers = (await client.query(`
    SELECT t.relname AS table_name, g.tgname, g.tgenabled, g.tgtype AS trigger_type,
           (g.tgqual IS NULL) AS no_condition, g.tgattr::text AS update_columns,
           g.tgnargs, encode(g.tgargs,'hex') AS trigger_args, pn.nspname AS function_schema,
           p.proname, l.lanname, p.pronargs, pg_catalog.format_type(p.prorettype,NULL) AS return_type,
           p.prosecdef, p.provolatile, COALESCE(array_to_string(p.proconfig,','),'') AS function_config,
           p.prosrc
    FROM pg_trigger g JOIN pg_class t ON t.oid=g.tgrelid
    JOIN pg_proc p ON p.oid=g.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
    JOIN pg_language l ON l.oid=p.prolang
    WHERE NOT g.tgisinternal AND t.relnamespace='public'::regnamespace AND t.relname=ANY($1::text[])
    ORDER BY t.relname,g.tgname`, [TABLES])).rows.map((row) =>
    `${row.table_name}|${row.tgname}|${row.tgenabled}|${row.trigger_type}|${row.no_condition}|${row.update_columns}|${row.tgnargs}|${row.trigger_args}|${row.function_schema}|${row.proname}|${row.lanname}|${row.pronargs}|${normalize(row.return_type)}|${row.prosecdef}|${row.provolatile}|${normalize(row.function_config)}|${normalize(row.prosrc)}`).sort();
  if (JSON.stringify(triggers) !== JSON.stringify(TRIGGERS)) problems.push("triggers or trigger function signatures");

  if (problems.length) {
    throw new Error(`Existing schema does not match canonical 0001 baseline (${problems.join(", ")}); baseline adoption was not recorded.`);
  }
  return true;
}

module.exports = { TABLES, validateBaseline };
