@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
  echo VCVARS_FAILED errorlevel=%errorlevel%
  exit /b 1
)
cd /d "C:\Users\Mr.hancard\WorkBuddy\2026-09-09-22-07-35\.workbuddy\neko_unpack"
echo === cwd ===
cd
echo === cl ===
where cl
echo === compile ===
cl /nologo /O2 /W4 neko_cursor_helper.c user32.lib /Fe:neko_cursor_helper.exe /Fo:neko_cursor_helper.obj
echo === exit %ERRORLEVEL% ===
dir neko_cursor_helper.*
exit /b %ERRORLEVEL%