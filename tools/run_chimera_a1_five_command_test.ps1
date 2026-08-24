[CmdletBinding()]
param(
    [string]$Account = "gafee"
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $quotedScript = '"' + $PSCommandPath.Replace('"', '\"') + '"'
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $quotedScript,
        "-Account", ('"' + $Account.Replace('"', '') + '"')
    ) | Out-Null
    exit 0
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$controller = Join-Path $PSScriptRoot "chimera_controller.py"
$config = Join-Path $projectRoot "config\chimera-strategy.a1-test.json"
$agent = Join-Path $projectRoot "build\agent-1236\Release\RaidChimeraAgent.dll"

try {
    $Host.UI.RawUI.WindowTitle = "Chimera injected controller - five-command A1 test"
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
    Write-Host "Starting a bounded five-command A1 test for $exactAccountName." -ForegroundColor Cyan
    Write-Host "Every command must receive a game-main-thread completion acknowledgement." -ForegroundColor Cyan
    Write-Host "Close this window or press Ctrl+C to stop early." -ForegroundColor Yellow
    Write-Host ""

    & $python $controller `
        --pid $targetPid `
        --account-name $exactAccountName `
        --account-user-id $userId `
        --config $config `
        --agent $agent `
        --execute `
        --bootstrap-current `
        --max-commands 5
    if ($LASTEXITCODE -ne 0) {
        throw "The bounded controller returned exit code $LASTEXITCODE."
    }
    Write-Host "`nThe bounded controller finished normally." -ForegroundColor Green
}
catch {
    Write-Host "`nThe bounded controller stopped: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Read-Host "Press Enter to close"
}
