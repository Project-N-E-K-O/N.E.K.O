@echo off
setlocal
chcp 65001 >nul 2>&1

rem Build frontend projects shipped with production distributions.
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

set "FAIL=0"

where uv >nul 2>&1
if errorlevel 1 (
  echo [build_frontend] uv not found, please install uv
  exit /b 1
)

rem --- 0. Built-in PNGTuber models ---
uv run --no-sync python "%ROOT_DIR%\scripts\unpack_builtin_pngtuber.py"
if errorlevel 1 (
  echo [build_frontend] built-in PNGTuber unpack failed
  exit /b 1
)

rem --- 1. Built-in Live2D models (unpack from assets/) ---
call :unpack_live2d yui-origin
if errorlevel 1 exit /b 1
call :unpack_live2d yui-lolita
if errorlevel 1 exit /b 1

rem --- 2. Plugin Manager (Vue) ---
set "PM_DIR=%ROOT_DIR%\frontend\plugin-manager"
set "PM_DIST=%PM_DIR%\dist"

if not exist "%PM_DIR%" (
  echo [build_frontend] plugin-manager dir not found: %PM_DIR%
  exit /b 1
)

echo [build_frontend] building plugin-manager...
pushd "%PM_DIR%" >nul
call npm ci
if errorlevel 1 (
  popd >nul
  echo [build_frontend] npm ci failed for plugin-manager
  exit /b 1
)
call npm run build-only
if errorlevel 1 (
  popd >nul
  echo [build_frontend] build failed for plugin-manager
  exit /b 1
)
popd >nul

if not exist "%PM_DIST%\index.html" (
  echo [build_frontend] plugin-manager build output missing: %PM_DIST%\index.html
  exit /b 1
)
echo [build_frontend] plugin-manager done: %PM_DIST%

rem --- 3. React Neko Chat ---
set "RC_DIR=%ROOT_DIR%\frontend\react-neko-chat"
set "RC_DIST=%ROOT_DIR%\static\react\neko-chat"

if not exist "%RC_DIR%" (
  echo [build_frontend] react-neko-chat dir not found: %RC_DIR%
  exit /b 1
)

echo [build_frontend] building react-neko-chat...
pushd "%RC_DIR%" >nul
call npm ci
if errorlevel 1 (
  popd >nul
  echo [build_frontend] npm ci failed for react-neko-chat
  exit /b 1
)
call npm run build
if errorlevel 1 (
  popd >nul
  echo [build_frontend] build failed for react-neko-chat
  exit /b 1
)
popd >nul

if not exist "%RC_DIST%\neko-chat-window.iife.js" (
  echo [build_frontend] react-neko-chat build output missing: %RC_DIST%\neko-chat-window.iife.js
  exit /b 1
)
echo [build_frontend] react-neko-chat done: %RC_DIST%

echo.
echo [build_frontend] all production frontend projects built successfully.
exit /b 0

rem --- helper: unpack one built-in Live2D model ---
rem Each model archive in assets is unpacked into its matching static directory.
:unpack_live2d
setlocal EnableExtensions
set "MODEL=%~1"
set "YUI_ARCHIVE=%ROOT_DIR%\assets\%MODEL%.tar.gz"
set "YUI_DIR=%ROOT_DIR%\static\%MODEL%"
set "YUI_MARKER=%YUI_DIR%\%MODEL%.moc3"

if not exist "%YUI_ARCHIVE%" (
  echo [build_frontend] %MODEL% archive missing: %YUI_ARCHIVE%
  endlocal & exit /b 1
)

set "YUI_NEED_EXTRACT=0"
if not exist "%YUI_MARKER%" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" call :compare_live2d_timestamps "%YUI_ARCHIVE%" "%YUI_MARKER%"
if "%YUI_NEED_EXTRACT%"=="0" set "YUI_MTIME_STATUS=%ERRORLEVEL%"
if "%YUI_NEED_EXTRACT%"=="0" if "%YUI_MTIME_STATUS%"=="0" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if not "%YUI_MTIME_STATUS%"=="0" if not "%YUI_MTIME_STATUS%"=="1" (
  echo [build_frontend] failed to compare timestamps for %MODEL% ^(exit %YUI_MTIME_STATUS%^)
  endlocal & exit /b %YUI_MTIME_STATUS%
)

if "%YUI_NEED_EXTRACT%"=="1" (
  echo [build_frontend] unpacking %MODEL%...
  if exist "%YUI_DIR%" (
    rmdir /s /q "%YUI_DIR%"
    if exist "%YUI_DIR%" (
      echo [build_frontend] cannot remove old %MODEL% directory: %YUI_DIR%
      echo [build_frontend] Close any process using the model files and try again.
      endlocal & exit /b 1
    )
  )
  tar -xzmf "%YUI_ARCHIVE%" -C "%ROOT_DIR%\static"
  if errorlevel 1 (
    echo [build_frontend] %MODEL% unpack failed
    endlocal & exit /b 1
  )
  if not exist "%YUI_MARKER%" (
    echo [build_frontend] %MODEL% marker missing after unpack: %YUI_MARKER%
    endlocal & exit /b 1
  )
  echo [build_frontend] %MODEL% done: %YUI_DIR%
) else (
  echo [build_frontend] %MODEL% up to date, skip
)
endlocal & exit /b 0

:compare_live2d_timestamps
uv run --no-sync python -c "import os, sys; sys.exit(0 if os.path.getmtime(sys.argv[1]) > os.path.getmtime(sys.argv[2]) else 1)" "%~1" "%~2"
exit /b %ERRORLEVEL%
