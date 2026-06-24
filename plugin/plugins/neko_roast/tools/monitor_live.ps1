param(
    [switch]$Once,
    [string]$BaseUrl = "http://127.0.0.1:48916",
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

function Get-SoloTestHint {
    param(
        [object]$Mode,
        [object]$LiveStatus,
        [object]$LiveState,
        [object]$IdleCandidate,
        [object]$IdleReady,
        [object]$IdleReason,
        [string]$LatencyStatus
    )
    if ("$Mode" -ne "solo_stream") {
        return "switch_to_solo_stream"
    }
    if ("$LiveStatus" -eq "cannot_stream") {
        return "fix_preflight"
    }
    if ("$LiveState" -eq "paused" -or "$LiveState" -eq "blocked") {
        return "wait_until_unblocked"
    }
    if ("$LatencyStatus" -eq "slow" -or "$LatencyStatus" -eq "warn") {
        return "watch_latency"
    }
    if ("$IdleCandidate" -eq "True" -and "$IdleReady" -eq "True") {
        return "expect_idle_hosting"
    }
    if ("$IdleCandidate" -eq "True" -and "$IdleReady" -ne "True") {
        if ("$IdleReason" -eq "minimum_interval") {
            return "wait_idle_cooldown"
        }
        return "check_idle_gate"
    }
    return "observe"
}

function Get-SoloTestFocus {
    param(
        [object]$DryRun,
        [object]$Mode,
        [object]$LiveStatus,
        [object]$LiveState,
        [object]$IdleCandidate,
        [object]$IdleReady,
        [string]$LatencyStatus
    )
    if ("$Mode" -ne "solo_stream") {
        return "setup_mode"
    }
    if ("$LiveStatus" -eq "cannot_stream") {
        return "preflight"
    }
    if ("$LiveState" -eq "paused" -or "$LiveState" -eq "blocked") {
        return "unblock"
    }
    if ("$DryRun" -eq "True") {
        return "chain_only"
    }
    if ("$LatencyStatus" -eq "slow" -or "$LatencyStatus" -eq "warn") {
        return "latency"
    }
    if ("$IdleCandidate" -eq "True" -and "$IdleReady" -eq "True") {
        return "idle_hosting"
    }
    return "danmaku_response"
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

function Get-CheckoutStatus {
    param([object]$Context)
    $configPath = $Context.plugin.config_path
    if ($null -eq $configPath -or "$configPath" -eq "") {
        return "unknown"
    }

    try {
        $expectedRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd("\", "/")
        $actualPath = [System.IO.Path]::GetFullPath("$configPath")
    } catch {
        return "unknown"
    }

    if ($actualPath.StartsWith($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return "ok"
    }
    return "mismatch"
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
    $liveStatus = $state.live_status
    $liveState = $state.live_state
    $idleHosting = $state.idle_hosting_status
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
    $latestRoute = "-"
    $latestSignal = "-"
    if ($null -ne $latest) {
        $latestRoute = Get-Field $latest.response_module
        $latestSignal = Get-Field $latest.event_signal
    }
    $latencyStatus = Get-LatencyStatus $latency $WarnLatencyMs $SlowLatencyMs
    $soloTestHint = Get-SoloTestHint $config.live_mode $liveStatus.summary $liveState.state $liveState.idle_hosting_candidate $idleHosting.eligible $idleHosting.reason $latencyStatus
    $soloTestFocus = Get-SoloTestFocus $config.dry_run $config.live_mode $liveStatus.summary $liveState.state $liveState.idle_hosting_candidate $idleHosting.eligible $latencyStatus

    $parts = @(
        "[neko_roast]",
        "checkout=$(Get-CheckoutStatus $context)",
        "dry_run=$(Get-Field $config.dry_run)",
        "mode=$(Get-Field $config.live_mode)",
        "live=$(Get-Field $live.state)",
        "connected=$(Get-Field $live.connected)",
        "live_status=$(Get-Field $liveStatus.summary)",
        "live_state=$(Get-Field $liveState.state)",
        "idle_candidate=$(Get-Field $liveState.idle_hosting_candidate)",
        "idle_ready=$(Get-Field $idleHosting.eligible)",
        "idle_reason=$(Get-Field $idleHosting.reason)",
        "safety=$(Get-Field $safety.status)",
        "speech=$(Get-Field $speech.summary)",
        "reason=$(Get-Field $speech.reason)",
        "last_result=$lastStatus",
        "latest_route=$latestRoute",
        "latest_signal=$latestSignal",
        "latency=$(Format-Latency $latency)",
        "latency_status=$latencyStatus",
        "solo_test_hint=$soloTestHint",
        "solo_test_focus=$soloTestFocus"
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
