# Pester tests for register-agent-task.ps1 (doc 06, "Runtime"): the running-agent guard, and every property of the task the
# script would register. Dot-sources the script (which, dot-sourced, only defines functions and returns). The guard is exercised
# against a FAKED process list and the task definition is only BUILT, never registered: no process is touched and no Scheduled
# Task is created, changed or removed. Run with: Invoke-Pester infra\desktop\register-agent-task.Tests.ps1 (Pester 3.4.0, ships
# with Windows PowerShell 5.1).

. (Join-Path $PSScriptRoot "register-agent-task.ps1")

function New-FakeProcess($Id, $CommandLine) {
    [pscustomobject]@{ ProcessId = $Id; CommandLine = $CommandLine }
}

Describe "Test-IsAgentCommandLine" {
    It "matches the command line the task runs" {
        Test-IsAgentCommandLine 'C:\repo\edge\.venv\Scripts\pythonw.exe -m agent.run --target prod' | Should Be $true
    }
    It "matches a console run of the same module" {
        Test-IsAgentCommandLine 'C:\repo\edge\.venv\Scripts\python.exe -m agent.run --target dev --no-ping' | Should Be $true
    }
    It "does not match the eval runner or an unrelated python command line" {
        Test-IsAgentCommandLine 'C:\venv\Scripts\python.exe -m agent.eval --runs 3' | Should Be $false
        Test-IsAgentCommandLine 'C:\venv\Scripts\python.exe -m http.server' | Should Be $false
    }
    It "does not match the bridge" {
        Test-IsAgentCommandLine 'C:\venv\Scripts\pythonw.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host x' | Should Be $false
    }
    It "does not match a null or empty command line" {
        Test-IsAgentCommandLine $null | Should Be $false
        Test-IsAgentCommandLine "" | Should Be $false
    }
}

Describe "Get-RunningAgentProcesses" {
    It "returns nothing when no process runs the agent" {
        $fake = @((New-FakeProcess 100 'C:\python.exe -m http.server'), (New-FakeProcess 101 'C:\python.exe other_script.py'))
        @(Get-RunningAgentProcesses -Processes $fake).Count | Should Be 0
    }

    It "returns exactly the process that runs agent.run, among others" {
        $fake = @(
            (New-FakeProcess 100 'C:\python.exe -m http.server'),
            (New-FakeProcess 222 'C:\repo\edge\.venv\Scripts\pythonw.exe -m agent.run --target prod'),
            (New-FakeProcess 300 'C:\python.exe unrelated.py')
        )
        $result = @(Get-RunningAgentProcesses -Processes $fake)
        $result.Count | Should Be 1
        $result[0].ProcessId | Should Be 222
    }

    It "ignores a process with a null CommandLine (a process CIM couldn't read, not a match)" {
        $fake = @((New-FakeProcess 100 $null), (New-FakeProcess 222 'C:\v\pythonw.exe -m agent.run --target prod'))
        $result = @(Get-RunningAgentProcesses -Processes $fake)
        $result.Count | Should Be 1
        $result[0].ProcessId | Should Be 222
    }

    It "looks for pythonw.exe as well as python.exe when it queries the live process list" {
        Mock Get-CimInstance { @() } -ParameterFilter { $Filter -like "*pythonw.exe*" -and $Filter -like "*python.exe*" }
        $null = @(Get-RunningAgentProcesses)
        Assert-MockCalled Get-CimInstance -Times 1 -Exactly -ParameterFilter { $Filter -like "*pythonw.exe*" -and $Filter -like "*python.exe*" }
    }
}

Describe "New-AgentTaskDefinition" {
    # Every command that could change a Scheduled Task is mocked BEFORE the definition is first built, and the last test counts
    # the calls the whole Describe made (building $def included).
    Mock Register-ScheduledTask {}
    Mock Unregister-ScheduledTask {}
    Mock Start-ScheduledTask {}
    Mock Stop-ScheduledTask {}
    Mock Set-ScheduledTask {}

    $user = 'CONTOSO\tester'
    $def = New-AgentTaskDefinition -RepoPath 'C:\repo' -User $user -At ([datetime]'2026-10-14T00:15:00')

    It "runs the windowless pythonw.exe with -m agent.run --target prod, from the repo folder" {
        $def.Action.Execute | Should Be 'C:\repo\edge\.venv\Scripts\pythonw.exe'
        $def.Action.Arguments | Should Be '-m agent.run --target prod'
        $def.Action.WorkingDirectory | Should Be 'C:\repo'
    }

    It "uses the venv given with -VenvPath instead of the default edge\.venv" {
        $custom = New-AgentTaskDefinition -RepoPath 'C:\repo' -VenvPath 'D:\venvs\agent' -User $user
        $custom.Action.Execute | Should Be 'D:\venvs\agent\Scripts\pythonw.exe'
    }

    It "has exactly one trigger, a daily one" {
        @($def.Triggers).Count | Should Be 1
        $def.Triggers[0].CimClass.CimClassName | Should Be 'MSFT_TaskDailyTrigger'
    }

    It "starts at 00:15 local time" {
        ([datetime]$def.Triggers[0].StartBoundary).ToString('HH:mm') | Should Be '00:15'
    }

    It "repeats every hour" {
        $def.Triggers[0].Repetition.Interval | Should Be 'PT1H'
    }

    It "repeats with no end: an empty duration, not [TimeSpan]::MaxValue" {
        $def.Triggers[0].Repetition.Duration | Should BeNullOrEmpty
    }

    It "runs as the named user, interactive, with limited rights (the same as ForceBridge)" {
        $def.Principal.UserId | Should Be $user
        $def.Principal.LogonType | Should Be 'Interactive'
        $def.Principal.RunLevel | Should Be 'Limited'
    }

    It "ignores an hourly tick while a cycle is running" {
        [string]$def.Settings.MultipleInstances | Should Be 'IgnoreNew'
    }

    It "limits one run to 45 minutes" {
        $def.Settings.ExecutionTimeLimit | Should Be 'PT45M'
    }

    It "catches up a tick missed while the machine was off" {
        $def.Settings.StartWhenAvailable | Should Be $true
    }

    It "keeps running on battery power" {
        $def.Settings.DisallowStartIfOnBatteries | Should Be $false
        $def.Settings.StopIfGoingOnBatteries | Should Be $false
    }

    It "puts no address, credential or environment file in the task definition" {
        $def.Action.Arguments | Should Not Match '(?i)password|secret|token|key|\.env|http'
        ($def.Action.Arguments -split ' ').Count | Should Be 4
    }

    # Keep this test last: -Scope Describe counts every call made by every test above it and by building $def.
    It "registered, unregistered, started, stopped or changed no task while the definitions above were built" {
        $null = New-AgentTaskDefinition -RepoPath 'C:\r' -User $user
        Assert-MockCalled Register-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Unregister-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Start-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Stop-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Set-ScheduledTask -Times 0 -Exactly -Scope Describe
    }
}
