param(
    [switch]$Help,
    [switch]$Once,
    [switch]$ExpectRealOutput,
    [string]$BaseUrl = "http://127.0.0.1:48916",
    [string]$ContextJsonPath = "",
    [string]$BackendLogPath = "",
    [int]$BackendLogTailLines = 200,
    [int]$ReplyLengthWarn = 80,
    [int]$LatestAgeWarnSec = 60,
    [int]$LatestAgeStaleSec = 180,
    [int]$WarnLatencyMs = 5000,
    [int]$SlowLatencyMs = 10000
)

$ErrorActionPreference = "Stop"
$script:LastSnapshotOk = $true

function Write-MonitorHelp {
    Write-Output @"
NEKO Live monitor

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\plugin\plugins\neko_roast\tools\monitor_live.ps1 -Once
  powershell -NoProfile -ExecutionPolicy Bypass -File .\plugin\plugins\neko_roast\tools\monitor_live.ps1 -Once -ExpectRealOutput -BackendLogPath <backend-log>

Important options:
  -Once              Print one snapshot and exit.
  -ExpectRealOutput  Add real-output alerts for dry_run, disconnects, stale latest results, latency, watchdogs, contamination, and long replies.
  -BackendLogPath    Read backend log tail for playback watchdog, unrelated proactive output, and send_lanlan_response length markers.
                      If omitted, the monitor tries .codex-backend-live-test.log in the current directory and repo root.

Key fields:
  alerts             '-' when no known risk is detected, otherwise comma-separated risks.
  director_action    Next automatic live action expected from NEKO.
  latest_route       Latest handled module, such as avatar_roast, danmaku_response, idle_hosting, or active_engagement.
  latest_age_status  ok / warn / stale freshness of the latest result.
"@
}

if ($Help) {
    Write-MonitorHelp
    exit 0
}

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

function Format-Seconds {
    param([object]$Value)
    if ($null -eq $Value) {
        return "-"
    }
    try {
        $seconds = [double]$Value
    } catch {
        return "-"
    }
    if ([double]::IsNaN($seconds) -or [double]::IsInfinity($seconds) -or $seconds -lt 0) {
        return "-"
    }
    return ("{0:N1}s" -f $seconds)
}

function Format-IsoAge {
    param([object]$Value)
    if ($null -eq $Value -or "$Value" -eq "") {
        return "-"
    }
    try {
        $timestamp = [datetimeoffset]::Parse("$Value")
        $seconds = ([datetimeoffset]::UtcNow - $timestamp.ToUniversalTime()).TotalSeconds
    } catch {
        return "-"
    }
    return Format-Seconds $seconds
}

function Get-IsoAgeSeconds {
    param([object]$Value)
    if ($null -eq $Value -or "$Value" -eq "") {
        return $null
    }
    try {
        $timestamp = [datetimeoffset]::Parse("$Value")
        return ([datetimeoffset]::UtcNow - $timestamp.ToUniversalTime()).TotalSeconds
    } catch {
        return $null
    }
}

function Get-AgeStatus {
    param(
        [object]$Value,
        [int]$WarnThresholdSec,
        [int]$StaleThresholdSec
    )
    $seconds = Get-IsoAgeSeconds $Value
    if ($null -eq $seconds -or $seconds -lt 0) {
        return "unknown"
    }
    if ($seconds -ge $StaleThresholdSec) {
        return "stale"
    }
    if ($seconds -ge $WarnThresholdSec) {
        return "warn"
    }
    return "ok"
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
        [object]$TestIsolationStatus,
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
    if ("$TestIsolationStatus" -eq "warning") {
        return "clear_viewer_profiles"
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
        [object]$TestIsolationStatus,
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
    if ("$TestIsolationStatus" -eq "warning") {
        return "test_isolation"
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

function Get-CompactField {
    param(
        [object]$Value,
        [string]$Default = "-"
    )
    $text = Get-Field $Value $Default
    if ($text -eq $Default) {
        return $text
    }
    return ($text -replace "\s+", "_")
}

function Get-CompactPreview {
    param(
        [object]$Value,
        [int]$MaxLength = 80,
        [string]$Default = "-"
    )
    $text = Get-CompactField $Value $Default
    if ($text -eq $Default) {
        return $text
    }
    if ($text.Length -le $MaxLength) {
        return $text
    }
    return $text.Substring(0, $MaxLength)
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

function Get-BackendLogSignals {
    param(
        [string]$Path,
        [int]$TailLines,
        [int]$ReplyWarnThreshold
    )
    $signals = [ordered]@{
        Watchdog = "-"
        Contamination = "-"
        ReplyLen = "-"
        ReplyLengthStatus = "-"
    }
    if (-not $Path) {
        return [pscustomobject]$signals
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]$signals
    }
    try {
        $text = (Get-Content -LiteralPath $Path -Tail $TailLines -ErrorAction Stop) -join "`n"
    } catch {
        return [pscustomobject]$signals
    }

    if ($text -match "(?i)(voice\s+playback\s+gate\s+watchdog|playback\s+gate\s+watchdog|voice_play_end.*missing|missing.*voice_play_end)") {
        $signals.Watchdog = "True"
    } else {
        $signals.Watchdog = "False"
    }
    if ($text -match "(?i)warthunder") {
        $signals.Contamination = "warthunder"
    } elseif ($text -match "(?i)(proactive\s+bridge\s+output|proactive.*queued)") {
        $signals.Contamination = "proactive"
    } else {
        $signals.Contamination = "none"
    }
    $replyLengthMatches = [regex]::Matches($text, "(?im)send_lanlan_response[^\r\n]*(?:len|text_len)\s*[=:]\s*(\d+)")
    if ($replyLengthMatches.Count -gt 0) {
        $replyLen = [int]$replyLengthMatches[$replyLengthMatches.Count - 1].Groups[1].Value
        $signals.ReplyLen = "$replyLen"
        if ($replyLen -ge $ReplyWarnThreshold) {
            $signals.ReplyLengthStatus = "warn"
        } else {
            $signals.ReplyLengthStatus = "ok"
        }
    }
    return [pscustomobject]$signals
}

function Get-EffectiveBackendLogPath {
    param([string]$Path)
    if ($Path) {
        return $Path
    }

    $candidates = @()
    $candidates += (Join-Path (Get-Location) ".codex-backend-live-test.log")
    try {
        $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\..\.."))
        $candidates += (Join-Path $repoRoot ".codex-backend-live-test.log")
    } catch {
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    return ""
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
    $activeEngagement = $state.active_engagement_status
    $liveDirector = $state.live_director_status
    $soloReadiness = $state.solo_test_readiness
    $safety = $state.safety
    $speech = $state.speech_explanation
    $recent = @($state.recent_results)
    $profiles = @($state.recent_profiles)
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
    $latestStatus = "-"
    $latestReason = "-"
    $latestSource = "-"
    $latestText = "-"
    $latestAge = "-"
    $latestAgeStatus = "unknown"
    if ($null -ne $latest) {
        $latestStatus = Get-Field $latest.status
        $latestReason = Get-CompactField $latest.reason
        $latestRoute = Get-Field $latest.response_module
        $latestSignal = Get-Field $latest.event_signal
        $latestAge = Format-IsoAge $latest.created_at
        $latestAgeStatus = Get-AgeStatus $latest.created_at $LatestAgeWarnSec $LatestAgeStaleSec
    }
    $latestTopicSource = "-"
    $latestTopicShape = "-"
    $latestTopicTitle = "-"
    $latestTopicKey = "-"
    $latestTopicHook = "-"
    $latestTopicPattern = "-"
    $latestTopicRepeat = "False"
    $latestHostBeatShape = "-"
    $latestHostBeatTitle = "-"
    $latestHostBeatHint = "-"
    if ($null -ne $latest -and $null -ne $latest.event) {
        $latestSource = Get-CompactField $latest.event.source
        $latestText = Get-CompactPreview $latest.event.danmaku_text
        $latestTopicSource = Get-CompactField $latest.event.topic_source
        $latestTopicShape = Get-CompactField $latest.event.topic_shape
        $latestTopicTitle = Get-CompactField $latest.event.topic_title
        $latestTopicKey = Get-CompactField $latest.event.topic_key
        $latestTopicHook = Get-CompactField $latest.event.topic_hook
        $latestTopicPattern = Get-CompactField $latest.event.topic_pattern
        if ($latestTopicKey -ne "-" -and $recent.Count -gt 1) {
            foreach ($previous in @($recent | Select-Object -Skip 1)) {
                $previousEvent = $previous.event
                if ($null -eq $previousEvent) {
                    continue
                }
                if ((Get-CompactField $previousEvent.topic_key) -eq $latestTopicKey) {
                    $latestTopicRepeat = "True"
                    break
                }
            }
        }
        $latestHostBeatShape = Get-CompactField $latest.event.host_beat_shape
        $latestHostBeatTitle = Get-CompactField $latest.event.host_beat_title
        $latestHostBeatHint = Get-CompactField $latest.event.host_beat_hint
    }
    $viewerAge = $liveState.last_viewer_activity_age_sec
    if ($null -eq $viewerAge) {
        $viewerAge = $liveState.last_activity_age_sec
    }
    $outputAge = $liveState.last_output_age_sec
    $activeMinWait = $activeEngagement.minimum_interval_remaining
    $activeDanmakuWait = $activeEngagement.recent_danmaku_cooldown_remaining
    $activeIdleWait = $activeEngagement.idle_hosting_wait_remaining
    $testIsolationStatus = "-"
    $testIsolationReason = "-"
    $readinessWarnings = @()
    $readinessBlocked = @()
    if ($null -ne $soloReadiness -and $null -ne $soloReadiness.items) {
        foreach ($item in @($soloReadiness.items)) {
            if ("$($item.status)" -eq "warning") {
                $readinessWarnings += "$(Get-CompactField $item.id)"
            }
            if ("$($item.status)" -eq "blocked") {
                $readinessBlocked += "$(Get-CompactField $item.id)"
            }
            if ("$($item.id)" -eq "test_isolation") {
                $testIsolationStatus = Get-Field $item.status
                $testIsolationReason = Get-CompactField $item.reason
            }
        }
    }
    $readinessWarnText = "-"
    if ($readinessWarnings.Count -gt 0) {
        $readinessWarnText = $readinessWarnings -join ","
    }
    $readinessBlockedText = "-"
    if ($readinessBlocked.Count -gt 0) {
        $readinessBlockedText = $readinessBlocked -join ","
    }
    $latencyStatus = Get-LatencyStatus $latency $WarnLatencyMs $SlowLatencyMs
    $soloTestHint = Get-SoloTestHint $config.live_mode $liveStatus.summary $liveState.state $liveState.idle_hosting_candidate $idleHosting.eligible $idleHosting.reason $testIsolationStatus $latencyStatus
    $soloTestFocus = Get-SoloTestFocus $config.dry_run $config.live_mode $liveStatus.summary $liveState.state $liveState.idle_hosting_candidate $idleHosting.eligible $testIsolationStatus $latencyStatus
    $effectiveBackendLogPath = Get-EffectiveBackendLogPath $BackendLogPath
    $backendLogAvailable = $false
    if ($effectiveBackendLogPath -and (Test-Path -LiteralPath $effectiveBackendLogPath)) {
        $backendLogAvailable = $true
    }
    $logSignals = Get-BackendLogSignals $effectiveBackendLogPath $BackendLogTailLines $ReplyLengthWarn
    $alerts = @()
    if ($ExpectRealOutput) {
        if ("$(Get-Field $config.dry_run)" -eq "True") {
            $alerts += "dry_run"
        }
        if ("$(Get-Field $live.connected)" -eq "False" -or "$(Get-Field $live.state)" -ne "connected") {
            $alerts += "live_disconnected"
        }
        if ("$(Get-Field $liveStatus.summary)" -ne "ready_to_stream") {
            $alerts += "live_not_ready"
        }
        if (-not $backendLogAvailable) {
            $alerts += "backend_log_missing"
        }
    }
    if ("$latestStatus" -eq "failed") {
        $alerts += "latest_failed"
    } elseif ("$latestStatus" -eq "skipped") {
        $alerts += "latest_skipped"
    }
    if ("$latestAgeStatus" -eq "stale") {
        $alerts += "latest_stale"
    }
    if ("$latencyStatus" -eq "slow") {
        $alerts += "latency_slow"
    } elseif ("$latencyStatus" -eq "warn") {
        $alerts += "latency_warn"
    }
    if ("$($logSignals.Watchdog)" -eq "True") {
        $alerts += "playback_watchdog"
    }
    if ("$($logSignals.Contamination)" -notin @("-", "none")) {
        $alerts += "contamination_$($logSignals.Contamination)"
    }
    if ("$($logSignals.ReplyLengthStatus)" -eq "warn") {
        $alerts += "long_reply"
    }
    $alertText = "-"
    if ($alerts.Count -gt 0) {
        $alertText = $alerts -join ","
    }

    $parts = @(
        "[neko_roast]",
        "checkout=$(Get-CheckoutStatus $context)",
        "dry_run=$(Get-Field $config.dry_run)",
        "mode=$(Get-Field $config.live_mode)",
        "live=$(Get-Field $live.state)",
        "connected=$(Get-Field $live.connected)",
        "live_status=$(Get-Field $liveStatus.summary)",
        "live_state=$(Get-Field $liveState.state)",
        "viewer_age=$(Format-Seconds $viewerAge)",
        "output_age=$(Format-Seconds $outputAge)",
        "profile_count=$($profiles.Count)",
        "solo_readiness=$(Get-Field $soloReadiness.summary)",
        "test_isolation=$testIsolationStatus",
        "test_isolation_reason=$testIsolationReason",
        "readiness_warn=$readinessWarnText",
        "readiness_blocked=$readinessBlockedText",
        "idle_candidate=$(Get-Field $liveState.idle_hosting_candidate)",
        "idle_ready=$(Get-Field $idleHosting.eligible)",
        "idle_reason=$(Get-Field $idleHosting.reason)",
        "active_min_wait=$(Format-Seconds $activeMinWait)",
        "active_danmaku_wait=$(Format-Seconds $activeDanmakuWait)",
        "active_idle_wait=$(Format-Seconds $activeIdleWait)",
        "director_action=$(Get-CompactField $liveDirector.next_auto_action)",
        "director_reason=$(Get-CompactField $liveDirector.reason)",
        "director_eligible=$(Get-Field $liveDirector.eligible)",
        "director_wait=$(Format-Seconds $liveDirector.cooldown_remaining)",
        "safety=$(Get-Field $safety.status)",
        "speech=$(Get-Field $speech.summary)",
        "reason=$(Get-Field $speech.reason)",
        "last_result=$lastStatus",
        "latest_status=$latestStatus",
        "latest_route=$latestRoute",
        "latest_signal=$latestSignal",
        "latest_source=$latestSource",
        "latest_text=$latestText",
        "latest_reason=$latestReason",
        "latest_age=$latestAge",
        "latest_age_status=$latestAgeStatus",
        "latest_topic_source=$latestTopicSource",
        "latest_topic_shape=$latestTopicShape",
        "latest_topic_title=$latestTopicTitle",
        "latest_topic_key=$latestTopicKey",
        "latest_topic_hook=$latestTopicHook",
        "latest_topic_pattern=$latestTopicPattern",
        "latest_topic_repeat=$latestTopicRepeat",
        "latest_host_beat_shape=$latestHostBeatShape",
        "latest_host_beat_title=$latestHostBeatTitle",
        "latest_host_beat_hint=$latestHostBeatHint",
        "latency=$(Format-Latency $latency)",
        "latency_status=$latencyStatus",
        "log_watchdog=$($logSignals.Watchdog)",
        "log_contamination=$($logSignals.Contamination)",
        "log_reply_len=$($logSignals.ReplyLen)",
        "log_reply_length_status=$($logSignals.ReplyLengthStatus)",
        "alerts=$alertText",
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
