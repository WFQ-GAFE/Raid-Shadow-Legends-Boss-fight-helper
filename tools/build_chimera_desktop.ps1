[CmdletBinding()]
param(
    [switch]$NoUac,
    [switch]$OneDir,
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildTools = Join-Path $projectRoot "third_party\build_tools"
$vendoredPython = Join-Path $projectRoot "third_party\python"
$toolsDir = Join-Path $projectRoot "tools"
$entry = Join-Path $toolsDir "chimera_web.py"
$uiDist = Join-Path $projectRoot "ui\dist"
$dataDir = Join-Path $projectRoot "data"
$agent = Join-Path $projectRoot "build\agent-1236\Release\RaidChimeraAgent.dll"
$workPath = Join-Path $projectRoot "out\chimera-desktop"
$distPath = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $projectRoot "build\release"
}
elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}
$specPath = Join-Path $projectRoot "out\chimera-spec"
$versionFile = Join-Path $toolsDir "windows_version_info.txt"
$version = (Get-Content -LiteralPath (Join-Path $projectRoot "VERSION") -Raw).Trim()
$applicationName = "AllianceBossStrategyStudio"
$artifactName = "$applicationName-$version"

if (-not (Test-Path -LiteralPath (Join-Path $uiDist "index.html") -PathType Leaf)) {
    throw "The React interface must be built before packaging."
}
if (-not (Test-Path -LiteralPath (Join-Path $buildTools "PyInstaller") -PathType Container)) {
    throw "PyInstaller is not installed in third_party\build_tools."
}
if (-not (Test-Path -LiteralPath $agent -PathType Leaf)) {
    throw "Build the x64 RaidChimeraAgent.dll before packaging the desktop application."
}
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
    throw "The Windows version metadata file is missing."
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = "$buildTools;$vendoredPython;$toolsDir"
try {
    & python.exe -c "import UnityPy, fmod_toolkit, archspec"
    if ($LASTEXITCODE -ne 0) {
        throw "UnityPy, fmod-toolkit, and archspec are required on the build machine so native game portraits and icons work in the packaged application."
    }
    $arguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        $(if ($OneDir) { "--onedir" } else { "--onefile" }),
        "--windowed",
        "--name", $artifactName,
        "--distpath", $distPath,
        "--workpath", $workPath,
        "--specpath", $specPath,
        "--version-file", $versionFile,
        "--paths", $toolsDir,
        "--paths", $vendoredPython,
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        "--exclude-module", "tkinter",
        "--exclude-module", "ttkbootstrap",
        "--collect-all", "UnityPy",
        "--collect-all", "fmod_toolkit",
        "--collect-all", "archspec",
        "--add-data", "$uiDist;ui\dist",
        "--add-data", "$dataDir;data",
        "--add-data", "$(Join-Path $projectRoot 'LICENSE');legal",
        "--add-data", "$(Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md');legal",
        "--add-data", "$(Join-Path $projectRoot 'VERSION');.",
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

$executable = if ($OneDir) {
    Join-Path $distPath "$artifactName\$artifactName.exe"
}
else {
    Join-Path $distPath "$artifactName.exe"
}
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "The desktop executable was not produced."
}
if ($OneDir) {
    $releaseRoot = Split-Path -Parent $executable
    foreach ($document in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "VERSION")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $document) -Destination $releaseRoot -Force
    }
}
Write-Output $executable
