param(
    [switch]$Apply
)
# Import every .docx in pipeline/docs: parse, preview, and (with -Apply) back up,
# load and enrich in one run. Without -Apply nothing in the database changes.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
$app = Join-Path $root 'app'
$compose = @('--project-directory', $app, '-f', (Join-Path $app 'docker-compose.yml'))
function Invoke-Docker([string[]]$DockerArgs) {
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "docker command failed ($LASTEXITCODE)" }
}
function Invoke-Pipeline([string[]]$PipelineArgs) {
    Invoke-Docker (@('compose') + $compose + @('--profile', 'pipeline', 'run', '--rm', 'pipeline') + $PipelineArgs)
}

Invoke-Docker (@('compose') + $compose + @('--profile', 'pipeline', 'build', 'pipeline'))
Invoke-Pipeline @('python', 'batch_parse.py', 'docs', '-o', 'parsed_out', '--per-doc')
Write-Output 'Review pipeline/parsed_out/report.json against the source documents.'
Invoke-Pipeline @('python', 'load_songs.py', 'parsed_out/songs.json', '--dry-run')

if (-not $Apply) {
    Write-Output ''
    Write-Output 'Preview only; the database was not changed. Review the output above, then rerun with -Apply.'
    return
}

Write-Output ''
Write-Output 'Sync every device first (Settings shows 0 pending): enrichment changes songs, and a queued tablet edit to the same song is replaced by the server copy.'
& (Join-Path $scriptDir 'backup.ps1')  # throws on failure, so nothing is loaded without a backup
Invoke-Pipeline @('python', 'load_songs.py', 'parsed_out/songs.json', '--enrich')
