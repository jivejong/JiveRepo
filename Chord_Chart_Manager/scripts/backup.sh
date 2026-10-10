#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
APP="$ROOT/app"
host_path(){ if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }
COMPOSE=(docker compose --project-directory "$(host_path "$APP")" -f "$(host_path "$APP/docker-compose.yml")")
compose_exec(){ MSYS_NO_PATHCONV=1 "${COMPOSE[@]}" exec "$@"; }
DB=""; KEEP=14
while (($#)); do case "$1" in
  --database) DB="${2:?missing database}"; shift 2;;
  --keep) KEEP="${2:?missing keep count}"; shift 2;;
  *) echo 'usage: backup.sh [--database NAME] [--keep N]' >&2; exit 2;;
esac; done
[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || { echo 'keep must be a positive integer' >&2; exit 2; }
read -r DEFAULT_DB DB_USER < <("${COMPOSE[@]}" exec -T db sh -c 'printf "%s %s\n" "${POSTGRES_DB:-postgres}" "${POSTGRES_USER:-postgres}"')
DB="${DB:-$DEFAULT_DB}"
for ident in "$DB" "$DB_USER"; do [[ "$ident" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || { echo 'invalid database/user identifier' >&2; exit 2; }; done
OUT="$ROOT/backups"; mkdir -p "$OUT"
STAMP="$(date +%Y%m%d-%H%M%S)"; TOKEN="t05-$(date +%s)-$$-${RANDOM}.dump"
TMP="$OUT/.$TOKEN"; TMP_WIN="$(host_path "$TMP")"; CFILE="/tmp/$TOKEN"; CID="$("${COMPOSE[@]}" ps -q db)"
cleanup(){ rm -f -- "$TMP"; compose_exec -T db rm -f "$CFILE" >/dev/null 2>&1 || true; }
trap cleanup EXIT
compose_exec -T -u postgres db pg_dump -Fc -U "$DB_USER" -d "$DB" -f "$CFILE"
MSYS_NO_PATHCONV=1 docker cp "$CID:$CFILE" "$TMP_WIN"
[[ -s "$TMP" ]] || { echo 'pg_dump archive is empty' >&2; exit 1; }
MSYS_NO_PATHCONV=1 docker cp "$TMP_WIN" "$CID:$CFILE"
compose_exec -T -u postgres db pg_restore --list "$CFILE" >/dev/null
FINAL="$OUT/$DB-$STAMP.dump"; mv -- "$TMP" "$FINAL"
find "$OUT" -maxdepth 1 -type f -regextype posix-extended -regex ".*/${DB}-[0-9]{8}-[0-9]{6}\.dump" -printf '%f\n' | sort -r | tail -n +$((KEEP+1)) | while IFS= read -r f; do rm -f -- "$OUT/$f"; done
printf '%s\n' "$FINAL"
