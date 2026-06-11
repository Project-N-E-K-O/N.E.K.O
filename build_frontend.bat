@echo off
setlocal
chcp 65001 >nul 2>&1

rem Build all frontend projects.
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

set "FAIL=0"

rem --- 0. yui-origin Live2D model (unpack from assets/) ---
set "YUI_ARCHIVE=%ROOT_DIR%\assets\yui-origin.tar.gz"
set "YUI_DIR=%ROOT_DIR%\static\yui-origin"
set "YUI_MARKER=%YUI_DIR%\yui-origin.moc3"

if not exist "%YUI_ARCHIVE%" (
  echo [build_frontend] yui-origin archive missing: %YUI_ARCHIVE%
  exit /b 1
)

set "YUI_NEED_EXTRACT=0"
if not exist "%YUI_MARKER%" (
  set "YUI_NEED_EXTRACT=1"
) else (
  for /f %%I in ('powershell -NoProfile -Command "if ((Get-Item -LiteralPath $env:YUI_ARCHIVE).LastWriteTime -gt (Get-Item -LiteralPath $env:YUI_MARKER).LastWriteTime) {1} else {0}"') do set "YUI_NEED_EXTRACT=%%I"
)

if "%YUI_NEED_EXTRACT%"=="1" (
  echo [build_frontend] unpacking yui-origin...
  if exist "%YUI_DIR%" rmdir /s /q "%YUI_DIR%"
  tar -xzmf "%YUI_ARCHIVE%" -C "%ROOT_DIR%\static"
  if errorlevel 1 (
    echo [build_frontend] yui-origin unpack failed
    exit /b 1
  )
  if not exist "%YUI_MARKER%" (
    echo [build_frontend] yui-origin marker missing after unpack: %YUI_MARKER%
    exit /b 1
  )
  echo [build_frontend] yui-origin done: %YUI_DIR%
) else (
  echo [build_frontend] yui-origin up to date, skip
)

rem --- 1. Plugin Manager (Vue) ---
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

rem --- 2. React Neko Chat ---
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
echo [build_frontend] all frontend projects built successfully.
exit /b 0
