[CmdletBinding()]
param(
    [switch]$SourceConsole
)

$ErrorActionPreference = "Stop"

function Show-LaunchError {
    param([string]$Message)
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "Raid Boss Strategy Center",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopExe = Join-Path $projectRoot "build\desktop\ChimeraStrategyCenter\ChimeraStrategyCenter.exe"
if (-not $SourceConsole -and (Test-Path -LiteralPath $desktopExe -PathType Leaf)) {
    try {
        Start-Process -FilePath $desktopExe -WorkingDirectory (Split-Path -Parent $desktopExe) | Out-Null
    }
    catch {
        Show-LaunchError "Failed to start the desktop application:`n`n$($_.Exception.Message)"
    }
    exit 0
}

if (-not (Test-IsAdministrator)) {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($SourceConsole) {
        $arguments += " -SourceConsole"
    }
    try {
        # Keep the elevation request interactive so the UAC confirmation is
        # never hidden behind the desktop app.
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arguments | Out-Null
    }
    catch {
        Show-LaunchError "Failed to request administrator access:`n`n$($_.Exception.Message)"
    }
    exit 0
}

try {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $entryArgument = "tools\chimera_web.py"
    $webIndex = Join-Path $projectRoot "ui\dist\index.html"
    if (-not (Test-Path -LiteralPath $webIndex -PathType Leaf)) {
        throw "The React interface has not been built. Run npm run build in the ui directory."
    }
    if ($SourceConsole) {
        $env:CHIMERA_PROJECT_ROOT = $projectRoot
        $entryPath = Join-Path $projectRoot $entryArgument
        $host.UI.RawUI.WindowTitle = "Raid Boss Strategy Center - Source Test"
        Write-Host "Administrator: True" -ForegroundColor Green
        Write-Host "Raid and account diagnostics will be shown here. Close this window to stop the tool." -ForegroundColor Cyan
        Write-Host ""
        & $python $entryPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "The tool exited with code $LASTEXITCODE." -ForegroundColor Red
            Read-Host "Press Enter to close"
        }
        exit $LASTEXITCODE
    }
    $pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        $pythonw = $python
    }
    $startParameters = @{
        FilePath = $pythonw
        ArgumentList = @($entryArgument)
        WorkingDirectory = $projectRoot
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $process = Start-Process @startParameters
    Start-Sleep -Milliseconds 1800
    $process.Refresh()
    if ($process.HasExited) {
        Show-LaunchError ((
            "The local UI service exited immediately (exit code {0}).`n`n" +
            "Close any existing Raid Boss tool window, then try again."
        ) -f $process.ExitCode)
    }
}
catch {
    Show-LaunchError "Startup failed:`n`n$($_.Exception.Message)"
}
