@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "MATCH_SERVER_ROOT=%PROJECT_ROOT%\local_server\battle_arena_server"
set "FRONTEND_ROOT=%PROJECT_ROOT%\battle-arena"

if not exist "%PROJECT_ROOT%\launcher.py" (
  echo [startup error] N.E.K.O launcher not found:
  echo   "%PROJECT_ROOT%\launcher.py"
  pause
  exit /b 1
)

if not exist "%MATCH_SERVER_ROOT%\server.py" (
  echo [startup error] Match server not found:
  echo   "%MATCH_SERVER_ROOT%\server.py"
  pause
  exit /b 1
)

if not exist "%FRONTEND_ROOT%\package.json" (
  echo [startup error] Battle arena frontend not found:
  echo   "%FRONTEND_ROOT%\package.json"
  pause
  exit /b 1
)

echo ====================================================
echo   Neko Battle Arena - One Click Startup
echo ====================================================
echo Project root: "%PROJECT_ROOT%"
echo.

echo [1/3] Opening N.E.K.O main server window (port 48911)...
start "N.E.K.O Main Server - 48911" "%ComSpec%" /k "cd /d ""%PROJECT_ROOT%"" && uv run .\launcher.py"

timeout /t 3 /nobreak >nul

echo [2/3] Opening matchmaking server window (port 3001)...
start "Neko Battle Arena Match Server - 3001" "%ComSpec%" /k "cd /d ""%MATCH_SERVER_ROOT%"" && uv run server.py"

timeout /t 2 /nobreak >nul

echo [3/3] Opening battle-arena frontend window (port 5173)...
start "Neko Battle Arena Frontend - 5173" "%ComSpec%" /k "cd /d ""%FRONTEND_ROOT%"" && npm run dev"

echo.
echo ====================================================
echo   Startup commands have been sent to 3 windows.
echo ====================================================
echo URLs:
echo   battle-arena: http://127.0.0.1:5173
echo   N.E.K.O main: http://localhost:48911
echo   Match server: http://localhost:3001/health
echo.
echo Keep the three opened command windows running while testing.
echo To stop these services later, run:
echo   "%PROJECT_ROOT%\stop-battle-arena.bat"
pause
