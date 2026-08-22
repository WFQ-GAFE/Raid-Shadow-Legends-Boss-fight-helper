[CmdletBinding()]
param(
    [switch]$NoUac
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildTools = Join-Path $projectRoot "third_party\build_tools"
$vendoredPython = Join-Path $projectRoot "third_party\python"
$toolsDir = Join-Path $projectRoot "tools"
$entry = Join-Path $toolsDir "chimera_web.py"
$uiDist = Join-Path $projectRoot "ui\dist"
$dataDir = Join-Path $projectRoot "data"
$agent = Join-Path $projectRoot "build\agent-1231\Release\RaidChimeraAgent.dll"
$workPath = Join-Path $projectRoot "out\chimera-desktop"
$distPath = Join-Path $projectRoot "build\desktop"
$specPath = Join-Path $projectRoot "out\chimera-spec"

if (-not (Test-Path -LiteralPath (Join-Path $uiDist "index.html") -PathType Leaf)) {
    throw "The React interface must be built before packaging."
}
if (-not (Test-Path -LiteralPath (Join-Path $buildTools "PyInstaller") -PathType Container)) {
    throw "PyInstaller is not installed in third_party\build_tools."
}
if (-not (Test-Path -LiteralPath $agent -PathType Leaf)) {
    throw "Build the x64 RaidChimeraAgent.dll before packaging the desktop application."
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = "$buildTools;$vendoredPython;$toolsDir"
try {
    & python.exe -c "import UnityPy"
    if ($LASTEXITCODE -ne 0) {
        throw "UnityPy is required on the build machine so native game portraits and icons can be bundled."
    }
    $arguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name", "ChimeraStrategyCenter",
        "--distpath", $distPath,
        "--workpath", $workPath,
        "--specpath", $specPath,
        "--paths", $toolsDir,
        "--paths", $vendoredPython,
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        "--exclude-module", "tkinter",
        "--exclude-module", "ttkbootstrap",
        "--collect-all", "UnityPy",
        "--add-data", "$uiDist;ui\dist",
        "--add-data", "$dataDir;data",
        "--add-binary", "$agent;agent"
    )
    if (-not $NoUac) {
        $arguments += "--uac-admin"
    }
    $arguments += $entry
    & python.exe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop packaging failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}

$executable = Join-Path $distPath "ChimeraStrategyCenter\ChimeraStrategyCenter.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "The desktop executable was not produced."
}
$releaseRoot = Split-Path -Parent $executable
foreach ($document in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "VERSION")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $document) -Destination $releaseRoot -Force
}
Write-Output $executable
