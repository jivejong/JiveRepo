<#
.SYNOPSIS
    dir-lens: Local AI Directory Auditor (PowerShell Edition)
    Uses Ollama for 100% offline, privacy-first storage analysis.

.DESCRIPTION
    Scans a folder, gathers structural metadata (top file extensions, 
    largest files, total size), and streams actionable cleanup recommendations
    using a local LLM via Ollama.

.PARAMETER Path
    Target directory path to inspect. Defaults to the current location.

.PARAMETER Model
    The local Ollama model to use. Defaults to "llama3.2".

.PARAMETER OllamaHost
    The endpoint where Ollama is reachable. Defaults to "http://localhost:11434".

.EXAMPLE
    .\dir-lens.ps1 -Path "C:\Users\username\Downloads"

.EXAMPLE
    .\dir-lens.ps1 -Path "./src" -Model "mistral"
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Path = ".",

    [string]$Model = $env:OLLAMA_MODEL,

    [string]$OllamaHost = $env:OLLAMA_HOST
)

$ErrorActionPreference = "Stop"

# --- Set Defaults If Env Vars Were Empty ---
if (-not $Model) { $Model = "llama3.2" }
if (-not $OllamaHost) { $OllamaHost = "http://localhost:11434" }

# --- 1. Path Resolution ---
$resolvedPath = Resolve-Path -Path $Path -ErrorAction SilentlyContinue
if (-not $resolvedPath -or -not (Test-Path -Path $resolvedPath -PathType Container)) {
    Write-Error "Invalid target path: '$Path' is not a valid directory."
    exit 1
}
$targetDir = $resolvedPath.Path

# --- 2. Ollama Daemon & Model Preflight Checks ---
function Test-OllamaHealth {
    Write-Host "==> " -ForegroundColor Cyan -NoNewline
    Write-Host "Verifying Ollama connection at $OllamaHost..."

    try {
        $tagsResponse = Invoke-RestMethod -Uri "$OllamaHost/api/tags" -Method Get -TimeoutSec 3
    }
    catch {
        Write-Host "Error: Cannot connect to Ollama at $OllamaHost" -ForegroundColor Red
        Write-Host "Ensure Ollama is running ('ollama serve' or open the desktop app)." -ForegroundColor Yellow
        exit 1
    }

    $installedModels = $tagsResponse.models | ForEach-Object { $_.name }
    $modelExists = $installedModels | Where-Object { 
        $_ -eq $Model -or $_ -eq "$Model`:latest" -or $_ -like "*$Model*" 
    }

    if (-not $modelExists) {
        Write-Host "Model '$Model' not found locally." -ForegroundColor Yellow
        Write-Host "Pulling '$Model' via Ollama CLI (this happens once)..." -ForegroundColor Cyan
        
        if (Get-Command "ollama" -ErrorAction SilentlyContinue) {
            & ollama pull $Model
            if ($LASTEXITCODE -ne 0) {
                Write-Error "Failed to pull model '$Model'."
                exit 1
            }
        }
        else {
            Write-Error "Ollama executable not found in PATH. Please run 'ollama pull $Model' manually."
            exit 1
        }
    }
}

# --- 3. Directory Metadata Extractor ---
function Get-DirectoryMetadata {
    param([string]$Dir)

    Write-Host "==> " -ForegroundColor Cyan -NoNewline
    Write-Host "Scanning: " -NoNewline
    Write-Host "$Dir" -ForegroundColor White

    # Gather items safely ignoring permission-denied entries
    $allItems = Get-ChildItem -Path$Dir -Recurse -Force -File -ErrorAction SilentlyContinue
    $allDirs = Get-ChildItem -Path$Dir -Recurse -Force -Directory -ErrorAction SilentlyContinue

    $fileCount = if ($allItems) { ($allItems \vert {} Measure-Object).Count } else { 0 }$dirCount = if ($allDirs) { ($allDirs | Measure-Object).Count }  else { 0 }
    
    $totalBytes = ($allItems | Measure-Object -Property Length -Sum).Sum
    if (-not $totalBytes) { $totalBytes = 0 }

    # Human-readable size formatting
    $formattedSize = switch ($totalBytes) {
        { $_ -ge 1GB } { "{0:N2} GB" -f ($_ / 1GB); break }
        { $_ -ge 1MB } { "{0:N2} MB" -f ($_ / 1MB); break }
        { $_ -ge 1KB } { "{0:N2} KB" -f ($_ / 1KB); break }
        default { "$_ Bytes" }
    }

    # Top 5 largest files
    $largestFiles = $allItems | 
    Sort-Object Length -Descending | 
    Select-Object -First 5 | 
    ForEach-Object {
        $sizeStr = switch ($_.Length) {
            { $_ -ge 1GB } { "{0:N2} GB" -f ($_ / 1GB); break }
            { $_ -ge 1MB } { "{0:N2} MB" -f ($_ / 1MB); break }
            { $_ -ge 1KB } { "{0:N2} KB" -f ($_ / 1KB); break }
            default { "$($_.Length) B" }
        }
        [PSCustomObject]@{
            size = $sizeStr
            path = $_.FullName
        }
    }

    # Top 8 extension counts
    $extensionSummary = @{}$allItems | 
    Where-Object { $_.Extension } | 
    Group-Object { $_.Extension.TrimStart('.').ToLowerInvariant() } | 
    Sort-Object Count -Descending | 
    Select-Object -First 8 | 
    ForEach-Object {
        $extensionSummary[$_.Name] = $_.Count
    }

    # Assemble metadata payload
    $metadataObj = [PSCustomObject]@{
        target         = $Dir
        stats          = [PSCustomObject]@{
            total_size      = $formattedSize
            file_count      = $fileCount
            subfolder_count = $dirCount
        }
        largest_files  = @($largestFiles)
        top_extensions = $extensionSummary
    }

    return ($metadataObj | ConvertTo-Json -Depth 4)
}

# --- 4. Main Execution ---
Test-OllamaHealth

$metadataJson = Get-DirectoryMetadata -Dir$targetDir

Write-Host "==> " -ForegroundColor Green -NoNewline
Write-Host "Analyzing metadata with local model " -NoNewline
Write-Host "($Model)..." -ForegroundColor White
Write-Host ""

$systemPrompt = "You are a senior systems administrator and storage optimization consultant. You analyze file metadata and provide a concise, high-signal audit in clean Markdown. Keep prose brief and actionable."

$userPrompt = @"
Analyze this directory inventory and provide:
1. Category breakdown (e.g., node_modules, build artifacts, raw footage, archives, temp files).
2. Storage inefficiencies or redundant file patterns.
3. 2-3 copy-pasteable PowerShell commands to inspect or clean the space safely.

Directory Metadata:
$metadataJson
"@

$payload = [PSCustomObject]@{
    model    = $Model
    messages = @(
        [PSCustomObject]@{ role = "system"; content = $systemPrompt },
        [PSCustomObject]@{ role = "user"; content = $userPrompt }
    )
    stream   = $false
    options  = [PSCustomObject]@{
        temperature = 0.2
    }
} | ConvertTo-Json -Depth 5

try {
    $response = Invoke-RestMethod `
        -Uri "$OllamaHost/api/chat" `
        -Method Post `
        -ContentType "application/json" `
        -Body $payload

    Write-Host "=== Local Directory Audit: $targetDir ===" -ForegroundColor Cyan
    Write-Output $response.message.content
}
catch {
    Write-Host "Inference request failed: $_" -ForegroundColor Red
    exit 1
}