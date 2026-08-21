[CmdletBinding()]
param([string]$Account = "gafee")

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"'),
        "-Account", ('"' + $Account.Replace('"', '') + '"')
    )
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$test = Join-Path $PSScriptRoot "test_hero_form_guard_live.py"
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"
$log = Join-Path $projectRoot "build\hero-form-guard-live.log"
Set-Location -LiteralPath $projectRoot
& $python $test --account $Account --agent $agent 2>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
