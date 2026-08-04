@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title N.E.K.O RVC Training (vendor)
echo.
echo  RVC voice training — vendored copy (does not touch D:\RVC)
echo  UI: http://127.0.0.1:7897
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_rvc_training.ps1" %*
if errorlevel 1 (
  echo.
  echo [ERROR] Failed. Ensure vendor\rvc was set up:
  echo   powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
  pause
  exit /b 1
)
pause
