@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "FORGE_SERVER_ROOT=%PROJECT_ROOT%\local_server\card_forge_server"
set "FRONTEND_ROOT=%PROJECT_ROOT%\card-forge"

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

echo ====================================================
echo   Neko Card Forge - One Click Startup
echo ====================================================
echo Project root: "%PROJECT_ROOT%"
echo.

echo [1/3] Opening N.E.K.O main server window (port 48911)...
start "N.E.K.O Main Server - 48911" "%ComSpec%" /k "cd /d ""%PROJECT_ROOT%"" && uv run .\launcher.py"

timeout /t 3 /nobreak >nul

echo [2/3] Opening card forge server window (port 3001)...
start "Neko Card Forge Server - 3001" "%ComSpec%" /k "cd /d ""%FORGE_SERVER_ROOT%"" && uv run server.py"

timeout /t 2 /nobreak >nul

echo [3/3] Opening card-forge frontend window (port 5173)...
start "Neko Card Forge Frontend - 5173" "%ComSpec%" /k "cd /d ""%FRONTEND_ROOT%"" && npm run dev"

echo.
echo ====================================================
echo   Startup commands have been sent to 3 windows.
echo ====================================================
echo URLs:
echo   card-forge:   http://127.0.0.1:5173
echo   N.E.K.O main: http://localhost:48911
echo   Forge server: http://localhost:3001/health
echo.
echo Keep the three opened command windows running while testing.
echo To stop these services later, run:
echo   "%PROJECT_ROOT%\stop-card-forge.bat"
pause
