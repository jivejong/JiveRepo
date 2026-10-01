# Pester tests for register-bridge-task.ps1's running-bridge guard (doc 05, "Bridge auto-start"). Dot-sources the
# script (which, dot-sourced, only defines functions and returns -- see the script's own $MyInvocation guard) and
# exercises Get-RunningBridgeProcesses / Test-IsBridgeCommandLine against a FAKED process list -- no real process
# is touched, no Scheduled Task is registered. Run with: Invoke-Pester (Pester 3.4.0, ships with Windows
# PowerShell 5.1 -- this repo adds no new dependency).

. (Join-Path $PSScriptRoot "register-bridge-task.ps1")

function New-FakeProcess($Id, $CommandLine) {
    [pscustomobject]@{ ProcessId = $Id; CommandLine = $CommandLine }
}

Describe "Test-IsBridgeCommandLine" {
    It "matches a command line that names bridge.py" {
        Test-IsBridgeCommandLine 'C:\venv\Scripts\python.exe "C:\repo\ingest\bridge\bridge.py" --mqtt-host x' | Should Be $true
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
}
