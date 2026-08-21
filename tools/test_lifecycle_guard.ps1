[CmdletBinding()]
param(
    [string]$Account = "gafee"
)

$ErrorActionPreference = "Stop"

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
        "-Account", ('"' + $Account.Replace('"', '') + '"')
    )
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"
$takeoverArmed = $false

try {
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
                    isinstance(account.get('userId'), int) and
                    account['userId'] > 0):
                matches.append((pid, account['userId']))
    except (FileNotFoundError, ValueError, OSError):
        pass
if len(matches) != 1:
    raise RuntimeError(f'Expected one in-game account match, found {len(matches)}')
print(f'{matches[0][0]},{matches[0][1]}')
"@
    $resolved = ((& $python -c $resolver | Out-String).Trim() -split ',')
    $pidUnderTest = [int]$resolved[0]
    $userId = [int64]$resolved[1]
    $session = [int64]1774899001
    $output = Join-Path $projectRoot "build\lifecycle-guard-test-$pidUnderTest.json"
    & $python "tools\inject_probe.py" --pid $pidUnderTest --agent $agent --begin-takeover --session-id $session --expected-user-id $userId | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not arm takeover." }
    $takeoverArmed = $true
    $code = @"
import json,sys,time
sys.path.insert(0, 'tools')
from pathlib import Path
from agent_ipc import AgentIpc
from inject_probe import queue_lifecycle_command
pid=$pidUnderTest
session=$session
queued=queue_lifecycle_command(pid, Path(r'$agent'), session_id=session, context=1, action=1, nonce=2147483649)
time.sleep(0.25)
with AgentIpc(pid) as ipc:
    ack=ipc.acknowledgement()
Path(r'$output').write_text(json.dumps({'queued':queued,'ack':ack}, ensure_ascii=False, indent=2), encoding='utf-8')
"@
    & $python -c $code
    if ($LASTEXITCODE -ne 0) { throw "Guard request failed." }
}
finally {
    if ($takeoverArmed) {
        & $python "tools\inject_probe.py" --pid $pidUnderTest --agent $agent --end-takeover --session-id $session | Out-Null
    }
}
