# Pester tests for register-bridge-task.ps1 (doc 05, "Bridge auto-start"): the running-bridge guard, and every
# property of the task the script would register. Dot-sources the script (which, dot-sourced, only defines functions
# and returns -- see the script's own $MyInvocation guard). The guard is exercised against a FAKED process list and the
# task definition is only BUILT, never registered: no process is touched and no Scheduled Task is created, changed or
# removed. Run with: Invoke-Pester (Pester 3.4.0, ships with Windows PowerShell 5.1 -- this repo adds no new
# dependency).

. (Join-Path $PSScriptRoot "register-bridge-task.ps1")

function New-FakeProcess($Id, $CommandLine) {
    [pscustomobject]@{ ProcessId = $Id; CommandLine = $CommandLine }
}

Describe "Test-IsBridgeCommandLine" {
    It "matches a command line that names bridge.py" {
        Test-IsBridgeCommandLine 'C:\venv\Scripts\python.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host x' | Should Be $true
    }
    It "matches the windowless pythonw.exe the task runs" {
        Test-IsBridgeCommandLine 'C:\venv\Scripts\pythonw.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host x' | Should Be $true
    }
    It "does not match an unrelated python command line" {
        Test-IsBridgeCommandLine 'C:\venv\Scripts\python.exe -m http.server' | Should Be $false
    }
    It "does not match a null or empty command line" {
        Test-IsBridgeCommandLine $null | Should Be $false
        Test-IsBridgeCommandLine "" | Should Be $false
    }
}

Describe "Get-RunningBridgeProcesses" {
    It "returns nothing when no process names bridge.py" {
        $fake = @((New-FakeProcess 100 'C:\python.exe -m http.server'), (New-FakeProcess 101 'C:\python.exe other_script.py'))
        @(Get-RunningBridgeProcesses -Processes $fake).Count | Should Be 0
    }

    It "returns exactly the process whose command line names bridge.py, among others" {
        # Each call is parenthesized deliberately: inside an @(...) array literal, PowerShell parses unparenthesized
        # space-separated positional arguments greedily across commas, so "f a, f b" silently becomes one call to f
        # with an array-valued second argument instead of two separate calls -- confirmed the hard way while writing
        # this file (an earlier, unparenthesized version of this test produced a 1-element array, not 3).
        $fake = @(
            (New-FakeProcess 100 'C:\python.exe -m http.server'),
            (New-FakeProcess 222 'C:\venv\Scripts\python.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host 192.0.2.10'),
            (New-FakeProcess 300 'C:\python.exe unrelated.py')
        )
        $fake.Count | Should Be 3
        $result = @(Get-RunningBridgeProcesses -Processes $fake)
        $result.Count | Should Be 1
        $result[0].ProcessId | Should Be 222
    }

    It "returns every match when more than one bridge process is somehow running" {
        $fake = @(
            (New-FakeProcess 222 'C:\venv\Scripts\python.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host a'),
            (New-FakeProcess 333 'C:\venv\Scripts\python.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host b')
        )
        @(Get-RunningBridgeProcesses -Processes $fake).Count | Should Be 2
    }

    It "ignores a process with a null CommandLine (a process CIM couldn't read, not a match)" {
        $fake = @(New-FakeProcess 400 $null)
        @(Get-RunningBridgeProcesses -Processes $fake).Count | Should Be 0
    }

    It "looks for pythonw.exe as well as python.exe when it queries the live process list" {
        # The live query goes through Get-CimInstance and cannot be faked here, so this reads the script's own text:
        # the task runs pythonw.exe, and a guard that only looked at python.exe would never see the running bridge.
        (Get-Content -Raw (Join-Path $PSScriptRoot "register-bridge-task.ps1")) |
            Should Match "Win32_Process -Filter ""Name = 'python\.exe' OR Name = 'pythonw\.exe'"""
    }
}

Describe "New-BridgeTaskDefinition" {
    $user = 'CONTOSO\tester'
    $def = New-BridgeTaskDefinition -RepoPath 'C:\repo' -MqttHost 'broker.invalid' -User $user `
        -Start ([datetime]'2026-10-10T12:00:00')

    It "builds the definition without registering, starting or changing any task" {
        Mock Register-ScheduledTask {}
        Mock Start-ScheduledTask {}
        Mock Unregister-ScheduledTask {}
        $null = New-BridgeTaskDefinition -RepoPath 'C:\r' -MqttHost 'h.invalid' -User $user
        Assert-MockCalled Register-ScheduledTask -Times 0
        Assert-MockCalled Start-ScheduledTask -Times 0
        Assert-MockCalled Unregister-ScheduledTask -Times 0
    }

    It "runs the windowless pythonw.exe on bridge.py with the broker host, from the repo folder" {
        $def.Action.Execute | Should Be 'C:\repo\edge\.venv\Scripts\pythonw.exe'
        $def.Action.Arguments | Should Be '"C:\repo\ingest\bridge\bridge.py" --mqtt-host broker.invalid'
        $def.Action.WorkingDirectory | Should Be 'C:\repo'
    }

    It "uses the venv given with -VenvPath instead of the default edge\.venv" {
        $custom = New-BridgeTaskDefinition -RepoPath 'C:\repo' -VenvPath 'D:\venvs\bridge' -MqttHost 'broker.invalid' -User $user
        $custom.Action.Execute | Should Be 'D:\venvs\bridge\Scripts\pythonw.exe'
    }

    It "has exactly two triggers: logon and a repetition" {
        @($def.Triggers).Count | Should Be 2
    }

    It "starts at the logon of the named user, not of any user (an any-user logon trigger was refused without elevation)" {
        $def.Triggers[0].CimClass.CimClassName | Should Be 'MSFT_TaskLogonTrigger'
        $def.Triggers[0].UserId | Should Be $user
    }

    It "repeats every 5 minutes" {
        $def.Triggers[1].CimClass.CimClassName | Should Be 'MSFT_TaskTimeTrigger'
        $def.Triggers[1].Repetition.Interval | Should Be 'PT5M'
    }

    It "repeats with no end: an empty duration, not [TimeSpan]::MaxValue (P99999999DT23H59M59S is out of range on some builds)" {
        $def.Triggers[1].Repetition.Duration | Should BeNullOrEmpty
    }

    It "runs as the named user, interactive, with limited rights" {
        $def.Principal.UserId | Should Be $user
        $def.Principal.LogonType | Should Be 'Interactive'
        $def.Principal.RunLevel | Should Be 'Limited'
    }

    It "ignores a repetition tick while an instance is running" {
        [string]$def.Settings.MultipleInstances | Should Be 'IgnoreNew'
    }

    It "catches up a tick missed while the machine was off" {
        $def.Settings.StartWhenAvailable | Should Be $true
    }

    It "has no execution time limit" {
        $def.Settings.ExecutionTimeLimit | Should Be 'PT0S'
    }

    It "restarts a failed START up to 999 times, a minute apart" {
        $def.Settings.RestartCount | Should Be 999
        $def.Settings.RestartInterval | Should Be 'PT1M'
    }

    It "keeps running on battery power" {
        $def.Settings.DisallowStartIfOnBatteries | Should Be $false
        $def.Settings.StopIfGoingOnBatteries | Should Be $false
    }

    It "puts no address or credential in the task definition beyond the broker host argument" {
        ($def.Action.Arguments -split ' ').Count | Should Be 3
        $def.Action.Arguments | Should Not Match '(?i)password|secret|token'
    }
}
