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
set "YUI_COMPLETE_MARKER=%YUI_DIR%\.unpacked"
set "YUI_TEMP_ROOT=%ROOT_DIR%\static\.%MODEL%.extract-%RANDOM%-%RANDOM%"
set "YUI_TEMP_DIR=%YUI_TEMP_ROOT%\%MODEL%"
set "YUI_TEMP_COMPLETE_MARKER=%YUI_TEMP_DIR%\.unpacked"
set "YUI_BACKUP_DIR=%ROOT_DIR%\static\.%MODEL%.backup-%RANDOM%-%RANDOM%"

if not exist "%YUI_ARCHIVE%" (
  echo [build_frontend] %MODEL% archive missing: %YUI_ARCHIVE%
  endlocal & exit /b 1
)

set "YUI_NEED_EXTRACT=0"
if not exist "%YUI_COMPLETE_MARKER%" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if not exist "%YUI_DIR%\%MODEL%.moc3" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if not exist "%YUI_DIR%\%MODEL%.model3.json" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if not exist "%YUI_DIR%\%MODEL%.physics3.json" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if not exist "%YUI_DIR%\%MODEL%.vtube.json" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if not exist "%YUI_DIR%\%MODEL%.4096\texture_00.png" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" set "YUI_COMPARE_RESULT_FILE=%TEMP%\neko-live2d-%RANDOM%-%RANDOM%.txt"
if "%YUI_NEED_EXTRACT%"=="0" call :compare_live2d_timestamps "%YUI_ARCHIVE%" "%YUI_COMPLETE_MARKER%" "%YUI_COMPARE_RESULT_FILE%"
if "%YUI_NEED_EXTRACT%"=="0" set "YUI_MTIME_STATUS=%ERRORLEVEL%"
if "%YUI_NEED_EXTRACT%"=="0" set /p "YUI_MTIME_RESULT="<"%YUI_COMPARE_RESULT_FILE%"
if "%YUI_NEED_EXTRACT%"=="0" if exist "%YUI_COMPARE_RESULT_FILE%" del /q "%YUI_COMPARE_RESULT_FILE%" >nul 2>&1
if "%YUI_NEED_EXTRACT%"=="0" if not "%YUI_MTIME_STATUS%"=="0" (
  echo [build_frontend] failed to compare timestamps for %MODEL% ^(exit %YUI_MTIME_STATUS%^)
  endlocal & exit /b %YUI_MTIME_STATUS%
)
if "%YUI_NEED_EXTRACT%"=="0" if /i "%YUI_MTIME_RESULT%"=="newer" set "YUI_NEED_EXTRACT=1"
if "%YUI_NEED_EXTRACT%"=="0" if /i not "%YUI_MTIME_RESULT%"=="older" (
  echo [build_frontend] invalid timestamp comparison result for %MODEL%: %YUI_MTIME_RESULT%
  endlocal & exit /b 1
)

if "%YUI_NEED_EXTRACT%"=="1" (
  echo [build_frontend] unpacking %MODEL%...
  if exist "%YUI_TEMP_ROOT%" rmdir /s /q "%YUI_TEMP_ROOT%"
  if exist "%YUI_TEMP_ROOT%" (
    echo [build_frontend] cannot clear temporary %MODEL% directory: %YUI_TEMP_ROOT%
    endlocal & exit /b 1
  )
  if exist "%YUI_BACKUP_DIR%" (
    echo [build_frontend] backup path already exists for %MODEL%: %YUI_BACKUP_DIR%
    endlocal & exit /b 1
  )
  mkdir "%YUI_TEMP_ROOT%"
  if errorlevel 1 (
    echo [build_frontend] cannot create temporary %MODEL% directory: %YUI_TEMP_ROOT%
    endlocal & exit /b 1
  )
  tar -xzmf "%YUI_ARCHIVE%" -C "%YUI_TEMP_ROOT%"
  if errorlevel 1 (
    echo [build_frontend] %MODEL% unpack failed
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  if not exist "%YUI_TEMP_DIR%\%MODEL%.moc3" (
    echo [build_frontend] %MODEL% moc3 missing after unpack
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  if not exist "%YUI_TEMP_DIR%\%MODEL%.model3.json" (
    echo [build_frontend] %MODEL% model3 config missing after unpack
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  if not exist "%YUI_TEMP_DIR%\%MODEL%.physics3.json" (
    echo [build_frontend] %MODEL% physics config missing after unpack
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  if not exist "%YUI_TEMP_DIR%\%MODEL%.vtube.json" (
    echo [build_frontend] %MODEL% vtube config missing after unpack
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  if not exist "%YUI_TEMP_DIR%\%MODEL%.4096\texture_00.png" (
    echo [build_frontend] %MODEL% texture missing after unpack
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  type nul > "%YUI_TEMP_COMPLETE_MARKER%"
  if not exist "%YUI_TEMP_COMPLETE_MARKER%" (
    echo [build_frontend] cannot create completion marker for %MODEL%
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  if exist "%YUI_DIR%" (
    move /y "%YUI_DIR%" "%YUI_BACKUP_DIR%" >nul
    if errorlevel 1 (
      echo [build_frontend] cannot preserve old %MODEL% directory: %YUI_DIR%
      echo [build_frontend] Close any process using the model files and try again.
      rmdir /s /q "%YUI_TEMP_ROOT%"
      endlocal & exit /b 1
    )
  )
  move /y "%YUI_TEMP_DIR%" "%YUI_DIR%" >nul
  if errorlevel 1 (
    echo [build_frontend] cannot replace old %MODEL% directory: %YUI_DIR%
    if exist "%YUI_BACKUP_DIR%" move /y "%YUI_BACKUP_DIR%" "%YUI_DIR%" >nul
    rmdir /s /q "%YUI_TEMP_ROOT%"
    endlocal & exit /b 1
  )
  rmdir /s /q "%YUI_TEMP_ROOT%"
  if exist "%YUI_BACKUP_DIR%" rmdir /s /q "%YUI_BACKUP_DIR%"
  echo [build_frontend] %MODEL% done: %YUI_DIR%
) else (
  echo [build_frontend] %MODEL% up to date, skip
)
endlocal & exit /b 0

:compare_live2d_timestamps
uv run --no-sync python -c "import os, sys; print('newer' if os.path.getmtime(sys.argv[1]) > os.path.getmtime(sys.argv[2]) else 'older')" "%~1" "%~2" > "%~3"
exit /b %ERRORLEVEL%
