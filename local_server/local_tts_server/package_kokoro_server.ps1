[CmdletBinding()]
param(
    [string]$OutputDir,
    [string]$PackageName = "neko-kokoro-local-tts",
    [ValidateSet("portable-python", "venv")]
    [string]$RuntimeMode = "portable-python",
    [ValidateSet("cpu", "cuda", "none")]
    [string]$TorchVariant = "cpu",
    [switch]$NoZip,
    [switch]$SkipEnv,
    [switch]$SkipModels,
    [switch]$SkipBundledUv
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

function Convert-ToPackageRelativePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootPrefix = $packageRoot.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar

    if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $fullPath.Substring($rootPrefix.Length)
    }

    return $fullPath
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

function Install-MinimalKokoroRuntimeDeps {
    param([Parameter(Mandatory = $true)][string]$PythonExe)

    if ($TorchVariant -eq "cpu") {
        Write-Host "Installing CPU torch into package runtime..." -ForegroundColor Yellow
        Invoke-Uv @(
            "pip", "install",
            "--python", $PythonExe,
            "--index-url", "https://download.pytorch.org/whl/cpu",
            "--force-reinstall",
            "torch"
        )
    } elseif ($TorchVariant -eq "cuda") {
        Write-Host "Installing CUDA torch into package runtime..." -ForegroundColor Yellow
        Invoke-Uv @(
            "pip", "install",
            "--python", $PythonExe,
            "--index-url", "https://download.pytorch.org/whl/cu128",
            "--force-reinstall",
            "torch==2.11.0+cu128"
        )
    }

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
    Invoke-Uv (@("pip", "install", "--python", $PythonExe) + $basePackages)

    Write-Host "Installing spaCy English model..." -ForegroundColor Yellow
    Invoke-Uv @(
        "pip", "install",
        "--python", $PythonExe,
        "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
    )
}

function New-MinimalVenv {
    $venvRoot = Join-Path $packageRoot ".venv-local-tts"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Host "Creating minimal local TTS venv: $venvRoot" -ForegroundColor Yellow
        Invoke-Uv @("venv", $venvRoot, "--python", "3.11")
    }

    Install-MinimalKokoroRuntimeDeps -PythonExe $venvPython

    return $venvPython
}

function New-PortablePythonRuntime {
    $pythonRoot = Join-Path $packageRoot "python-runtime"

    Write-Host "Installing portable Python runtime into package: $pythonRoot" -ForegroundColor Yellow
    Invoke-Uv @(
        "python", "install",
        "3.11",
        "--install-dir", $pythonRoot,
        "--no-bin",
        "--no-registry"
    )

    $pythonCandidates = @(
        Get-ChildItem -LiteralPath $pythonRoot -Recurse -Filter "python.exe" -File |
            Where-Object { $_.FullName -notmatch "\\Scripts\\" } |
            Sort-Object FullName
    )
    if ($pythonCandidates.Count -eq 0) {
        throw "uv installed Python, but no python.exe was found under $pythonRoot"
    }

    $runtimePython = $pythonCandidates[0].FullName
    Install-MinimalKokoroRuntimeDeps -PythonExe $runtimePython
    return $runtimePython
}

function Copy-BundledUv {
    if ($SkipBundledUv) {
        return
    }

    $uvExe = Get-UvExe
    $binDir = Join-Path $packageRoot "bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    Copy-Item -LiteralPath $uvExe -Destination (Join-Path $binDir "uv.exe") -Force
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
    if ($RuntimeMode -eq "portable-python") {
        $runtimePython = New-PortablePythonRuntime
    } else {
        $runtimePython = New-MinimalVenv
    }
    Copy-BundledUv
} else {
    $runtimePython = $null
}

$runtimeEnvLines = @()
if ($runtimePython -and $RuntimeMode -eq "portable-python") {
    $runtimeEnvLines += 'set "LOCAL_TTS_PYTHON=%~dp0' + (Convert-ToPackageRelativePath $runtimePython) + '"'
} elseif ($runtimePython) {
    $runtimeEnvLines += 'set "LOCAL_TTS_VENV_DIR=%~dp0.venv-local-tts"'
}

$serverOnlyArgs = "-ServerOnly"
if ($TorchVariant -eq "cpu") {
    $serverOnlyArgs += " -CpuOnly"
}

$rootBat = @"
@echo off
setlocal
cd /d "%~dp0"
$($runtimeEnvLines -join "`r`n")
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_server\local_tts_server\start_kokoro_server.ps1" $serverOnlyArgs
endlocal
"@
Set-Content -Path (Join-Path $packageRoot "start_kokoro_local_tts.bat") -Value $rootBat -Encoding ASCII

$readme = @()
$readme += '# NEKO Kokoro Local TTS Package'
$readme += ''
$readme += 'This package contains a standalone Kokoro runtime for the NEKO local TTS server.'
if (-not $SkipEnv) {
    if ($RuntimeMode -eq "portable-python") {
        $readme += 'It includes a bundled Python runtime and Kokoro dependencies.'
    } else {
        $readme += 'It includes a bundled Python venv and Kokoro dependencies.'
    }
    if (-not $SkipBundledUv) {
        $readme += 'A bundled bin\uv.exe is included for runtime repair when dependencies are missing.'
    }
} else {
    $readme += 'This build does not include a bundled Python environment.'
}
$readme += "Torch variant: $TorchVariant."
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
$readme += 'This package starts NEKO''s own Kokoro-compatible WebSocket server. It does not expose a third-party Kokoro HTTP API.'
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
