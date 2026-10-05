@echo off
setlocal
chcp 65001 >nul 2>&1

rem Best-effort Windows 7 bootstrap: create the project .venv and install the
rem pinned dependencies with plain pip. uv and Node.js are not supported on
rem Windows 7, so this script deliberately avoids both; the frontend static
rem build is already committed to the repository.
rem
rem Usage:
rem     setup_win7.bat
rem     setup_win7.bat "C:\path\to\python-3.11.exe"

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

set "PY_EXE=%~1"
if "%PY_EXE%"=="" set "PY_EXE=python"

echo [N.E.K.O] Windows 7 bootstrap ^| interpreter: %PY_EXE%

"%PY_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [N.E.K.O] ERROR: cannot run "%PY_EXE%".
    echo           Install a Windows 7 build of Python 3.11 first, see
    echo           docs\zh-CN\guide\windows-7.md
    exit /b 1
)

set "PY_VERSION="
for /f "delims=" %%v in ('"%PY_EXE%" --version 2^>^&1') do set "PY_VERSION=%%v"
echo [N.E.K.O] %PY_VERSION%
echo %PY_VERSION%| findstr /r /c:"Python 3\.11" >nul
if errorlevel 1 (
    echo [N.E.K.O] ERROR: this project requires Python 3.11, found: %PY_VERSION%
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [N.E.K.O] creating virtual environment .venv ...
    "%PY_EXE%" -m venv .venv
    if errorlevel 1 (
        echo [N.E.K.O] ERROR: failed to create .venv
        exit /b 1
    )
)

echo [N.E.K.O] upgrading pip (non-fatal if it fails) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 echo [N.E.K.O] WARN: pip upgrade failed, continuing with the bundled pip

echo [N.E.K.O] installing pinned dependencies with pip, this may take a few minutes ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [N.E.K.O] ERROR: pip install failed.
    echo           See docs\zh-CN\guide\windows-7.md for troubleshooting.
    exit /b 1
)

echo.
echo [N.E.K.O] Done. Start the app with:
echo     .venv\Scripts\python launcher.py
echo     then open http://localhost:48911 in Chrome 109 or Firefox 115 ESR.
exit /b 0
