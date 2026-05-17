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

$script:uvExe = $null

function Get-UvExe {
    if ($script:uvExe) {
        return $script:uvExe
    }

    $bundledUvCandidates = @(
        (Join-Path $repoRoot "bin\uv.exe"),
        (Join-Path $scriptDir "bin\uv.exe")
    )
    foreach ($candidate in $bundledUvCandidates) {
        if (Test-Path $candidate) {
            $script:uvExe = $candidate
            return $script:uvExe
        }
    }

    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "uv is required to create or repair the local TTS environment. This package can run without system uv only when a bundled runtime is already complete or bin\uv.exe is included."
    }

    $script:uvExe = $cmd.Source
    return $script:uvExe
}

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache-local"
}

if (-not (Test-Path $env:UV_CACHE_DIR)) {
    New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR | Out-Null
}

$launcherPython = if ($env:NEKO_LAUNCHER_PYTHON) {
    $env:NEKO_LAUNCHER_PYTHON
} else {
    $repoVenvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $repoVenvPython) { $repoVenvPython } else { "python" }
}

if (-not $env:LOCAL_TTS_DEFAULT_MODEL) {
    $env:LOCAL_TTS_DEFAULT_MODEL = "kokoro"
}

function Normalize-KokoroProfile {
    param([string]$Value)

    $raw = if ($null -eq $Value) { "" } else { "$Value" }
    $raw = $raw.Trim().ToLowerInvariant()
    if ($raw -match "kokoro[-_]?en|(^|[-_])en$|^en$|english|^kokoro:(af|am|bf|bm)_|^(af|am|bf|bm)_") {
        return "kokoro-en"
    }
    if (($raw -match "kokoro-82m") -and ($raw -notmatch "zh")) {
        return "kokoro-en"
    }
    return "kokoro-zh"
}

function Get-KokoroProfileDefaults {
    param([string]$Profile)

    if ((Normalize-KokoroProfile $Profile) -eq "kokoro-en") {
        return [pscustomobject]@{
            Profile = "kokoro-en"
            RepoId = "hexgrad/Kokoro-82M"
            DefaultVoice = "af_heart"
            ModelDirCandidates = @(
                "Kokoro-82M",
                "Kokoro-82M-v1.0",
                "Kokoro-82M-en"
            )
        }
    }

    return [pscustomobject]@{
        Profile = "kokoro-zh"
        RepoId = "hexgrad/Kokoro-82M-v1.1-zh"
        DefaultVoice = "zf_001"
        ModelDirCandidates = @(
            "Kokoro-82M-v1.1-zh"
        )
    }
}

function Get-NekoSavedKokoroProfileHint {
    $code = @'
import json
from pathlib import Path

try:
    from utils.config_manager import get_config_manager
    path = get_config_manager().get_config_path("core_config.json")
except Exception:
    path = Path("config") / "core_config.json"

try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
except Exception:
    data = {}

print(data.get("localKokoroProfile") or data.get("ttsModelId") or data.get("ttsVoiceId") or "")
'@

    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = & $launcherPython -c $code 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            return (($output | Select-Object -Last 1) | Out-String).Trim()
        }
    } catch {
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return ""
}

$savedKokoroProfileHint = if ($env:LOCAL_TTS_KOKORO_PROFILE) {
    $env:LOCAL_TTS_KOKORO_PROFILE
} elseif ($env:LOCAL_TTS_KOKORO_REPO_ID) {
    $env:LOCAL_TTS_KOKORO_REPO_ID
} else {
    Get-NekoSavedKokoroProfileHint
}
$kokoroProfile = Normalize-KokoroProfile $savedKokoroProfileHint
$kokoroProfileDefaults = Get-KokoroProfileDefaults $kokoroProfile
$env:LOCAL_TTS_KOKORO_PROFILE = $kokoroProfileDefaults.Profile

# Hugging Face auto-download is intentionally disabled for now.
# The local TTS launcher supports user-managed Kokoro models under kokoro_models,
# but it should not silently download large model files or decide model provenance
# until the expected download/update policy is agreed.
$env:LOCAL_TTS_KOKORO_DISABLE_HF_DOWNLOAD = "1"

if (-not $env:LOCAL_TTS_KOKORO_DEFAULT_VOICE) {
    $env:LOCAL_TTS_KOKORO_DEFAULT_VOICE = $kokoroProfileDefaults.DefaultVoice
}

if (-not $env:LOCAL_TTS_DEFAULT_VOICE) {
    $env:LOCAL_TTS_DEFAULT_VOICE = "kokoro:$env:LOCAL_TTS_KOKORO_DEFAULT_VOICE"
}

if (-not $env:LOCAL_TTS_KOKORO_REPO_ID) {
    $env:LOCAL_TTS_KOKORO_REPO_ID = $kokoroProfileDefaults.RepoId
}

if (-not $env:LOCAL_TTS_KOKORO_MODEL_DIR) {
    foreach ($candidateName in $kokoroProfileDefaults.ModelDirCandidates) {
        $candidateDir = Join-Path $scriptDir ("kokoro_models\" + $candidateName)
        if (Test-Path $candidateDir) {
            $env:LOCAL_TTS_KOKORO_MODEL_DIR = $candidateDir
            break
        }
    }
}

if (-not $env:LOCAL_TTS_KOKORO_MODEL_DIR) {
    $candidateList = ($kokoroProfileDefaults.ModelDirCandidates -join ", ")
    throw "No local Kokoro model directory found for profile $($kokoroProfileDefaults.Profile). Expected one of: $candidateList under local_server\local_tts_server\kokoro_models. Hugging Face auto-download is disabled intentionally: this launcher currently expects users to download and manage Kokoro model files themselves."
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
$runtimePythonOverride = if ($env:LOCAL_TTS_PYTHON) { $env:LOCAL_TTS_PYTHON } else { "" }
$venvPython = if ($runtimePythonOverride) { $runtimePythonOverride } else { Join-Path $localTtsVenv "Scripts\python.exe" }
$cudaInstallFailedMarker = Join-Path $localTtsVenv ".cuda_torch_install_failed"

function Invoke-UvChecked {
    param([string[]]$Arguments)

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $uv = Get-UvExe
        $output = & $uv @Arguments 2>&1
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

function Test-NvidiaGpuAvailable {
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidiaSmi) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $output = & $nvidiaSmi.Source -L 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) {
                return $true
            }
        } catch {
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }

    try {
        $controllers = Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue
        return [bool]($controllers | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1)
    } catch {
        return $false
    }
}

function Ensure-LocalTtsRuntime {
    if ($runtimePythonOverride) {
        if (-not (Test-Path $venvPython)) {
            throw "LOCAL_TTS_PYTHON points to a missing Python runtime: $venvPython"
        }
        return
    }

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

    $hasNvidiaGpu = Test-NvidiaGpuAvailable
    $torchProbe = Test-VenvPython "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())"
    if ($torchProbe.Ok -and ($torchProbe.Text -match "True")) {
        return
    }

    if (-not $hasNvidiaGpu) {
        if (-not $torchProbe.Ok) {
            Write-Host "No NVIDIA GPU detected; skipping CUDA torch install. Common deps will provide CPU torch when needed." -ForegroundColor Yellow
        }
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

Ensure-LocalTtsRuntime
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

function Format-LocalUriHost {
    param([string]$HostAddress)

    $value = if ($null -eq $HostAddress) { "" } else { "$HostAddress" }
    $trimmed = $value.Trim()
    if (-not $trimmed) {
        return "127.0.0.1"
    }
    if ($trimmed.StartsWith("[") -and $trimmed.EndsWith("]")) {
        return $trimmed
    }
    if ($trimmed.Contains(":")) {
        return "[{0}]" -f $trimmed
    }
    return $trimmed
}

$serverScript = Join-Path $scriptDir "server.py"
$launcherScript = Join-Path $repoRoot "launcher.py"
$uriHost = Format-LocalUriHost $env:LOCAL_TTS_HOST
$wsUrl = "ws://{0}:{1}" -f $uriHost, $env:LOCAL_TTS_PORT
$healthUrl = "http://{0}:{1}/health" -f $uriHost, $env:LOCAL_TTS_PORT
$existingLocalTtsKept = $false

function Normalize-LocalHostAddress {
    param([string]$HostAddress)

    $value = if ($null -eq $HostAddress) { "" } else { "$HostAddress" }
    switch ($value.Trim().ToLowerInvariant()) {
        "localhost" { return "127.0.0.1" }
        "::1" { return "127.0.0.1" }
        default { return $value.Trim() }
    }
}

function Get-PortOwner {
    param(
        [string]$HostAddress,
        [int]$Port
    )

    $normalizedHost = Normalize-LocalHostAddress $HostAddress
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        foreach ($connection in $connections) {
            $connectionAddress = Normalize-LocalHostAddress $connection.LocalAddress
            if ($connectionAddress -eq $normalizedHost -or $connection.LocalAddress -in @("0.0.0.0", "::")) {
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

function Test-IsNekoLocalTtsHealth {
    param([object]$Health)

    return $Health -and
        $Health.status -eq "ok" -and
        $Health.service -eq "neko-local-tts" -and
        $Health.engines -contains "kokoro"
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
    $isLocalTts = Test-IsNekoLocalTtsHealth $health

    if ($KeepExisting) {
        if ($isLocalTts) {
            Write-Host "Port $Port already has a NEKO local TTS server; keeping existing process because -KeepExisting was passed." -ForegroundColor Yellow
            Write-Host "PID       : $($owner.Pid)"
            Write-Host "Process   : $($owner.Name)"
            Write-Host "Path      : $($owner.Path)"
            $script:existingLocalTtsKept = $true
            return
        }

        throw "Port $Port is already used by PID $($owner.Pid) ($($owner.Name)), but it is not a healthy NEKO local TTS server. Stop it first, or set LOCAL_TTS_PORT to another port."
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
Write-Host "Profile   : $env:LOCAL_TTS_KOKORO_PROFILE"
Write-Host "Repo ID   : $env:LOCAL_TTS_KOKORO_REPO_ID"
Write-Host "Model Dir : $env:LOCAL_TTS_KOKORO_MODEL_DIR"
Write-Host "Voice     : $env:LOCAL_TTS_DEFAULT_VOICE"
Write-Host "Mode      : $env:LOCAL_TTS_SYNTHESIS_MODE"
Write-Host "Device    : $env:LOCAL_TTS_KOKORO_DEVICE"
Write-Host "Warmup    : on_connect=$env:LOCAL_TTS_WARMUP_ON_CONNECT startup=$env:LOCAL_TTS_STARTUP_WARMUP"
if ($runtimePythonOverride) {
    Write-Host "Runtime   : bundled Python at $venvPython"
} else {
    Write-Host "Runtime   : uv isolated venv at $localTtsVenv"
}
Write-Host "Launcher  : $launcherPython"
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
        $health = Test-LocalTtsHealth -Url $healthUrl
        if (Test-IsNekoLocalTtsHealth $health) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        throw "Kokoro local TTS server failed to become ready: $healthUrl"
    }

    Write-Host "Kokoro local TTS server ready, launching NEKO launcher..." -ForegroundColor Green
    & $launcherPython $launcherScript
    exit $LASTEXITCODE
}
finally {
    Write-Host "NEKO launcher exited. Kokoro server window is still open for log inspection." -ForegroundColor Yellow
}
