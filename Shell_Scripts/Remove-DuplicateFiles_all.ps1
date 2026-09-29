# ============================================================
#  Remove-DuplicateFiles.ps1
#
#  Detects duplicate files using a three-signal approach:
#    1. File size must match exactly.
#    2. SHA-256 hash must match exactly.
#    3. File name similarity is REPORTED but not required
#       (size+hash alone are the authoritative duplicate signal).
#
#  Strategy
#  ────────
#  • Group all files by size first (cheap) — mismatched sizes
#    can never be duplicates.
#  • Within each size group, compute SHA-256 hashes.
#  • Files with identical hash = definite duplicates.
#  • Keep the copy closest to the root (shallowest path) and,
#    among ties, the alphabetically first full path.
#  • Move each extra copy to $QuarantinePath preserving its
#    relative sub-folder so you can review originals in context.
#
#  Run with -WhatIf first to see what would be moved.
# ============================================================

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $TargetPath,
    [Parameter(Mandatory)][string] $QuarantinePath,
    [string]  $LogFile      = ".\RemoveDuplicateFiles_$(Get-Date -f 'yyyyMMdd_HHmmss').log",
    [int]     $MinSizeBytes = 0
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

function Get-FileHash256([string]$Path) {
    try { (Get-FileHash -Path $Path -Algorithm SHA256 -ErrorAction Stop).Hash }
    catch { "HASH_ERROR_$(New-Guid)" }   # unique so it never falsely groups
}

# Levenshtein distance (used for name-similarity reporting only)
function Get-Similarity([string]$A, [string]$B) {
    $a = $A.ToLower(); $b = $B.ToLower()
    if ($a -eq $b) { return 1.0 }
    $la = $a.Length; $lb = $b.Length
    if ($la -eq 0 -or $lb -eq 0) { return 0.0 }
    $d = New-Object 'int[,]' ($la+1),($lb+1)
    for ($i=0;$i -le $la;$i++){$d[$i,0]=$i}
    for ($j=0;$j -le $lb;$j++){$d[0,$j]=$j}
    for ($i=1;$i -le $la;$i++){
        for ($j=1;$j -le $lb;$j++){
            $cost = if ($a[$i-1] -eq $b[$j-1]) {0} else {1}
            $d[$i,$j] = [Math]::Min([Math]::Min($d[$i-1,$j]+1,$d[$i,$j-1]+1),$d[$i-1,$j-1]+$cost)
        }
    }
    $dist = $d[$la,$lb]
    1.0 - ($dist / [Math]::Max($la,$lb))
}

function Safe-Quarantine([string]$SourceFile, [string]$QBase, [string]$RootPath) {
    # Preserve relative path under quarantine so context is visible
    $rel  = $SourceFile.Substring($RootPath.TrimEnd('\').Length).TrimStart('\','/')
    $dest = Join-Path $QBase $rel
    $dir  = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    # Avoid collision in quarantine
    if (Test-Path $dest) {
        $base = [System.IO.Path]::GetFileNameWithoutExtension($dest)
        $ext  = [System.IO.Path]::GetExtension($dest)
        $i    = 1
        while (Test-Path $dest) { $dest = Join-Path $dir "$base`_dup$i$ext"; $i++ }
    }
    Move-Item -Path $SourceFile -Destination $dest -ErrorAction Stop
    $dest
}

# ── validate ─────────────────────────────────────────────────
if (-not (Test-Path $TargetPath)) { Write-Error "TargetPath not found."; exit 1 }
if (-not (Test-Path $QuarantinePath)) {
    New-Item -ItemType Directory -Path $QuarantinePath -Force | Out-Null
}

Write-Log "=== Remove-DuplicateFiles START ==="
Write-Log "Target     : $TargetPath"
Write-Log "Quarantine : $QuarantinePath"
Write-Log "MinSize    : $MinSizeBytes bytes"
Write-Log "WhatIf     : $($WhatIfPreference -eq 'Continue')"

$moved  = 0
$errors = 0

# ── PHASE 1 — collect & group by size ────────────────────────
Write-Log "Phase 1: Collecting files..."
$allFiles = Get-ChildItem -Path $TargetPath -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Length -ge $MinSizeBytes }

Write-Log "Files found: $($allFiles.Count)"

$sizeGroups = $allFiles | Group-Object Length | Where-Object { $_.Count -gt 1 }
Write-Log "Size-collision groups (candidates): $($sizeGroups.Count)"

# ── PHASE 2 — hash within each size group ────────────────────
Write-Log "Phase 2: Hashing candidate files..."

$hashMap = @{}   # hash -> list of file objects

$total = ($sizeGroups | ForEach-Object { $_.Group } | Measure-Object).Count
$idx   = 0

foreach ($sg in $sizeGroups) {
    foreach ($file in $sg.Group) {
        $idx++
        Write-Progress -Activity "Hashing" -Status $file.Name `
                       -PercentComplete ([int](($idx / $total) * 100))

        $h = Get-FileHash256 $file.FullName
        if (-not $hashMap.ContainsKey($h)) { $hashMap[$h] = [System.Collections.Generic.List[object]]::new() }
        $hashMap[$h].Add($file)
    }
}
Write-Progress -Activity "Hashing" -Completed

$dupGroups = $hashMap.GetEnumerator() | Where-Object { $_.Value.Count -gt 1 }
Write-Log "True duplicate groups (size + hash match): $($($dupGroups | Measure-Object).Count)"

# ── PHASE 3 — quarantine duplicates ──────────────────────────
Write-Log "Phase 3: Quarantining duplicates..."

foreach ($entry in $dupGroups) {
    # Sort: shallowest path wins; ties broken alphabetically
    $members = $entry.Value | Sort-Object { $_.FullName.Split('\').Count }, FullName
    $keeper  = $members[0]
    $dupes   = $members | Select-Object -Skip 1

    Write-Log "--- Hash $($entry.Key.Substring(0,12))...  Keeper: $($keeper.FullName)"

    foreach ($dupe in $dupes) {
        if (-not (Test-Path $dupe.FullName)) {
            Write-Log "  SKIP (already gone): $($dupe.FullName)" "WARN"
            continue
        }

        # Name similarity report (informational)
        $sim = Get-Similarity ([System.IO.Path]::GetFileNameWithoutExtension($keeper.Name)) `
                               ([System.IO.Path]::GetFileNameWithoutExtension($dupe.Name))
        $simPct = [Math]::Round($sim * 100, 1)

        if ($WhatIfPreference -eq 'Continue') {
            Write-Log "  [WHATIF] Would quarantine (name sim: $simPct%): $($dupe.FullName)"
        } else {
            try {
                $dest = Safe-Quarantine $dupe.FullName $QuarantinePath $TargetPath
                Write-Log "  Quarantined (sim: $simPct%): $($dupe.FullName) -> $dest"
                $moved++
            } catch {
                Write-Log "  FAILED: $($dupe.FullName) — $_" "ERROR"
                $errors++
            }
        }
    }
}

Write-Log "=== SUMMARY: Quarantined=$moved  Errors=$errors ==="
Write-Log "Review '$QuarantinePath' then delete when satisfied."
Write-Log "=== Remove-DuplicateFiles END ==="
