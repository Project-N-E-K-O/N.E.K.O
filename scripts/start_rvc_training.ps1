# Launch vendored RVC Gradio UI for voice training (never touches D:\RVC).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\start_rvc_training.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start_rvc_training.ps1 -Port 7897

param(
    [int]$Port = 7897,
    [string]$ServerName = "127.0.0.1",
    [switch]$Lan
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RvcRoot = Join-Path $RepoRoot "vendor\rvc"
$Python = Join-Path $RvcRoot "runtime\python.exe"
$InferWeb = Join-Path $RvcRoot "infer-web.py"

if (-not (Test-Path $InferWeb)) {
    Write-Host "[ERROR] Training UI missing: $InferWeb"
    Write-Host "Run: powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1"
    exit 1
}
if (-not (Test-Path $Python)) {
    Write-Host "[ERROR] Vendored runtime missing: $Python"
    Write-Host "Run setup without -SkipRuntime."
    exit 1
}

$pretrained = Join-Path $RvcRoot "assets\pretrained"
$pretrainedV2 = Join-Path $RvcRoot "assets\pretrained_v2"
if (-not (Test-Path $pretrained) -and -not (Test-Path $pretrainedV2)) {
    Write-Warning "assets/pretrained* not found — training may fail without pretrained G/D."
    Write-Warning "Re-run scripts\setup_rvc_vendor.ps1 (training is included by default)."
}

if ($Lan) {
    $ServerName = "0.0.0.0"
}

Write-Host "RVC training UI (vendored)"
Write-Host "  cwd:  $RvcRoot"
Write-Host "  url:  http://127.0.0.1:$Port"
Write-Host "  note: all writes stay under vendor\rvc (D:\RVC untouched)"
Write-Host ""

Set-Location -LiteralPath $RvcRoot
$pycmd = Join-Path $RvcRoot "runtime\python.exe"
& $Python $InferWeb --pycmd $pycmd --port $Port --api --server_name $ServerName
exit $LASTEXITCODE
