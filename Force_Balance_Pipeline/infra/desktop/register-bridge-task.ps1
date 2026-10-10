<#
.SYNOPSIS
Registers a Windows Scheduled Task that runs the MQTT bridge (ingest/bridge/bridge.py, doc 05) without a console
window: at your logon, and again every 5 minutes if it is not running.

.DESCRIPTION
Creates (or replaces) a Scheduled Task that runs the bridge under the given Python venv's pythonw.exe (no console
window to close by mistake). Two triggers: your logon, and a repetition every 5 minutes with no end. The task's
MultipleInstances setting is IgnoreNew, so a repetition tick while the bridge is running does nothing, and a tick
after it has exited starts it again. (The task's own RestartCount only covers a failure to START the process, not one
that starts and later exits, so the repetition trigger is what supervises it.) The bridge also holds its own
single-instance lock (logs/bridge.lock) and logs to logs/bridge.log (doc 05, "Bridge auto-start").
This script does not start the bridge itself unless -StartNow is passed; by default it only registers the task.

Refuses to register -- or, with -StartNow, to start -- a second bridge if one is already running. The bridge
connects with a fixed MQTT client id ("force-bridge", doc 05: "a fixed client id and clean_session=false", so the
broker queues QoS 1 messages for it while it's down). Two processes sharing that client id would kick each other
off the broker the moment the second one connects, silently, with no error from either side -- exactly the
failure mode this check exists to prevent. The check itself fails CLOSED: if the process list can't be read at
all, this script stops rather than silently treating "couldn't check" as "nothing is running".

No secrets and no real network addresses live in this file or get written into the registered task's own
definition: the broker address is a required parameter (or $env:BRIDGE_MQTT_HOST), and the bridge's own MQTT
credentials still come from .env.mqtt at the repo root (doc 05), read by the bridge process itself at startup --
never passed on this script's command line or the scheduled task's.

Elevation: run this in an ordinary, NON-elevated PowerShell. The logon trigger names the current user
(-AtLogOn -User DOMAIN\name) and the principal is that user, Interactive, Limited. Proven 2026-10-10 in a
non-elevated shell: that form registers; the same trigger WITHOUT -User (an "any user logon" trigger, which this
script used to create) fails with "Access is denied" (ENGINEERING-LOG.md, Phase 5 Round 2). This script no longer
creates the any-user trigger.

.PARAMETER MqttHost
The broker's address (a LAN IP or hostname). Required unless $env:BRIDGE_MQTT_HOST is already set. Never given a
default here, on purpose -- this script must never guess at or embed a real address.

.PARAMETER RepoPath
Path to the Force_Balance_Pipeline folder. Defaults to this script's own ..\.. (this file lives at
infra/desktop/register-bridge-task.ps1).

.PARAMETER VenvPath
Path to the Python venv the bridge runs under (paho-mqtt installed, edge/requirements.txt). Defaults to
<RepoPath>\edge\.venv -- override with -VenvPath if yours lives elsewhere.

.PARAMETER TaskName
The Scheduled Task's name. Defaults to "ForceBridge".

.PARAMETER User
The account the task runs as and whose logon starts it, as DOMAIN\name. Defaults to the current user.

.PARAMETER StartNow
Also start the task immediately after registering (still refuses if a bridge is already running).

.EXAMPLE
.\register-bridge-task.ps1 -MqttHost 192.0.2.10

.EXAMPLE
.\register-bridge-task.ps1 -MqttHost 192.0.2.10 -StartNow
#>
param(
    [string]$MqttHost = $env:BRIDGE_MQTT_HOST,
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$VenvPath,
    [string]$TaskName = "ForceBridge",
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

function Test-IsBridgeCommandLine {
    # The one piece of matching logic the running-bridge guard depends on, pulled out so a test can exercise it
    # directly without a real process list: true only when the command line actually names bridge.py -- matching
    # on the executable name alone (python.exe) would false-positive on every other Python process on the box.
    param([string]$CommandLine)
    [bool]($CommandLine -and $CommandLine -like "*bridge.py*")
}

function Get-RunningBridgeProcesses {
    # -Processes lets a test inject a fake process list; omitted (the real path), it queries Win32_Process live.
    # No -ErrorAction SilentlyContinue here, on purpose: if the live query itself fails, this must surface as a
    # terminating error (via $ErrorActionPreference above), not be swallowed into "no bridge found" -- a failed
    # check is not the same thing as a clean check that found nothing, and only the second one is safe to act on.
    # python.exe AND pythonw.exe: the scheduled task runs the windowless pythonw.exe, and the venv launcher starts
    # a second process (the base interpreter) with the same command line, so one bridge shows up as two PIDs.
    param([Parameter(ValueFromPipeline)][object[]]$Processes)
    if (-not $PSBoundParameters.ContainsKey('Processes')) {
        $Processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    }
    $Processes | Where-Object { Test-IsBridgeCommandLine $_.CommandLine }
}

function Get-BridgePaths {
    # Where the task's two files are: pythonw.exe (the windowless interpreter; python.exe would open a console window
    # that someone closes at logon -- 0xC000013A on 2026-10-09) and bridge.py.
    param([Parameter(Mandatory)][string]$RepoPath, [string]$VenvPath)
    if (-not $VenvPath) {
        $VenvPath = Join-Path $RepoPath "edge\.venv"
    }
    [pscustomobject]@{
        PythonwExe   = Join-Path $VenvPath "Scripts\pythonw.exe"
        BridgeScript = Join-Path $RepoPath "ingest\bridge\bridge.py"
    }
}

function New-BridgeTaskDefinition {
    # Builds the task's action, triggers, principal and settings and returns them. Nothing is registered, started or
    # written: Register-ScheduledTask is the caller's job. Kept separate so a test can assert every property of what
    # the script would register (Pester tests in register-bridge-task.Tests.ps1).
    param(
        [Parameter(Mandatory)][string]$RepoPath,
        [string]$VenvPath,
        [Parameter(Mandatory)][string]$MqttHost,
        [Parameter(Mandatory)][string]$User,
        [datetime]$Start = (Get-Date)
    )

    $paths = Get-BridgePaths -RepoPath $RepoPath -VenvPath $VenvPath
    $action = New-ScheduledTaskAction -Execute $paths.PythonwExe `
        -Argument ("`"{0}`" --mqtt-host {1}" -f $paths.BridgeScript, $MqttHost) `
        -WorkingDirectory $RepoPath -ErrorAction Stop

    # Logon of THIS user. Without -User the trigger is "any user logs on", which was refused here with "Access is
    # denied" in a non-elevated shell (proven 2026-10-10).
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User -ErrorAction Stop

    # Every 5 minutes, with no end. -RepetitionDuration is deliberately omitted: it makes the duration empty, which
    # means "indefinitely". The documented spelling, -RepetitionDuration ([TimeSpan]::MaxValue), produces
    # P99999999DT23H59M59S, a value some Windows builds reject as out of range when the task is registered.
    $repeatTrigger = New-ScheduledTaskTrigger -Once -At $Start -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -ErrorAction Stop

    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited -ErrorAction Stop

    # IgnoreNew: a repetition tick while the bridge runs is dropped. StartWhenAvailable: a tick missed because the
    # machine was asleep or off runs when it is back. ExecutionTimeLimit 0: no time limit, the bridge is meant to run
    # for weeks. RestartCount/Interval cover only a failure to start the process. AllowStartIfOnBatteries /
    # DontStopIfGoingOnBatteries: a laptop running the staging setup unplugged keeps the bridge.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0) `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ErrorAction Stop

    [pscustomobject]@{
        Action    = $action
        Triggers  = @($logonTrigger, $repeatTrigger)
        Principal = $principal
        Settings  = $settings
    }
}

if ($MyInvocation.InvocationName -eq '.') {
    # Dot-sourced (a test importing the functions above) -- stop here, before any of the real registration logic
    # below runs or any parameter is validated.
    return
}

if (-not $MqttHost) {
    throw ("MqttHost is required: pass -MqttHost <address> or set `$env:BRIDGE_MQTT_HOST first. There is no " +
           "default -- this script never guesses or embeds a real network address.")
}

$paths = Get-BridgePaths -RepoPath $RepoPath -VenvPath $VenvPath
if (-not (Test-Path $paths.PythonwExe)) {
    throw "No pythonw.exe found at $($paths.PythonwExe) -- create the venv first (edge/requirements.txt), or pass -VenvPath."
}
if (-not (Test-Path $paths.BridgeScript)) {
    throw "bridge.py not found at $($paths.BridgeScript) -- check -RepoPath (expected the Force_Balance_Pipeline folder)."
}

$existing = Get-RunningBridgeProcesses
if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ", "
    throw ("A bridge process is already running (PID $pids; one bridge started from a venv shows as two). Refusing " +
           "to register or start a second one -- two bridges would share the fixed MQTT client id 'force-bridge' " +
           "(doc 05) and the newer connection would silently disconnect the older one from the broker. Stop the " +
           "existing process first.")
}

$definition = New-BridgeTaskDefinition -RepoPath $RepoPath -VenvPath $VenvPath -MqttHost $MqttHost -User $User

Register-ScheduledTask -TaskName $TaskName -Action $definition.Action -Trigger $definition.Triggers `
    -Principal $definition.Principal -Settings $definition.Settings `
    -Description ("Runs the Force Balance Pipeline MQTT bridge (ingest/bridge/bridge.py) with no console window: at " +
                  "logon, and every 5 minutes if it is not running. Registered by register-bridge-task.ps1.") `
    -Force -ErrorAction Stop | Out-Null

Write-Host "Registered scheduled task '$TaskName' for $User. It will start the bridge at your next logon (and every 5 minutes if it is not running)."

if ($StartNow) {
    $existing = Get-RunningBridgeProcesses
    if ($existing) {
        throw "A bridge process started between the check above and now -- not starting a second one. Re-run this script."
    }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Started '$TaskName' now."
} else {
    Write-Host "Not started now (pass -StartNow to also start it immediately), or run:"
    Write-Host "  Start-ScheduledTask -TaskName $TaskName"
}
