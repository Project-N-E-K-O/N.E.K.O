@echo off
REM tests/run_testbench.cmd - Windows wrapper for run_testbench.ps1
REM
REM Purpose: Bypass the default PowerShell ExecutionPolicy (which blocks
REM unsigned local .ps1 files) without requiring the user to change
REM system-level policy. Each launch runs the PS1 in a sandboxed "Bypass"
REM scope tied only to this invocation.
REM
REM Usage (from project root):
REM     .\tests\run_testbench.cmd            REM default port 48920
REM     .\tests\run_testbench.cmd -Port 48921
REM     .\tests\run_testbench.cmd -Force     REM kill any holder of the port
REM
REM Works with double-click from Explorer too (opens in a new console
REM window and stays open until uvicorn exits).

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_testbench.ps1" %*
