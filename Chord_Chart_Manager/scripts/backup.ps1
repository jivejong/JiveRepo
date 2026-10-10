param(
    [string]$Database,
    [ValidateRange(1,2147483647)][int]$Keep = 14
)
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
$app = Join-Path $root 'app'
$outDir = Join-Path $root 'backups'
$compose = @('--project-directory', $app, '-f', (Join-Path $app 'docker-compose.yml'))
function Invoke-Docker([string[]]$DockerArgs) {
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "docker command failed ($LASTEXITCODE)" }
}
function Get-DbConfig {
    $db = & docker compose @compose exec -T db printenv POSTGRES_DB
    if ($LASTEXITCODE -ne 0 -or -not $db) { throw 'Could not read database name from db container.' }
    $user = & docker compose @compose exec -T db printenv POSTGRES_USER
    if ($LASTEXITCODE -ne 0 -or -not $user) { throw 'Could not read database user from db container.' }
    return @(([string]$db).Trim(), ([string]$user).Trim())
}
$cfg = Get-DbConfig; $defaultDb = $cfg[0]; $dbUser = $cfg[1]
if (-not $Database) { $Database = $defaultDb }
foreach ($identifier in @($Database, $dbUser)) { if ($identifier -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') { throw 'Database/user identifier is invalid.' } }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$container = 't05-' + [guid]::NewGuid().ToString('N') + '.dump'
$stage = Join-Path $outDir ('.' + $container)
$verify = '/tmp/' + $container
$final = Join-Path $outDir "$Database-$stamp.dump"
try {
    Invoke-Docker (@('compose') + $compose + @('exec','-T','-u','postgres','db','pg_dump','-Fc','-U',$dbUser,'-d',$Database,'-f',$verify))
    Invoke-Docker (@('cp', ((& docker compose @compose ps -q db).Trim() + ':' + $verify), $stage))
    if (-not (Test-Path $stage) -or (Get-Item $stage).Length -le 0) { throw 'pg_dump archive is empty.' }
    Invoke-Docker (@('cp', $stage, ((& docker compose @compose ps -q db).Trim() + ':' + $verify)))
    & docker compose @compose exec -T -u postgres db pg_restore --list $verify | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_restore --list failed ($LASTEXITCODE)" }
    Move-Item -LiteralPath $stage -Destination $final
    Get-ChildItem -LiteralPath $outDir -File | Where-Object { $_.Name -match ('^' + [regex]::Escape($Database) + '-\d{8}-\d{6}\.dump$') } | Sort-Object Name -Descending | Select-Object -Skip $Keep | Remove-Item -Force
    Write-Output $final
} finally {
    Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
    & docker compose @compose exec -T db rm -f $verify 2>$null | Out-Null
}
