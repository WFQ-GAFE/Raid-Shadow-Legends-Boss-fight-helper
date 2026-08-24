[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$RaidPid,
    [string]$AccountName = "gafee",
    [Int64]$UserId = 95815853,
    [int]$MaxCommands = 5,
    [string]$Config = "",
    [switch]$ManualInterruptOnly
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
        "-UserId", $UserId,
        "-MaxCommands", $MaxCommands
    )
    if ($Config) {
        $elevatedArguments += @("-Config", ('"' + $Config.Replace('"', '') + '"'))
    }
    if ($ManualInterruptOnly) {
        $elevatedArguments += "-ManualInterruptOnly"
    }
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $elevatedArguments
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$controller = Join-Path $PSScriptRoot "chimera_controller.py"
$config = if ($Config) { $Config } else { Join-Path $projectRoot "config\chimera-live-autotest.json" }
$agent = Join-Path $projectRoot "build\agent-1236\Release\RaidChimeraAgent.dll"
$testName = if ($ManualInterruptOnly) { "live-manual-interrupt" } else { "live-auto-start" }
$stdout = Join-Path $projectRoot "build\$testName-$RaidPid.out.log"
$stderr = Join-Path $projectRoot "build\$testName-$RaidPid.err.log"
$status = Join-Path $projectRoot "build\$testName-$RaidPid.status.json"

$arguments = @(
    ('"' + $controller + '"'),
    "--pid", $RaidPid,
    "--account-name", ('"' + $AccountName + '"'),
    "--account-user-id", $UserId,
    "--config", ('"' + $config + '"'),
    "--agent", ('"' + $agent + '"'),
    "--bootstrap-current"
)
if (-not $ManualInterruptOnly) {
    $arguments += @("--execute", "--auto-start", "--max-commands", $MaxCommands)
}

$process = Start-Process -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

@{
    raidPid = $RaidPid
    accountName = $AccountName
    userId = $UserId
    controllerPid = $process.Id
    testMode = if ($ManualInterruptOnly) { "manual-interrupt" } else { "auto-start" }
    maxCommands = $MaxCommands
    stdout = $stdout
    stderr = $stderr
    launchedAt = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $status -Encoding UTF8
