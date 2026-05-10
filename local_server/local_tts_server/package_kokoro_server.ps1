[CmdletBinding()]
param(
    [string]$OutputDir,
    [string]$PackageName = "neko-kokoro-local-tts",
    [switch]$NoZip,
    [switch]$SkipEnv,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
Set-Location $repoRoot

if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot "dist"
}

$outputRoot = [System.IO.Path]::GetFullPath($OutputDir)
$packageRoot = [System.IO.Path]::GetFullPath((Join-Path $outputRoot $PackageName))

if (-not (Test-Path $outputRoot)) {
    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
}

if (-not ($packageRoot.StartsWith($outputRoot, [System.StringComparison]::OrdinalIgnoreCase))) {
    throw "Refusing to write package outside output directory: $packageRoot"
}

if (Test-Path $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

function Get-UvExe {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "uv is required to build the package environment."
    }
    return $cmd.Source
}

function Invoke-Uv {
    param([string[]]$Arguments)
    $uvExe = Get-UvExe
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $uvExe @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "uv failed with exit code $LASTEXITCODE"
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$Exclude = @()
    )

    if (-not (Test-Path $Source)) {
        throw "Missing source: $Source"
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force -Exclude $Exclude
}

function New-MinimalVenv {
    $venvRoot = Join-Path $packageRoot ".venv-local-tts"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"

    if (Test-Path $venvPython) {
        return $venvPython
    }

    Write-Host "Creating minimal local TTS venv: $venvRoot" -ForegroundColor Yellow
    Invoke-Uv @("venv", $venvRoot, "--python", "3.11")

    $basePackages = @(
        "fastapi",
        "uvicorn[standard]",
        "numpy",
        "websockets",
        "soundfile",
        "spacy",
        "kokoro>=0.8.2",
        "misaki[zh]>=0.8.2"
    )

    Write-Host "Installing minimal Kokoro server dependencies..." -ForegroundColor Yellow
    Invoke-Uv (@("pip", "install", "--python", $venvPython) + $basePackages)

    Write-Host "Installing CUDA torch into package venv..." -ForegroundColor Yellow
    Invoke-Uv @(
        "pip", "install",
        "--python", $venvPython,
        "--index-url", "https://download.pytorch.org/whl/cu128",
        "--force-reinstall",
        "torch==2.11.0+cu128"
    )

    Write-Host "Installing spaCy English model..." -ForegroundColor Yellow
    Invoke-Uv @(
        "pip", "install",
        "--python", $venvPython,
        "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
    )

    return $venvPython
}

$packageServerDir = Join-Path $packageRoot "local_server\local_tts_server"
New-Item -ItemType Directory -Force -Path $packageServerDir | Out-Null

Copy-Item -LiteralPath (Join-Path $scriptDir "server.py") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "kokoro_cli.py") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "start_kokoro_server.ps1") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "start_kokoro_server.bat") -Destination $packageServerDir -Force

if (-not $SkipModels) {
    $modelsDir = Join-Path $scriptDir "kokoro_models"
    if (Test-Path $modelsDir) {
        Copy-Tree -Source $modelsDir -Destination (Join-Path $packageServerDir "kokoro_models") -Exclude @("__pycache__", "*.log")
    } else {
        New-Item -ItemType Directory -Force -Path (Join-Path $packageServerDir "kokoro_models") | Out-Null
    }
}

if (-not $SkipEnv) {
    New-MinimalVenv | Out-Null
}

$rootBat = @'
@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_server\local_tts_server\start_kokoro_server.ps1" -ServerOnly
endlocal
'@
Set-Content -Path (Join-Path $packageRoot "start_kokoro_local_tts.bat") -Value $rootBat -Encoding ASCII

$readme = @'
# NEKO Kokoro Local TTS Package

This package contains a standalone Kokoro local TTS server, a bundled Python
environment, and local model files.

## Start

Double click:

  start_kokoro_local_tts.bat

Or run from PowerShell:

  powershell -NoProfile -ExecutionPolicy Bypass -File local_server\local_tts_server\start_kokoro_server.ps1 -ServerOnly

## NEKO WebSocket URL

Use this in NEKO custom API TTS:

  ws://127.0.0.1:50000

## Notes

- The bundled environment is local to this package.
- Model files live under local_server\local_tts_server\kokoro_models.
- Hugging Face auto-download is intentionally disabled.
- If the bundled environment is removed, the launcher can rebuild it only when uv is installed.
'@
Set-Content -Path (Join-Path $packageRoot "README.txt") -Value $readme -Encoding ASCII

if (-not $NoZip) {
    $zipPath = Join-Path $outputRoot "$PackageName.zip"
    if (Test-Path $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $zipPath -Force
}

Write-Host "Package directory: $packageRoot" -ForegroundColor Green
if (-not $NoZip) {
    Write-Host "Package zip      : $(Join-Path $outputRoot "$PackageName.zip")" -ForegroundColor Green
}
