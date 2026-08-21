[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

Write-Error "此旧版‘选择第一个 Raid 进程’探测入口已停用；请使用主工具按游戏内账户刷新。"
exit 9

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"')
    )
    exit $process.ExitCode
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$injector = Join-Path $PSScriptRoot "inject_probe.py"
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"
$targetPid = [int](Get-Process -Name "Raid" -ErrorAction Stop | Sort-Object Id | Select-Object -First 1).Id
$output = Join-Path $projectRoot "build\chimera-context-probe-$targetPid.json"

Set-Location -LiteralPath $projectRoot
& $python $injector --pid $targetPid --agent $agent --reload --output $output | Out-Null
exit $LASTEXITCODE
