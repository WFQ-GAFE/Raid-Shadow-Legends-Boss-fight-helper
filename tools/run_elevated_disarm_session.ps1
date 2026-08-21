[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$RaidPid,
    [Parameter(Mandatory = $true)]
    [UInt64]$SessionId
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
        "-SessionId", $SessionId
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $elevatedArguments
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$probe = Join-Path $PSScriptRoot "inject_probe.py"
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"

& $python $probe `
    --pid $RaidPid `
    --agent $agent `
    --end-takeover `
    --session-id $SessionId
exit $LASTEXITCODE
