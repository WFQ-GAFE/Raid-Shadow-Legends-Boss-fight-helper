[CmdletBinding()]
param(
    [string]$Account = "gafee",
    [string]$Log = ""
)

$ErrorActionPreference = "Stop"
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
        "-Account", ('"' + $Account.Replace('"', '') + '"')
    )
    if ($Log) {
        $arguments += @("-Log", ('"' + $Log.Replace('"', '') + '"'))
    }
    $elevated = Start-Process -FilePath "powershell.exe" -Verb RunAs `
        -WindowStyle Hidden -Wait -PassThru -ArgumentList $arguments
    exit $elevated.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$tool = Join-Path $PSScriptRoot "return_to_chimera_preparation.py"
$agent = Join-Path $projectRoot "build\agent-1236\Release\RaidChimeraAgent.dll"
if (-not $Log) {
    $Log = Join-Path $projectRoot "build\chimera-return-to-preparation.log"
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

& $python $tool `
    --pid $targetPid `
    --account-name $exactAccountName `
    --user-id $userId `
    --agent $agent 2>&1 |
    Tee-Object -FilePath $Log
exit $LASTEXITCODE
