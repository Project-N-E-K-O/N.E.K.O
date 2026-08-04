@echo off
setlocal
cd /d "%~dp0"

if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

echo Removing broken .venv ...
if exist ".venv" rmdir /s /q ".venv"

echo Creating Python 3.11 venv ...
uv python install 3.11
uv venv --python 3.11 .venv
if not exist ".venv\Scripts\python.exe" (
  echo FAILED: python.exe missing. Antivirus may be blocking uv.
  echo Allow: uv.exe, python.exe, and folder d:\N.E.K.O
  pause
  exit /b 1
)

echo uv sync ...
uv sync --python ".venv\Scripts\python.exe"
if errorlevel 1 (
  echo uv sync FAILED
  pause
  exit /b 1
)

echo OK. Next: run install_env.bat again for frontend, or start_neko.bat if frontend already built.
pause
exit /b 0
