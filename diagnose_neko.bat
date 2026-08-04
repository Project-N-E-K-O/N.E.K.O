@echo off
setlocal
cd /d "%~dp0"
set "OUT=%~dp0diagnose_neko.txt"
echo ==== diagnose %DATE% %TIME% ==== > "%OUT%"

echo Writing diagnose_neko.txt ...
echo.

>>"%OUT%" echo [paths]
>>"%OUT%" echo cwd=%CD%
if exist ".venv\Scripts\python.exe" (>>"%OUT%" echo venv=YES) else (>>"%OUT%" echo venv=NO)
if exist "static\react\neko-chat\neko-chat-window.iife.js" (>>"%OUT%" echo frontend=YES) else (>>"%OUT%" echo frontend=NO)
if exist "launcher.py" (>>"%OUT%" echo launcher=YES) else (>>"%OUT%" echo launcher=NO)

>>"%OUT%" echo.
>>"%OUT%" echo [python import]
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; print(sys.version); import fastapi; print('fastapi', fastapi.__version__)" >> "%OUT%" 2>&1
) else (
  >>"%OUT%" echo no venv python
)

>>"%OUT%" echo.
>>"%OUT%" echo [ports]
netstat -ano | findstr "48911 48912 48913" >> "%OUT%" 2>&1

>>"%OUT%" echo.
>>"%OUT%" echo [health curl]
curl -s -m 3 -w "\nhttp_code=%%{http_code}\n" http://127.0.0.1:48911/health >> "%OUT%" 2>&1
curl -s -m 3 -w "\nhttp_code=%%{http_code}\n" http://127.0.0.1:48911/ >> "%OUT%" 2>&1

>>"%OUT%" echo.
>>"%OUT%" echo [done]
echo Done. Open diagnose_neko.txt and send it to me.
notepad "%OUT%"
pause
