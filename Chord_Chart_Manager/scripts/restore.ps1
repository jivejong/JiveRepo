param(
    [Parameter(Mandatory=$true)][string]$Path,
    [string]$Database,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
if (-not $Force) { throw 'Restore refused: pass -Force to confirm.' }
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
$app = Join-Path $root 'app'
$compose = @('--project-directory', $app, '-f', (Join-Path $app 'docker-compose.yml'))
function Run([string[]]$DockerArgs) { & docker @DockerArgs; if ($LASTEXITCODE -ne 0) { throw "docker command failed ($LASTEXITCODE)" } }
$file = (Resolve-Path -LiteralPath $Path).Path
if ((Get-Item -LiteralPath $file).Length -le 0) { throw 'Archive is empty.' }
$defaultDb = & docker compose @compose exec -T db printenv POSTGRES_DB
if ($LASTEXITCODE -ne 0 -or -not $defaultDb) { throw 'Could not read database name.' }
$user = & docker compose @compose exec -T db printenv POSTGRES_USER
if ($LASTEXITCODE -ne 0 -or -not $user) { throw 'Could not read database user.' }
$db = if ($Database) { $Database } else { ([string]$defaultDb).Trim() }; $user = ([string]$user).Trim()
foreach ($identifier in @($db,$user)) { if ($identifier -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') { throw 'Database/user identifier is invalid.' } }
$token = 't05-restore-' + [guid]::NewGuid().ToString('N') + '.dump'; $inside = '/tmp/' + $token
$cid = (& docker compose @compose ps -q db).Trim()
try {
    Run @('cp',$file,"${cid}:$inside")
    & docker compose @compose exec -T -u postgres db pg_restore --list $inside | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_restore --list failed ($LASTEXITCODE)" }
    $backup = Join-Path $scriptDir 'backup.ps1'
    $safety = & $backup -Database $db -Keep 2147483647
    if ($LASTEXITCODE -ne 0 -or -not $safety) { throw 'Safety backup failed; restore stopped.' }
    Run ((@('compose') + $compose + @('exec','-T','-u','postgres','db','pg_restore','--clean','--if-exists','--exit-on-error','-U',$user,'-d',$db,$inside)))
    Write-Output "Restore completed for $db. Safety backup: $safety"
} finally { & docker compose @compose exec -T db rm -f $inside 2>$null | Out-Null }
