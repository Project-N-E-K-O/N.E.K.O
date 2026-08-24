# Create Windows installer artifacts for Testbench.
# Prefer Inno Setup (ISCC) when available; otherwise ship zip + Install-Testbench.ps1.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tests/testbench_dist/scripts/build_installer_win_ps1.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $Root "output\pyinstaller\Testbench"
$OutDir = Join-Path $Root "output\installer"
$Zip = Join-Path $OutDir "Testbench-win-x64.zip"
$InstallerPs1 = Join-Path $OutDir "Install-Testbench.ps1"

if (-not (Test-Path $Src)) {
  throw "Missing PyInstaller output: $Src"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $Src "*") -DestinationPath $Zip -Force

$installScript = @'
# N.E.K.O. Testbench portable installer (no admin required)
$ErrorActionPreference = "Stop"
$DefaultDir = Join-Path $env:LOCALAPPDATA "Programs\NEKO-Testbench"
$Target = if ($args.Count -ge 1 -and $args[0]) { $args[0] } else { $DefaultDir }
$ZipBeside = Join-Path $PSScriptRoot "Testbench-win-x64.zip"
if (-not (Test-Path $ZipBeside)) { throw "Missing Testbench-win-x64.zip next to this script" }

Write-Host "Installing to $Target ..."
New-Item -ItemType Directory -Force -Path $Target | Out-Null
Expand-Archive -Path $ZipBeside -DestinationPath $Target -Force

$exe = Join-Path $Target "Testbench.exe"
if (-not (Test-Path $exe)) { throw "Testbench.exe missing after extract" }

$wsh = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
foreach ($linkDir in @($desktop, $startMenu)) {
  $lnkPath = Join-Path $linkDir "N.E.K.O. Testbench.lnk"
  $shortcut = $wsh.CreateShortcut($lnkPath)
  $shortcut.TargetPath = $exe
  $shortcut.WorkingDirectory = $Target
  $shortcut.Description = "N.E.K.O. Testbench"
  $shortcut.Save()
}

Write-Host "Done. Launching..."
Start-Process -FilePath $exe
'@

Set-Content -Path $InstallerPs1 -Value $installScript -Encoding UTF8
Write-Host "Wrote $Zip"
Write-Host "Wrote $InstallerPs1"

$isccCandidates = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
  "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
  Write-Host "Compiling Inno Setup with $iscc"
  & $iscc (Join-Path $PSScriptRoot "build_installer_win.iss")
} else {
  Write-Host "Inno Setup (ISCC) not found; shipped zip + Install-Testbench.ps1 instead."
}
