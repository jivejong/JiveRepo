#!/usr/bin/env bash
#
# scan_injections.sh - Pre-ingestion scanner for common LLM prompt injection signatures.

set -euo pipefail

# --- Usage & Help ---
show_usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") <input_directory> [quarantine_directory]

Description:
  Scans flat files (.txt, .md, .json, .csv, .log) for prompt injection and
  jailbreak signatures before data ingestion. Flagged files are isolated.

Arguments:
  input_directory       Required. Directory containing files to inspect.
  quarantine_directory  Optional. Target directory for suspicious files.
                        Default: ./quarantine

Options:
  -h, --help            Show this help text and exit.

Exit Codes:
  0  Scan clean (no suspicious patterns detected).
  1  Invalid arguments, missing paths, or runtime failure.
  2  One or more suspicious files detected and quarantined.

Examples:
  $(basename "$0") ./incoming_prompts
  $(basename "$0") /var/data/raw_inputs /var/data/flagged_inputs
EOF
}

# Check for explicit help flags
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_usage
  exit 0
fi

# Validate presence of required argument
if [[ $# -eq 0 ]]; then
  echo "Error: Missing required argument <input_directory>." >&2
  echo "" >&2
  show_usage
  exit 1
fi

INPUT_DIR="$1"
QUARANTINE_DIR="${2:-./quarantine}"
SCAN_EXTENSIONS='^.*\.(txt|md|json|csv|log)$'

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "Error: Target input directory does not exist: $INPUT_DIR" >&2
  exit 1
fi

mkdir -p "$QUARANTINE_DIR"

# Injection patterns
PATTERNS=(
  "ignore (all )?(previous|above|prior) (instructions|prompts|directions)"
  "disregard (all )?(previous|above|prior) (instructions|rules)"
  "you are no longer (a|an)"
  "new system prompt:"
  "system prompt override"
  "start new session"
  "bypass safety guidelines"
  "developer mode enabled"
  "DAN mode"
  "jailbreak"
  "do anything now"
  "output the (original|system) (prompt|instructions)"
  "reveal your initial instructions"
)

REGEX=$(IFS="|"; echo "${PATTERNS[*]}")
INFECTED_COUNT=0
CLEAN_COUNT=0

echo "=== Starting Ingestion Scan on: $INPUT_DIR ==="

while IFS= read -r -d '' filepath; do
  if [[ ! "$filepath" =~ $SCAN_EXTENSIONS ]]; then
    continue
  fi

  if matches=$(grep -Ein -- "$REGEX" "$filepath"); then
    echo "[!] ALERT: Potential prompt injection detected in: $filepath"
    while IFS= read -r line; do
      echo "    Line: $line"
    done <<< "$matches"

    mv "$filepath" "$QUARANTINE_DIR/"
    echo "    Action: Moved to $QUARANTINE_DIR/$(basename "$filepath")"
    ((INFECTED_COUNT++))
  else
    ((CLEAN_COUNT++))
  fi
done < <(find "$INPUT_DIR" -maxdepth 2 -type f -print0)

echo "============================================="
echo "Scan complete."
echo "Clean files verified: $CLEAN_COUNT"
echo "Suspicious files quarantined: $INFECTED_COUNT"

if (( INFECTED_COUNT > 0 )); then
  exit 2
fi

exit 0