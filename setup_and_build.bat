@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  N.E.K.O check + deps + frontend build
echo ========================================
echo.

echo [1/4] Checking tools...
where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js not found. Install Node ^>=20.19 then retry.
  echo         Or run install_env.bat
  echo         https://nodejs.org/
  goto :fail
)
where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm not found.
  goto :fail
)
where uv >nul 2>&1
if errorlevel 1 (
  echo [ERROR] uv not found. Run install_env.bat first.
  echo         Or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  goto :fail
)

echo   node:
node -v
echo   npm:
npm -v
echo   uv:
uv --version
echo.

echo [2/4] uv sync...
uv sync
if errorlevel 1 (
  echo [ERROR] uv sync failed
  goto :fail
)
echo.

echo [3/4] Building frontend...
call "%~dp0build_frontend.bat"
if errorlevel 1 (
  echo [ERROR] frontend build failed
  goto :fail
)
echo.

echo [4/4] Verifying outputs...
set "OK=1"
if not exist "static\react\neko-chat\neko-chat-window.iife.js" (
  echo [MISSING] static\react\neko-chat\neko-chat-window.iife.js
  set "OK=0"
) else (
  echo [OK] static\react\neko-chat\neko-chat-window.iife.js
)
if not exist "frontend\plugin-manager\dist\index.html" (
  echo [MISSING] frontend\plugin-manager\dist\index.html
  set "OK=0"
) else (
  echo [OK] frontend\plugin-manager\dist\index.html
)

if "%OK%"=="0" goto :fail

echo.
echo ========================================
echo  Done. Start with:
echo    uv run python launcher.py
echo  Then open: http://localhost:48911
echo ========================================
pause
exit /b 0

:fail
echo.
echo ========================================
echo  Failed. Fix errors above and retry.
echo ========================================
pause
exit /b 1
