# 🛠️ Shell Scripts

Standalone operations scripts — no build step, no dependencies beyond what each script declares. Two families live here:

- **Bash scripts** (`*.sh`) — Linux/macOS operational tooling: database CRUD, backups, and pre-ingestion content scanning.
- **PowerShell scripts** (`*.ps1`) — Windows filesystem hygiene: duplicate detection and empty-folder cleanup.

---

## Bash Scripts

### 🗄️ `db_orchestrator.sh`

A single CRUD front-end for three database engines — Teradata (BTEQ), Oracle (SQL\*Plus), and SQL Server (`sqlcmd`) — designed so that **credentials never appear in the process table**.

```bash
./db_orchestrator.sh -e <engine> -a <action> [options]
```

| Flag             | Purpose                                                        |
| ---------------- | -------------------------------------------------------------- |
| `-e <engine>`    | `teradata`, `oracle`, or `sqlserver` (required)                 |
| `-a <action>`    | `create`, `read`, `update`, or `delete` (required)              |
| `-i <id>`        | Record ID — required for update/delete, optional filter on read |
| `-u <username>`  | Username — required for create                                  |
| `-m <email>`     | Email — required for create                                     |
| `--config <path>`| Credentials env file (default `~/.db_credentials.env`)          |
| `-h, --help`     | Usage text                                                      |

All actions target a fixed table, `app_users (id, username, email)`.

**How credentials stay out of `ps`:**

| Engine     | Technique                                                                       |
| ---------- | ------------------------------------------------------------------------------- |
| Oracle     | Invoked as `sqlplus -s -S /nolog`; the `CONNECT user/pass@tns` is fed via stdin  |
| Teradata   | `bteq` is invoked with zero flags; `.LOGON` arrives via a stdin heredoc          |
| SQL Server | `SQLCMDPASSWORD` is exported and `-P` is omitted, so the password is never in argv |

**Config file.** The script refuses to run unless the config file exists and its permissions are exactly `600` or `400`. It is sourced as shell, so use plain `KEY=value` lines:

```bash
# ~/.db_credentials.env   (chmod 600)
ORACLE_USER=...        ORACLE_PASS=...        ORACLE_TNS=...
TERADATA_TDP=...       TERADATA_USER=...      TERADATA_PASS=...
SQLSERVER_HOST=...     SQLSERVER_USER=...     SQLSERVER_DB=...
SQLCMDPASSWORD=...
```

Only the variables for the engine you invoke need to be present.

```bash
./db_orchestrator.sh -e oracle    -a create -u "jdoe" -m "jdoe@corp.internal"
./db_orchestrator.sh -e teradata  -a read   -i 101
./db_orchestrator.sh -e sqlserver -a update -i 101 -m "newemail@corp.internal"
```

> ⚠️ **Note on input handling.** `-i` is validated as an integer, but `-u` and `-m` are interpolated directly into the SQL text. Treat this as a trusted-operator tool: do not wire it up behind untrusted input without adding parameter binding or quoting.

---

### 💾 `rolling_backup.sh`

Creates a timestamped `.tar.gz` of a directory, verifies it with `gzip -t`, then prunes older archives of the same target.

```bash
./rolling_backup.sh <source_directory> <backup_directory> [retention_days]
```

- Archive name: `<sourceBasename>_backup_YYYYMMDD_HHMMSS.tar.gz`
- `retention_days` defaults to **3**; pruning uses `find -mtime +N` and only matches archives for the same source basename.
- A failed integrity check deletes the broken archive and exits `1` — nothing is pruned in that case.

```bash
./rolling_backup.sh /var/www/site /mnt/backups        # 3-day retention
./rolling_backup.sh /opt/database /backups/db 7       # 7-day retention
```

| Exit | Meaning                                              |
| ---- | ---------------------------------------------------- |
| `0`  | Backup created and pruning completed                 |
| `1`  | Bad/missing arguments, or archive verification failed |

---

### 🛡️ `scan_injections.sh`

Pre-ingestion guard for LLM pipelines. Scans flat files for prompt-injection and jailbreak signatures and moves anything suspicious out of the ingestion path.

```bash
./scan_injections.sh <input_directory> [quarantine_directory]
```

- Walks `<input_directory>` to a depth of 2, inspecting only `.txt`, `.md`, `.json`, `.csv`, `.log`.
- Matches a case-insensitive regex built from a pattern list in the script — "ignore previous instructions", "new system prompt:", "developer mode enabled", "DAN mode", "reveal your initial instructions", and similar.
- Prints each matching line, then moves the file to the quarantine directory (default `./quarantine`).
- Extend coverage by adding entries to the `PATTERNS` array near the top of the file.

| Exit | Meaning                                        |
| ---- | ---------------------------------------------- |
| `0`  | Clean — nothing matched                        |
| `1`  | Invalid arguments or missing input directory   |
| `2`  | One or more files were flagged and quarantined |

Exit `2` is the useful one for CI: fail the ingestion job when anything is quarantined.

---

## PowerShell Scripts

Four duplicate-finders and one empty-folder cleaner. They differ in **what they compare** and **which copy survives** — the table below is the fast way to pick one.

| Script                        | Compares                         | Hash    | Keeps                                     | Duplicates go to        |
| ----------------------------- | -------------------------------- | ------- | ----------------------------------------- | ----------------------- |
| `DeDupeArchive.ps1`           | All files under one path         | MD5     | **Deepest** copy                          | Flat isolation folder   |
| `Move-DuplicateFiles.ps1`     | All files under one path         | SHA-256 | **Deepest** copy                          | Quarantine, tree kept   |
| `Remove-DuplicateFiles_all.ps1` | All files under one path       | SHA-256 | **Shallowest** copy                       | Quarantine, tree kept   |
| `Remove-DuplicateFiles.ps1`   | Folder A against Folder B        | SHA-256 | Everything in **Folder B**                | Quarantine, tree kept   |
| `Remove-DuplicateFolders.ps1` | Whole folders (name + contents)  | SHA-256 | Shortest path, ties alphabetical          | Quarantine              |

Nothing here permanently deletes files — every duplicate is **moved**, so you review the quarantine folder and delete it yourself when satisfied. `Remove-EmptyFolders.ps1` is the one exception; it deletes.

> 🔍 **Always dry-run first.** Every script supports `-WhatIf`.

### 🗃️ `DeDupeArchive.ps1`

The lightest of the set: hashes every file under `-AnalyzePath` with MD5, and for each duplicate cluster keeps the copy **deepest** in the tree, relocating the shallower copies into a single flat folder. Name collisions in that folder get a `_Duplicate_N` suffix. Reports total files processed and space recovered in GB.

```powershell
.\DeDupeArchive.ps1 -AnalyzePath 'D:\Archive' -DuplicatesPath 'D:\Dupes' -WhatIf
.\DeDupeArchive.ps1 -AnalyzePath 'D:\Archive' -DuplicatesPath 'D:\Dupes'
```

Files already inside `-DuplicatesPath` are skipped, so the isolation folder can safely sit inside the analyzed tree. No log file — output is console only.

### 📦 `Move-DuplicateFiles.ps1`

Three-phase scan of a single tree: group by size (cheap), hash only the size collisions with SHA-256, then quarantine. The **deepest** copy is kept; on a depth tie the alphabetically **last** path is kept and the earlier ones are moved. Quarantined files keep their relative sub-path so you can see where each came from.

```powershell
.\Move-DuplicateFiles.ps1 -SourcePath 'D:\Photos' -QuarantinePath 'D:\Quarantine' -WhatIf
```

Writes `.\Move-DuplicateFiles_<timestamp>.log` in the working directory.

> The header comment in the file states that on a depth tie the alphabetically earlier path is kept; the sort applies `-Descending` to both keys, so the later path is actually the keeper. Only affects tie-breaks between equal-depth copies.

### 🧹 `Remove-DuplicateFiles_all.ps1`

Same single-tree, size-then-SHA-256 approach as `Move-DuplicateFiles.ps1`, with the **opposite** survival rule: the copy **closest to the root** is kept, ties broken alphabetically first. It also computes a Levenshtein name-similarity percentage between keeper and duplicate and logs it — informational only; size + hash remain the authoritative signal.

```powershell
.\Remove-DuplicateFiles_all.ps1 -TargetPath 'D:\Media' -QuarantinePath 'D:\Quarantine' -WhatIf
```

| Parameter        | Default                                          |
| ---------------- | ------------------------------------------------ |
| `-TargetPath`    | required                                         |
| `-QuarantinePath`| required                                         |
| `-LogFile`       | `.\RemoveDuplicateFiles_<timestamp>.log`         |
| `-MinSizeBytes`  | `0` — raise it to skip small files entirely      |

### 🔀 `Remove-DuplicateFiles.ps1`

A **two-folder** comparison rather than a self-scan. Folder B is the reference set and is never touched; any file in Folder A that matches a Folder B file by size + SHA-256 is quarantined. Folder B is indexed by size first and hashed lazily, so only files whose size actually collides get read.

```powershell
.\Remove-DuplicateFiles.ps1 -FolderA 'D:\Inbox' -FolderB 'D:\Master' `
                            -QuarantinePath 'D:\Quarantine' -WhatIf
```

Use this when merging a staging folder into a curated library: point `-FolderA` at the staging copy.

### 📁 `Remove-DuplicateFolders.ps1`

Operates on whole directories. Each folder is fingerprinted as the SHA-256 of a sorted `relativePath|size|hash` manifest of everything beneath it; folders sharing **both a name and a fingerprint** are duplicates. The shortest path wins (ties alphabetical) and the rest are moved to quarantine, with a `_dupN` suffix on collision.

```powershell
.\Remove-DuplicateFolders.ps1 -TargetPath 'D:\Projects' -QuarantinePath 'D:\Quarantine' -WhatIf
```

Fingerprinting hashes every file in the tree, including folders that turn out to have no duplicate — expect this to be slow on large trees. Empty folders all fingerprint as `EMPTY`, so same-named empty folders are treated as duplicates of each other.

### 🗑️ `Remove-EmptyFolders.ps1`

Recursively deletes empty folders under `-TargetPath`, looping until a full pass finds nothing — one pass can empty a parent, which the next pass then catches.

```powershell
.\Remove-EmptyFolders.ps1 -TargetPath 'D:\Archive' -WhatIf
.\Remove-EmptyFolders.ps1 -TargetPath 'D:\Archive'
```

> ⚠️ This script **deletes** rather than quarantines. Run `-WhatIf` first.

---

## Conventions

**Bash.** All three scripts use `set -euo pipefail`, accept `-h/--help`, and write errors to stderr. Make them executable with `chmod +x *.sh`.

**PowerShell.** Every `.ps1` except `DeDupeArchive.ps1` uses `[CmdletBinding(SupportsShouldProcess)]`, so `-WhatIf` behaves as a standard PowerShell dry run. `DeDupeArchive.ps1` implements `-WhatIf` as a plain switch — same intent, but it is not the built-in mechanism and does not inherit `$WhatIfPreference`.

Scripts that log write a timestamped `.log` file into the **current working directory**, not the script directory — `cd` somewhere writable before running.

**Secrets.** The local [.gitignore](.gitignore) excludes `.logins/` and `logins/`. Keep credential files there or outside the repo — never commit `~/.db_credentials.env` or its equivalents.
