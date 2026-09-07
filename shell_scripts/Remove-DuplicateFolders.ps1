# ============================================================
#  Remove-DuplicateFolders.ps1
#
#  Detects duplicate folders -- same name AND identical file
#  contents (count + sizes + hashes).
#
#  Strategy
#  --------
#  1. Group all folders by their base name.
#  2. Within each name-group, fingerprint every folder:
#       SHA-256 of the sorted "relativePath|size|hash" manifest.
#  3. Folders sharing a name AND fingerprint are true duplicates.
#  4. Keep the first occurrence (by full path, alphabetically).
#  5. Move every other copy to $QuarantinePath.
#
#  Always run with -WhatIf first to preview what will happen.
# ============================================================

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $TargetPath,
    [Parameter(Mandatory)][string] $QuarantinePath,
    [string] $LogFile = ".\RemoveDuplicateFolders_$(Get-Date -f 'yyyyMMdd_HHmmss').log"
)

# -- helpers --------------------------------------------------
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

function Get-FileHash256([string]$Path) {
    try { (Get-FileHash -Path $Path -Algorithm SHA256 -ErrorAction Stop).Hash }
    catch { "HASH_ERROR" }
}

# Build a single fingerprint for an entire folder tree.
function Get-FolderFingerprint([string]$FolderPath) {
    $files = Get-ChildItem -Path $FolderPath -Recurse -File -Force -ErrorAction SilentlyContinue |
             Sort-Object FullName

    if (-not $files) { return "EMPTY" }

    $manifest = foreach ($f in $files) {
        $rel  = $f.FullName.Substring($FolderPath.Length).TrimStart('\','/')
        $hash = Get-FileHash256 $f.FullName
        "$rel|$($f.Length)|$hash"
    }

    $joined = $manifest -join "`n"
    $bytes  = [System.Text.Encoding]::UTF8.GetBytes($joined)
    $sha    = [System.Security.Cryptography.SHA256]::Create()
    ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join ""
}

function Safe-Move([string]$Source, [string]$Destination) {
    $dest = $Destination
    $i    = 1
    while (Test-Path $dest) { $dest = "${Destination}_dup$i"; $i++ }
    Move-Item -Path $Source -Destination $dest -ErrorAction Stop
    $dest
}

# -- validate -------------------------------------------------
if (-not (Test-Path $TargetPath)) { Write-Error "TargetPath not found."; exit 1 }

if (-not (Test-Path $QuarantinePath)) {
    New-Item -ItemType Directory -Path $QuarantinePath -Force | Out-Null
}

Write-Log "=== Remove-DuplicateFolders START ==="
Write-Log "Target     : $TargetPath"
Write-Log "Quarantine : $QuarantinePath"
Write-Log "WhatIf     : $($WhatIfPreference -eq 'Continue')"

$moved  = 0
$errors = 0

# -- collect all folders, shallowest-first --------------------
$allFolders = Get-ChildItem -Path $TargetPath -Recurse -Directory -Force |
              Sort-Object { $_.FullName.Length }

Write-Log "Total folders found: $($allFolders.Count)"
Write-Log "Fingerprinting folders -- this may take a while for large trees..."

$fpTable = @{}
$total   = $allFolders.Count
$idx     = 0

foreach ($f in $allFolders) {
    $idx++
    Write-Progress -Activity "Fingerprinting" -Status $f.Name `
                   -PercentComplete ([int](($idx / $total) * 100))
    $fpTable[$f.FullName] = Get-FolderFingerprint $f.FullName
}
Write-Progress -Activity "Fingerprinting" -Completed

# -- group by name + fingerprint ------------------------------
$groups    = $allFolders | Group-Object { "$($_.Name)||$($fpTable[$_.FullName])" }
$dupGroups = $groups | Where-Object { $_.Count -gt 1 }

Write-Log "Duplicate groups found: $($dupGroups.Count)"

foreach ($group in $dupGroups) {
    $members = $group.Group | Sort-Object { $_.FullName.Length }, FullName
    $keeper  = $members[0]
    $dupes   = $members | Select-Object -Skip 1

    Write-Log "--- Duplicate group: '$($keeper.Name)' (keeping: $($keeper.FullName))"

    foreach ($dupe in $dupes) {
        if (-not (Test-Path $dupe.FullName)) {
            Write-Log "  SKIP (already gone): $($dupe.FullName)" "WARN"
            continue
        }

        $destBase = Join-Path $QuarantinePath $dupe.Name

        if ($WhatIfPreference -eq 'Continue') {
            Write-Log "  [WHATIF] Would quarantine: $($dupe.FullName) -> $destBase"
        } else {
            try {
                $actual = Safe-Move $dupe.FullName $destBase
                Write-Log "  Quarantined: $($dupe.FullName) -> $actual"
                $moved++
            } catch {
                Write-Log "  Move FAILED: $($dupe.FullName) - $_" "ERROR"
                $errors++
            }
        }
    }
}

Write-Log "=== SUMMARY: Quarantined=$moved  Errors=$errors ==="
Write-Log "Review '$QuarantinePath' before permanent deletion."
Write-Log "=== Remove-DuplicateFolders END ==="
