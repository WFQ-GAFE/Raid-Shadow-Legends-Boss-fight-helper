[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$RaidPid,
    [Parameter(Mandatory = $true)][string]$AccountName,
    [Parameter(Mandatory = $true)][Int64]$UserId,
    [Parameter(Mandatory = $true)][string]$Agent,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][string]$HeroIds
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
        "-Output", ('"' + $Output.Replace('"', '') + '"'),
        "-HeroIds", ('"' + $HeroIds.Replace('"', '') + '"')
    )
    $elevated = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    exit $elevated.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$worker = Join-Path $PSScriptRoot "test_select_team_live.py"
$parsedHeroIds = @($HeroIds -split ',' | ForEach-Object { [int]$_.Trim() })
if ($parsedHeroIds.Count -ne 5) {
    throw "Exactly five hero IDs are required."
}
$arguments = @(
    $worker,
    "--pid", $RaidPid,
    "--account-name", $AccountName,
    "--user-id", $UserId,
    "--agent", $Agent,
    "--output", $Output
)
foreach ($heroId in $parsedHeroIds) {
    $arguments += @("--hero-id", $heroId)
}
Set-Location -LiteralPath $projectRoot
& $python @arguments
exit $LASTEXITCODE
