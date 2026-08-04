@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOG=%~dp0start_neko.log"
echo ==== start %DATE% %TIME% ==== > "%LOG%"

echo ========================================
echo  N.E.K.O start
echo ========================================
echo.

if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"

set "FAIL=0"

if not exist "static\react\neko-chat\neko-chat-window.iife.js" (
  echo [FAIL] frontend not built - run install_env.bat
  >>"%LOG%" echo [FAIL] frontend
  set "FAIL=1"
) else ( echo [OK] frontend )

if not exist ".venv\Scripts\python.exe" (
  echo [FAIL] .venv missing - run install_env.bat
  >>"%LOG%" echo [FAIL] venv
  set "FAIL=1"
) else ( echo [OK] .venv )

".venv\Scripts\python.exe" -c "import fastapi,uvicorn; print('[OK] python deps')"
if errorlevel 1 (
  echo [FAIL] python deps missing - run install_env.bat again
  >>"%LOG%" echo [FAIL] deps
  set "FAIL=1"
)

if "%FAIL%"=="1" (
  pause
  exit /b 1
)

echo.
echo KEEP THIS WINDOW OPEN.
echo Browser URL: http://127.0.0.1:48911
echo.
echo Waiting for startup logs below...
echo If the window closes or shows red traceback, copy it to me.
echo.

start "" cmd /c "timeout /t 25 /nobreak >nul & start http://127.0.0.1:48911/"

".venv\Scripts\python.exe" -u launcher.py
set "RC=%ERRORLEVEL%"

echo.
echo ========================================
echo launcher exited code %RC%
echo ========================================
>>"%LOG%" echo exited %RC%
if not "%RC%"=="0" (
  echo Startup failed. Scroll up for the error and send it to me.
)
pause
exit /b %RC%
