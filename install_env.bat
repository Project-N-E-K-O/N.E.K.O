@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "LOG=%~dp0install_env.log"
echo ==== N.E.K.O install %DATE% %TIME% ==== > "%LOG%"

call :log ========================================
call :log  N.E.K.O env install
call :log  (progress shows here; details also in install_env.log)
call :log ========================================
echo.

rem Refresh PATH
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
if exist "%LocalAppData%\Programs\nodejs\node.exe" set "PATH=%LocalAppData%\Programs\nodejs;%PATH%"
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
if exist "%ProgramFiles%\Python311\python.exe" set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"

rem Project-local TEMP to reduce AV fights
set "NEKO_TEMP=%~dp0.neko_tmp"
if not exist "%NEKO_TEMP%" mkdir "%NEKO_TEMP%"
set "TEMP=%NEKO_TEMP%"
set "TMP=%NEKO_TEMP%"

set "HAS_NODE=0"
set "HAS_UV=0"
where node >nul 2>&1 && set "HAS_NODE=1"
where uv >nul 2>&1 && set "HAS_UV=1"

call :log [check]
if "%HAS_NODE%"=="1" (for /f "delims=" %%V in ('node -v 2^>nul') do call :log   node = %%V) else (call :log   node = MISSING)
if "%HAS_UV%"=="1" (for /f "delims=" %%V in ('uv --version 2^>nul') do call :log   uv = %%V) else (call :log   uv = MISSING)
echo.

if "%HAS_UV%"=="0" (
  call :log [1/6] Installing uv...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
  where uv >nul 2>&1
  if errorlevel 1 (
    call :log [ERROR] uv missing after install. Re-open terminal and retry.
    goto :fail
  )
) else (
  call :log [1/6] uv ok, skip
)

if "%HAS_NODE%"=="0" (
  call :log [2/6] Installing Node.js LTS via winget...
  where winget >nul 2>&1
  if errorlevel 1 (
    call :log [ERROR] winget missing. Install Node from https://nodejs.org/
    goto :fail
  )
  winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
  if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
  where node >nul 2>&1
  if errorlevel 1 (
    call :log [ERROR] node missing after install. Close window and re-run.
    goto :fail
  )
) else (
  call :log [2/6] Node ok, skip
)

call :log [3/6] Try Defender exclusions (optional)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-MpPreference -ExclusionPath 'd:\N.E.K.O' -ErrorAction Stop; 'defender-ok' } catch { 'defender-skip' }"

call :log [4/6] Create .venv ...
if exist "%~dp0.venv" (
  if not exist "%~dp0.venv\Scripts\python.exe" (
    call :log Removing broken .venv ...
    rmdir /s /q "%~dp0.venv"
  )
)

if exist "%~dp0.venv\Scripts\python.exe" (
  call :log [OK] existing .venv python found, skip create
  goto :deps
)

set "PY="
where py >nul 2>&1 && for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('uv python find 3.11 2^>nul') do set "PY=%%P"
)

if not defined PY (
  call :log No Python 3.11. Installing via winget...
  winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
  if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
  if exist "%ProgramFiles%\Python311\python.exe" set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"
  where py >nul 2>&1 && for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
  if not defined PY if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
  if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
)

if not defined PY (
  call :log [ERROR] Still no Python 3.11.
  call :log Install https://www.python.org/downloads/release/python-3119/
  call :log Check "Add python.exe to PATH", then re-run.
  goto :fail
)

call :log Using Python: %PY%
"%PY%" -m venv "%~dp0.venv"
if errorlevel 1 (
  call :log python -m venv failed, trying uv venv...
  uv venv --python 3.11 "%~dp0.venv"
)
if not exist "%~dp0.venv\Scripts\python.exe" (
  call :log [ERROR] .venv\Scripts\python.exe missing.
  call :log Disable Huorong/antivirus, then re-run.
  goto :fail
)
call :log [OK] venv ready

:deps
call :log [5/6] Install Python deps (may take several minutes)...
call :log Please wait - window is NOT stuck.
uv sync --python "%~dp0.venv\Scripts\python.exe"
if errorlevel 1 (
  call :log uv sync failed - fallback to pip...
  "%~dp0.venv\Scripts\python.exe" -m pip install -U pip setuptools wheel
  uv export --frozen --no-dev -o "%~dp0.neko_requirements.txt"
  if exist "%~dp0.neko_requirements.txt" (
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0.neko_requirements.txt"
  ) else (
    "%~dp0.venv\Scripts\python.exe" -m pip install -e .
  )
  "%~dp0.venv\Scripts\python.exe" -c "import fastapi; print('fastapi-ok')"
  if errorlevel 1 (
    call :log [ERROR] pip fallback failed
    goto :fail
  )
)
call :log [OK] Python deps installed

call :log [6/6] Building frontend (npm - may take a few minutes)...
call :log Please wait - progress will scroll below.
call "%~dp0build_frontend.bat"
if errorlevel 1 (
  call :log [ERROR] frontend build failed
  goto :fail
)

echo.
call :log ========================================
call :log  SUCCESS - env ready
call :log  Next: double-click start_neko.bat
call :log  Browser: http://localhost:48911
call :log ========================================
pause
exit /b 0

:fail
echo.
call :log ========================================
call :log  FAILED - also see install_env.log
call :log ========================================
pause
exit /b 1

:log
echo %*
>>"%LOG%" echo %*
goto :eof
