[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Int64]$WindowHandle,
    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-WindowHandle", $WindowHandle,
        "-Output", ('"' + $Output.Replace('"', '') + '"')
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    exit $process.ExitCode
}

Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ReadOnlyWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint flags);
}
'@

$handle = [IntPtr]$WindowHandle
$rect = New-Object ReadOnlyWindowCapture+RECT
if (-not [ReadOnlyWindowCapture]::GetWindowRect($handle, [ref]$rect)) {
    throw "GetWindowRect failed"
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) {
    throw "Window dimensions are invalid"
}
$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$hdc = $graphics.GetHdc()
try {
    $captured = [ReadOnlyWindowCapture]::PrintWindow($handle, $hdc, 2)
} finally {
    $graphics.ReleaseHdc($hdc)
    $graphics.Dispose()
}
if (-not $captured) {
    $bitmap.Dispose()
    throw "PrintWindow failed"
}
$fullOutput = [System.IO.Path]::GetFullPath($Output)
$parent = [System.IO.Path]::GetDirectoryName($fullOutput)
[System.IO.Directory]::CreateDirectory($parent) | Out-Null
$bitmap.Save($fullOutput, [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()
@{path=$fullOutput;width=$width;height=$height} | ConvertTo-Json
