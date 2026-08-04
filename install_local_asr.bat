@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  Install local faster-whisper ASR
echo ========================================
echo.

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] .venv missing. Run install_env.bat first.
  pause
  exit /b 1
)

echo Installing faster-whisper into .venv ...
"%PY%" -m pip install -U faster-whisper
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)

echo.
echo Writing asrProvider=faster_whisper into local AI config...
"%PY%" "%~dp0scripts\set_local_ai.py" --tts edge --verify
if errorlevel 1 (
  echo [ERROR] set_local_ai failed
  pause
  exit /b 1
)

echo.
echo Preparing Smart Turn / Silero ONNX assets (required for local voice turn)...
if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"
"%PY%" "%~dp0scripts\prepare_voice_turn_assets.py"
if errorlevel 1 (
  echo [ERROR] voice-turn asset prepare failed
  echo         Check network access to GitHub / hf-mirror, then retry.
  pause
  exit /b 1
)

echo.
echo Warming faster-whisper model into local cache...
"%PY%" "%~dp0scripts\warm_local_asr.py"
if errorlevel 1 (
  echo [ERROR] Whisper model warm failed
  echo         Check network access to hf-mirror, then retry.
  pause
  exit /b 1
)

echo.
echo DONE.
echo 1. Restart start_desktop.bat
echo 2. GPU: start_desktop.bat auto-finds cublas and uses cuda+medium for Mandarin
echo 3. Optional env: NEKO_WHISPER_MODEL=large-v3 for max accuracy if VRAM allows
echo 4. Optional env: NEKO_WHISPER_DEVICE / NEKO_WHISPER_LANGUAGE / NEKO_WHISPER_BEAM_SIZE
echo 5. Speech gate env: NEKO_VAD_ONSET / NEKO_VAD_MIN_SPEECH_MS
echo 6. Optional env: HF_ENDPOINT (default https://hf-mirror.com)
echo.
pause
exit /b 0
