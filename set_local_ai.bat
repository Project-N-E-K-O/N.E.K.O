@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  Switch N.E.K.O to LOCAL AI
echo  Text/Vision: Ollama
echo  TTS: Edge TTS bridge (local)
echo  ASR: faster-whisper (local)
echo ========================================
echo.

if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "D:\ai\python.exe" set "PY=D:\ai\python.exe"
if not defined PY (
  where py >nul 2>&1 && for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
)
if not defined PY (
  echo [ERROR] No Python found
  pause
  exit /b 1
)

echo [1/5] Ensure edge-tts...
"%PY%" -m pip install -U edge-tts >nul 2>&1

echo [2/5] Ensure faster-whisper (local ASR)...
"%PY%" -m pip install -U faster-whisper
if errorlevel 1 (
  echo [WARN] faster-whisper install failed. Mic ASR will not work locally.
  echo        Re-run install_local_asr.bat later.
)

echo [3/5] Check Ollama...
curl -s -m 2 http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
  echo [WARN] Ollama not running.
  echo        Install https://ollama.com then:
  echo          ollama pull qwen2.5:7b
  echo          ollama pull llava
) else (
  echo [OK] Ollama is up
  where ollama >nul 2>&1 && (
    echo Pulling models if missing...
    ollama pull qwen2.5:7b
    ollama pull llava
  )
)

echo [4/5] Write local core_config...
"%PY%" "%~dp0scripts\set_local_ai.py" --tts edge --chat-model qwen2.5:7b --vision-model llava --verify
if errorlevel 1 (
  echo [ERROR] set_local_ai failed
  pause
  exit /b 1
)

echo [5/5] Start Edge TTS bridge in new window...
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [WARN] ffmpeg missing. TTS conversion needs it.
  echo        winget install Gyan.FFmpeg
  echo        Then re-run start_edge_tts.bat
)
start "Neko Edge TTS" cmd /k "cd /d "%~dp0" && "%PY%" scripts\edge_tts_bridge.py --port 19000"

echo.
echo DONE.
echo 1. Keep the Edge TTS window open
echo 2. Restart N.E.K.O desktop (start_desktop.bat)
echo 3. Text: Ollama  /  TTS: Edge  /  Mic ASR: faster-whisper
echo 4. Voice LLM reply may still use free Core cloud
echo.
pause
exit /b 0
