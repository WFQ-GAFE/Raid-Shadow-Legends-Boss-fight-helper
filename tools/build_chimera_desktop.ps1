[CmdletBinding()]
param(
    [switch]$NoUac,
    [switch]$OneDir,
    [switch]$SkipPublish,
    [string]$OutputDirectory = "",
    [string]$AgentDirectory = "build\agent-1236",
    [string]$OfflineRuntimeDirectory = "build\offline-runtime"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildTools = Join-Path $projectRoot "third_party\build_tools"
$vendoredPython = Join-Path $projectRoot "third_party\python"
$toolsDir = Join-Path $projectRoot "tools"
$entry = Join-Path $toolsDir "chimera_web.py"
$uiDist = Join-Path $projectRoot "ui\dist"
$dataDir = Join-Path $projectRoot "data"
$agentRoot = if ([System.IO.Path]::IsPathRooted($AgentDirectory)) {
    [System.IO.Path]::GetFullPath($AgentDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $AgentDirectory))
}
$agent = Join-Path $agentRoot "Release\RaidChimeraAgent.dll"
$agentManifestPath = if ([System.IO.Path]::IsPathRooted($AgentDirectory)) {
    $agent
}
else {
    Join-Path $AgentDirectory "Release\RaidChimeraAgent.dll"
}
$agentCache = Join-Path $agentRoot "CMakeCache.txt"
$offlineRuntimeRoot = if ([System.IO.Path]::IsPathRooted($OfflineRuntimeDirectory)) {
    [System.IO.Path]::GetFullPath($OfflineRuntimeDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OfflineRuntimeDirectory))
}
# Isolated original-engine runner for the Hydra battle-start forecast. Game
# binaries are never packaged; the app copies them from the local install.
$offlineProbe = Join-Path $offlineRuntimeRoot "Release\raid_offline_probe.exe"
$workPath = Join-Path $projectRoot "out\chimera-desktop"
$distPath = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    # Build separately: an occupied shortcut target must never interrupt
    # packaging or cause PyInstaller to remove an existing published file.
    $buildStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
    Join-Path $workPath "packages\$buildStamp"
}
elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}
$specPath = Join-Path $projectRoot "out\chimera-spec"
$versionFile = Join-Path $toolsDir "windows_version_info.txt"
$iconFile = Join-Path $projectRoot "branding\alliance-boss-strategy-icon-v3.ico"
$version = (Get-Content -LiteralPath (Join-Path $projectRoot "VERSION") -Raw).Trim()
$applicationName = "RSL-Boss-helper"
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
if (-not (Test-Path -LiteralPath $offlineProbe -PathType Leaf)) {
    throw "Build src\offline_runtime (x64 Release) into $OfflineRuntimeDirectory before packaging."
}
$offlineProbeSha256 = (Get-FileHash -LiteralPath $offlineProbe -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not (Test-Path -LiteralPath $agentCache -PathType Leaf)) {
    throw "The agent CMake cache is missing; configure the agent build before packaging."
}
$agentCacheText = Get-Content -LiteralPath $agentCache -Raw
if ($agentCacheText -notmatch '(?m)^RAID_HYDRA_SELECTOR_HOOK:BOOL=OFF\s*$') {
    throw "Refusing to package: RAID_HYDRA_SELECTOR_HOOK must be OFF. Live selector capture is retired."
}
if ($agentCacheText -notmatch '(?m)^RAID_HYDRA_RESEARCH_CAPTURE:BOOL=OFF\s*$') {
    throw "Refusing to package: RAID_HYDRA_RESEARCH_CAPTURE must be OFF. Live replay input capture is retired after the GameAssembly crash; use offline capture and simulation."
}
$agentSource = Get-Content -LiteralPath (Join-Path $projectRoot "src\agent\agent.cpp") -Raw
if ($agentSource -notmatch 'kAgentBuildId\s*=\s*(\d+)ULL') {
    throw "The agent source does not declare a readable build ID."
}
$agentBuildId = [UInt64]$Matches[1]
$agentBytes = [System.IO.File]::ReadAllBytes($agent)
$agentText = [System.Text.Encoding]::ASCII.GetString($agentBytes)
foreach ($unsafeMarker in @(
        "hydra_mark_selector_observation_installed",
        "RandomHungerVictimSelectedFrom",
        "hydraReplayInput",
        "ToPackedMessagePack",
        "pack_with_game_messagepack"
    )) {
    if ($agentText.Contains($unsafeMarker)) {
        throw "Refusing to package: the agent binary contains retired Hydra research code marker '$unsafeMarker'."
    }
}
foreach ($captureMarker in @("hydra_replay_source", "chimera_replay_source", "_captured generation=")) {
    if (-not $agentText.Contains($captureMarker)) {
        throw "Refusing to package: the agent binary lacks the validated Hydra/Chimera JSON input capture path ($captureMarker)."
    }
}
$agentBuildIdBytes = [BitConverter]::GetBytes([UInt32]$agentBuildId)
$agentBuildIdFound = $false
for ($offset = 0; $offset -le ($agentBytes.Length - $agentBuildIdBytes.Length); $offset++) {
    $matchesBuildId = $true
    for ($byteIndex = 0; $byteIndex -lt $agentBuildIdBytes.Length; $byteIndex++) {
        if ($agentBytes[$offset + $byteIndex] -ne $agentBuildIdBytes[$byteIndex]) {
            $matchesBuildId = $false
            break
        }
    }
    if ($matchesBuildId) {
        $agentBuildIdFound = $true
        break
    }
}
if (-not $agentBuildIdFound) {
    throw "Refusing to package: the agent binary build ID does not match src\agent\agent.cpp. Rebuild the agent first."
}
$agentSha256 = (Get-FileHash -LiteralPath $agent -Algorithm SHA256).Hash.ToLowerInvariant()
$researchCaptureEnabled = $false
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
    throw "The Windows version metadata file is missing."
}
if (-not (Test-Path -LiteralPath $iconFile -PathType Leaf)) {
    throw "The application icon (branding\alliance-boss-strategy-icon-v3.ico) is missing."
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
        "--icon", $iconFile,
        "--paths", $toolsDir,
        "--paths", $vendoredPython,
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "hydra_forecast_live",
        "--hidden-import", "chimera_capture_live",
        "--hidden-import", "chimera_replay_source",
        "--hidden-import", "chimera_simulation",
        "--hidden-import", "chimera_simulation_service",
        "--hidden-import", "chimera_forecast_live",
        "--hidden-import", "team_preview",
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
        "--add-data", "$iconFile;branding",
        "--add-binary", "$agent;agent",
        "--add-binary", "$offlineProbe;offline"
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
$executableSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
$safetyManifest = [ordered]@{
    schema = 1
    version = $version
    agentBuildId = $agentBuildId
    selectorHookEnabled = $false
    researchInputCaptureEnabled = [bool]$researchCaptureEnabled
    validatedHydraJsonCaptureEnabled = $true
    agentPath = $agentManifestPath
    agentSha256 = $agentSha256
    offlineForecastProbeSha256 = $offlineProbeSha256
    executableSha256 = $executableSha256
    builtAtLocal = [DateTimeOffset]::Now.ToString("o")
}
$safetyManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $distPath "agent-safety.json") -Encoding UTF8
if ($OneDir) {
    $releaseRoot = Split-Path -Parent $executable
    foreach ($document in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "VERSION")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $document) -Destination $releaseRoot -Force
    }
    Write-Output $executable
    Write-Warning "OneDir builds require their complete folder and do not update the one-file release entry."
}
elseif ($SkipPublish) {
    Write-Output $executable
}
else {
    $checksum = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    & python.exe (Join-Path $toolsDir "publish_chimera_release.py") `
        --source $executable --version $version --expected-sha256 $checksum
    if ($LASTEXITCODE -ne 0) {
        throw "The package is ready at '$executable', but release publication failed. Close the tool and retry publish_chimera_release.py with this package."
    }
}
