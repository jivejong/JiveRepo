<#
.SYNOPSIS
Registers the Windows Scheduled Task "ForceAgent", which runs one Yoda agent cycle (agent/run.py, doc 06) every hour at :11,
without a console window.

.DESCRIPTION
Creates (or replaces) a Scheduled Task that runs "pythonw.exe -m agent.run --target prod" from the repo folder, under the
given Python venv. One daily trigger at 00:11 local time that repeats every hour with no end, so the cycle starts at :11 past
every hour. The task's MultipleInstances setting is IgnoreNew, and the agent also holds its own single-instance lock
(logs/agent.lock) and logs to logs/agent.log. ExecutionTimeLimit is 45 minutes (a cycle stops starting new incidents after 30).
The cycle itself skips the 03:xx UTC hour and any run in which Job 1 is active, and pings the S4 check (doc 05, "Monitoring").

The principal and logon settings are those of ForceBridge (register-bridge-task.ps1): the named user, Interactive, Limited.
Run this in an ordinary, NON-elevated PowerShell.

No secret lives in this file or in the registered task: the agent reads .env.agent at the repo root itself, at startup, and
nothing is passed on the task's command line. This script does not start the agent unless -StartNow is passed.

Refuses to register -- or, with -StartNow, to start -- a second agent if one is already running (the process list is
checked, and the check fails CLOSED: if it cannot be read, this script stops).

.PARAMETER RepoPath
Path to the Force_Balance_Pipeline folder. Defaults to this script's own ..\.. (this file lives at
infra/desktop/register-agent-task.ps1).

.PARAMETER VenvPath
Path to the Python venv the agent runs under. Defaults to <RepoPath>\edge\.venv.

.PARAMETER TaskName
The Scheduled Task's name. Defaults to "ForceAgent".

.PARAMETER User
The account the task runs as, as DOMAIN\name. Defaults to the current user.

.PARAMETER StartNow
Also start the task immediately after registering (still refuses if an agent is already running).

.EXAMPLE
.\register-agent-task.ps1

.EXAMPLE
.\register-agent-task.ps1 -StartNow
#>
param(
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$VenvPath,
    [string]$TaskName = "ForceAgent",
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

function Test-IsAgentCommandLine {
    # True only when the command line runs the agent module: "-m agent.run". Matching on the executable name alone
    # (python.exe) would false-positive on every other Python process on the box.
    param([string]$CommandLine)
    [bool]($CommandLine -and $CommandLine -like "*-m agent.run*")
}

function Get-RunningAgentProcesses {
    # -Processes lets a test inject a fake process list; omitted (the real path), it queries Win32_Process live. No
    # -ErrorAction SilentlyContinue, on purpose: a failed query must surface as a terminating error, not as "no agent
    # found". python.exe AND pythonw.exe: the task runs pythonw.exe, and a venv launcher starts a second process with
    # the same command line, so one agent can show up as two PIDs.
    param([Parameter(ValueFromPipeline)][object[]]$Processes)
    if (-not $PSBoundParameters.ContainsKey('Processes')) {
        $Processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    }
    $Processes | Where-Object { Test-IsAgentCommandLine $_.CommandLine }
}

function Get-AgentPaths {
    param([Parameter(Mandatory)][string]$RepoPath, [string]$VenvPath)
    if (-not $VenvPath) {
        $VenvPath = Join-Path $RepoPath "edge\.venv"
    }
    [pscustomobject]@{
        PythonwExe = Join-Path $VenvPath "Scripts\pythonw.exe"
        AgentModule = Join-Path $RepoPath "agent\run.py"
    }
}

function New-AgentTaskDefinition {
    # Builds the task's action, trigger, principal and settings and returns them. Nothing is registered, started or
    # written: Register-ScheduledTask is the caller's job. Kept separate so a test can assert every property of what the
    # script would register (Pester tests in register-agent-task.Tests.ps1).
    param(
        [Parameter(Mandatory)][string]$RepoPath,
        [string]$VenvPath,
        [Parameter(Mandatory)][string]$User,
        [datetime]$At = ([datetime]'00:11')
    )

    $paths = Get-AgentPaths -RepoPath $RepoPath -VenvPath $VenvPath
    $action = New-ScheduledTaskAction -Execute $paths.PythonwExe -Argument "-m agent.run --target prod" `
        -WorkingDirectory $RepoPath -ErrorAction Stop

    # A daily trigger at 00:11 local time that repeats every hour with no end. -RepetitionDuration is deliberately
    # omitted so the duration is empty, which means "indefinitely" ([TimeSpan]::MaxValue produces a value some Windows
    # builds reject). A -Daily trigger takes no repetition parameters, so the repetition is copied from a -Once trigger.
    $trigger = New-ScheduledTaskTrigger -Daily -At $At -ErrorAction Stop
    $repetition = New-ScheduledTaskTrigger -Once -At $At -RepetitionInterval (New-TimeSpan -Hours 1) -ErrorAction Stop
    $trigger.Repetition = $repetition.Repetition

    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited -ErrorAction Stop

    # IgnoreNew: an hourly tick while a cycle runs is dropped. StartWhenAvailable: a tick missed while the machine was
    # off runs when it is back. ExecutionTimeLimit 45 minutes: a cycle that has not finished by then is stopped.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ErrorAction Stop

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

$paths = Get-AgentPaths -RepoPath $RepoPath -VenvPath $VenvPath
if (-not (Test-Path $paths.PythonwExe)) {
    throw "No pythonw.exe found at $($paths.PythonwExe) -- create the venv first (edge/requirements.txt), or pass -VenvPath."
}
if (-not (Test-Path $paths.AgentModule)) {
    throw "agent\run.py not found at $($paths.AgentModule) -- check -RepoPath (expected the Force_Balance_Pipeline folder)."
}

$existing = Get-RunningAgentProcesses
if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ", "
    throw ("An agent process is already running (PID $pids). Refusing to register or start a second one; wait for the " +
           "cycle to finish (the agent also holds logs\agent.lock).")
}

$definition = New-AgentTaskDefinition -RepoPath $RepoPath -VenvPath $VenvPath -User $User

Register-ScheduledTask -TaskName $TaskName -Action $definition.Action -Trigger $definition.Triggers `
    -Principal $definition.Principal -Settings $definition.Settings `
    -Description ("Runs one Yoda agent cycle (agent/run.py, doc 06) every hour at :11 with no console window. " +
                  "Registered by register-agent-task.ps1.") `
    -Force -ErrorAction Stop | Out-Null

Write-Host "Registered scheduled task '$TaskName' for $User. It runs a cycle every hour at :11."

if ($StartNow) {
    $existing = Get-RunningAgentProcesses
    if ($existing) {
        throw "An agent process started between the check above and now -- not starting a second one. Re-run this script."
    }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Started '$TaskName' now."
} else {
    Write-Host "Not started now (pass -StartNow to also start it immediately), or run:"
    Write-Host "  Start-ScheduledTask -TaskName $TaskName"
}
