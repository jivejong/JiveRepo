<#
.SYNOPSIS
Registers the Windows Scheduled Task "ForceServe", which runs one serving pull to the local Postgres (serving/publish.py, doc 05
"Serving (Phase 7)") every hour at :17, without a console window.

.DESCRIPTION
Creates (or replaces) a Scheduled Task that runs "pythonw.exe -m serving.publish" from the repo folder, under the serving venv
(serving\.venv). One daily trigger at 00:17 local time that repeats every hour with no end, so the pull starts at :17 past every hour.
The task's MultipleInstances setting is IgnoreNew, and serving.publish also holds its own single-instance lock (logs/serve.lock) and
logs to logs/serve.log. ExecutionTimeLimit is 30 minutes. The pull itself skips the 03:xx UTC hour, waits up to 10 minutes for the
agent's cycle (logs/agent.lock) to finish, and pings the S5 check (doc 05, "Monitoring").

The principal and logon settings are those of ForceAgent (register-agent-task.ps1) and ForceBridge: the named user, Interactive,
Limited. Run this in an ordinary, NON-elevated PowerShell.

No secret lives in this file or in the registered task: serving.publish reads .env.serving at the repo root itself, at startup, and
nothing is passed on the task's command line. This script does not start the pull unless -StartNow is passed.

Refuses to register -- or, with -StartNow, to start -- a second pull if one is already running (the process list is checked, and the
check fails CLOSED: if it cannot be read, this script stops).

.PARAMETER RepoPath
Path to the Force_Balance_Pipeline folder. Defaults to this script's own ..\.. (this file lives at
infra/desktop/register-serve-task.ps1).

.PARAMETER VenvPath
Path to the Python venv the pull runs under. Defaults to <RepoPath>\serving\.venv.

.PARAMETER TaskName
The Scheduled Task's name. Defaults to "ForceServe".

.PARAMETER User
The account the task runs as, as DOMAIN\name. Defaults to the current user.

.PARAMETER StartNow
Also start the task immediately after registering (still refuses if a pull is already running).

.EXAMPLE
.\register-serve-task.ps1

.EXAMPLE
.\register-serve-task.ps1 -StartNow
#>
param(
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$VenvPath,
    [string]$TaskName = "ForceServe",
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

function Test-IsServeCommandLine {
    # True only when the command line runs the publish module: "-m serving.publish". Matching on the executable name alone
    # (python.exe) would false-positive on every other Python process on the box.
    param([string]$CommandLine)
    [bool]($CommandLine -and $CommandLine -like "*-m serving.publish*")
}

function Get-RunningServeProcesses {
    # -Processes lets a test inject a fake process list; omitted (the real path), it queries Win32_Process live. No
    # -ErrorAction SilentlyContinue, on purpose: a failed query must surface as a terminating error, not as "no pull
    # found". python.exe AND pythonw.exe: the task runs pythonw.exe, and a venv launcher starts a second process with
    # the same command line, so one pull can show up as two PIDs.
    param([Parameter(ValueFromPipeline)][object[]]$Processes)
    if (-not $PSBoundParameters.ContainsKey('Processes')) {
        $Processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    }
    $Processes | Where-Object { Test-IsServeCommandLine $_.CommandLine }
}

function Get-ServePaths {
    param([Parameter(Mandatory)][string]$RepoPath, [string]$VenvPath)
    if (-not $VenvPath) {
        $VenvPath = Join-Path $RepoPath "serving\.venv"
    }
    [pscustomobject]@{
        PythonwExe = Join-Path $VenvPath "Scripts\pythonw.exe"
        ServeModule = Join-Path $RepoPath "serving\publish.py"
    }
}

function New-ServeTaskDefinition {
    # Builds the task's action, trigger, principal and settings and returns them. Nothing is registered, started or
    # written: Register-ScheduledTask is the caller's job. Kept separate so a test can assert every property of what the
    # script would register (Pester tests in register-serve-task.Tests.ps1).
    param(
        [Parameter(Mandatory)][string]$RepoPath,
        [string]$VenvPath,
        [Parameter(Mandatory)][string]$User,
        [datetime]$At = ([datetime]'00:17')
    )

    $paths = Get-ServePaths -RepoPath $RepoPath -VenvPath $VenvPath
    $action = New-ScheduledTaskAction -Execute $paths.PythonwExe -Argument "-m serving.publish" `
        -WorkingDirectory $RepoPath -ErrorAction Stop

    # A daily trigger at 00:17 local time that repeats every hour with no end. -RepetitionDuration is deliberately
    # omitted so the duration is empty, which means "indefinitely" ([TimeSpan]::MaxValue produces a value some Windows
    # builds reject). A -Daily trigger takes no repetition parameters, so the repetition is copied from a -Once trigger.
    $trigger = New-ScheduledTaskTrigger -Daily -At $At -ErrorAction Stop
    $repetition = New-ScheduledTaskTrigger -Once -At $At -RepetitionInterval (New-TimeSpan -Hours 1) -ErrorAction Stop
    $trigger.Repetition = $repetition.Repetition

    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited -ErrorAction Stop

    # IgnoreNew: an hourly tick while a pull runs is dropped. StartWhenAvailable: a tick missed while the machine was
    # off runs when it is back. ExecutionTimeLimit 30 minutes: a pull that has not finished by then is stopped.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ErrorAction Stop

    [pscustomobject]@{
        Action    = $action
        Triggers  = @($trigger)
        Principal = $principal
        Settings  = $settings
    }
}

if ($MyInvocation.InvocationName -eq '.') {
    # Dot-sourced (a test importing the functions above) -- stop here, before any of the real registration logic runs.
    return
}

$paths = Get-ServePaths -RepoPath $RepoPath -VenvPath $VenvPath
if (-not (Test-Path $paths.PythonwExe)) {
    throw "No pythonw.exe found at $($paths.PythonwExe) -- create the serving venv first (serving/requirements.txt), or pass -VenvPath."
}
if (-not (Test-Path $paths.ServeModule)) {
    throw "serving\publish.py not found at $($paths.ServeModule) -- check -RepoPath (expected the Force_Balance_Pipeline folder)."
}

$existing = Get-RunningServeProcesses
if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ", "
    throw ("A serving pull is already running (PID $pids). Refusing to register or start a second one; wait for the " +
           "pull to finish (it also holds logs\serve.lock).")
}

$definition = New-ServeTaskDefinition -RepoPath $RepoPath -VenvPath $VenvPath -User $User

Register-ScheduledTask -TaskName $TaskName -Action $definition.Action -Trigger $definition.Triggers `
    -Principal $definition.Principal -Settings $definition.Settings `
    -Description "Force Balance Pipeline: hourly serving pull to local Postgres (doc 05, Serving)" `
    -Force -ErrorAction Stop | Out-Null

Write-Host "Registered scheduled task '$TaskName' for $User. It runs a pull every hour at :17."

if ($StartNow) {
    $existing = Get-RunningServeProcesses
    if ($existing) {
        throw "A serving pull started between the check above and now -- not starting a second one. Re-run this script."
    }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Started '$TaskName' now."
} else {
    Write-Host "Not started now (pass -StartNow to also start it immediately), or run:"
    Write-Host "  Start-ScheduledTask -TaskName $TaskName"
}
