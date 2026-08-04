@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  N.E.K.O Desktop (Steam Electron shell)
echo ========================================
echo.

if not exist "%~dp0desktop_release\N.E.K.O.exe" (
  echo [ERROR] desktop_release\N.E.K.O.exe missing.
  echo Run: scripts\import_steam_desktop.bat
  pause
  exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [ERROR] .venv missing. Run install_env.bat first.
  pause
  exit /b 1
)

if not exist "%~dp0static\react\neko-chat\neko-chat-window.iife.js" (
  echo [WARN] frontend not built. Run install_env.bat / build_frontend.bat
  echo.
)

set "NEKO_SRC_ROOT=%~dp0"
if "%NEKO_SRC_ROOT:~-1%"=="\" set "NEKO_SRC_ROOT=%NEKO_SRC_ROOT:~0,-1%"
echo NEKO_SRC_ROOT=%NEKO_SRC_ROOT%

rem Local ASR / HuggingFace downloads: default to mirror unless user overrides.
if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"
echo HF_ENDPOINT=%HF_ENDPOINT%

rem Prefer GPU Whisper when cuBLAS DLLs are available (e.g. local Torch/RVC).
for /f "usebackq delims=" %%I in (`"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\prepare_cuda_asr_path.py"`) do set "NEKO_CUDA_DLL_DIR=%%I"
if defined NEKO_CUDA_DLL_DIR (
  set "PATH=%NEKO_CUDA_DLL_DIR%;%PATH%"
  echo NEKO_CUDA_DLL_DIR=%NEKO_CUDA_DLL_DIR%
  if not defined NEKO_WHISPER_DEVICE set "NEKO_WHISPER_DEVICE=cuda"
  rem medium = much better Mandarin than small; 5070 Ti can hold it.
  if not defined NEKO_WHISPER_MODEL set "NEKO_WHISPER_MODEL=medium"
) else (
  echo [WARN] No cublas DLL found; local ASR stays on CPU.
  if not defined NEKO_WHISPER_DEVICE set "NEKO_WHISPER_DEVICE=cpu"
  if not defined NEKO_WHISPER_MODEL set "NEKO_WHISPER_MODEL=base"
)
rem Speech gate: only open ASR turns on sustained real speech.
if not defined NEKO_VAD_ONSET set "NEKO_VAD_ONSET=0.55"
if not defined NEKO_VAD_MIN_SPEECH_MS set "NEKO_VAD_MIN_SPEECH_MS=350"
if not defined NEKO_WHISPER_LANGUAGE set "NEKO_WHISPER_LANGUAGE=zh"
if not defined NEKO_WHISPER_BEAM_SIZE set "NEKO_WHISPER_BEAM_SIZE=5"
echo NEKO_WHISPER_MODEL=%NEKO_WHISPER_MODEL% NEKO_WHISPER_DEVICE=%NEKO_WHISPER_DEVICE%
echo NEKO_WHISPER_LANGUAGE=%NEKO_WHISPER_LANGUAGE% NEKO_WHISPER_BEAM_SIZE=%NEKO_WHISPER_BEAM_SIZE%
echo NEKO_VAD_ONSET=%NEKO_VAD_ONSET% NEKO_VAD_MIN_SPEECH_MS=%NEKO_VAD_MIN_SPEECH_MS%

echo.
echo [0/3] Clear stale single-instance lock / orphan backend...
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\clear_stale_neko_runtime.py" --force
echo.

rem If local AI was configured, ensure Edge TTS bridge is listening.
if exist "%LOCALAPPDATA%\N.E.K.O\config\.neko_local_ai_seeded" (
  curl -s -m 1 http://127.0.0.1:19000/health >nul 2>&1
  if errorlevel 1 (
    echo [1/3] Starting Edge TTS bridge on :19000 (no console)...
    if exist "%~dp0.venv\Scripts\pythonw.exe" (
      start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\edge_tts_bridge.py" --port 19000
    ) else (
      start "" /b "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\edge_tts_bridge.py" --port 19000
    )
    timeout /t 2 /nobreak >nul
  ) else (
    echo [1/3] Edge TTS bridge already up.
  )
) else (
  echo [1/3] Local AI marker not found; skip Edge TTS auto-start.
)

if exist "%LOCALAPPDATA%\N.E.K.O\config\.neko_local_ai_seeded" (
  echo [1.5/3] Verifying local voice-turn ONNX assets...
  "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\prepare_voice_turn_assets.py" --offline >nul 2>&1
  if errorlevel 1 (
    echo      Missing Smart Turn/Silero models. Downloading via hf-mirror...
    "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\prepare_voice_turn_assets.py"
    if errorlevel 1 (
      echo [WARN] voice-turn assets still missing. Local mic may start then drop.
      echo        Run install_local_asr.bat when network is available.
    ) else (
      echo      Voice-turn assets ready.
    )
  ) else (
    echo      Voice-turn assets OK.
  )
  echo [1.6/3] Warming Whisper in background ^(does not block Electron^)...
  start "" /b "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\warm_local_asr.py"
)

echo [2/3] Starting Electron shell...
echo      If a window flashes and vanishes, another N.E.K.O is in the tray.
echo      Check the system tray, or re-run this bat ^(it force-clears locks^).
echo.

start "" "%~dp0desktop_release\N.E.K.O.exe"

echo [3/3] Waiting for backend health on :48911 ...
set "OK="
for /L %%I in (1,1,40) do (
  curl -s -m 1 http://127.0.0.1:48911/health >nul 2>&1
  if not errorlevel 1 (
    set "OK=1"
    goto :healthy
  )
  timeout /t 1 /nobreak >nul
)

:healthy
if defined OK (
  echo [OK] Backend is up: http://127.0.0.1:48911
) else (
  echo [WARN] Backend health not ready yet.
  echo        Check: %%LOCALAPPDATA%%\N.E.K.O\logs\
  echo        Electron log: %%APPDATA%%\N.E.K.O\neko-electron-debug.log
)

echo.
pause
exit /b 0
