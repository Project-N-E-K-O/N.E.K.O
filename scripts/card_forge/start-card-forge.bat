@echo off
setlocal

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "FORGE_SERVER_ROOT=%PROJECT_ROOT%\local_server\card_forge_server"
set "FRONTEND_ROOT=%PROJECT_ROOT%\frontend\card-forge"

if not exist "%PROJECT_ROOT%\launcher.py" (
  echo [startup error] N.E.K.O launcher not found:
  echo   "%PROJECT_ROOT%\launcher.py"
  pause
  exit /b 1
)

if not exist "%FORGE_SERVER_ROOT%\server.py" (
  echo [startup error] Card forge server not found:
  echo   "%FORGE_SERVER_ROOT%\server.py"
  pause
  exit /b 1
)

if not exist "%FRONTEND_ROOT%\package.json" (
  echo [startup error] Card forge frontend not found:
  echo   "%FRONTEND_ROOT%\package.json"
  pause
  exit /b 1
)

if not exist "%FRONTEND_ROOT%\package-lock.json" (
  echo [startup error] Card forge package lock not found:
  echo   "%FRONTEND_ROOT%\package-lock.json"
  pause
  exit /b 1
)

if not exist "%FRONTEND_ROOT%\node_modules\.bin\vite.cmd" (
  where npm.cmd >nul 2>&1
  if errorlevel 1 (
    echo [startup error] npm.cmd not found. Install Node.js/npm and retry.
    pause
    exit /b 1
  )
  echo [preflight] Card forge dependencies are missing; running npm ci...
  pushd "%FRONTEND_ROOT%"
  call npm ci
  if errorlevel 1 (
    popd
    echo [startup error] npm ci failed for Card Forge.
    pause
    exit /b 1
  )
  popd
  if not exist "%FRONTEND_ROOT%\node_modules\.bin\vite.cmd" (
    echo [startup error] npm ci completed but Vite is still missing.
    pause
    exit /b 1
  )
)

set "MAIN_SERVER_PORT_VALUE=48911"
set "CARD_FORGE_PORT_VALUE=3001"
for /f "usebackq delims=" %%P in (`node "%FRONTEND_ROOT%\port-config.js" MAIN_SERVER_PORT 48911`) do set "MAIN_SERVER_PORT_VALUE=%%P"
for /f "usebackq delims=" %%P in (`node "%FRONTEND_ROOT%\port-config.js" CARD_FORGE_PORT 3001`) do set "CARD_FORGE_PORT_VALUE=%%P"
set "NEKO_MAIN_SERVER_PORT=%MAIN_SERVER_PORT_VALUE%"
set "NEKO_CARD_FORGE_PORT=%CARD_FORGE_PORT_VALUE%"

echo ====================================================
echo   Neko Card Forge - One Click Startup
echo ====================================================
echo Project root: "%PROJECT_ROOT%"
echo.

echo [1/3] Opening N.E.K.O main server window (port %MAIN_SERVER_PORT_VALUE%)...
start "N.E.K.O Main Server - %MAIN_SERVER_PORT_VALUE%" "%ComSpec%" /k "cd /d ""%PROJECT_ROOT%"" && uv run .\launcher.py"

timeout /t 3 /nobreak >nul

echo [2/3] Opening card forge server window (port %CARD_FORGE_PORT_VALUE%)...
start "Neko Card Forge Server - %CARD_FORGE_PORT_VALUE%" "%ComSpec%" /k "cd /d ""%FORGE_SERVER_ROOT%"" && uv run server.py"

timeout /t 2 /nobreak >nul

echo [3/3] Opening card-forge frontend window (port 5173)...
start "Neko Card Forge Frontend - 5173" "%ComSpec%" /k "cd /d ""%FRONTEND_ROOT%"" && npm run dev"

echo.
echo ====================================================
echo   Startup commands have been sent to 3 windows.
echo ====================================================
echo URLs:
echo   card-forge:   http://127.0.0.1:5173
echo   N.E.K.O main: http://localhost:%MAIN_SERVER_PORT_VALUE%
echo   Forge server: http://localhost:%CARD_FORGE_PORT_VALUE%/health
echo.
echo Keep the three opened command windows running while testing.
echo To stop these services later, run:
echo   "%~dp0stop-card-forge.bat"
pause
