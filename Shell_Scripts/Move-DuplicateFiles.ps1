# ============================================================
#  Move-DuplicateFiles.ps1
#
#  Finds duplicate files within a folder and its subfolders.
#  Duplicates are matched by file size + SHA-256 hash.
#
#  Keep/move logic
#  ---------------
#  When duplicates are found, the copy highest in the folder
#  tree (fewest path segments) is moved to the quarantine
#  folder. The deepest copy (most nested) is kept in place.
#  If two copies are at equal depth, the alphabetically
#  later path is moved and the earlier one is kept.
#
#  Run with -WhatIf first to preview what will be moved.
# ============================================================

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $SourcePath,
    [Parameter(Mandatory)][string] $QuarantinePath
)

# -- helpers --------------------------------------------------
$LogFile = ".\Move-DuplicateFiles_$(Get-Date -f 'yyyyMMdd_HHmmss').log"

function Write-Log {
    param([string]$Msg, [string]$Level = "INFO")
    $line = "[$(Get-Date -f 'yyyy-MM-dd HH:mm:ss')] [$Level] $Msg"
    Add-Content -Path $LogFile -Value $line -WhatIf:$false
    switch ($Level) {
        "WARN"  { Write-Host $line -ForegroundColor Yellow }
        "ERROR" { Write-Host $line -ForegroundColor Red    }
        default { Write-Host $line }
    }
}

function Get-FileHash256([string]$Path) {
    try { (Get-FileHash -Path $Path -Algorithm SHA256 -ErrorAction Stop).Hash }
    catch { "HASH_ERROR_$(New-Guid)" }
}

function Get-PathDepth([string]$Path) {
    $Path.TrimEnd('\').Split('\').Count
}

function Safe-Quarantine([string]$SourceFile, [string]$QBase, [string]$RootPath) {
    $rel  = $SourceFile.Substring($RootPath.TrimEnd('\').Length).TrimStart('\','/')
    $dest = Join-Path $QBase $rel
    $dir  = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force -WhatIf:$false | Out-Null }
    if (Test-Path $dest) {
        $base = [System.IO.Path]::GetFileNameWithoutExtension($dest)
        $ext  = [System.IO.Path]::GetExtension($dest)
        $i    = 1
        while (Test-Path $dest) { $dest = Join-Path $dir "${base}_dup${i}${ext}"; $i++ }
    }
    Move-Item -Path $SourceFile -Destination $dest -ErrorAction Stop
    $dest
}

# -- validate -------------------------------------------------
if (-not (Test-Path $SourcePath)) { Write-Error "SourcePath '$SourcePath' not found."; exit 1 }
if (-not (Test-Path $QuarantinePath)) {
    New-Item -ItemType Directory -Path $QuarantinePath -Force | Out-Null
}

Write-Log "=== Move-DuplicateFiles START ==="
Write-Log "Source     : $SourcePath"
Write-Log "Quarantine : $QuarantinePath"
Write-Log "WhatIf     : $($WhatIfPreference -eq 'Continue')"

$moved  = 0
$errors = 0

# -- PHASE 1: collect and group by size -----------------------
Write-Log "Phase 1: Collecting files..."

$allFiles = Get-ChildItem -Path $SourcePath -Recurse -File -Force -ErrorAction SilentlyContinue
Write-Log "Total files found: $($allFiles.Count)"

$sizeGroups = $allFiles | Group-Object Length | Where-Object { $_.Count -gt 1 }
Write-Log "Size-collision groups (candidates): $($sizeGroups.Count)"

# -- PHASE 2: hash candidates ---------------------------------
Write-Log "Phase 2: Hashing candidate files..."

$hashMap = @{}

$candidates = $sizeGroups | ForEach-Object { $_.Group }
$total      = ($candidates | Measure-Object).Count
$idx        = 0

foreach ($file in $candidates) {
    $idx++
    Write-Progress -Activity "Hashing" -Status $file.Name `
                   -PercentComplete ([int](($idx / $total) * 100))

    $h = Get-FileHash256 $file.FullName
    if (-not $hashMap.ContainsKey($h)) {
        $hashMap[$h] = [System.Collections.Generic.List[object]]::new()
    }
    $hashMap[$h].Add($file)
}
Write-Progress -Activity "Hashing" -Completed

$dupGroups = $hashMap.GetEnumerator() | Where-Object { $_.Value.Count -gt 1 }
Write-Log "True duplicate groups (size + hash match): $($($dupGroups | Measure-Object).Count)"

# -- PHASE 3: move shallowest copy ----------------------------
Write-Log "Phase 3: Processing duplicates..."

foreach ($entry in $dupGroups) {
    # Sort deepest first -- most path segments = lowest in tree = keep
    # On depth tie, alphabetically first path is kept
    $members = $entry.Value | Sort-Object { Get-PathDepth $_.FullName }, FullName -Descending
    $keeper  = $members[0]
    $dupes   = $members | Select-Object -Skip 1

    Write-Log "--- Hash $($entry.Key.Substring(0,12))...  Keeping: $($keeper.FullName)"

    foreach ($dupe in $dupes) {
        if (-not (Test-Path $dupe.FullName)) {
            Write-Log "  SKIP (already gone): $($dupe.FullName)" "WARN"
            continue
        }

        if ($WhatIfPreference -eq 'Continue') {
            Write-Log "  [WHATIF] Would move (depth $( Get-PathDepth $dupe.FullName )): $($dupe.FullName)"
        } else {
            try {
                $dest = Safe-Quarantine $dupe.FullName $QuarantinePath $SourcePath
                Write-Log "  Moved : $($dupe.FullName)"
                Write-Log "  To    : $dest"
                $moved++
            } catch {
                Write-Log "  FAILED: $($dupe.FullName) - $_" "ERROR"
                $errors++
            }
        }
    }
}

Write-Log "=== SUMMARY: Moved=$moved  Errors=$errors ==="
Write-Log "Review '$QuarantinePath' then delete when satisfied."
Write-Log "=== Move-DuplicateFiles END ==="
