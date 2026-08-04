@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_desktop_shortcut.ps1"
if errorlevel 1 (
  echo [ERROR] failed to create desktop shortcut
  pause
  exit /b 1
)
echo.
echo 桌面图标已更新。请双击桌面上的 N.E.K.O 启动。
pause
exit /b 0
