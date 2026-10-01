<#
.SYNOPSIS
Registers a Windows Scheduled Task that starts the MQTT bridge (ingest/bridge/bridge.py, doc 05) at logon.

.DESCRIPTION
Creates (or replaces) a Scheduled Task that runs the bridge under the given Python venv, starting at the next
logon and restarting automatically if it exits. This script does not start the bridge itself unless -StartNow
is passed; by default it only registers the task.

Refuses to register -- or, with -StartNow, to start -- a second bridge if one is already running. The bridge
connects with a fixed MQTT client id ("force-bridge", doc 05: "a fixed client id and clean_session=false", so the
broker queues QoS 1 messages for it while it's down). Two processes sharing that client id would kick each other
off the broker the moment the second one connects, silently, with no error from either side -- exactly the
failure mode this check exists to prevent.

No secrets and no real network addresses live in this file or get written into the registered task's own
definition: the broker address is a required parameter (or $env:BRIDGE_MQTT_HOST), and the bridge's own MQTT
credentials still come from .env.mqtt at the repo root (doc 05), read by the bridge process itself at startup --
never passed on this script's command line or the scheduled task's.

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
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

if (-not $VenvPath) {
    $VenvPath = Join-Path $RepoPath "edge\.venv"
}

if (-not $MqttHost) {
    throw ("MqttHost is required: pass -MqttHost <address> or set `$env:BRIDGE_MQTT_HOST first. There is no " +
           "default -- this script never guesses or embeds a real network address.")
}

$pythonExe = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "No python.exe found at $pythonExe -- create the venv first (edge/requirements.txt), or pass -VenvPath."
}

$bridgeScript = Join-Path $RepoPath "ingest\bridge\bridge.py"
if (-not (Test-Path $bridgeScript)) {
    throw "bridge.py not found at $bridgeScript -- check -RepoPath (expected the Force_Balance_Pipeline folder)."
}

function Get-RunningBridgeProcesses {
    # Matches on the command line, not the executable name alone -- python.exe is shared by every Python task on
    # the machine, so only a command line that actually names bridge.py counts as a running bridge.
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*bridge.py*" }
}

$existing = Get-RunningBridgeProcesses
if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ", "
    throw ("A bridge process is already running (PID $pids). Refusing to register or start a second one -- two " +
           "bridges would share the fixed MQTT client id 'force-bridge' (doc 05) and the newer connection would " +
           "silently disconnect the older one from the broker. Stop the existing process first.")
}

$action = New-ScheduledTaskAction -Execute $pythonExe `
    -Argument ("`"{0}`" --mqtt-host {1}" -f $bridgeScript, $MqttHost) `
    -WorkingDirectory $RepoPath

$trigger = New-ScheduledTaskTrigger -AtLogOn

# Restart on failure: up to 999 times, one minute apart, no overall time limit -- a long-running bridge is meant
# to just keep going. AllowStartIfOnBatteries / DontStopIfGoingOnBatteries: a desktop normally has no battery, but
# this covers a laptop running the Phase 3 staging setup without the bridge dying when it's unplugged.
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description ("Starts the Force Balance Pipeline MQTT bridge (ingest/bridge/bridge.py) at logon; " +
                  "restarts on failure. Registered by register-bridge-task.ps1.") `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName'. It will start the bridge at your next logon."

if ($StartNow) {
    $existing = Get-RunningBridgeProcesses
    if ($existing) {
        throw "A bridge process started between the check above and now -- not starting a second one. Re-run this script."
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started '$TaskName' now."
} else {
    Write-Host "Not started now (pass -StartNow to also start it immediately), or run:"
    Write-Host "  Start-ScheduledTask -TaskName $TaskName"
}
