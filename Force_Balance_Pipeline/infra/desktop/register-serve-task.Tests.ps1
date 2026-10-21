# Pester tests for register-serve-task.ps1 (doc 05, "Serving (Phase 7)"): the running-pull guard, and every property of the task the
# script would register. Dot-sources the script (which, dot-sourced, only defines functions and returns). The guard is exercised
# against a FAKED process list and the task definition is only BUILT, never registered: no process is touched and no Scheduled
# Task is created, changed or removed. Run with: Invoke-Pester infra\desktop\register-serve-task.Tests.ps1 (Pester 3.4.0, ships
# with Windows PowerShell 5.1).

. (Join-Path $PSScriptRoot "register-serve-task.ps1")

function New-FakeProcess($Id, $CommandLine) {
    [pscustomobject]@{ ProcessId = $Id; CommandLine = $CommandLine }
}

Describe "Test-IsServeCommandLine" {
    It "matches the command line the task runs" {
        Test-IsServeCommandLine 'C:\repo\serving\.venv\Scripts\pythonw.exe -m serving.publish' | Should Be $true
    }
    It "matches a console run of the same module" {
        Test-IsServeCommandLine 'C:\repo\serving\.venv\Scripts\python.exe -m serving.publish --no-ping' | Should Be $true
    }
    It "does not match the fixture loader, the schema tool or an unrelated python command line" {
        Test-IsServeCommandLine 'C:\venv\Scripts\python.exe -m serving.fixtures --reset' | Should Be $false
        Test-IsServeCommandLine 'C:\venv\Scripts\python.exe -m serving.schema --recreate' | Should Be $false
        Test-IsServeCommandLine 'C:\venv\Scripts\python.exe -m http.server' | Should Be $false
    }
    It "does not match the agent or the bridge" {
        Test-IsServeCommandLine 'C:\v\pythonw.exe -m agent.run --target prod' | Should Be $false
        Test-IsServeCommandLine 'C:\venv\Scripts\pythonw.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host x' | Should Be $false
    }
    It "does not match a null or empty command line" {
        Test-IsServeCommandLine $null | Should Be $false
        Test-IsServeCommandLine "" | Should Be $false
    }
}

Describe "Get-RunningServeProcesses" {
    It "returns nothing when no process runs the pull" {
        $fake = @((New-FakeProcess 100 'C:\python.exe -m http.server'), (New-FakeProcess 101 'C:\python.exe other_script.py'))
        @(Get-RunningServeProcesses -Processes $fake).Count | Should Be 0
    }

    It "returns exactly the process that runs serving.publish, among others" {
        $fake = @(
            (New-FakeProcess 100 'C:\python.exe -m http.server'),
            (New-FakeProcess 222 'C:\repo\serving\.venv\Scripts\pythonw.exe -m serving.publish'),
            (New-FakeProcess 300 'C:\python.exe unrelated.py')
        )
        $result = @(Get-RunningServeProcesses -Processes $fake)
        $result.Count | Should Be 1
        $result[0].ProcessId | Should Be 222
    }

    It "ignores a process with a null CommandLine (a process CIM couldn't read, not a match)" {
        $fake = @((New-FakeProcess 100 $null), (New-FakeProcess 222 'C:\v\pythonw.exe -m serving.publish'))
        $result = @(Get-RunningServeProcesses -Processes $fake)
        $result.Count | Should Be 1
        $result[0].ProcessId | Should Be 222
    }

    It "looks for pythonw.exe as well as python.exe when it queries the live process list" {
        Mock Get-CimInstance { @() } -ParameterFilter { $Filter -like "*pythonw.exe*" -and $Filter -like "*python.exe*" }
        $null = @(Get-RunningServeProcesses)
        Assert-MockCalled Get-CimInstance -Times 1 -Exactly -ParameterFilter { $Filter -like "*pythonw.exe*" -and $Filter -like "*python.exe*" }
    }
}

Describe "Get-ServePaths" {
    It "defaults the venv to serving\.venv under the repo" {
        $p = Get-ServePaths -RepoPath 'C:\repo'
        $p.PythonwExe | Should Be 'C:\repo\serving\.venv\Scripts\pythonw.exe'
        $p.PythonwExe | Should Match 'serving\\\.venv\\Scripts\\pythonw\.exe$'
        $p.ServeModule | Should Be 'C:\repo\serving\publish.py'
    }
}

Describe "New-ServeTaskDefinition" {
    # Every command that could change a Scheduled Task is mocked BEFORE the definition is first built, and the last test counts
    # the calls the whole Describe made (building $def included).
    Mock Register-ScheduledTask {}
    Mock Unregister-ScheduledTask {}
    Mock Start-ScheduledTask {}
    Mock Stop-ScheduledTask {}
    Mock Set-ScheduledTask {}

    $user = 'CONTOSO\tester'
    $def = New-ServeTaskDefinition -RepoPath 'C:\repo' -User $user -At ([datetime]'2026-10-14T00:12:00')

    It "runs the windowless pythonw.exe with -m serving.publish, from the repo folder" {
        $def.Action.Execute | Should Be 'C:\repo\serving\.venv\Scripts\pythonw.exe'
        $def.Action.Arguments | Should Be '-m serving.publish'
        $def.Action.WorkingDirectory | Should Be 'C:\repo'
    }

    It "uses the venv given with -VenvPath instead of the default serving\.venv" {
        $custom = New-ServeTaskDefinition -RepoPath 'C:\repo' -VenvPath 'D:\venvs\serve' -User $user
        $custom.Action.Execute | Should Be 'D:\venvs\serve\Scripts\pythonw.exe'
    }

    It "has exactly one trigger, a daily one" {
        @($def.Triggers).Count | Should Be 1
        $def.Triggers[0].CimClass.CimClassName | Should Be 'MSFT_TaskDailyTrigger'
    }

    It "starts at 00:12 local time" {
        ([datetime]$def.Triggers[0].StartBoundary).ToString('HH:mm') | Should Be '00:12'
    }

    It "defaults the trigger to minute 12" {
        $default = New-ServeTaskDefinition -RepoPath 'C:\repo' -User $user
        ([datetime]$default.Triggers[0].StartBoundary).Minute | Should Be 12
    }

    It "repeats every hour" {
        $def.Triggers[0].Repetition.Interval | Should Be 'PT1H'
    }

    It "repeats with no end: an empty duration, not [TimeSpan]::MaxValue" {
        $def.Triggers[0].Repetition.Duration | Should BeNullOrEmpty
    }

    It "runs as the named user, interactive, with limited rights (the same as ForceAgent and ForceBridge)" {
        $def.Principal.UserId | Should Be $user
        $def.Principal.LogonType | Should Be 'Interactive'
        $def.Principal.RunLevel | Should Be 'Limited'
    }

    It "ignores an hourly tick while a pull is running" {
        [string]$def.Settings.MultipleInstances | Should Be 'IgnoreNew'
    }

    It "limits one run to 30 minutes" {
        $def.Settings.ExecutionTimeLimit | Should Be 'PT30M'
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
        ($def.Action.Arguments -split ' ').Count | Should Be 2
    }

    # Keep this test last: -Scope Describe counts every call made by every test above it and by building $def.
    It "registered, unregistered, started, stopped or changed no task while the definitions above were built" {
        $null = New-ServeTaskDefinition -RepoPath 'C:\r' -User $user
        Assert-MockCalled Register-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Unregister-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Start-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Stop-ScheduledTask -Times 0 -Exactly -Scope Describe
        Assert-MockCalled Set-ScheduledTask -Times 0 -Exactly -Scope Describe
    }
}

Describe "the registration path of the script" {
    # The missing-pythonw guard sits in the part that runs only when the script is not dot-sourced, so it is checked on the text.
    $text = Get-Content (Join-Path $PSScriptRoot "register-serve-task.ps1") -Raw

    It "refuses to register when pythonw.exe is missing from the venv" {
        $text | Should Match 'if \(-not \(Test-Path \$paths\.PythonwExe\)\) \{\s+throw'
    }
    It "refuses to register a second pull and fails closed" {
        $text | Should Match '\$existing = Get-RunningServeProcesses\s+if \(\$existing\) \{\s+\$pids'
    }
}
