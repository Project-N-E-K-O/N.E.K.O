# tests/run_testbench.ps1 — Testbench quick-start for Windows PowerShell
#
# Usage:
#   .\tests\run_testbench.ps1              # port 48920 (default)
#   .\tests\run_testbench.ps1 -Port 48921  # custom port
#   .\tests\run_testbench.ps1 -Force       # kill any process on target port first
#
# What it does:
#   1. Check if target port is occupied; offer to kill, change, or abort
#   2. Activate .venv/Scripts/Activate.ps1
#   3. uv run python tests/testbench/run_testbench.py --port <port>
#
# See P24_BLUEPRINT.md §12.6 / dev_note L24.

param(
    [int]$Port = 48920,
    [string]$TestbenchHost = '127.0.0.1',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# 1. Resolve project root (script lives at <root>/tests/run_testbench.ps1)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot
Write-Host "[run_testbench] project root: $ProjectRoot" -ForegroundColor DarkGray

# 2. Check port occupancy (LISTEN state only — FinWait/TimeWait are stale)
function Get-PortOwner {
    param([int]$P)
    $conns = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return $null }
    # Return the first owning process
    $ownerPid = ($conns | Select-Object -First 1).OwningProcess
    return Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
}

$owner = Get-PortOwner -P $Port
if ($owner) {
    Write-Host ""
    Write-Host "[WARN] Port $Port already in use:" -ForegroundColor Yellow
    Write-Host "  PID $($owner.Id) · $($owner.ProcessName) · started $($owner.StartTime)"
    Write-Host ""
    if ($Force) {
        Write-Host "  -Force passed, killing PID $($owner.Id)..." -ForegroundColor Yellow
        Stop-Process -Id $owner.Id -Force
        Start-Sleep -Seconds 2
        $still = Get-PortOwner -P $Port
        if ($still) {
            Write-Host "[ERR] Port $Port still held after kill. Aborting." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Port $Port released." -ForegroundColor Green
    } else {
        $choice = Read-Host "  [k]ill it / [c]hange port / [a]bort (k/c/a)"
        switch -Regex ($choice) {
            '^k' {
                Stop-Process -Id $owner.Id -Force
                Start-Sleep -Seconds 2
                $still = Get-PortOwner -P $Port
                if ($still) {
                    Write-Host "[ERR] Port $Port still held. Aborting." -ForegroundColor Red
                    exit 1
                }
                Write-Host "  Port $Port released." -ForegroundColor Green
            }
            '^c' {
                $newPort = Read-Host "  New port (1024-65535)"
                $Port = [int]$newPort
                if ((Get-PortOwner -P $Port)) {
                    Write-Host "[ERR] Port $Port also in use. Aborting." -ForegroundColor Red
                    exit 1
                }
            }
            default {
                Write-Host "  Aborting." -ForegroundColor DarkGray
                exit 0
            }
        }
    }
}

# 3. Activate venv
$VenvActivate = Join-Path $ProjectRoot '.venv\Scripts\Activate.ps1'
if (-not (Test-Path $VenvActivate)) {
    Write-Host "[ERR] .venv not found at $VenvActivate" -ForegroundColor Red
    Write-Host "      Run 'uv venv' first." -ForegroundColor DarkGray
    exit 1
}
& $VenvActivate

# 4. Launch
Write-Host ""
Write-Host "[run_testbench] starting on $TestbenchHost`:$Port ..." -ForegroundColor Cyan
Write-Host ""
uv run python tests/testbench/run_testbench.py --host $TestbenchHost --port $Port
