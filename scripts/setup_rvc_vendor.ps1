# Copy a self-contained RVC bundle into the N.E.K.O repo.
# Includes inference + voice training (Gradio infer-web) by default.
# Source (D:\RVC by default) is read-only: this script never writes back to it.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1 -SkipTraining
#   powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1 -IncludeUvr -SkipRuntime

param(
    [string]$SourceRoot = "D:\RVC",
    [string]$DestRoot = "",
    [switch]$SkipRuntime,
    [switch]$SkipWeights,
    # Slim infer-only bundle (no Gradio UI / pretrained checkpoints)
    [switch]$SkipTraining,
    # Skip copying training experiment folders under logs/ (Gradio "项目" list)
    [switch]$SkipLogs,
    # Also copy UVR5 weights + audio_tools (larger; needed for UVR tab in infer-web)
    [switch]$IncludeUvr
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DestRoot) {
    $DestRoot = Join-Path $RepoRoot "vendor\rvc"
}

if (-not (Test-Path $SourceRoot)) {
    throw "Source RVC root not found: $SourceRoot"
}

$IncludeTraining = -not $SkipTraining

Write-Host "Source (read-only): $SourceRoot"
Write-Host "Dest (N.E.K.O vendor): $DestRoot"
Write-Host "Training bundle: $IncludeTraining  UVR: $IncludeUvr"

New-Item -ItemType Directory -Force -Path $DestRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DestRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DestRoot "assets\weights") | Out-Null

function Copy-Tree([string]$Rel) {
    $src = Join-Path $SourceRoot $Rel
    $dst = Join-Path $DestRoot $Rel
    if (-not (Test-Path $src)) {
        Write-Warning "Skip missing: $src"
        return
    }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Write-Host "Copying $Rel ..."
    # /E copy subdirs; /XO newer only; /R:1 /W:1 retry lightly; /NFL /NDL quieter
    & robocopy $src $dst /E /XO /R:1 /W:1 /NFL /NDL /NJH /NP /XD __pycache__ .git | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "robocopy failed for $Rel (exit=$code)"
    }
}

function Copy-FileRel([string]$Rel) {
    $src = Join-Path $SourceRoot $Rel
    $dst = Join-Path $DestRoot $Rel
    if (-not (Test-Path $src)) {
        Write-Warning "Skip missing: $src"
        return
    }
    $parent = Split-Path -Parent $dst
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Write-Host "Copying $Rel ..."
    Copy-Item -LiteralPath $src -Destination $dst -Force
}

# Inference / train shared code + configs
@(
    "infer",
    "tools",
    "configs",
    "i18n"
) | ForEach-Object { Copy-Tree $_ }

# ffmpeg next to RVC root (required by infer.lib.audio.load_audio)
foreach ($bin in @("ffmpeg.exe", "ffprobe.exe")) {
    Copy-FileRel $bin
}

# Runtime assets needed for inference
Copy-Tree "assets\hubert"
Copy-Tree "assets\rmvpe"
if (-not $SkipWeights) {
    Copy-Tree "assets\weights"
    Copy-Tree "assets\indices"
}

# Voice training: Gradio UI + pretrained G/D checkpoints
if ($IncludeTraining) {
    Copy-FileRel "infer-web.py"
    # Optional helpers sometimes referenced by custom packs
    foreach ($extra in @("gui_v1.py", "config.py", "VC.py")) {
        $p = Join-Path $SourceRoot $extra
        if (Test-Path $p) { Copy-FileRel $extra }
    }
    Copy-Tree "assets\pretrained"
    Copy-Tree "assets\pretrained_v2"
    # Experiment folders power Gradio's training "项目" dropdown.
    # Keep plugin_work / smoke artifacts already in dest; only sync named experiments.
    if (-not $SkipLogs) {
        $srcLogs = Join-Path $SourceRoot "logs"
        $dstLogs = Join-Path $DestRoot "logs"
        if (Test-Path $srcLogs) {
            New-Item -ItemType Directory -Force -Path $dstLogs | Out-Null
            Get-ChildItem -LiteralPath $srcLogs -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                $name = $_.Name
                if ($name -match '^(plugin_|__pycache__|\.)') { return }
                Write-Host "Copying logs\$name ..."
                $dstExp = Join-Path $dstLogs $name
                New-Item -ItemType Directory -Force -Path $dstExp | Out-Null
                & robocopy $_.FullName $dstExp /E /XO /R:1 /W:1 /NFL /NDL /NJH /NP /XD __pycache__ .git | Out-Null
                if ($LASTEXITCODE -ge 8) {
                    throw "robocopy failed for logs\$name (exit=$LASTEXITCODE)"
                }
            }
        } else {
            Write-Warning "Skip missing: $srcLogs"
        }
    }
}

if ($IncludeUvr) {
    Copy-Tree "assets\uvr5_weights"
    Copy-Tree "audio_tools"
}

if (-not $SkipRuntime) {
    Copy-Tree "runtime"
}

# Fresh env for the vendored tree — do not copy the user's D:\RVC\.env
$envPath = Join-Path $DestRoot ".env"
$envLines = @(
    "OPENBLAS_NUM_THREADS = 1",
    "no_proxy = localhost, 127.0.0.1, ::1",
    "",
    "weight_root = assets/weights",
    "index_root = logs",
    "outside_index_root = assets/indices",
    "rmvpe_root = assets/rmvpe",
    "hubert_path = assets/hubert/hubert_base.pt"
)
if ($IncludeUvr -or (Test-Path (Join-Path $DestRoot "assets\uvr5_weights"))) {
    $envLines += "weight_uvr5_root = assets/uvr5_weights"
}
$envLines -join "`n" | Set-Content -Path $envPath -Encoding UTF8

$readme = Join-Path $DestRoot "README.md"
@"
# Vendored RVC (N.E.K.O local copy)

This directory is a **copy** from your RVC install (default ``D:\RVC``).
The original folder is **never modified** by N.E.K.O.

## Inference (翻唱插件)

Used by ``plugin/plugins/rvc_cover`` via ``[rvc].rvc_root = vendor/rvc``.

## Voice training (声音训练)

Gradio UI (same as the original pack):

``````bat
scripts\start_rvc_training.bat
``````

Or:

``````powershell
powershell -ExecutionPolicy Bypass -File scripts\start_rvc_training.ps1
``````

Training writes under this folder only (``logs/``, ``assets/weights/``).
After training, export / copy the ``.pth`` into ``assets/weights`` so the cover plugin can use it:

``````powershell
powershell -ExecutionPolicy Bypass -File scripts\sync_rvc_weights.ps1
``````

## Refresh from source

``````powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
# slim infer-only:
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1 -SkipTraining
# include UVR tab assets:
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1 -IncludeUvr
``````
"@ | Set-Content -Path $readme -Encoding UTF8

Write-Host "Done. Vendored RVC ready at: $DestRoot"
if ($IncludeTraining) {
    Write-Host "Training UI: start with scripts\start_rvc_training.bat"
}
Write-Host "Original source was not modified."
