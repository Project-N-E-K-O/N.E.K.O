# Create / refresh a Desktop .lnk that points at the repo-local silent launcher.
# Launcher files stay in the project root; Desktop only gets N.E.K.O.lnk.
param(
    [string]$RepoRoot = "",
    [string]$ShortcutName = "N.E.K.O.lnk"
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$vbs = Join-Path $RepoRoot "start_desktop_silent.vbs"
$icon = Join-Path $RepoRoot "assets\icon.ico"
if (-not (Test-Path -LiteralPath $icon)) {
    $icon = Join-Path $RepoRoot "desktop_release\resources\icon.ico"
}
if (-not (Test-Path -LiteralPath $icon)) {
    $icon = Join-Path $RepoRoot "static\favicon.ico"
}
if (-not (Test-Path -LiteralPath $vbs)) {
    throw "Missing launcher: $vbs"
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "desktop_release\N.E.K.O.exe"))) {
    throw "Missing desktop_release\N.E.K.O.exe"
}

$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
if (-not (Test-Path -LiteralPath $wscript)) {
    $wscript = "wscript.exe"
}

$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop) {
    $desktop = Join-Path $env:USERPROFILE "Desktop"
}
$lnkPath = Join-Path $desktop $ShortcutName

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($lnkPath)
# Point at wscript.exe — more reliable than TargetPath=*.vbs on some Windows setups.
$sc.TargetPath = $wscript
$sc.Arguments = "//Nologo `"$vbs`""
$sc.WorkingDirectory = $RepoRoot
$sc.WindowStyle = 1
$sc.Description = "一键启动 N.E.K.O（静默，无命令窗口）"
if (Test-Path -LiteralPath $icon) {
    $sc.IconLocation = "$icon,0"
}
$sc.Save()

Write-Host "Desktop shortcut ready:"
Write-Host "  $lnkPath"
Write-Host "  Target: $wscript"
Write-Host "  Args:   //Nologo `"$vbs`""
Write-Host "  Icon:   $icon"
