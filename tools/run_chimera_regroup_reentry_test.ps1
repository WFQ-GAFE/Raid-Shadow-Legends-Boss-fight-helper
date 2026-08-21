[CmdletBinding()]
param(
    [string]$Account = "gafee",
    [UInt64]$SeedContext = 0,
    [UInt64]$SeedSelectionContext = 0,
    [switch]$ResumeAfterRegroup
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $elevatedArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-Account", ('"' + $Account.Replace('"', '') + '"'),
        "-SeedContext", $SeedContext.ToString(),
        "-SeedSelectionContext", $SeedSelectionContext.ToString()
    )
    if ($ResumeAfterRegroup) {
        $elevatedArguments += "-ResumeAfterRegroup"
    }
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $elevatedArguments
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$test = Join-Path $PSScriptRoot "test_chimera_regroup_reentry.py"
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"
$log = Join-Path $projectRoot "build\chimera-regroup-reentry-test.log"

Set-Location -LiteralPath $projectRoot
$arguments = @(
    $test, "--account", $Account, "--agent", $agent,
    "--seed-context", $SeedContext,
    "--seed-selection-context", $SeedSelectionContext
)
if ($ResumeAfterRegroup) {
    $arguments += "--resume-after-regroup"
}
& $python @arguments 2>&1 |
    Tee-Object -FilePath $log
exit $LASTEXITCODE
