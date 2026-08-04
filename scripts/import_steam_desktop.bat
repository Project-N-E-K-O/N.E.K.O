@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

set "STEAM=E:\SteamLibrary\steamapps\common\n.e.k.o"
set "DEST=%CD%\desktop_release"
set "EXTRACT=%CD%\.tmp_asar_extract"

if not exist "%STEAM%\N.E.K.O.exe" (
  echo [ERROR] Steam install not found: %STEAM%
  exit /b 1
)
if not exist "%EXTRACT%\src\main.js" (
  echo [ERROR] Missing extracted/patched asar at %EXTRACT%
  echo Run scripts\extract_asar.py first.
  exit /b 1
)

echo [1/4] Copy Electron shell (exclude resources\bin ~1.9GB)...
if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%"
robocopy "%STEAM%" "%DEST%" /E /XD bin /NFL /NDL /NJH /NJS /nc /ns /np
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo [ERROR] robocopy failed code %RC%
  exit /b 1
)

echo [2/4] Replace app.asar with patched unpacked app...
if exist "%DEST%\resources\app.asar" del /f /q "%DEST%\resources\app.asar"
if exist "%DEST%\resources\app" rmdir /s /q "%DEST%\resources\app"
mkdir "%DEST%\resources\app"
robocopy "%EXTRACT%" "%DEST%\resources\app" /E /NFL /NDL /NJH /NJS /nc /ns /np
if errorlevel 8 (
  echo [ERROR] copy patched app failed
  exit /b 1
)

echo [3/4] Ensure resources\bin absent so source launcher is used...
if exist "%DEST%\resources\bin" rmdir /s /q "%DEST%\resources\bin"

echo [4/4] Write marker...
> "%DEST%\USE_SOURCE_BACKEND.txt" echo This Electron shell launches d:\N.E.K.O\.venv + launcher.py
>> "%DEST%\USE_SOURCE_BACKEND.txt" echo Set NEKO_SRC_ROOT to override.

echo.
echo DONE: %DEST%\N.E.K.O.exe
echo Start with: start_desktop.bat
exit /b 0
