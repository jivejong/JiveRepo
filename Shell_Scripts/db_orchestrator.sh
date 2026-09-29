#!/usr/bin/env bash
#
# db_orchestrator.sh - Unified CRUD orchestrator with zero process-table credential leakage.

set -euo pipefail

# --- Help & Usage ---
show_usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") -e <engine> -a <action> [options]

Required Flags:
  -e <engine>     Database engine: 'teradata', 'oracle', or 'sqlserver'
  -a <action>     CRUD action: 'create', 'read', 'update', or 'delete'

Entity Options (Target Table: app_users [id, username, email]):
  -i <id>         Record ID (numeric integer; required for update/delete, optional for read)
  -u <username>   Username string (required for create, optional for update)
  -m <email>      Email address (required for create, optional for update)

Configuration:
  --config <path> Path to credentials env file (Default: ~/.db_credentials.env)

Examples:
  $(basename "$0") -e oracle -a create -u "jdoe" -m "jdoe@corp.internal"
  $(basename "$0") -e teradata -a read -i 101
  $(basename "$0") -e sqlserver -a update -i 101 -m "newemail@corp.internal"
EOF
}

# --- State Variables ---
ENGINE=""
ACTION=""
REC_ID=""
REC_USER=""
REC_EMAIL=""
CONFIG_FILE="${HOME}/.db_credentials.env"

# --- Argument Parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e) ENGINE="$(echo "$2" | tr '[:upper:]' '[:lower:]')"; shift 2 ;;
    -a) ACTION="$(echo "$2" | tr '[:upper:]' '[:lower:]')"; shift 2 ;;
    -i) REC_ID="$2"; shift 2 ;;
    -u) REC_USER="$2"; shift 2 ;;
    -m) REC_EMAIL="$2"; shift 2 ;;
    --config) CONFIG_FILE="$2"; shift 2 ;;
    -h|--help) show_usage; exit 0 ;;
    *) echo "Error: Unknown argument '$1'" >&2; show_usage; exit 1 ;;
  esac
done

# --- Validations ---
if [[ -z "$ENGINE" || -z "$ACTION" ]]; then
  echo "Error: Both -e <engine> and -a <action> are required." >&2
  show_usage
  exit 1
fi

if [[ ! "$ENGINE" =~ ^(teradata|oracle|sqlserver)$ ]]; then
  echo "Error: Invalid engine '$ENGINE'." >&2
  exit 1
fi

if [[ ! "$ACTION" =~ ^(create|read|update|delete)$ ]]; then
  echo "Error: Invalid action '$ACTION'." >&2
  exit 1
fi

if [[ -n "$REC_ID" && ! "$REC_ID" =~ ^[0-9]+$ ]]; then
  echo "Error: Record ID (-i) must be an integer." >&2
  exit 1
fi

case "$ACTION" in
  create)
    [[ -z "$REC_USER" || -z "$REC_EMAIL" ]] && { echo "Error: create requires -u and -m." >&2; exit 1; }
    ;;
  update)
    [[ -z "$REC_ID" || ( -z "$REC_USER" && -z "$REC_EMAIL" ) ]] && { echo "Error: update requires -i and (-u or -m)." >&2; exit 1; }
    ;;
  delete)
    [[ -z "$REC_ID" ]] && { echo "Error: delete requires -i." >&2; exit 1; }
    ;;
esac

# --- Secure Config Loader ---
load_credentials() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: Config file not found at $CONFIG_FILE" >&2
    exit 1
  fi

  # Verify file permissions are strictly owner-only (600 or 400)
  local perms
  perms=$(stat -c "%a" "$CONFIG_FILE" 2>/dev/null || stat -f "%Lp" "$CONFIG_FILE" 2>/dev/null)
  if [[ "$perms" != "600" && "$perms" != "400" ]]; then
    echo "Security Warning: Permissions on $CONFIG_FILE are $perms. Must be 600 or 400." >&2
    exit 1
  fi

  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
}

load_credentials

# --- Engine Implementations ---

run_oracle() {
  command -v sqlplus >/dev/null 2>&1 || { echo "Error: sqlplus not found in PATH." >&2; exit 1; }
  
  echo ">>> Routing [$ACTION] to Oracle SQL*Plus"

  # Technique: Pass '/nolog' as the CLI arg so ps shows no credentials.
  # The actual 'connect user/password@tns' is fed securely via standard input.
  case "$ACTION" in
    create)
      sqlplus -s -S /nolog <<EOF
CONNECT ${ORACLE_USER}/${ORACLE_PASS}@${ORACLE_TNS}
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
SET FEEDBACK OFF HEADING OFF AUTOCOMMIT ON;
INSERT INTO app_users (username, email) VALUES ('${REC_USER}', '${REC_EMAIL}');
PROMPT [OK] Row inserted successfully.
EXIT;
EOF
      ;;
    read)
      local where_clause=""
      [[ -n "$REC_ID" ]] && where_clause="WHERE id = ${REC_ID}"
      sqlplus -s -S /nolog <<EOF
CONNECT ${ORACLE_USER}/${ORACLE_PASS}@${ORACLE_TNS}
WHENEVER SQLERROR EXIT SQL.SQLCODE;
SET LINESIZE 200 PAGESIZE 50 TRIMSPOOL ON FEEDBACK OFF;
COLUMN id FORMAT 99999;
COLUMN username FORMAT A30;
COLUMN email FORMAT A40;
SELECT id, username, email FROM app_users ${where_clause} ORDER BY id ASC;
EXIT;
EOF
      ;;
    update)
      local set_clauses=()
      [[ -n "$REC_USER" ]] && set_clauses+=("username = '${REC_USER}'")
      [[ -n "$REC_EMAIL" ]] && set_clauses+=("email = '${REC_EMAIL}'")
      local assignments
      assignments=$(IFS=", "; echo "${set_clauses[*]}")

      sqlplus -s -S /nolog <<EOF
CONNECT ${ORACLE_USER}/${ORACLE_PASS}@${ORACLE_TNS}
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
SET FEEDBACK OFF AUTOCOMMIT ON;
UPDATE app_users SET ${assignments} WHERE id = ${REC_ID};
PROMPT [OK] Record ${REC_ID} updated.
EXIT;
EOF
      ;;
    delete)
      sqlplus -s -S /nolog <<EOF
CONNECT ${ORACLE_USER}/${ORACLE_PASS}@${ORACLE_TNS}
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;
SET FEEDBACK OFF AUTOCOMMIT ON;
DELETE FROM app_users WHERE id = ${REC_ID};
PROMPT [OK] Record ${REC_ID} deleted.
EXIT;
EOF
      ;;
  esac
}

run_teradata() {
  command -v bteq >/dev/null 2>&1 || { echo "Error: bteq not found in PATH." >&2; exit 1; }

  echo ">>> Routing [$ACTION] to Teradata BTEQ"

  # Technique: BTEQ accepts the .LOGON command directly via stdin heredoc.
  # The command invocation itself has zero flags, exposing no credentials.
  case "$ACTION" in
    create)
      bteq <<EOF
.LOGON ${TERADATA_TDP}/${TERADATA_USER},${TERADATA_PASS};
.SET WIDTH 200;
.SET QUIET ON;
.SET ERRORLEVEL (3807) SEVERITY 8;
.SET MAXERROR 1;

INSERT INTO app_users (username, email) VALUES ('${REC_USER}', '${REC_EMAIL}');

.IF ERRORCODE <> 0 THEN .QUIT ERRORCODE;
.LOGOFF;
.QUIT 0;
EOF
      ;;
    read)
      local where_clause=""
      [[ -n "$REC_ID" ]] && where_clause="WHERE id = ${REC_ID}"
      bteq <<EOF
.LOGON ${TERADATA_TDP}/${TERADATA_USER},${TERADATA_PASS};
.SET WIDTH 250;
.SET SEPARATOR '|';
.SET TITLEDASHES OFF;
.SET MAXERROR 1;

SELECT TRIM(id), TRIM(username), TRIM(email) FROM app_users ${where_clause} ORDER BY id ASC;

.IF ERRORCODE <> 0 THEN .QUIT ERRORCODE;
.LOGOFF;
.QUIT 0;
EOF
      ;;
    update)
      local set_clauses=()
      [[ -n "$REC_USER" ]] && set_clauses+=("username = '${REC_USER}'")
      [[ -n "$REC_EMAIL" ]] && set_clauses+=("email = '${REC_EMAIL}'")
      local assignments
      assignments=$(IFS=", "; echo "${set_clauses[*]}")

      bteq <<EOF
.LOGON ${TERADATA_TDP}/${TERADATA_USER},${TERADATA_PASS};
.SET MAXERROR 1;

UPDATE app_users SET ${assignments} WHERE id = ${REC_ID};

.IF ERRORCODE <> 0 THEN .QUIT ERRORCODE;
.LOGOFF;
.QUIT 0;
EOF
      ;;
    delete)
      bteq <<EOF
.LOGON ${TERADATA_TDP}/${TERADATA_USER},${TERADATA_PASS};
.SET MAXERROR 1;

DELETE FROM app_users WHERE id = ${REC_ID};

.IF ERRORCODE <> 0 THEN .QUIT ERRORCODE;
.LOGOFF;
.QUIT 0;
EOF
      ;;
  esac
}

run_sqlserver() {
  command -v sqlcmd >/dev/null 2>&1 || { echo "Error: sqlcmd not found in PATH." >&2; exit 1; }

  echo ">>> Routing [$ACTION] to Microsoft SQL Server sqlcmd"

  # Technique: sqlcmd automatically reads the $SQLCMDPASSWORD environment variable.
  # By exporting SQLCMDPASSWORD and omitting the -P flag, the password is never in argv.
  export SQLCMDPASSWORD

  local base_args=(-S "$SQLSERVER_HOST" -U "$SQLSERVER_USER" -d "$SQLSERVER_DB" -b -V 16)

  case "$ACTION" in
    create)
      sqlcmd "${base_args[@]}" -Q "
        SET NOCOUNT ON;
        INSERT INTO app_users (username, email) VALUES ('${REC_USER}', '${REC_EMAIL}');
        PRINT '[OK] Record created successfully.';
      "
      ;;
    read)
      local where_clause=""
      [[ -n "$REC_ID" ]] && where_clause="WHERE id = ${REC_ID}"
      sqlcmd "${base_args[@]}" -W -s "|" -Q "
        SET NOCOUNT ON;
        SELECT id, username, email FROM app_users ${where_clause} ORDER BY id ASC;
      "
      ;;
    update)
      local set_clauses=()
      [[ -n "$REC_USER" ]] && set_clauses+=("username = '${REC_USER}'")
      [[ -n "$REC_EMAIL" ]] && set_clauses+=("email = '${REC_EMAIL}'")
      local assignments
      assignments=$(IFS=", "; echo "${set_clauses[*]}")

      sqlcmd "${base_args[@]}" -Q "
        SET NOCOUNT ON;
        UPDATE app_users SET ${assignments} WHERE id = ${REC_ID};
        PRINT '[OK] Record updated.';
      "
      ;;
    delete)
      sqlcmd "${base_args[@]}" -Q "
        SET NOCOUNT ON;
        DELETE FROM app_users WHERE id = ${REC_ID};
        PRINT '[OK] Record deleted.';
      "
      ;;
  esac
}

# --- Execution Router ---
case "$ENGINE" in
  oracle)    run_oracle ;;
  teradata)  run_teradata ;;
  sqlserver) run_sqlserver ;;
esac

echo ">>> Execution finished cleanly."
exit 0