@echo off
REM tests/testbench/smoke/_run_all.cmd
REM
REM One-click smoke runner for Windows. Sidesteps every PowerShell gotcha
REM by staying in native cmd.exe: no `&&` (cmd supports it fine), no heredocs,
REM no `-c` string escaping. Just hands off to the Python runner.
REM
REM Usage (from project root, or double-click from Explorer):
REM     .\tests\testbench\smoke\_run_all.cmd              :: all smokes
REM     .\tests\testbench\smoke\_run_all.cmd p25_*        :: subset
REM     .\tests\testbench\smoke\_run_all.cmd --list       :: list only
REM     .\tests\testbench\smoke\_run_all.cmd --fail-fast  :: stop on first fail
REM
REM The runner resolves .venv/python.exe itself, so no need to activate venv.

setlocal

REM Resolve project root: this script lives at <root>\tests\testbench\smoke\
REM %~dp0 = dir of this .cmd (with trailing backslash)
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\..\.."

REM Prefer the venv Python; fall back to `python` on PATH if missing.
set "VENV_PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    set "PY=%VENV_PY%"
) else (
    echo [smoke] Warning: %VENV_PY% not found; falling back to `python` on PATH.
    set "PY=python"
)

REM Hand off every argument (%*) untouched to the Python runner.
"%PY%" "%SCRIPT_DIR%_run_all.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

REM Only pause when launched by double-click (i.e. Explorer's console has no
REM extra args and is not a child of an agent tool). Detect by checking if
REM the parent cmd was invoked *without* /c, which is how Explorer does it.
REM When in doubt, skip the pause: agent/CI callers never want it, and a
REM human running from a shell can always re-read their terminal buffer.
echo %CMDCMDLINE% | find /i "/c " >nul
if errorlevel 1 (
    echo.
    pause
)

exit /b %EXIT_CODE%
