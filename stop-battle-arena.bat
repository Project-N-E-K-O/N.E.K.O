@echo off
setlocal

echo ====================================================
echo   Neko Battle Arena - One Click Stop
echo ====================================================
echo This will stop the services opened by start-battle-arena.bat.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-battle-arena.ps1"

echo.
echo ====================================================
echo   Stop command finished.
echo ====================================================
pause
