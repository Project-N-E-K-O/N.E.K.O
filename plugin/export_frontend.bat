@echo off
setlocal enabledelayedexpansion

rem Resolve project root as the directory where this bat lives
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

set "FRONTEND_DIR=%PROJECT_ROOT%\frontend\vue-project"
set "DIST_DIR=%FRONTEND_DIR%\dist"
set "EXPORT_DIR=%PROJECT_ROOT%\frontend\exported"

echo %EXPORT_DIR% | findstr /I /C:"%PROJECT_ROOT%" >nul
if errorlevel 1 (
  echo [export_frontend] EXPORT_DIR outside project: %EXPORT_DIR%
  exit /b 1
)

if "%EXPORT_DIR%"=="%SystemDrive%\" (
  echo [export_frontend] EXPORT_DIR points to protected location
  exit /b 1
)

if not exist "%FRONTEND_DIR%" (
  echo [export_frontend] frontend dir not found: %FRONTEND_DIR%
  exit /b 1
)

echo [export_frontend] building frontend in: %FRONTEND_DIR%
pushd "%FRONTEND_DIR%" >nul
call npm run build-only
if errorlevel 1 (
  popd >nul
  echo [export_frontend] npm build failed
  exit /b 1
)
popd >nul

if not exist "%DIST_DIR%" (
  echo [export_frontend] build output not found: %DIST_DIR%
  exit /b 1
)

echo [export_frontend] exporting dist -^> %EXPORT_DIR%
if "%EXPORT_DIR%"=="" (
  echo [export_frontend] EXPORT_DIR is empty, refusing to delete
  exit /b 1
)

if exist "%EXPORT_DIR%" (
  rmdir /s /q "%EXPORT_DIR%"
  if errorlevel 1 (
    echo [export_frontend] failed to remove old export directory
    exit /b 1
  )
)
mkdir "%EXPORT_DIR%" >nul 2>&1

rem Copy dist contents into exported\ (robocopy returns codes > 0 for success too)
robocopy "%DIST_DIR%" "%EXPORT_DIR%" /E /NFL /NDL /NJH /NJS
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo [export_frontend] robocopy failed with code %RC%
  exit /b %RC%
)

echo [export_frontend] done. exported at: %EXPORT_DIR%
exit /b 0
