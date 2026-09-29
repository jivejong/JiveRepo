param (
    [Parameter(Mandatory=$true, Position=0, HelpMessage="The folder to analyze for duplicate files.")]
    [string]$AnalyzePath,

    [Parameter(Mandatory=$true, Position=1, HelpMessage="The folder where duplicate files will be isolated.")]
    [string]$DuplicatesPath,

    [Parameter(Mandatory=$false, Position=2)]
    [switch]$WhatIf
)

# Clean and resolve paths
if (Test-Path $AnalyzePath) {
    $AnalyzePath = (Convert-Path $AnalyzePath)
} else {
    Write-Error "The analysis path '$AnalyzePath' does not exist."
    return
}

# Create duplicates folder if needed and not in a dry run
if (-not (Test-Path $DuplicatesPath)) {
    if ($WhatIf) {
        Write-Host "[WhatIf] Would create duplicates directory: $DuplicatesPath" -ForegroundColor Cyan
    } else {
        New-Item -ItemType Directory -Path $DuplicatesPath | Out-Null
        $DuplicatesPath = (Convert-Path $DuplicatesPath)
    }
} else {
    $DuplicatesPath = (Convert-Path $DuplicatesPath)
}

Write-Host "Scanning all files under: $AnalyzePath..." -ForegroundColor Gray
$AllFiles = Get-ChildItem -Path $AnalyzePath -Recurse -File
$HashSet = @{}

Write-Host "Calculating MD5 cryptographic hashes..." -ForegroundColor Gray
foreach ($File in $AllFiles) {
    # Protection: Do not scan inside the duplicates folder if it sits inside the target folder
    if ($File.FullName -like "$DuplicatesPath*") { continue }

    try {
        $HashObj = Get-FileHash -Path $File.FullName -Algorithm MD5 -ErrorAction Stop
        $Hash    = $HashObj.Hash
        
        # Calculate folder depth by counting directory separator characters
        $Depth = $File.FullName.Split([System.IO.Path]::DirectorySeparatorChar).Count

        # Packaging file payload for structural evaluation
        $FilePayload = [PSCustomObject]@{
            FileInfo = $File
            Depth    = $Depth
        }

        if (-not $HashSet.ContainsKey($Hash)) {
            $HashSet[$Hash] = [System.Collections.Generic.List[PSCustomObject]]::new()
        }
        $HashSet[$Hash].Add($FilePayload)
    } catch {
        Write-Host "SKIPPED (Read Error): $($File.FullName)" -ForegroundColor Red
    }
}

Write-Host "`nEvaluating duplicate clusters and applying structural survival rules..." -ForegroundColor Gray
$DuplicateCount = 0
$SpaceSaved = 0

foreach ($Hash in $HashSet.Keys) {
    $Group = $HashSet[$Hash]
    if ($Group.Count -le 1) { continue } # No duplicates for this hash

    # Sort descending by depth: Deepest nested paths (lowest level) come FIRST
    $SortedGroup = $Group | Sort-Object -Property Depth -Descending

    # Master File Selection: The file at index 0 is preserved (deepest in folder tree)
    $PreservedMaster = $SortedGroup[0].FileInfo

    # Eviction Execution: All remaining files are shallower (highest level) and must be moved
    for ($i = 1; $i -lt $SortedGroup.Count; $i++) {
        $FileToMove = $SortedGroup[$i].FileInfo
        $DuplicateCount++
        $SpaceSaved += $FileToMove.Length

        # Collision protection inside the isolation folder
        $BaseName    = $FileToMove.BaseName
        $Extension   = $FileToMove.Extension
        $NewFileName = $FileToMove.Name
        $Counter     = 1

        while (Test-Path (Join-Path $DuplicatesPath $NewFileName)) {
            $NewFileName = "${BaseName}_Duplicate_${Counter}${Extension}"
            $Counter++
        }
        $DestinationPath = Join-Path $DuplicatesPath $NewFileName

        if ($WhatIf) {
            Write-Host "[Dry-Run] WOULD REMOVE (Shallower/Highest Level Copy):" -ForegroundColor Cyan
            Write-Host "          Path: $($FileToMove.FullName)" -ForegroundColor Gray
            Write-Host "          -> Relocating To: $DestinationPath" -ForegroundColor DarkCyan
            Write-Host "          -> Preserving Master (Deepest/Lowest Level Copy): $($PreservedMaster.FullName)`n" -ForegroundColor Gray
        } else {
            try {
                Copy-Item -Path $FileToMove.FullName -Destination $DestinationPath -Force
                Remove-Item -Path $FileToMove.FullName -Force
                Write-Host "ISOLATED: $($FileToMove.FullName)" -ForegroundColor Yellow
                Write-Host "          Preserved Deepest Copy: $($PreservedMaster.FullName)`n" -ForegroundColor Gray
            } catch {
                Write-Host "ERROR: Execution failure migrating $($FileToMove.FullName)" -ForegroundColor Red
            }
        }
    }
}

# Output Metrics
$TotalGB = [Math]::Round($SpaceSaved / 1GB, 2)
Write-Host "--- ANALYSIS LOG COMPLETE ---" -ForegroundColor Green
Write-Host "Total redundant files processed: $DuplicateCount" -ForegroundColor Green
Write-Host "Recovered Storage Space: $TotalGB GB" -ForegroundColor Green