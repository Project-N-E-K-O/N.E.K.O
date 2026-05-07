[CmdletBinding()]
param(
    [switch]$ServerOnly,
    [switch]$RunServer,
    [switch]$CpuOnly,
    [switch]$KeepExisting,
    [switch]$RetryCudaInstall,
    [switch]$KeepServerWindow
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
Set-Location $repoRoot

$uvExe = (Get-Command uv -ErrorAction Stop).Source

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache-local"
}

if (-not (Test-Path $env:UV_CACHE_DIR)) {
    New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR | Out-Null
}

if (-not $env:LOCAL_TTS_DEFAULT_MODEL) {
    $env:LOCAL_TTS_DEFAULT_MODEL = "kokoro"
}

if (-not $env:LOCAL_TTS_KOKORO_DEFAULT_VOICE) {
    $env:LOCAL_TTS_KOKORO_DEFAULT_VOICE = "zf_001"
}

if (-not $env:LOCAL_TTS_DEFAULT_VOICE) {
    $env:LOCAL_TTS_DEFAULT_VOICE = "kokoro:$env:LOCAL_TTS_KOKORO_DEFAULT_VOICE"
}

if (-not $env:LOCAL_TTS_KOKORO_REPO_ID) {
    $env:LOCAL_TTS_KOKORO_REPO_ID = "hexgrad/Kokoro-82M-v1.1-zh"
}

if (-not $env:LOCAL_TTS_KOKORO_MODEL_DIR) {
    $localKokoroModelDir = Join-Path $scriptDir "kokoro_models\Kokoro-82M-v1.1-zh"
    if (Test-Path $localKokoroModelDir) {
        $env:LOCAL_TTS_KOKORO_MODEL_DIR = $localKokoroModelDir
    }
}

if (-not $env:LOCAL_TTS_KOKORO_CMD) {
    $kokoroCli = Join-Path $scriptDir "kokoro_cli.py"
    $env:LOCAL_TTS_KOKORO_CMD = '"{python}" "' + $kokoroCli + '" "{text_file}" "{out_file}" "{voice}" {speed}'
}

if (-not $env:LOCAL_TTS_SYNTHESIS_MODE) {
    $env:LOCAL_TTS_SYNTHESIS_MODE = "merged"
}

if (-not $env:LOCAL_TTS_WARMUP_ON_CONNECT) {
    $env:LOCAL_TTS_WARMUP_ON_CONNECT = "1"
}

if (-not $env:LOCAL_TTS_STARTUP_WARMUP) {
    $env:LOCAL_TTS_STARTUP_WARMUP = "0"
}

if (-not $env:LOCAL_TTS_HOST) {
    $env:LOCAL_TTS_HOST = "127.0.0.1"
}

if (-not $env:LOCAL_TTS_PORT) {
    $env:LOCAL_TTS_PORT = "50000"
}

$localTtsVenv = if ($env:LOCAL_TTS_VENV_DIR) { $env:LOCAL_TTS_VENV_DIR } else { Join-Path $repoRoot ".venv-local-tts" }
$venvPython = Join-Path $localTtsVenv "Scripts\python.exe"
$cudaInstallFailedMarker = Join-Path $localTtsVenv ".cuda_torch_install_failed"

function Invoke-UvChecked {
    param([string[]]$Arguments)

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $uvExe @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            ExitCode = $exitCode
            Text = ($output | Out-String)
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Test-VenvPython {
    param([string]$Code)

    if (-not (Test-Path $venvPython)) {
        return [pscustomobject]@{ Ok = $false; Text = "venv python missing" }
    }

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $venvPython -c $Code 2>&1
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            Ok = ($exitCode -eq 0)
            Text = ($output | Out-String)
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Ensure-LocalTtsVenv {
    if (-not (Test-Path $venvPython)) {
        Write-Host "Creating uv isolated local TTS venv: $localTtsVenv" -ForegroundColor Yellow
        $created = Invoke-UvChecked @("venv", $localTtsVenv, "--python", "3.11")
        if ($created.ExitCode -ne 0) {
            throw "Failed to create local TTS venv: $($created.Text.Trim())"
        }
    }
}

function Ensure-LocalTtsCommonDeps {
    $commonProbe = Test-VenvPython "import fastapi, uvicorn, kokoro, soundfile, numpy, websockets, spacy; import misaki; import en_core_web_sm; spacy.load('en_core_web_sm')"
    if (-not $commonProbe.Ok) {
        Write-Host "Installing Kokoro server dependencies into isolated uv venv. This happens only when missing." -ForegroundColor Yellow
        $installed = Invoke-UvChecked @(
            "pip", "install",
            "--python", $venvPython,
            "fastapi",
            "uvicorn[standard]",
            "websockets",
            "kokoro>=0.8.2",
            "misaki[zh]>=0.8.2",
            "soundfile",
            "numpy",
            "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
        )
        if ($installed.ExitCode -ne 0) {
            throw "Failed to install Kokoro server dependencies: $($installed.Text.Trim())"
        }
    }
}

function Ensure-CudaTorchIfNeeded {
    if ($CpuOnly) {
        return
    }

    $torchProbe = Test-VenvPython "import torch; print(torch.__version__); print(torch.cuda.is_available())"
    if ($torchProbe.Ok -and ($torchProbe.Text -match "True")) {
        return
    }

    if ((Test-Path $cudaInstallFailedMarker) -and -not $RetryCudaInstall) {
        Write-Host "CUDA torch is not ready in the local TTS venv, and a previous install attempt failed." -ForegroundColor Yellow
        Write-Host "Skipping online CUDA torch install this time. Run with -RetryCudaInstall to try again." -ForegroundColor Yellow
        return
    }

    Write-Host "Installing CUDA torch into local TTS venv. This is a one-time setup when missing." -ForegroundColor Yellow
    $installed = Invoke-UvChecked @(
        "pip", "install",
        "--python", $venvPython,
        "--force-reinstall",
        "--index-url", "https://download.pytorch.org/whl/cu128",
        "torch"
    )

    if ($installed.ExitCode -ne 0) {
        New-Item -ItemType File -Force -Path $cudaInstallFailedMarker | Out-Null
        Write-Host "CUDA torch install failed; keeping marker so future starts do not retry automatically." -ForegroundColor Yellow
        Write-Host $installed.Text.Trim() -ForegroundColor Yellow
        return
    }

    if (Test-Path $cudaInstallFailedMarker) {
        Remove-Item -LiteralPath $cudaInstallFailedMarker -Force -ErrorAction SilentlyContinue
    }
}

Ensure-LocalTtsVenv
Ensure-CudaTorchIfNeeded
Ensure-LocalTtsCommonDeps

$cudaProbe = if ($CpuOnly) {
    [pscustomobject]@{ Ok = $false; Text = "CPU forced by -CpuOnly" }
} else {
    Test-VenvPython "import torch; print(torch.__version__); print(torch.cuda.is_available())"
}

$useGpu = $cudaProbe.Ok -and ($cudaProbe.Text -match "True")
if ($useGpu) {
    $env:LOCAL_TTS_KOKORO_DEVICE = "cuda"
    Write-Host "CUDA torch detected in local TTS venv, Kokoro will request GPU." -ForegroundColor Green
} else {
    $env:LOCAL_TTS_KOKORO_DEVICE = "cpu"
    Write-Host "CUDA torch is unavailable in local TTS venv, Kokoro will use CPU." -ForegroundColor Yellow
    Write-Host "CUDA probe: $($cudaProbe.Text.Trim())" -ForegroundColor Yellow
}

$serverScript = Join-Path $scriptDir "server.py"
$launcherScript = Join-Path $repoRoot "launcher.py"
$wsUrl = "ws://{0}:{1}" -f $env:LOCAL_TTS_HOST, $env:LOCAL_TTS_PORT
$healthUrl = "http://{0}:{1}/health" -f $env:LOCAL_TTS_HOST, $env:LOCAL_TTS_PORT
$existingLocalTtsKept = $false

function Get-PortOwner {
    param(
        [string]$HostAddress,
        [int]$Port
    )

    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        foreach ($connection in $connections) {
            if ($connection.LocalAddress -eq $HostAddress -or $connection.LocalAddress -in @("0.0.0.0", "::")) {
                $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
                return [pscustomobject]@{
                    Pid = $connection.OwningProcess
                    Name = if ($process) { $process.ProcessName } else { "<unknown>" }
                    Path = if ($process) { $process.Path } else { "" }
                }
            }
        }
    } catch {
    }

    return $null
}

function Test-LocalTtsHealth {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) {
            return $null
        }
        return $response.Content | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-UserInterruptExitCode {
    param([int]$ExitCode)

    return $ExitCode -eq -1073741510 -or $ExitCode -eq 3221225786
}

function Stop-ExistingLocalTtsIfNeeded {
    param(
        [string]$HostAddress,
        [int]$Port,
        [string]$Url
    )

    $owner = Get-PortOwner -HostAddress $HostAddress -Port $Port
    if (-not $owner) {
        return
    }

    $health = Test-LocalTtsHealth -Url $Url
    $isLocalTts = $health -and $health.status -eq "ok" -and $health.engines

    if ($KeepExisting) {
        Write-Host "Port $Port is already in use; keeping existing process because -KeepExisting was passed." -ForegroundColor Yellow
        Write-Host "PID       : $($owner.Pid)"
        Write-Host "Process   : $($owner.Name)"
        Write-Host "Path      : $($owner.Path)"
        $script:existingLocalTtsKept = $true
        return
    }

    if ($isLocalTts) {
        Write-Host "Port $Port already has a NEKO local TTS server; stopping old process first." -ForegroundColor Yellow
        Write-Host "Old PID   : $($owner.Pid)"
        Write-Host "Old Device: $($health.device_request)"
        Stop-Process -Id $owner.Pid -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 800
        return
    }

    throw "Port $Port is already used by PID $($owner.Pid) ($($owner.Name)) at $($owner.Path). Stop it first, or set LOCAL_TTS_PORT to another port."
}

Stop-ExistingLocalTtsIfNeeded -HostAddress $env:LOCAL_TTS_HOST -Port ([int]$env:LOCAL_TTS_PORT) -Url $healthUrl

Write-Host ""
Write-Host "=== NEKO Kokoro Local TTS ===" -ForegroundColor Cyan
Write-Host "Repo Root : $repoRoot"
Write-Host "UV Cache  : $env:UV_CACHE_DIR"
Write-Host "Repo ID   : $env:LOCAL_TTS_KOKORO_REPO_ID"
Write-Host "Model Dir : $env:LOCAL_TTS_KOKORO_MODEL_DIR"
Write-Host "Voice     : $env:LOCAL_TTS_DEFAULT_VOICE"
Write-Host "Mode      : $env:LOCAL_TTS_SYNTHESIS_MODE"
Write-Host "Device    : $env:LOCAL_TTS_KOKORO_DEVICE"
Write-Host "Warmup    : on_connect=$env:LOCAL_TTS_WARMUP_ON_CONNECT startup=$env:LOCAL_TTS_STARTUP_WARMUP"
Write-Host "Runtime   : uv isolated venv at $localTtsVenv"
Write-Host "UV Cache  : $env:UV_CACHE_DIR"
Write-Host "WS URL    : $wsUrl"
Write-Host "Health    : $healthUrl"
Write-Host ""
Write-Host "Fill this in NEKO custom API TTS:" -ForegroundColor Yellow
Write-Host "  $wsUrl"
Write-Host ""

if ($RunServer) {
    if ($existingLocalTtsKept) {
        Write-Host "Existing Kokoro local TTS server is still running; no new server was started." -ForegroundColor Green
        exit 0
    }

    $serverRunArgs = @(
        $serverScript,
        "--host", $env:LOCAL_TTS_HOST,
        "--port", $env:LOCAL_TTS_PORT
    )
    & $venvPython @serverRunArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not (Test-UserInterruptExitCode $exitCode)) {
        Write-Host ""
        Write-Host "Kokoro local TTS server exited with code $exitCode." -ForegroundColor Red
        Write-Host "Press Enter to close this window." -ForegroundColor Yellow
        Read-Host | Out-Null
    }
    exit $exitCode
}

if ($ServerOnly) {
    if ($existingLocalTtsKept) {
        Write-Host "Existing Kokoro local TTS server is still running in another process." -ForegroundColor Green
        Write-Host "WS URL: $wsUrl"
        exit 0
    }

    $serverOnlyRunArgs = @(
        $serverScript,
        "--host", $env:LOCAL_TTS_HOST,
        "--port", $env:LOCAL_TTS_PORT
    )
    & $venvPython @serverOnlyRunArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not (Test-UserInterruptExitCode $exitCode)) {
        Write-Host ""
        Write-Host "Kokoro local TTS server exited with code $exitCode." -ForegroundColor Red
        Write-Host "Press Enter to close this window." -ForegroundColor Yellow
        Read-Host | Out-Null
    }
    exit $exitCode
}

$childArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $MyInvocation.MyCommand.Path,
    "-RunServer"
)
if ($CpuOnly) {
    $childArgs += "-CpuOnly"
}
if ($RetryCudaInstall) {
    $childArgs += "-RetryCudaInstall"
}
if ($KeepServerWindow) {
    $childArgs = @("-NoExit") + $childArgs
}

if (-not $existingLocalTtsKept) {
    $serverProcess = Start-Process -FilePath "powershell" `
        -ArgumentList $childArgs `
        -WorkingDirectory $repoRoot `
        -PassThru
}

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-WebRequest $healthUrl -UseBasicParsing -TimeoutSec 2
            if ($health.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
        }
    }

    if (-not $ready) {
        throw "Kokoro local TTS server failed to become ready: $healthUrl"
    }

    Write-Host "Kokoro local TTS server ready, launching NEKO launcher..." -ForegroundColor Green
    & python $launcherScript
    exit $LASTEXITCODE
}
finally {
    Write-Host "NEKO launcher exited. Kokoro server window is still open for log inspection." -ForegroundColor Yellow
}
