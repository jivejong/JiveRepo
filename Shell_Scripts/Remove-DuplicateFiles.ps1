# ============================================================
#  Remove-DuplicateFiles.ps1
#
#  Compares two folders (FolderA and FolderB).
#  If the same file exists in both (matched by size + SHA-256),
#  the copy in FolderA is moved to QuarantinePath.
#  The copy in FolderB is always kept.
#
#  Strategy
#  --------
#  1. Collect all files from FolderB and build a hash index
#     (size + SHA-256) -- this is the "keep" reference set.
#  2. Collect all files from FolderA.
#  3. For each FolderA file: if size matches any FolderB file,
#     compute SHA-256 and check for a match in the index.
#  4. On match: move the FolderA copy to QuarantinePath,
#     preserving its relative sub-folder for review.
#
#  Run with -WhatIf first to preview what will be moved.
# ============================================================

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $FolderA,
    [Parameter(Mandatory)][string] $FolderB,
    [Parameter(Mandatory)][string] $QuarantinePath,
    [string] $LogFile      = ".\RemoveDuplicateFiles_$(Get-Date -f 'yyyyMMdd_HHmmss').log",
    [int]    $MinSizeBytes = 0
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
    catch { "HASH_ERROR_$(New-Guid)" }
}

function Safe-Quarantine([string]$SourceFile, [string]$QBase, [string]$RootPath) {
    $rel  = $SourceFile.Substring($RootPath.TrimEnd('\').Length).TrimStart('\','/')
    $dest = Join-Path $QBase $rel
    $dir  = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
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
if (-not (Test-Path $FolderA)) { Write-Error "FolderA '$FolderA' not found."; exit 1 }
if (-not (Test-Path $FolderB)) { Write-Error "FolderB '$FolderB' not found."; exit 1 }
if (-not (Test-Path $QuarantinePath)) {
    New-Item -ItemType Directory -Path $QuarantinePath -Force | Out-Null
}

Write-Log "=== Remove-DuplicateFiles START ==="
Write-Log "Folder A (delete from) : $FolderA"
Write-Log "Folder B (keep from)   : $FolderB"
Write-Log "Quarantine             : $QuarantinePath"
Write-Log "MinSize                : $MinSizeBytes bytes"
Write-Log "WhatIf                 : $($WhatIfPreference -eq 'Continue')"

$moved  = 0
$errors = 0

# -- PHASE 1: index FolderB by size then hash -----------------
Write-Log "Phase 1: Indexing FolderB..."

$bFiles = Get-ChildItem -Path $FolderB -Recurse -File -Force -ErrorAction SilentlyContinue |
          Where-Object { $_.Length -ge $MinSizeBytes }

Write-Log "FolderB files found: $($bFiles.Count)"

# Size index: size -> list of FileInfo (avoid hashing everything upfront)
$bSizeIndex = @{}
foreach ($f in $bFiles) {
    if (-not $bSizeIndex.ContainsKey($f.Length)) {
        $bSizeIndex[$f.Length] = [System.Collections.Generic.List[object]]::new()
    }
    $bSizeIndex[$f.Length].Add($f)
}

# Hash index built lazily as FolderA files are checked: hash -> $true
$bHashIndex = @{}

# -- PHASE 2: scan FolderA and compare ------------------------
Write-Log "Phase 2: Scanning FolderA and comparing..."

$aFiles = Get-ChildItem -Path $FolderA -Recurse -File -Force -ErrorAction SilentlyContinue |
          Where-Object { $_.Length -ge $MinSizeBytes }

Write-Log "FolderA files found: $($aFiles.Count)"

$total = $aFiles.Count
$idx   = 0

foreach ($aFile in $aFiles) {
    $idx++
    Write-Progress -Activity "Comparing FolderA to FolderB" -Status $aFile.Name `
                   -PercentComplete ([int](($idx / $total) * 100))

    # Quick size pre-filter -- if no FolderB file shares this size, skip
    if (-not $bSizeIndex.ContainsKey($aFile.Length)) { continue }

    # Hash the FolderA file
    $aHash = Get-FileHash256 $aFile.FullName

    # Lazily hash FolderB candidates for this size if not already done
    foreach ($bCandidate in $bSizeIndex[$aFile.Length]) {
        $key = $bCandidate.FullName
        if (-not $bHashIndex.ContainsKey($key)) {
            $bHashIndex[$key] = Get-FileHash256 $bCandidate.FullName
        }
    }

    # Check if aHash matches any FolderB file of the same size
    $matchedB = $bSizeIndex[$aFile.Length] |
                Where-Object { $bHashIndex[$_.FullName] -eq $aHash } |
                Select-Object -First 1

    if ($null -eq $matchedB) { continue }

    # Duplicate confirmed -- move FolderA copy to quarantine
    if ($WhatIfPreference -eq 'Continue') {
        Write-Log "[WHATIF] Would quarantine: $($aFile.FullName)"
        Write-Log "         Matched by     : $($matchedB.FullName)"
    } else {
        try {
            $dest = Safe-Quarantine $aFile.FullName $QuarantinePath $FolderA
            Write-Log "Quarantined : $($aFile.FullName)"
            Write-Log "  Matched   : $($matchedB.FullName)"
            Write-Log "  Moved to  : $dest"
            $moved++
        } catch {
            Write-Log "FAILED: $($aFile.FullName) - $_" "ERROR"
            $errors++
        }
    }
}

Write-Progress -Activity "Comparing FolderA to FolderB" -Completed

Write-Log "=== SUMMARY: Quarantined=$moved  Errors=$errors ==="
Write-Log "Review '$QuarantinePath' then delete when satisfied."
Write-Log "=== Remove-DuplicateFiles END ==="
