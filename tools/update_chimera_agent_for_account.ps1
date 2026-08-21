[CmdletBinding()]
param(
    [string]$Account = "gafee",
    [UInt64]$SelectionContext = 0
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
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-Account", ('"' + $Account.Replace('"', '') + '"'),
        "-SelectionContext", ([string]$SelectionContext)
    )
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$injector = Join-Path $PSScriptRoot "inject_probe.py"
$newAgent = Join-Path $projectRoot "build\agent-1226\Release\RaidChimeraAgent.dll"
$probeResult = Join-Path $projectRoot "build\chimera-agent-current-gafee.json"

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
                    r'$escapedAccount'.casefold()):
                lifecycle = ipc.lifecycle() or {}
                selection = lifecycle.get('selection') or {}
                context = selection.get('context', 0)
                matches.append((pid, context if isinstance(context, int) else 0))
    except (FileNotFoundError, ValueError):
        pass
if len(matches) != 1:
    raise RuntimeError(f'Expected one in-game account match, found {len(matches)}')
print(f'{matches[0][0]}|{matches[0][1]}')
"@
$resolvedText = (& $python -c $resolver | Out-String).Trim()
$resolved = (($resolvedText -split "`r?`n")[-1]) -split '\|', 2
$targetPid = [int]$resolved[0]
$observedSelectionContext = if ($resolved.Count -gt 1) { [UInt64]$resolved[1] } else { 0 }
if ($targetPid -le 0) {
    throw "Unable to resolve the requested in-game account."
}
if ($SelectionContext -eq 0) {
    $SelectionContext = $observedSelectionContext
}

& $python $injector --pid $targetPid --agent $newAgent --reload --output $probeResult | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The agent update failed for in-game account $Account."
}
if ($SelectionContext -gt 0) {
    & $python $injector --pid $targetPid --agent $newAgent `
        --seed-selection-context --context $SelectionContext --output $probeResult | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The agent updated, but restoring the preparation screen failed for $Account."
    }
}
exit 0
