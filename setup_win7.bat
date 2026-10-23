@echo off
setlocal

rem Best-effort Windows 7 bootstrap: create the project .venv, install the
rem pinned dependencies with plain pip and unpack the built-in avatar models.
rem uv, Node.js and tar.exe are not available on Windows 7, so this script
rem only uses the Python interpreter. The two Node-built frontend bundles
rem (chat window and plugin manager) still have to be built on another
rem machine and copied over, see docs\zh-CN\guide\windows-7.md.
rem
rem Usage:
rem     setup_win7.bat
rem     setup_win7.bat "C:\path\to\python.exe"

set "PY_EXE=%~1"
if "%PY_EXE%"=="" set "PY_EXE=python"
rem Resolve an interpreter given as a relative path before changing directory.
rem A bare name such as "python" is not a file here and keeps its PATH lookup.
if not "%~1"=="" if exist "%~f1" set "PY_EXE=%~f1"

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

set "VENV_PY=.venv\Scripts\python.exe"
rem Version checks use the exit code instead of parsing "python --version"
rem output, so interpreter paths with spaces or parentheses keep working.
set "PY311_CHECK=import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)"

echo [N.E.K.O] Windows 7 bootstrap
if not exist "%VENV_PY%" goto :create_venv
rem Every later step runs inside .venv, so a rerun does not need PY_EXE to be
rem runnable (for example when the interpreter was only passed the first time).
echo [N.E.K.O] checking existing virtual environment .venv ...
"%VENV_PY%" --version
"%VENV_PY%" -c "%PY311_CHECK%"
if errorlevel 1 goto :bad_venv
goto :venv_ready

:create_venv
echo [N.E.K.O] interpreter: "%PY_EXE%"
"%PY_EXE%" --version
if errorlevel 1 goto :no_python
"%PY_EXE%" -c "%PY311_CHECK%"
if errorlevel 1 goto :wrong_python
echo [N.E.K.O] creating virtual environment .venv ...
"%PY_EXE%" -m venv .venv
if errorlevel 1 goto :venv_failed

:venv_ready
rem build_frontend.bat steps 0 and 1, without uv or tar.exe. They only need
rem the standard library, so run them before pip: a failed pip install (for
rem example a network error) then still leaves the models in place.
echo [N.E.K.O] unpacking built-in PNGTuber models ...
"%VENV_PY%" scripts\unpack_builtin_pngtuber.py
if errorlevel 1 goto :unpack_failed
echo [N.E.K.O] unpacking built-in Live2D models ...
"%VENV_PY%" scripts\unpack_builtin_live2d.py
if errorlevel 1 goto :unpack_failed

"%VENV_PY%" -m pip --version >nul 2>&1
if not errorlevel 1 goto :pip_ready
echo [N.E.K.O] pip is missing in .venv, bootstrapping it with ensurepip ...
"%VENV_PY%" -m ensurepip --upgrade
if errorlevel 1 goto :bad_venv

:pip_ready
echo [N.E.K.O] upgrading pip, non-fatal if it fails ...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 echo [N.E.K.O] WARN: pip upgrade failed, continuing with the bundled pip

echo [N.E.K.O] installing pinned dependencies with pip, this may take a few minutes ...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_failed

set "FRONTEND_MISSING=0"
if not exist "static\react\neko-chat\neko-chat-window.iife.js" set "FRONTEND_MISSING=1"
if not exist "frontend\plugin-manager\dist\index.html" set "FRONTEND_MISSING=1"

echo.
echo [N.E.K.O] Done. Start the app with:
echo     .venv\Scripts\python.exe launcher.py
echo     then open http://localhost:48911 in Chrome 109 or Firefox 115 ESR.
if "%FRONTEND_MISSING%"=="0" exit /b 0

echo.
echo [N.E.K.O] WARN: frontend bundles built with Node.js are missing:
if not exist "static\react\neko-chat\neko-chat-window.iife.js" echo     static\react\neko-chat\         - chat window, the home page cannot chat without it
if not exist "frontend\plugin-manager\dist\index.html" echo     frontend\plugin-manager\dist\   - plugin manager page
echo     Node.js does not run on Windows 7. Check out the same commit on a
echo     Windows 10+, macOS or Linux machine, run build_frontend.bat or
echo     build_frontend.sh there, then copy the directories above into this
echo     folder. See docs\zh-CN\guide\windows-7.md
exit /b 0

:no_python
echo [N.E.K.O] ERROR: cannot run "%PY_EXE%".
echo           Install a Windows 7 build of Python 3.11 first, see
echo           docs\zh-CN\guide\windows-7.md
exit /b 1

:wrong_python
echo [N.E.K.O] ERROR: this project requires Python 3.11, "%PY_EXE%" is a different version.
exit /b 1

:bad_venv
echo [N.E.K.O] ERROR: the existing .venv is not a working Python 3.11 environment with pip.
echo           It was probably created by another Python or copied from another machine.
echo           Delete the .venv folder and run setup_win7.bat again.
exit /b 1

:venv_failed
echo [N.E.K.O] ERROR: failed to create .venv
exit /b 1

:pip_failed
echo [N.E.K.O] ERROR: pip install failed.
echo           See docs\zh-CN\guide\windows-7.md for troubleshooting.
exit /b 1

:unpack_failed
echo [N.E.K.O] ERROR: unpacking the built-in models failed, see the message above.
exit /b 1
