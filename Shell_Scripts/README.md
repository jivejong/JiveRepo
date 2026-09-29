# Shell Scripts

A small cross-platform operations toolkit: Bash utilities for backup, database operations, and AI-ingestion hygiene; PowerShell utilities for Windows storage analysis and cleanup. Each script is standalone and intentionally keeps its scope narrow.

## Highlights

- **Security-conscious database automation**: `db_orchestrator.sh` supports the same CRUD workflow across Oracle, Teradata, and SQL Server without putting passwords in command-line arguments.
- **Safe cleanup patterns**: duplicate-file tools verify content with hashes and quarantine files for review instead of permanently deleting them.
- **Practical AI tooling**: `scan_injections.sh` provides a simple pre-ingestion screening step, while `dir-lens.ps1` produces a local-LLM directory audit through Ollama.
- **Defensive defaults**: Bash scripts use `set -euo pipefail`; PowerShell cleanup scripts support previewing work with `-WhatIf`.

## Requirements

| Area | Requirements |
| --- | --- |
| Bash scripts | Bash, plus standard Unix tools such as `tar`, `gzip`, `find`, and `grep` where applicable |
| Database script | One supported client: Oracle SQL*Plus, Teradata BTEQ, or Microsoft `sqlcmd` |
| PowerShell scripts | PowerShell 5.1+ or PowerShell 7+ on Windows; file hashing is built in |
| `dir-lens.ps1` | [Ollama](https://ollama.com/) running locally and a local model (default: `llama3.2`) |

On Linux or macOS, make the Bash scripts executable before first use:

```bash
chmod +x *.sh
```

## Script Guide

### Bash

| Script | Purpose | Example |
| --- | --- | --- |
| `db_orchestrator.sh` | Runs CRUD actions against a fixed `app_users` table on Oracle, Teradata, or SQL Server. | `./db_orchestrator.sh -e oracle -a read -i 101` |
| `rolling_backup.sh` | Creates and verifies timestamped `.tar.gz` backups, then removes expired archives for that source. | `./rolling_backup.sh /var/www/site /mnt/backups 7` |
| `scan_injections.sh` | Screens common text formats for configurable prompt-injection signatures and moves flagged files to quarantine. | `./scan_injections.sh ./incoming ./quarantine` |
| `wordle.sh` | A terminal Wordle-style game with duplicate-letter-aware feedback. | `./wordle.sh` |

### PowerShell

| Script | Scope | Detection / action | Example |
| --- | --- | --- | --- |
| `dir-lens.ps1` | One directory tree | Collects size, extension, and large-file metadata; asks a local Ollama model for cleanup recommendations. | `.\dir-lens.ps1 -Path 'C:\Users\me\Downloads'` |
| `DeDupeArchive.ps1` | One directory tree | MD5; keeps the deepest copy and places others in a flat isolation folder. | `.\DeDupeArchive.ps1 -AnalyzePath 'D:\Archive' -DuplicatesPath 'D:\Dupes' -WhatIf` |
| `Move-DuplicateFiles.ps1` | One directory tree | Size + SHA-256; keeps the deepest copy and moves the rest to a path-preserving quarantine. | `.\Move-DuplicateFiles.ps1 -SourcePath 'D:\Photos' -QuarantinePath 'D:\Quarantine' -WhatIf` |
| `Remove-DuplicateFiles_all.ps1` | One directory tree | Size + SHA-256; keeps the shallowest copy and quarantines the rest. Includes informational filename-similarity logging. | `.\Remove-DuplicateFiles_all.ps1 -TargetPath 'D:\Media' -QuarantinePath 'D:\Quarantine' -WhatIf` |
| `Remove-DuplicateFiles.ps1` | Folder A compared to Folder B | Size + SHA-256; preserves Folder B and quarantines matching copies from Folder A. | `.\Remove-DuplicateFiles.ps1 -FolderA 'D:\Inbox' -FolderB 'D:\Master' -QuarantinePath 'D:\Quarantine' -WhatIf` |
| `Remove-DuplicateFolders.ps1` | Directory trees | Matches folders with the same name and a SHA-256 manifest fingerprint; keeps the shortest path. | `.\Remove-DuplicateFolders.ps1 -TargetPath 'D:\Projects' -QuarantinePath 'D:\Quarantine' -WhatIf` |
| `Remove-EmptyFolders.ps1` | One directory tree | Removes empty folders bottom-up, repeating until no newly empty parents remain. | `.\Remove-EmptyFolders.ps1 -TargetPath 'D:\Archive' -WhatIf` |

## Database Orchestrator

`db_orchestrator.sh` targets `app_users (id, username, email)` and accepts these core options:

```text
./db_orchestrator.sh -e <teradata|oracle|sqlserver> -a <create|read|update|delete> [options]
```

| Option | Meaning |
| --- | --- |
| `-i <id>` | Numeric ID; required for update and delete, optional filter for read |
| `-u <username>` | Required for create; optional for update |
| `-m <email>` | Required for create; optional for update |
| `--config <path>` | Credentials file; defaults to `~/.db_credentials.env` |

The credentials file must be permissioned `600` or `400`. It is sourced by Bash, so use ordinary `KEY=value` entries; only the selected engine's variables are needed.

```bash
# chmod 600 ~/.db_credentials.env
ORACLE_USER=...
ORACLE_PASS=...
ORACLE_TNS=...

TERADATA_TDP=...
TERADATA_USER=...
TERADATA_PASS=...

SQLSERVER_HOST=...
SQLSERVER_USER=...
SQLSERVER_DB=...
SQLCMDPASSWORD=...
```

Oracle and Teradata credentials are sent through standard input; SQL Server reads `SQLCMDPASSWORD` from the environment. This avoids password exposure in process arguments. The script interpolates username and email values into SQL, so it is a trusted-operator utility, not a public-facing API.

## Safety Notes

- Start every PowerShell cleanup run with `-WhatIf`, inspect the log and quarantine contents, then rerun without it only when the result is expected.
- `Remove-EmptyFolders.ps1` is the exception: it deletes empty folders rather than quarantining them.
- Quarantine folders should be outside the source tree where practical. `DeDupeArchive.ps1` explicitly avoids scanning its isolation folder when it is nested within the source tree.
- Hashing and folder fingerprinting read file contents and can take significant time on large or remote volumes.
- `scan_injections.sh` is pattern-based triage, not a complete prompt-injection defense. Review flagged files before discarding them, and extend its `PATTERNS` array for your threat model.
- `rolling_backup.sh` removes matching archives older than the retention window only after the newly created archive passes `gzip -t` validation. Test retention behavior in a non-production location first.

## Operational Details

Most PowerShell cleanup scripts write timestamped logs to the **current working directory**. Run them from a writable location or provide `-LogFile` where that parameter is available. `DeDupeArchive.ps1` has a custom `-WhatIf` switch; the other cleanup scripts use PowerShell's standard `SupportsShouldProcess` pattern.

Do not commit database credential files or quarantine data. The repository `.gitignore` excludes `logins/` and `.logins/`; storing secrets outside the repository remains the safest choice.
