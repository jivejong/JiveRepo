#!/usr/bin/env bash
#
# rolling_backup.sh - Creates a compressed archive and maintains a rolling retention cycle.

set -euo pipefail

# --- Usage & Help ---
show_usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") <source_directory> <backup_directory> [retention_days]

Description:
  Creates a timestamped .tar.gz archive of a given directory, verifies archive
  integrity with gzip, and deletes archives older than the retention threshold.

Arguments:
  source_directory    Required. The target directory to archive.
  backup_directory    Required. Directory where the .tar.gz files are placed.
  retention_days      Optional. Maximum age in days before old archives are deleted.
                      Default: 3

Options:
  -h, --help          Show this help text and exit.

Exit Codes:
  0  Backup and pruning completed successfully.
  1  Missing or invalid arguments, or archive verification failed.

Examples:
  $(basename "$0") /var/www/site /mnt/backups
  $(basename "$0") /opt/database /backups/db 7
EOF
}

# Check for explicit help flags
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_usage
  exit 0
fi

# Validate presence of required arguments
if [[ $# -eq 0 ]]; then
  echo "Error: No parameters provided." >&2
  echo "" >&2
  show_usage
  exit 1
fi

if [[ $# -lt 2 ]]; then
  echo "Error: Both <source_directory> and <backup_directory> are required." >&2
  echo "" >&2
  show_usage
  exit 1
fi

SOURCE_DIR="$1"
BACKUP_DIR="$2"
RETENTION_DAYS="${3:-3}"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Error: Source directory does not exist or is not a directory: $SOURCE_DIR" >&2
  exit 1
fi

# Validate retention_days is a positive integer
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "Error: retention_days must be a positive integer. Got: $RETENTION_DAYS" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# Generate archive paths
TARGET_NAME=$(basename "$(realpath "$SOURCE_DIR")")
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
ARCHIVE_NAME="${TARGET_NAME}_backup_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"

echo "=== Backup Process Started ==="
echo "Source:      $SOURCE_DIR"
echo "Destination: $ARCHIVE_PATH"
echo "Retention:   $RETENTION_DAYS day(s)"

PARENT_DIR=$(dirname "$(realpath "$SOURCE_DIR")")
DIR_NAME=$(basename "$(realpath "$SOURCE_DIR")")

tar -czf "$ARCHIVE_PATH" -C "$PARENT_DIR" "$DIR_NAME"

# Integrity check
if gzip -t "$ARCHIVE_PATH"; then
  ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)
  echo "Backup successfully created. Archive size: $ARCHIVE_SIZE"
else
  echo "Error: Archive integrity check failed. Removing broken archive." >&2
  rm -f "$ARCHIVE_PATH"
  exit 1
fi

# Pruning
echo "--- Pruning backups older than $RETENTION_DAYS days ---"
PRUNED_FILES=$(find "$BACKUP_DIR" -maxdepth 1 -name "${TARGET_NAME}_backup_*.tar.gz" -type f -mtime "+$RETENTION_DAYS")

if [[ -n "$PRUNED_FILES" ]]; then
  while IFS= read -r file; do
    echo "Pruning expired snapshot: $(basename "$file")"
    rm -f "$file"
  done <<< "$PRUNED_FILES"
else
  echo "No stale backups outside the ${RETENTION_DAYS}-day window."
fi

echo "=== Backup completed successfully. ==="
exit 0