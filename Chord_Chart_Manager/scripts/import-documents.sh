#!/usr/bin/env bash
# Import every .docx in pipeline/docs: parse, preview, and (with --apply) back up,
# load and enrich in one run. Without --apply nothing in the database changes.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
APP="$ROOT/app"
host_path(){ if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }
COMPOSE=(docker compose --project-directory "$(host_path "$APP")" -f "$(host_path "$APP/docker-compose.yml")")
pipeline(){ MSYS_NO_PATHCONV=1 "${COMPOSE[@]}" --profile pipeline run --rm pipeline "$@"; }
APPLY=0
while (($#)); do case "$1" in
  --apply) APPLY=1; shift;;
  *) echo 'usage: import-documents.sh [--apply]' >&2; exit 2;;
esac; done

"${COMPOSE[@]}" --profile pipeline build pipeline
pipeline python batch_parse.py docs -o parsed_out --per-doc
echo 'Review pipeline/parsed_out/report.json against the source documents.'
pipeline python load_songs.py parsed_out/songs.json --dry-run

if ((APPLY == 0)); then
  echo
  echo 'Preview only; the database was not changed. Review the output above, then rerun with --apply.'
  exit 0
fi

echo
echo 'Sync every device first (Settings shows 0 pending): enrichment changes songs, and a queued tablet edit to the same song is replaced by the server copy.'
"$SCRIPT_DIR/backup.sh"
pipeline python load_songs.py parsed_out/songs.json --enrich
