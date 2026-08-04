# Copy finished voice models from vendor/rvc/logs into assets/weights
# so rvc_cover inference can pick them up. Never touches D:\RVC.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\sync_rvc_weights.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\sync_rvc_weights.ps1 -Experiment my_voice

param(
    [string]$Experiment = "",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RvcRoot = Join-Path $RepoRoot "vendor\rvc"
$Logs = Join-Path $RvcRoot "logs"
$Weights = Join-Path $RvcRoot "assets\weights"

if (-not (Test-Path $Logs)) {
    Write-Host "No logs dir yet: $Logs"
    exit 0
}
New-Item -ItemType Directory -Force -Path $Weights | Out-Null

$expDirs = @()
if ($Experiment) {
    $one = Join-Path $Logs $Experiment
    if (-not (Test-Path $one)) { throw "Experiment not found: $one" }
    $expDirs = @(Get-Item -LiteralPath $one)
} else {
    $expDirs = Get-ChildItem -LiteralPath $Logs -Directory -ErrorAction SilentlyContinue
}

$copied = 0
foreach ($dir in $expDirs) {
    $pths = Get-ChildItem -LiteralPath $dir.FullName -Filter "*.pth" -File -ErrorAction SilentlyContinue
    foreach ($pth in $pths) {
        # Prefer exported G_*.pth / named models; skip tiny optimizer-only if obvious
        $destName = $pth.Name
        # If name is like G_23333.pth under experiment folder, keep experiment prefix
        if ($pth.Name -match '^(G_|D_)\d+\.pth$') {
            $destName = "$($dir.Name)_$($pth.Name)"
        }
        $dest = Join-Path $Weights $destName
        if ($WhatIf) {
            Write-Host "[WhatIf] $($pth.FullName) -> $dest"
            continue
        }
        Copy-Item -LiteralPath $pth.FullName -Destination $dest -Force
        Write-Host "Synced: $destName"
        $copied++
    }
}

Write-Host "Done. Synced $copied file(s) into $Weights"
Write-Host "Set plugin.toml [rvc].model_name to the .pth filename to use it."
