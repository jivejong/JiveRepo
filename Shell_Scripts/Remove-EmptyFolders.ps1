# ============================================================
#  Remove-EmptyFolders.ps1
#  Recursively deletes all empty folders under a target path.
#  Supports -WhatIf (dry run) and writes a log file.
# ============================================================

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $TargetPath,
    [string]  $LogFile  = ".\RemoveEmptyFolders_$(Get-Date -f 'yyyyMMdd_HHmmss').log"
)

# ── helpers ──────────────────────────────────────────────────
function Write-Log {
    param([string]$Msg, [string]$Level = "INFO")
    $line = "[$(Get-Date -f 'yyyy-MM-dd HH:mm:ss')] [$Level] $Msg"
    Add-Content -Path $LogFile -Value $line
    switch ($Level) {
        "WARN"  { Write-Host $line -ForegroundColor Yellow }
        "ERROR" { Write-Host $line -ForegroundColor Red    }
        default { Write-Host $line }
    }
}

# ── validate ─────────────────────────────────────────────────
if (-not (Test-Path $TargetPath)) {
    Write-Error "TargetPath '$TargetPath' does not exist."
    exit 1
}

Write-Log "=== Remove-EmptyFolders START ==="
Write-Log "Target : $TargetPath"
Write-Log "WhatIf : $($WhatIfPreference -eq 'Continue')"

$removed = 0
$errors  = 0

# Keep looping until no more empty folders are found.
# One pass can expose newly-emptied parents, so we iterate.
do {
    $emptied = 0

    # Deepest first (bottom-up) so parents are checked after children.
    $folders = Get-ChildItem -Path $TargetPath -Recurse -Directory |
               Sort-Object { $_.FullName.Length } -Descending

    foreach ($folder in $folders) {
        $children = Get-ChildItem -Path $folder.FullName -Force -ErrorAction SilentlyContinue

        if ($null -eq $children -or $children.Count -eq 0) {
            if ($WhatIfPreference -eq 'Continue') {
                Write-Log "[WHATIF] Would delete empty folder: $($folder.FullName)"
            } else {
                try {
                    Remove-Item -Path $folder.FullName -Force -Recurse -ErrorAction Stop
                    Write-Log "Deleted: $($folder.FullName)"
                    $removed++
                    $emptied++
                } catch {
                    Write-Log "Failed to delete '$($folder.FullName)': $_" "ERROR"
                    $errors++
                }
            }
        }
    }
} while ($emptied -gt 0)

Write-Log "=== SUMMARY: Removed=$removed  Errors=$errors ==="
Write-Log "=== Remove-EmptyFolders END ==="
