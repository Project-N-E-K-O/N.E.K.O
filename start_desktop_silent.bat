@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Repo-local silent launcher (console). Desktop icon uses start_desktop_silent.vbs via shortcut.
set "LOG=%~dp0logs\start_desktop_silent.log"
set "POSTLOG=%~dp0logs\start_desktop_post.log"
if not exist "%~dp0logs" mkdir "%~dp0logs" >nul 2>&1
echo ==== silent start %DATE% %TIME% ==== > "%LOG%" 2>nul

if not exist "%~dp0desktop_release\N.E.K.O.exe" (
  echo [ERROR] desktop_release\N.E.K.O.exe missing>> "%LOG%" 2>nul
  exit /b 1
)
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [ERROR] .venv missing>> "%LOG%" 2>nul
  exit /b 1
)

set "NEKO_SRC_ROOT=%~dp0"
if "%NEKO_SRC_ROOT:~-1%"=="\" set "NEKO_SRC_ROOT=%NEKO_SRC_ROOT:~0,-1%"
echo NEKO_SRC_ROOT=%NEKO_SRC_ROOT%>> "%LOG%" 2>nul

if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"
if not defined NEKO_WHISPER_DEVICE set "NEKO_WHISPER_DEVICE=cuda"
if not defined NEKO_WHISPER_MODEL set "NEKO_WHISPER_MODEL=medium"

echo [0] clear stale runtime>> "%LOG%" 2>nul
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\clear_stale_neko_runtime.py" --force >> "%LOG%" 2>&1

echo [1] Starting Electron...>> "%LOG%" 2>nul
start "" "%~dp0desktop_release\N.E.K.O.exe"
echo [OK] N.E.K.O.exe launched>> "%LOG%" 2>nul

if exist "%~dp0scripts\_silent_post_start.bat" (
  start "" /min "%ComSpec%" /c ""%~dp0scripts\_silent_post_start.bat" > "%POSTLOG%" 2>&1"
)

exit /b 0
