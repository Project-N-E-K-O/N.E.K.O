@echo off
setlocal

echo ====================================================
echo   Neko Card Forge - One Click Stop
echo ====================================================
echo This will stop the services opened by start-card-forge.bat.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-card-forge.ps1"

echo.
echo ====================================================
echo   Stop command finished.
echo ====================================================
pause
