[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$RaidPid,
    [string]$AccountName = "gafee",
    [Int64]$UserId = 95815853
)

$ErrorActionPreference = "Stop"

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
        "-RaidPid", $RaidPid,
        "-AccountName", ('"' + $AccountName.Replace('"', '') + '"'),
        "-UserId", $UserId
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $elevatedArguments
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$worker = Join-Path $PSScriptRoot "test_manual_interrupt_bound_live.py"
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"
$output = Join-Path $projectRoot "build\gafee-manual-interrupt-live.json"

& $python $worker `
    --pid $RaidPid `
    --account-name $AccountName `
    --user-id $UserId `
    --agent $agent `
    --output $output
exit $LASTEXITCODE
