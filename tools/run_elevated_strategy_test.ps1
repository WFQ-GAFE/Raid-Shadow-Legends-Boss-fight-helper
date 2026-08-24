[CmdletBinding()]
param(
    [string]$Account = "gafee",
    [Parameter(Mandatory = $true)][string]$Config,
    [ValidateRange(1, 1000)][int]$MaxCommands = 1,
    [string]$Log = "",
    [switch]$AutoStart
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-Account", ('"' + $Account.Replace('"', '') + '"'),
        "-Config", ('"' + $Config.Replace('"', '') + '"'),
        "-MaxCommands", $MaxCommands
    )
    if ($Log) {
        $arguments += @("-Log", ('"' + $Log.Replace('"', '') + '"'))
    }
    if ($AutoStart) {
        $arguments += "-AutoStart"
    }
    $elevated = Start-Process -FilePath "powershell.exe" -Verb RunAs `
        -WindowStyle Hidden -Wait -PassThru -ArgumentList $arguments
    exit $elevated.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$controller = Join-Path $PSScriptRoot "chimera_controller.py"
$agent = Join-Path $projectRoot "build\agent-1236\Release\RaidChimeraAgent.dll"
$configPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $Config))
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Strategy config does not exist: $configPath"
}
if (-not $Log) {
    $Log = Join-Path $projectRoot "build\chimera-strategy-test.log"
} elseif (-not [IO.Path]::IsPathRooted($Log)) {
    $Log = Join-Path $projectRoot $Log
}

Set-Location -LiteralPath $projectRoot
$escapedAccount = $Account.Replace("'", "''")
$resolver = @"
import sys
sys.path.insert(0, r'$PSScriptRoot')
from agent_ipc import AgentIpc, SHARED_STATE_VERSION
from raid_processes import raid_processes
matches = []
for pid in raid_processes():
    try:
        with AgentIpc(pid) as ipc:
            header = ipc.header()
            account = ipc.account() or {}
            if (header.get('sharedStateVersion') == SHARED_STATE_VERSION and
                    str(account.get('accountName', '')).casefold() ==
                    r'$escapedAccount'.casefold() and
                    isinstance(account.get('userId'), int) and account['userId'] > 0):
                matches.append((pid, account['userId'], account.get('accountName', '')))
    except (FileNotFoundError, ValueError, OSError):
        pass
if len(matches) != 1:
    raise RuntimeError(f'Expected one in-game account match, found {len(matches)}')
print(f'{matches[0][0]},{matches[0][1]},{matches[0][2]}')
"@
$resolved = ((& $python -c $resolver | Out-String).Trim() -split ',', 3)
$targetPid = [int]$resolved[0]
$userId = [int64]$resolved[1]
$exactAccountName = $resolved[2]

$controllerArguments = @(
    $controller,
    "--pid", $targetPid,
    "--account-name", $exactAccountName,
    "--account-user-id", $userId,
    "--config", $configPath,
    "--agent", $agent,
    "--execute",
    "--bootstrap-current",
    "--max-commands", $MaxCommands
)
if ($AutoStart) {
    $controllerArguments += "--auto-start"
}

& $python @controllerArguments 2>&1 |
    Tee-Object -FilePath $Log
<#
& $python $controller `
    --pid $targetPid `
    --account-name $exactAccountName `
    --account-user-id $userId `
    --config $configPath `
    --agent $agent `
    --execute `
    --bootstrap-current `
    --max-commands $MaxCommands 2>&1 |
    Tee-Object -FilePath $Log
#>
exit $LASTEXITCODE
