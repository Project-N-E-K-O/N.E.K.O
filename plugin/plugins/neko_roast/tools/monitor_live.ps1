param(
    [switch]$Once,
    [string]$BaseUrl = "http://127.0.0.1:48911",
    [string]$ContextJsonPath = "",
    [int]$WarnLatencyMs = 5000,
    [int]$SlowLatencyMs = 10000
)

$ErrorActionPreference = "Stop"
$script:LastSnapshotOk = $true

function Format-Latency {
    param([object]$Value)
    if ($null -eq $Value) {
        return "-"
    }
    try {
        $ms = [double]$Value
    } catch {
        return "-"
    }
    if ([double]::IsNaN($ms) -or [double]::IsInfinity($ms) -or $ms -lt 0) {
        return "-"
    }
    if ($ms -lt 10000) {
        return ("{0:N1}s" -f ($ms / 1000.0))
    }
    return ("{0:N0}s" -f [Math]::Ceiling($ms / 1000.0))
}

function Get-LatencyStatus {
    param(
        [object]$Value,
        [int]$WarnThresholdMs,
        [int]$SlowThresholdMs
    )
    if ($null -eq $Value) {
        return "unknown"
    }
    try {
        $ms = [double]$Value
    } catch {
        return "unknown"
    }
    if ([double]::IsNaN($ms) -or [double]::IsInfinity($ms) -or $ms -lt 0) {
        return "unknown"
    }
    if ($ms -ge $SlowThresholdMs) {
        return "slow"
    }
    if ($ms -ge $WarnThresholdMs) {
        return "warn"
    }
    return "ok"
}

function Read-Context {
    if ($ContextJsonPath) {
        return Get-Content -LiteralPath $ContextJsonPath -Raw | ConvertFrom-Json
    }

    $uri = "$BaseUrl/plugin/neko_roast/hosted-ui/context?kind=panel&id=main"
    return Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 5
}

function Get-Field {
    param(
        [object]$Value,
        [string]$Default = "-"
    )
    if ($null -eq $Value -or "$Value" -eq "") {
        return $Default
    }
    return "$Value"
}

function Format-Error {
    param([object]$ErrorValue)
    $text = "$ErrorValue"
    if (-not $text) {
        return "unknown"
    }
    return ($text -replace "\s+", "_")
}

function Write-Snapshot {
    try {
        $context = Read-Context
    } catch {
        $script:LastSnapshotOk = $false
        Write-Output ("[neko_roast] context=failed error=$(Format-Error $_.Exception.Message)")
        return
    }
    $script:LastSnapshotOk = $true
    $state = $context.state
    if ($null -eq $state) {
        $state = $context
    }

    $config = $state.config
    $live = $state.live_connection
    $safety = $state.safety
    $speech = $state.speech_explanation
    $recent = @($state.recent_results)
    $latest = $null
    if ($recent.Count -gt 0) {
        $latest = $recent[0]
    }

    $lastStatus = Get-Field $speech.last_result_status
    if ($lastStatus -eq "-" -and $null -ne $latest) {
        $lastStatus = Get-Field $latest.status
    }

    $latency = $speech.last_result_latency_ms
    if ($null -eq $latency -and $null -ne $latest) {
        $latency = $latest.response_latency_ms
    }

    $parts = @(
        "[neko_roast]",
        "dry_run=$(Get-Field $config.dry_run)",
        "live=$(Get-Field $live.state)",
        "connected=$(Get-Field $live.connected)",
        "safety=$(Get-Field $safety.status)",
        "speech=$(Get-Field $speech.summary)",
        "reason=$(Get-Field $speech.reason)",
        "last_result=$lastStatus",
        "latency=$(Format-Latency $latency)",
        "latency_status=$(Get-LatencyStatus $latency $WarnLatencyMs $SlowLatencyMs)"
    )
    Write-Output ($parts -join " ")
}

do {
    Write-Snapshot
    if ($Once) {
        if (-not $script:LastSnapshotOk) {
            exit 1
        }
        break
    }
    Start-Sleep -Seconds 10
} while ($true)
