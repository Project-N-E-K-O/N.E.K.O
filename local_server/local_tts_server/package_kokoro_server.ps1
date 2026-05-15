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

function Test-PathInsideDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ChildPath,
        [Parameter(Mandatory = $true)][string]$ParentPath
    )

    $childFull = [System.IO.Path]::GetFullPath($ChildPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $parentFull = [System.IO.Path]::GetFullPath($ParentPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $parentPrefix = $parentFull + [System.IO.Path]::DirectorySeparatorChar

    return $childFull.StartsWith($parentPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

if (-not (Test-Path $outputRoot)) {
    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
}

if (-not (Test-PathInsideDirectory -ChildPath $packageRoot -ParentPath $outputRoot)) {
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

function Test-SupportedKokoroModelDir {
    param([Parameter(Mandatory = $true)][System.IO.DirectoryInfo]$Directory)

    $supportedModelDirNames = @(
        "Kokoro-82M",
        "Kokoro-82M-v1.0",
        "Kokoro-82M-en",
        "Kokoro-82M-v1.1-zh"
    )
    if ($Directory.Name -notin $supportedModelDirNames) {
        return $false
    }

    $configPath = Join-Path $Directory.FullName "config.json"
    $modelFiles = @(Get-ChildItem -LiteralPath $Directory.FullName -Filter "*.pth" -File -ErrorAction SilentlyContinue)
    $voiceFiles = @(Get-ChildItem -LiteralPath (Join-Path $Directory.FullName "voices") -Filter "*.pt" -File -ErrorAction SilentlyContinue)
    return (Test-Path $configPath) -and $modelFiles.Count -gt 0 -and $voiceFiles.Count -gt 0
}

$packageServerDir = Join-Path $packageRoot "local_server\local_tts_server"
New-Item -ItemType Directory -Force -Path $packageServerDir | Out-Null

Copy-Item -LiteralPath (Join-Path $scriptDir "server.py") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "kokoro_cli.py") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "local_tts_profiles.py") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "start_kokoro_server.ps1") -Destination $packageServerDir -Force
Copy-Item -LiteralPath (Join-Path $scriptDir "start_kokoro_server.bat") -Destination $packageServerDir -Force

if (-not $SkipModels) {
    $modelsDir = Join-Path $scriptDir "kokoro_models"
    $availableModelDirs = @(
        Get-ChildItem -LiteralPath $modelsDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-SupportedKokoroModelDir $_ }
    )
    if (-not (Test-Path $modelsDir) -or $availableModelDirs.Count -eq 0) {
        throw "No supported Kokoro model directories found under $modelsDir. Pass -SkipModels only when you will provide LOCAL_TTS_KOKORO_MODEL_DIR at runtime."
    }
    Copy-Tree -Source $modelsDir -Destination (Join-Path $packageServerDir "kokoro_models") -Exclude @("__pycache__", "*.log")
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

$readme = @()
$readme += '# NEKO Kokoro Local TTS Package'
$readme += ''
$readme += 'This package contains a standalone Kokoro local TTS server.'
if (-not $SkipEnv) {
    $readme += 'It includes a bundled Python environment for offline start.'
} else {
    $readme += 'This build does not include a bundled Python environment.'
}
if (-not $SkipModels) {
    $readme += 'Local model files are included under local_server\local_tts_server\kokoro_models.'
} else {
    $readme += 'Local model files are not bundled in this build; place them under local_server\local_tts_server\kokoro_models or point LOCAL_TTS_KOKORO_MODEL_DIR at a local directory.'
}
$readme += ''
$readme += '## Start'
$readme += ''
$readme += 'Double click:'
$readme += ''
$readme += '  start_kokoro_local_tts.bat'
$readme += ''
$readme += 'Or run from PowerShell:'
$readme += ''
$readme += '  powershell -NoProfile -ExecutionPolicy Bypass -File local_server\local_tts_server\start_kokoro_server.ps1 -ServerOnly'
$readme += ''
$readme += '## NEKO WebSocket URL'
$readme += ''
$readme += 'Use this in NEKO custom API TTS:'
$readme += ''
$readme += '  ws://127.0.0.1:50000'
$readme += ''
$readme += '## Notes'
$readme += ''
$readme += '- Hugging Face auto-download is intentionally disabled.'
if (-not $SkipEnv) {
    $readme += '- If the bundled environment is removed, the launcher can rebuild it only when uv is installed.'
} else {
    $readme += '- This build expects uv to recreate the environment if you choose to rebuild it.'
}
$readme = $readme -join "`r`n"
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
