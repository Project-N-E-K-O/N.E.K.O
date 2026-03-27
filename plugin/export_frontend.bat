@echo off
setlocal

rem Build plugin manager UI into plugin/frontend/vue-project/dist (single artifact path).
set "PLUGIN_DIR=%~dp0"
if "%PLUGIN_DIR:~-1%"=="\" set "PLUGIN_DIR=%PLUGIN_DIR:~0,-1%"

set "FRONTEND_DIR=%PLUGIN_DIR%\frontend\vue-project"
set "DIST_DIR=%FRONTEND_DIR%\dist"

if not exist "%FRONTEND_DIR%" (
  echo [export_frontend] frontend dir not found: %FRONTEND_DIR%
  exit /b 1
)

echo [export_frontend] building in: %FRONTEND_DIR%
pushd "%FRONTEND_DIR%" >nul
call npm run build-only
if errorlevel 1 (
  popd >nul
  echo [export_frontend] npm build failed
  exit /b 1
)
popd >nul

if not exist "%DIST_DIR%\index.html" (
  echo [export_frontend] build output missing: %DIST_DIR%\index.html
  exit /b 1
)

echo [export_frontend] done: %DIST_DIR%
exit /b 0
