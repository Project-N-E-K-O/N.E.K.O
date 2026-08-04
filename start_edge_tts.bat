@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install -U edge-tts
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo Install ffmpeg first: winget install Gyan.FFmpeg
)
"%PY%" "%~dp0scripts\edge_tts_bridge.py" --port 19000
pause
