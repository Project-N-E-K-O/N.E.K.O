@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: ── UV & Python venv setup (same as start_neko_servers.bat) ──────────

where uv >nul 2>&1
if errorlevel 1 (
  echo [ERROR] uv ^(astral-sh/uv^) not found in PATH.
  echo         Install uv: https://astral.sh/uv
  exit /b 1
)

set "VENV_DIR=.venv"
set "REQ_PY=3.11"
set "VENV_PY=%~dp0%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo [WARN] Existing venv is not Python %REQ_PY%. Recreating: %VENV_DIR%
    rmdir /s /q "%VENV_DIR%" >nul 2>&1
  )
)

if not exist "%VENV_PY%" (
  echo [INFO] Creating UV venv: %VENV_DIR% ^(Python %REQ_PY%^)
  uv venv --python %REQ_PY% "%VENV_DIR%"
  if errorlevel 1 exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 exit /b 1

set "NEKO_DEPS_OK=0"
"%VENV_PY%" -c "import fastapi, uvicorn, websockets" >nul 2>&1
if not errorlevel 1 set "NEKO_DEPS_OK=1"

if "%NEKO_DEPS_OK%"=="1" (
  echo [INFO] Python dependencies already installed. Skipping install.
) else (
  echo [INFO] Installing Python dependencies...
  if exist "uv.lock" (
    uv sync
  ) else if exist "pyproject.toml" (
    uv sync
  ) else if exist "requirements.txt" (
    uv pip install -r requirements.txt
  ) else (
    echo [ERROR] No uv.lock / pyproject.toml / requirements.txt found.
    exit /b 1
  )
  if errorlevel 1 exit /b 1
)

:: ── Launch mode selection ────────────────────────────────────────────

echo.
echo ========================================
echo        N.E.K.O. 启动器
echo ========================================
echo.
echo   [1] 仅启动后端 (Python 服务)
echo   [2] 启动后端 + 桌面端 (Electron)
echo.
choice /c 12 /n /m "请选择启动模式 [1/2]: "
set "LAUNCH_MODE=%ERRORLEVEL%"

if not exist "launcher.py" (
  echo [ERROR] launcher.py not found
  exit /b 1
)

if "%LAUNCH_MODE%"=="1" goto :start_backend_only

:: ── Check N.E.K.O.-PC prerequisites ─────────────────────────────────

set "PC_DIR=%~dp0N.E.K.O.-PC"

if not exist "%PC_DIR%\package.json" (
  echo [WARN] N.E.K.O.-PC\package.json not found. Falling back to backend only.
  goto :start_backend_only
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [WARN] npm not found in PATH. Falling back to backend only.
  goto :start_backend_only
)

if not exist "%PC_DIR%\node_modules\.package-lock.json" (
  echo [INFO] Installing N.E.K.O.-PC npm dependencies...
  pushd "%PC_DIR%"
  call npm install
  if errorlevel 1 (
    echo [WARN] npm install failed. Falling back to backend only.
    popd
    goto :start_backend_only
  )
  popd
)

:: ── Start Python backend in a new window ─────────────────────────────

echo [INFO] Starting Python backend (new window)...
start "N.E.K.O. Backend" cmd /c ""%VENV_PY%" "%~dp0launcher.py" & echo. & echo [Backend exited] & pause"

:: ── Wait for backend to be ready ─────────────────────────────────────

echo [INFO] Waiting for backend to be ready...
set "MAIN_PORT=48911"
set "MAX_WAIT=60"
set "WAITED=0"

:wait_loop
if %WAITED% geq %MAX_WAIT% (
  echo [WARN] Backend did not respond within %MAX_WAIT%s. Starting Electron anyway...
  goto :start_electron
)

"%VENV_PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%MAIN_PORT%/health', timeout=2)" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Backend is ready on port %MAIN_PORT%.
  goto :start_electron
)

timeout /t 2 /nobreak >nul
set /a WAITED+=2
echo [INFO] Waiting... (%WAITED%s / %MAX_WAIT%s)
goto :wait_loop

:: ── Start Electron (N.E.K.O.-PC) ────────────────────────────────────

:start_electron
echo [INFO] Starting N.E.K.O.-PC (Electron)...
pushd "%PC_DIR%"
start "N.E.K.O. PC" cmd /c "chcp 65001 >nul & npm start & echo. & echo [Electron exited] & pause"
popd

echo.
echo [INFO] All services started.
echo   - Backend:  new window (Python)
echo   - Frontend: new window (Electron)
echo.
echo Press any key to exit this launcher...
pause >nul
exit /b 0

:: ── Backend only ─────────────────────────────────────────────────────

:start_backend_only
echo [INFO] Starting backend only...
"%VENV_PY%" "%~dp0launcher.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [INFO] Launcher exited with code: %EXIT_CODE%
pause >nul
exit /b %EXIT_CODE%
