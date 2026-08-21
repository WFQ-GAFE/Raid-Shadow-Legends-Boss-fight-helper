[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$RaidPid,
    [Parameter(Mandatory = $true)][string]$AccountName,
    [Parameter(Mandatory = $true)][Int64]$UserId,
    [Parameter(Mandatory = $true)][string]$Agent,
    [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-RaidPid", $RaidPid,
        "-AccountName", ('"' + $AccountName.Replace('"', '') + '"'),
        "-UserId", $UserId,
        "-Agent", ('"' + $Agent.Replace('"', '') + '"'),
        "-Output", ('"' + $Output.Replace('"', '') + '"')
    )
    $elevated = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    exit $elevated.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$worker = Join-Path $PSScriptRoot "reload_bound_agent.py"
Set-Location -LiteralPath $projectRoot
& $python $worker --pid $RaidPid --account-name $AccountName --user-id $UserId --agent $Agent --output $Output
exit $LASTEXITCODE
