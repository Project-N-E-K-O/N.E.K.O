@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

rem Background work after Electron is already launched (do not block desktop icon).

if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"

for /f "usebackq delims=" %%I in (`".venv\Scripts\python.exe" "scripts\prepare_cuda_asr_path.py" 2^>nul`) do set "NEKO_CUDA_DLL_DIR=%%I"
if defined NEKO_CUDA_DLL_DIR (
  set "PATH=%NEKO_CUDA_DLL_DIR%;%PATH%"
  if not defined NEKO_WHISPER_DEVICE set "NEKO_WHISPER_DEVICE=cuda"
  if not defined NEKO_WHISPER_MODEL set "NEKO_WHISPER_MODEL=medium"
) else (
  if not defined NEKO_WHISPER_DEVICE set "NEKO_WHISPER_DEVICE=cpu"
  if not defined NEKO_WHISPER_MODEL set "NEKO_WHISPER_MODEL=base"
)

if exist "%LOCALAPPDATA%\N.E.K.O\config\.neko_local_ai_seeded" (
  curl -s -m 1 http://127.0.0.1:19000/health >nul 2>&1
  if errorlevel 1 (
    echo [post] start Edge TTS bridge
    if exist ".venv\Scripts\pythonw.exe" (
      start "" /b ".venv\Scripts\pythonw.exe" "scripts\edge_tts_bridge.py" --port 19000
    ) else (
      start "" /b ".venv\Scripts\python.exe" "scripts\edge_tts_bridge.py" --port 19000
    )
  ) else (
    echo [post] Edge TTS already up
  )

  echo [post] prepare voice-turn / warm ASR
  ".venv\Scripts\python.exe" "scripts\prepare_voice_turn_assets.py" --offline
  if errorlevel 1 ".venv\Scripts\python.exe" "scripts\prepare_voice_turn_assets.py"
  ".venv\Scripts\python.exe" "scripts\warm_local_asr.py"
)

set "OK="
for /L %%I in (1,1,30) do (
  curl -s -m 1 http://127.0.0.1:48911/health >nul 2>&1
  if not errorlevel 1 (
    set "OK=1"
    goto :done
  )
  timeout /t 1 /nobreak >nul
)

:done
if defined OK (
  echo [post] backend health OK
) else (
  echo [post] WARN backend health not ready yet
)
exit /b 0
