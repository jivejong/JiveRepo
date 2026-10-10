#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
APP="$ROOT/app"
host_path(){ if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }
COMPOSE=(docker compose --project-directory "$(host_path "$APP")" -f "$(host_path "$APP/docker-compose.yml")")
compose_exec(){ MSYS_NO_PATHCONV=1 "${COMPOSE[@]}" exec "$@"; }
FILE=""; DB=""; YES=0
while (($#)); do case "$1" in
  --file) FILE="${2:?missing file}"; shift 2;;
  --database) DB="${2:?missing database}"; shift 2;;
  --yes) YES=1; shift;;
  *) echo 'usage: restore.sh --file PATH [--database NAME] --yes' >&2; exit 2;;
esac; done
((YES)) || { echo 'restore refused: pass --yes to confirm' >&2; exit 2; }
[[ -n "$FILE" && -f "$FILE" && -s "$FILE" ]] || { echo 'archive path missing, invalid, or empty' >&2; exit 2; }
read -r DEFAULT_DB DB_USER < <("${COMPOSE[@]}" exec -T db sh -c 'printf "%s %s\n" "${POSTGRES_DB:-postgres}" "${POSTGRES_USER:-postgres}"')
DB="${DB:-$DEFAULT_DB}"
for ident in "$DB" "$DB_USER"; do [[ "$ident" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || { echo 'invalid database/user identifier' >&2; exit 2; }; done
TOKEN="t05-restore-$(date +%s)-$$-${RANDOM}.dump"; INSIDE="/tmp/$TOKEN"; CID="$("${COMPOSE[@]}" ps -q db)"
cleanup(){ compose_exec -T db rm -f "$INSIDE" >/dev/null 2>&1 || true; }
trap cleanup EXIT
FILE_WIN="$(host_path "$FILE")"
MSYS_NO_PATHCONV=1 docker cp "$FILE_WIN" "$CID:$INSIDE"
compose_exec -T -u postgres db pg_restore --list "$INSIDE" >/dev/null
SAFETY="$("$SCRIPT_DIR/backup.sh" --database "$DB" --keep 2147483647)"
[[ -n "$SAFETY" && -s "$SAFETY" ]] || { echo 'safety backup failed; restore stopped' >&2; exit 1; }
compose_exec -T -u postgres db pg_restore --clean --if-exists --exit-on-error -U "$DB_USER" -d "$DB" "$INSIDE"
printf 'Restore completed for %s. Safety backup: %s\n' "$DB" "$SAFETY"
