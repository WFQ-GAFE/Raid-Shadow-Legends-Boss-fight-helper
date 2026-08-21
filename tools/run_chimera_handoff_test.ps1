[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Write-Error "此旧版 PID/临时日志交接测试已停用；请使用主工具按游戏内账户启动。"
exit 9

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
        "-File", $quotedScript
    ) | Out-Null
    exit 0
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
$controller = Join-Path $PSScriptRoot "chimera_controller.py"
$injector = Join-Path $PSScriptRoot "inject_probe.py"
$config = Join-Path $projectRoot "config\chimera-strategy.a1-test.json"
$agent = Join-Path $projectRoot "build\agent-1225\Release\RaidChimeraAgent.dll"
$agentLog = Join-Path $env:TEMP "RaidChimeraAgent-73664.log"
$tag = Get-Date -Format "yyyyMMdd-HHmmssfff"
$controllerOut = Join-Path $env:TEMP "chimera-controller-$tag.out.log"
$controllerErr = Join-Path $env:TEMP "chimera-controller-$tag.err.log"
$castOut = Join-Path $env:TEMP "chimera-cast-$tag.json"
$controllerProcess = $null

function Get-Capture([string]$Text, [string]$Pattern, [string]$Label) {
    $match = [regex]::Match($Text, $Pattern, [Text.RegularExpressions.RegexOptions]::Singleline)
    if (-not $match.Success) {
        throw "Cannot read live-state field: $Label"
    }
    return $match.Groups[1].Value
}

try {
    $Host.UI.RawUI.WindowTitle = "Chimera injected controller - bounded handoff test"
    Write-Host "Validating the test account and current Chimera turn..." -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $agentLog -PathType Leaf)) {
        throw "The live-state log for the test account was not found."
    }

    $decisionLine = Get-Content -LiteralPath $agentLog -Tail 400 |
        Where-Object { $_ -match 'decision_state ' } |
        Select-Object -Last 1
    if (-not $decisionLine) {
        throw "No Chimera decision state is available."
    }

    $area = [int](Get-Capture $decisionLine '"areaTypeId":(-?\d+)' "area")
    $region = [int](Get-Capture $decisionLine '"regionTypeId":(-?\d+)' "region")
    $waiting = Get-Capture $decisionLine '"waitingForManualCommand":(true|false)' "manual command window"
    $activeId = [int](Get-Capture $decisionLine '"activeHeroId":(-?\d+)' "active hero ID")
    $activeType = [int](Get-Capture $decisionLine '"activeHeroTypeId":(-?\d+)' "active hero type")
    $generator = [UInt64](Get-Capture $decisionLine '"generator":(\d+)' "command generator")
    $mode = [UInt64](Get-Capture $decisionLine '"mode":(\d+)' "battle mode")

    $skillMatch = [regex]::Match(
        $decisionLine,
        '"skills":\[\{"slot":1,"skillId":0,"typeId":(\d+).*?"skillDataPtr":(\d+),"validTargetIds":\[([^\]]*)\]',
        [Text.RegularExpressions.RegexOptions]::Singleline
    )
    if (-not $skillMatch.Success) {
        throw "Cannot read the live target data for Wixwell skill 1."
    }
    $skillType = [int]$skillMatch.Groups[1].Value
    $skillData = [UInt64]$skillMatch.Groups[2].Value
    $validTargets = @($skillMatch.Groups[3].Value -split ',' | ForEach-Object { [int]$_.Trim() })

    if ($area -ne 13 -or $region -ne 1302 -or $waiting -ne "true") {
        throw "The game is not waiting for a manual command in Chimera."
    }
    if ($activeId -ne 0 -or $activeType -ne 8896 -or $skillType -ne 88901) {
        throw "The active hero or selected skill changed; the test was cancelled."
    }
    if ($validTargets -notcontains 5) {
        throw "Chimera ID 5 is not a valid skill-1 target; the test was cancelled."
    }

    Write-Host "Validation passed. The controller may submit one skill-1 command for the next ally." -ForegroundColor Green
    $controllerArgs = @(
        ('"' + $controller + '"'),
        "--pid", "73664",
        "--config", ('"' + $config + '"'),
        "--agent", ('"' + $agent + '"'),
        "--execute",
        "--max-commands", "1"
    )
    $controllerProcess = Start-Process -FilePath $python `
        -ArgumentList $controllerArgs `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $controllerOut `
        -RedirectStandardError $controllerErr `
        -PassThru

    Start-Sleep -Milliseconds 1200
    if ($controllerProcess.HasExited) {
        throw "The controller did not enter its waiting state."
    }

    Write-Host "Submitting Wixwell skill 1 against Chimera..." -ForegroundColor Cyan
    & $python $injector `
        --pid 73664 `
        --agent $agent `
        --cast `
        --generator $generator `
        --mode $mode `
        --skill-data $skillData `
        --target-id 5 `
        --skill-id 0 `
        --verified-skill-type-id 88901 `
        --expected-area-id 13 `
        --expected-region-id 1302 `
        --nonce 9001 `
        --output $castOut
    if ($LASTEXITCODE -ne 0) {
        throw "The Wixwell command did not pass its safety checks."
    }

    Write-Host "First command submitted. Waiting to act once for the next ally..." -ForegroundColor Cyan
    $completed = $controllerProcess.WaitForExit(40000)
    if (-not $completed) {
        Stop-Process -Id $controllerProcess.Id -Force
        $controllerProcess.WaitForExit()
        throw "No executable ally turn appeared within 40 seconds; the controller was stopped."
    }
    if ($controllerProcess.ExitCode -ne 0) {
        throw "The controller returned an error and was stopped."
    }

    Write-Host "`nBounded handoff complete: two commands submitted; the controller is stopped." -ForegroundColor Green
    if (Test-Path -LiteralPath $controllerOut) {
        Write-Host "`nController log:" -ForegroundColor DarkCyan
        Get-Content -LiteralPath $controllerOut
    }
}
catch {
    if ($controllerProcess -and -not $controllerProcess.HasExited) {
        Stop-Process -Id $controllerProcess.Id -Force
        $controllerProcess.WaitForExit()
    }
    Write-Host "`nTest did not complete: $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path -LiteralPath $controllerErr) {
        $errorText = Get-Content -LiteralPath $controllerErr -Raw
        if ($errorText) {
            Write-Host $errorText -ForegroundColor DarkRed
        }
    }
}
finally {
    Write-Host "`nResult files:" -ForegroundColor DarkGray
    Write-Host "  $castOut" -ForegroundColor DarkGray
    Write-Host "  $controllerOut" -ForegroundColor DarkGray
    Write-Host "  $controllerErr" -ForegroundColor DarkGray
    Read-Host "Press Enter to close"
}
