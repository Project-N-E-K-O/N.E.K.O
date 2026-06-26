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
  -ExpectRealOutput  Add real-output alerts for dry_run, disconnects, stale latest results, latency, test isolation, watchdogs, contamination, and long replies.
  -BackendLogPath    Read backend log tail for playback watchdog, unrelated proactive output, and send_lanlan_response length markers.
                      If omitted, the monitor tries .codex-backend-live-test.log in the current directory and repo root.

Key fields:
  alerts             '-' when no known risk is detected, otherwise comma-separated risks.
  director_action    Next automatic live action expected from NEKO.
  latest_route       Latest handled module, such as avatar_roast, danmaku_response, warmup_hosting, idle_hosting, or active_engagement.
  latest_uid / avatar_repeat_uid
                    Latest viewer UID and repeated avatar-roast UID, useful for catching repeated first-appearance roasts.
  latest_output_len  Length of latest result output from hosted-ui context; useful when backend log is missing.
  recent_long_reply_count
                    Count of recent hosted-ui outputs over the reply length warning threshold.
  recent_long_reply_*
                    Per-route long-reply counts for avatar_roast, danmaku_response, idle_hosting, active_engagement, and warmup_hosting.
  recent_generic_host_prompt_count
                    Count of recent hosted-ui outputs that look like template host-bait lines.
  log_generic_host_prompt
                    True when send_lanlan_response text in backend log contains template host-bait reply text.
  avatar_repeat_count
                    How many recent avatar_roast results were seen for avatar_repeat_uid.
  recent_*          Recent route counts for avatar_roast, danmaku_response, warmup_hosting, idle_hosting, and active_engagement.
  recent_actual_*   Recent pushed/dry_run route counts for avatar_roast, danmaku_response, warmup_hosting, idle_hosting, and active_engagement.
  recent_total      Total recent result count in the hosted-ui context snapshot.
  recent_pushed / recent_dry_run / recent_skipped / recent_failed
                    Recent result status counts, so route attempts are not mistaken for actual output.
  recent_topic_skip_*
                    Recent active-topic material skip reason counts: single-viewer flood, stale danmaku, avatar-roast context, or non-output danmaku.
  recent_topic_source_*
                    Recent Active Engagement topic source counts for fallback, Bili trending, and recent danmaku material.
  recent_topic_intent_*
                    Recent Active Engagement reply-intent counts, useful for spotting whether proactive topics are too one-note.
  avatar_roast_share / avatar_roast_bias
                    Recent danmaku-route mix; avatar_roast_bias warns when first-appearance roasts dominate.
  latest_age_status  ok / warn / stale freshness of the latest result.
  quiet_after / idle_after
                    Current live-state thresholds for quiet and idle hosting checks.
  entrance_pacing_window
                    Current first-appearance roast pacing window derived from activity_level.
  active_min_interval
                    Current Active Engagement minimum interval derived from activity_level.
  topic_repeat / avatar_repeat
                    Alert names for repeated active-topic material or repeated avatar roast for the same UID.
  topic_filter_direct_request / topic_filter_reaction / topic_filter_runtime_feedback
                    Alert names for active-topic material filtered as viewer requests, reaction-only messages, or runtime/test feedback.
  topic_intent_bias
                    Alert name when recent Active Engagement topics overuse one reply intent, making proactive hosting feel one-note.
  topic_source_bias
                    Alert name when recent Active Engagement topics overuse one source, making proactive hosting material feel narrow.
  generic_host_prompt
                    Alert name for template-like "please interact / send danmaku" output.
  host_beat_repeat  Alert name for repeated idle-hosting host beat material.
  proactive_in_engaged
                    Alert name when the latest actual proactive output happened while live_state is engaged.
  warmup_repeat     Alert name when warmup_hosting has more than one recent actual output.
  warmup_missing / idle_missing / active_missing
                    Alert names when the director says an automatic warmup, idle, or active line is ready but recent results contain no such output yet.
  test_isolation    Alert name for real-output solo-stream tests when readiness says the validation window is not isolated.
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

function Get-EntrancePacingWindow {
    param([object]$ActivityLevel)
    $level = "$(Get-Field $ActivityLevel)"
    if ($level -eq "quiet") {
        return 75.0
    }
    if ($level -eq "active") {
        return 30.0
    }
    return 45.0
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
        [string]$LatencyStatus,
        [object]$DirectorAction = "",
        [object]$DirectorEligible = ""
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
    if ("$DirectorAction" -eq "warmup_hosting" -and "$DirectorEligible" -eq "True") {
        return "expect_warmup_hosting"
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
    if ("$DirectorAction" -eq "active_engagement" -and "$DirectorEligible" -eq "True") {
        return "expect_active_engagement"
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
        [string]$LatencyStatus,
        [object]$DirectorAction = "",
        [object]$DirectorEligible = ""
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
    if ("$DirectorAction" -eq "warmup_hosting" -and "$DirectorEligible" -eq "True") {
        return "warmup_hosting"
    }
    if ("$IdleCandidate" -eq "True" -and "$IdleReady" -eq "True") {
        return "idle_hosting"
    }
    if ("$DirectorAction" -eq "active_engagement" -and "$DirectorEligible" -eq "True") {
        return "active_engagement"
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

function Test-GenericHostPromptOutput {
    param([object]$Value)
    if ($null -eq $Value) {
        return $false
    }
    $text = "$Value".Trim()
    if (-not $text) {
        return $false
    }
    $patterns = @(
        "\u5927\u5bb6.{0,8}(\u4e92\u52a8|\u53d1\u5f39\u5e55|\u5237\u5f39\u5e55|\u5f39\u5e55\u5237\u8d77\u6765|\u804a\u8d77\u6765)",
        "(\u5f39\u5e55|\u8bc4\u8bba).{0,6}(\u5237\u8d77\u6765|\u53d1\u8d77\u6765|\u8d70\u4e00\u8d70)",
        "\u5feb\u6765.{0,8}(\u4e92\u52a8|\u53d1\u5f39\u5e55|\u804a\u5929)",
        "\u4f60\u4eec.{0,8}(\u60f3\u542c|\u60f3\u804a|\u60f3\u8ba9\u6211\u8bf4|\u6700\u60f3\u542c)",
        "\u60f3\u542c.{0,8}(\u4ec0\u4e48|\u5565)",
        "\u804a\u70b9.{0,8}(\u4ec0\u4e48|\u5565)",
        "(?i)what\s+should\s+we\s+talk\s+about",
        "(?i)what\s+do\s+you\s+want\s+to\s+(hear|talk)",
        "(?i)(get|keep).{0,12}chat.{0,12}(moving|alive|going)",
        "(?i)(send|drop).{0,12}(chat|message|comment)",
        "(?i)come\s+(chat|interact)"
    )
    foreach ($pattern in $patterns) {
        if ($text -match $pattern) {
            return $true
        }
    }
    return $false
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
        GenericHostPrompt = "-"
    }
    if (-not $Path) {
        return [pscustomobject]$signals
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]$signals
    }
    try {
        $text = (Get-Content -LiteralPath $Path -Tail $TailLines -Encoding UTF8 -ErrorAction Stop) -join "`n"
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
    $responseTextMatches = [regex]::Matches($text, "(?im)send_lanlan_response[^\r\n]*(?:text|response|content)\s*[=:]\s*(.+)$")
    $signals.GenericHostPrompt = "False"
    foreach ($match in $responseTextMatches) {
        if (Test-GenericHostPromptOutput $match.Groups[1].Value) {
            $signals.GenericHostPrompt = "True"
            break
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
    $recentRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
        warmup_hosting = 0
        idle_hosting = 0
        active_engagement = 0
    }
    $recentActualRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
        warmup_hosting = 0
        idle_hosting = 0
        active_engagement = 0
    }
    $recentActualDanmakuRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
    }
    $recentStatusCounts = @{
        pushed = 0
        dry_run = 0
        skipped = 0
        failed = 0
    }
    $recentTopicSkipCounts = @{
        single_viewer_flood = 0
        stale_recent_danmaku = 0
        avatar_roast_context = 0
        non_output_danmaku = 0
        filtered_recent_danmaku = 0
        filtered_direct_request = 0
        filtered_reaction = 0
        filtered_runtime_feedback = 0
        viewer_to_viewer_mention = 0
        recent_danmaku_source_streak = 0
    }
    $recentTopicIntentCounts = @{
        quick_vote = 0
        tiny_answer = 0
        tease_back = 0
        agree_or_pushback = 0
    }
    $recentTopicSourceCounts = @{
        fallback = 0
        bili_trending = 0
        recent_danmaku = 0
    }
    $recentLongReplyRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
        idle_hosting = 0
        active_engagement = 0
        warmup_hosting = 0
    }
    $recentLongReplyCount = 0
    $recentGenericHostPromptCount = 0
    foreach ($result in $recent) {
        $route = "$(Get-Field $result.response_module)"
        if ($recentRouteCounts.ContainsKey($route)) {
            $recentRouteCounts[$route] += 1
        }
        $status = "$(Get-Field $result.status)"
        if ($recentStatusCounts.ContainsKey($status)) {
            $recentStatusCounts[$status] += 1
        }
        if ("$status" -in @("pushed", "dry_run") -and $recentActualRouteCounts.ContainsKey($route)) {
            $recentActualRouteCounts[$route] += 1
        }
        if ("$status" -in @("pushed", "dry_run") -and $recentActualDanmakuRouteCounts.ContainsKey($route)) {
            $recentActualDanmakuRouteCounts[$route] += 1
        }
        if ($null -ne $result.event) {
            $topicSkipReason = "$(Get-Field $result.event.topic_recent_skip_reason)"
            if ($recentTopicSkipCounts.ContainsKey($topicSkipReason)) {
                $recentTopicSkipCounts[$topicSkipReason] += 1
            }
            if (
                ("$status" -in @("pushed", "dry_run")) -and
                ($route -eq "active_engagement" -or "$(Get-Field $result.event.source)" -eq "active_engagement")
            ) {
                $topicIntent = "$(Get-Field $result.event.topic_intent)"
                if ($recentTopicIntentCounts.ContainsKey($topicIntent)) {
                    $recentTopicIntentCounts[$topicIntent] += 1
                }
                $topicSource = "$(Get-Field $result.event.topic_source)"
                if ($recentTopicSourceCounts.ContainsKey($topicSource)) {
                    $recentTopicSourceCounts[$topicSource] += 1
                }
            }
        }
        if ($null -ne $result.output -and "$($result.output)" -ne "") {
            if ("$($result.output)".Length -ge $ReplyLengthWarn) {
                $recentLongReplyCount += 1
                if ($recentLongReplyRouteCounts.ContainsKey($route)) {
                    $recentLongReplyRouteCounts[$route] += 1
                }
            }
            if (Test-GenericHostPromptOutput $result.output) {
                $recentGenericHostPromptCount += 1
            }
        }
    }
    $danmakuRouteTotal = $recentActualDanmakuRouteCounts['avatar_roast'] + $recentActualDanmakuRouteCounts['danmaku_response']
    $avatarRoastShare = "-"
    $avatarRoastBias = "False"
    if ($danmakuRouteTotal -gt 0) {
        $avatarSharePercent = [int][math]::Round(($recentActualDanmakuRouteCounts['avatar_roast'] * 100.0) / $danmakuRouteTotal)
        $avatarRoastShare = "$avatarSharePercent%"
        if ($danmakuRouteTotal -ge 4 -and $avatarSharePercent -ge 75) {
            $avatarRoastBias = "True"
        }
    }
    $topicIntentTotal = 0
    $topicIntentMax = 0
    foreach ($intentCount in $recentTopicIntentCounts.Values) {
        $topicIntentTotal += [int]$intentCount
        if ([int]$intentCount -gt $topicIntentMax) {
            $topicIntentMax = [int]$intentCount
        }
    }
    $topicIntentBias = "False"
    if ($topicIntentTotal -ge 3 -and (($topicIntentMax * 100.0) / $topicIntentTotal) -ge 75.0) {
        $topicIntentBias = "True"
    }
    $topicSourceTotal = 0
    $topicSourceMax = 0
    foreach ($sourceCount in $recentTopicSourceCounts.Values) {
        $topicSourceTotal += [int]$sourceCount
        if ([int]$sourceCount -gt $topicSourceMax) {
            $topicSourceMax = [int]$sourceCount
        }
    }
    $topicSourceBias = "False"
    if ($topicSourceTotal -ge 3 -and (($topicSourceMax * 100.0) / $topicSourceTotal) -ge 75.0) {
        $topicSourceBias = "True"
    }
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
    $latestUid = "-"
    $latestSource = "-"
    $latestText = "-"
    $latestAge = "-"
    $latestAgeStatus = "unknown"
    $latestOutputLen = "-"
    $latestOutputLengthStatus = "-"
    if ($null -ne $latest) {
        $latestStatus = Get-Field $latest.status
        $latestReason = Get-CompactField $latest.reason
        $latestRoute = Get-Field $latest.response_module
        $latestSignal = Get-Field $latest.event_signal
        $latestAge = Format-IsoAge $latest.created_at
        $latestAgeStatus = Get-AgeStatus $latest.created_at $LatestAgeWarnSec $LatestAgeStaleSec
        if ($null -ne $latest.output -and "$($latest.output)" -ne "") {
            $latestOutputText = "$($latest.output)"
            $latestOutputLen = "$($latestOutputText.Length)"
            if ($latestOutputText.Length -ge $ReplyLengthWarn) {
                $latestOutputLengthStatus = "warn"
            } else {
                $latestOutputLengthStatus = "ok"
            }
        }
    }
    $latestTopicSource = "-"
    $latestTopicShape = "-"
    $latestTopicTitle = "-"
    $latestTopicKey = "-"
    $latestTopicHook = "-"
    $latestTopicPattern = "-"
    $latestTopicIntent = "-"
    $latestTopicReplyAffordance = "-"
    $latestTopicRecentSkipReason = "-"
    $latestTopicShapeGuardReason = "-"
    $latestTopicRepeat = "False"
    $latestHostBeatShape = "-"
    $latestHostBeatTitle = "-"
    $latestHostBeatKey = "-"
    $latestHostBeatHint = "-"
    $latestHostBeatRepeat = "False"
    if ($null -ne $latest -and $null -ne $latest.event) {
        $latestSource = Get-CompactField $latest.event.source
        $latestUid = Get-CompactField $latest.event.uid
        $latestText = Get-CompactPreview $latest.event.danmaku_text
        $latestTopicSource = Get-CompactField $latest.event.topic_source
        $latestTopicShape = Get-CompactField $latest.event.topic_shape
        $latestTopicTitle = Get-CompactField $latest.event.topic_title
        $latestTopicKey = Get-CompactField $latest.event.topic_key
        $latestTopicHook = Get-CompactField $latest.event.topic_hook
        $latestTopicPattern = Get-CompactField $latest.event.topic_pattern
        $latestTopicIntent = Get-CompactField $latest.event.topic_intent
        $latestTopicReplyAffordance = Get-CompactField $latest.event.topic_reply_affordance
        $latestTopicRecentSkipReason = Get-CompactField $latest.event.topic_recent_skip_reason
        $latestTopicShapeGuardReason = Get-CompactField $latest.event.shape_guard_reason
        if ("$latestStatus" -in @("pushed", "dry_run") -and $latestTopicKey -ne "-" -and $recent.Count -gt 1) {
            foreach ($previous in @($recent | Select-Object -Skip 1)) {
                if ("$(Get-Field $previous.status)" -notin @("pushed", "dry_run")) {
                    continue
                }
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
        $latestHostBeatKey = Get-CompactField $latest.event.host_beat_key
        $latestHostBeatHint = Get-CompactField $latest.event.host_beat_hint
        if ("$latestStatus" -in @("pushed", "dry_run") -and $latestHostBeatKey -ne "-" -and $recent.Count -gt 1) {
            foreach ($previous in @($recent | Select-Object -Skip 1)) {
                if ("$(Get-Field $previous.status)" -notin @("pushed", "dry_run")) {
                    continue
                }
                $previousEvent = $previous.event
                if ($null -eq $previousEvent) {
                    continue
                }
                if ((Get-CompactField $previousEvent.host_beat_key) -eq $latestHostBeatKey) {
                    $latestHostBeatRepeat = "True"
                    break
                }
            }
        }
    }
    $avatarRepeatUid = "-"
    $avatarRepeatCount = 0
    $avatarRoastCounts = @{}
    foreach ($result in $recent) {
        if ("$(Get-Field $result.status)" -notin @("pushed", "dry_run")) {
            continue
        }
        if ("$(Get-Field $result.response_module)" -ne "avatar_roast") {
            continue
        }
        $event = $result.event
        if ($null -eq $event) {
            continue
        }
        $uid = Get-CompactField $event.uid
        if ($uid -eq "-") {
            continue
        }
        if (-not $avatarRoastCounts.ContainsKey($uid)) {
            $avatarRoastCounts[$uid] = 0
        }
        $avatarRoastCounts[$uid] += 1
        if ($avatarRoastCounts[$uid] -gt 1 -and $avatarRoastCounts[$uid] -gt $avatarRepeatCount) {
            $avatarRepeatUid = $uid
            $avatarRepeatCount = $avatarRoastCounts[$uid]
        }
    }
    $viewerAge = $liveState.last_viewer_activity_age_sec
    if ($null -eq $viewerAge) {
        $viewerAge = $liveState.last_activity_age_sec
    }
    $outputAge = $liveState.last_output_age_sec
    $quietAfter = $liveState.engaged_threshold_seconds
    $idleAfter = $liveState.idle_threshold_seconds
    $entrancePacingWindow = Get-EntrancePacingWindow $config.activity_level
    $activeMinWait = $activeEngagement.minimum_interval_remaining
    $activeMinInterval = $activeEngagement.min_interval_seconds
    if ($null -eq $activeMinInterval) {
        $activeMinInterval = $activeEngagement.minimum_interval_seconds
    }
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
    $soloTestHint = Get-SoloTestHint $config.live_mode $liveStatus.summary $liveState.state $liveState.idle_hosting_candidate $idleHosting.eligible $idleHosting.reason $testIsolationStatus $latencyStatus $liveDirector.next_auto_action $liveDirector.eligible
    $soloTestFocus = Get-SoloTestFocus $config.dry_run $config.live_mode $liveStatus.summary $liveState.state $liveState.idle_hosting_candidate $idleHosting.eligible $testIsolationStatus $latencyStatus $liveDirector.next_auto_action $liveDirector.eligible
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
        if ("$testIsolationStatus" -in @("warning", "blocked")) {
            $alerts += "test_isolation"
        }
    }
    if ("$latestStatus" -eq "failed") {
        $alerts += "latest_failed"
    } elseif ("$latestStatus" -eq "skipped") {
        $alerts += "latest_skipped"
    }
    if ($recentStatusCounts['failed'] -gt 0 -and $alerts -notcontains "latest_failed") {
        $alerts += "recent_failed"
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
    if ("$latestOutputLengthStatus" -eq "warn" -and $alerts -notcontains "long_reply") {
        $alerts += "long_reply"
    }
    if ($recentLongReplyCount -gt 0 -and $alerts -notcontains "long_reply") {
        $alerts += "long_reply"
    }
    if ($recentGenericHostPromptCount -gt 0) {
        $alerts += "generic_host_prompt"
    }
    if ("$($logSignals.GenericHostPrompt)" -eq "True" -and $alerts -notcontains "generic_host_prompt") {
        $alerts += "generic_host_prompt"
    }
    if ($avatarRepeatUid -ne "-") {
        $alerts += "avatar_repeat"
    }
    if ("$avatarRoastBias" -eq "True") {
        $alerts += "avatar_bias"
    }
    if ("$latestTopicRepeat" -eq "True") {
        $alerts += "topic_repeat"
    }
    if ($recentTopicSkipCounts['filtered_direct_request'] -gt 0) {
        $alerts += "topic_filter_direct_request"
    }
    if ($recentTopicSkipCounts['filtered_reaction'] -gt 0) {
        $alerts += "topic_filter_reaction"
    }
    if ($recentTopicSkipCounts['filtered_runtime_feedback'] -gt 0) {
        $alerts += "topic_filter_runtime_feedback"
    }
    if ($recentTopicSkipCounts['viewer_to_viewer_mention'] -gt 0) {
        $alerts += "topic_viewer_mention"
    }
    if ($recentTopicSkipCounts['recent_danmaku_source_streak'] -gt 0) {
        $alerts += "topic_source_streak"
    }
    if ($latestTopicShapeGuardReason -ne "-") {
        $alerts += "topic_shape_guard"
    }
    if ("$topicIntentBias" -eq "True") {
        $alerts += "topic_intent_bias"
    }
    if ("$topicSourceBias" -eq "True") {
        $alerts += "topic_source_bias"
    }
    if ("$latestHostBeatRepeat" -eq "True") {
        $alerts += "host_beat_repeat"
    }
    if (
        "$(Get-CompactField $liveState.state)" -eq "engaged" -and
        "$latestStatus" -in @("pushed", "dry_run") -and
        "$latestRoute" -in @("warmup_hosting", "idle_hosting", "active_engagement")
    ) {
        $alerts += "proactive_in_engaged"
    }
    if ($recentActualRouteCounts['warmup_hosting'] -gt 1) {
        $alerts += "warmup_repeat"
    }
    if ("$(Get-CompactField $liveDirector.next_auto_action)" -eq "idle_hosting" -and "$(Get-Field $liveDirector.eligible)" -eq "True" -and $recentActualRouteCounts['idle_hosting'] -eq 0) {
        $alerts += "idle_missing"
    }
    if ("$(Get-CompactField $liveDirector.next_auto_action)" -eq "warmup_hosting" -and "$(Get-Field $liveDirector.eligible)" -eq "True" -and $recentActualRouteCounts['warmup_hosting'] -eq 0) {
        $alerts += "warmup_missing"
    }
    if ("$(Get-CompactField $liveDirector.next_auto_action)" -eq "active_engagement" -and "$(Get-Field $liveDirector.eligible)" -eq "True" -and $recentActualRouteCounts['active_engagement'] -eq 0) {
        $alerts += "active_missing"
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
        "quiet_after=$(Format-Seconds $quietAfter)",
        "idle_after=$(Format-Seconds $idleAfter)",
        "entrance_pacing_window=$(Format-Seconds $entrancePacingWindow)",
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
        "active_min_interval=$(Format-Seconds $activeMinInterval)",
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
        "latest_uid=$latestUid",
        "latest_source=$latestSource",
        "latest_text=$latestText",
        "latest_reason=$latestReason",
        "latest_age=$latestAge",
        "latest_age_status=$latestAgeStatus",
        "latest_output_len=$latestOutputLen",
        "latest_output_length_status=$latestOutputLengthStatus",
        "recent_long_reply_count=$recentLongReplyCount",
        "recent_long_reply_avatar_roast=$($recentLongReplyRouteCounts['avatar_roast'])",
        "recent_long_reply_danmaku_response=$($recentLongReplyRouteCounts['danmaku_response'])",
        "recent_long_reply_idle_hosting=$($recentLongReplyRouteCounts['idle_hosting'])",
        "recent_long_reply_active_engagement=$($recentLongReplyRouteCounts['active_engagement'])",
        "recent_long_reply_warmup_hosting=$($recentLongReplyRouteCounts['warmup_hosting'])",
        "recent_generic_host_prompt_count=$recentGenericHostPromptCount",
        "recent_total=$($recent.Count)",
        "recent_avatar_roast=$($recentRouteCounts['avatar_roast'])",
        "recent_danmaku_response=$($recentRouteCounts['danmaku_response'])",
        "recent_warmup_hosting=$($recentRouteCounts['warmup_hosting'])",
        "recent_idle_hosting=$($recentRouteCounts['idle_hosting'])",
        "recent_active_engagement=$($recentRouteCounts['active_engagement'])",
        "recent_actual_avatar_roast=$($recentActualRouteCounts['avatar_roast'])",
        "recent_actual_danmaku_response=$($recentActualRouteCounts['danmaku_response'])",
        "recent_actual_warmup_hosting=$($recentActualRouteCounts['warmup_hosting'])",
        "recent_actual_idle_hosting=$($recentActualRouteCounts['idle_hosting'])",
        "recent_actual_active_engagement=$($recentActualRouteCounts['active_engagement'])",
        "recent_pushed=$($recentStatusCounts['pushed'])",
        "recent_dry_run=$($recentStatusCounts['dry_run'])",
        "recent_skipped=$($recentStatusCounts['skipped'])",
        "recent_failed=$($recentStatusCounts['failed'])",
        "recent_topic_skip_single_viewer_flood=$($recentTopicSkipCounts['single_viewer_flood'])",
        "recent_topic_skip_stale_recent_danmaku=$($recentTopicSkipCounts['stale_recent_danmaku'])",
        "recent_topic_skip_avatar_roast_context=$($recentTopicSkipCounts['avatar_roast_context'])",
        "recent_topic_skip_non_output_danmaku=$($recentTopicSkipCounts['non_output_danmaku'])",
        "recent_topic_skip_filtered_recent_danmaku=$($recentTopicSkipCounts['filtered_recent_danmaku'])",
        "recent_topic_skip_filtered_direct_request=$($recentTopicSkipCounts['filtered_direct_request'])",
        "recent_topic_skip_filtered_reaction=$($recentTopicSkipCounts['filtered_reaction'])",
        "recent_topic_skip_filtered_runtime_feedback=$($recentTopicSkipCounts['filtered_runtime_feedback'])",
        "recent_topic_skip_viewer_to_viewer_mention=$($recentTopicSkipCounts['viewer_to_viewer_mention'])",
        "recent_topic_skip_recent_danmaku_source_streak=$($recentTopicSkipCounts['recent_danmaku_source_streak'])",
        "recent_topic_source_fallback=$($recentTopicSourceCounts['fallback'])",
        "recent_topic_source_bili_trending=$($recentTopicSourceCounts['bili_trending'])",
        "recent_topic_source_recent_danmaku=$($recentTopicSourceCounts['recent_danmaku'])",
        "recent_topic_source_bias=$topicSourceBias",
        "recent_topic_intent_quick_vote=$($recentTopicIntentCounts['quick_vote'])",
        "recent_topic_intent_tiny_answer=$($recentTopicIntentCounts['tiny_answer'])",
        "recent_topic_intent_tease_back=$($recentTopicIntentCounts['tease_back'])",
        "recent_topic_intent_agree_or_pushback=$($recentTopicIntentCounts['agree_or_pushback'])",
        "recent_topic_intent_bias=$topicIntentBias",
        "avatar_roast_share=$avatarRoastShare",
        "avatar_roast_bias=$avatarRoastBias",
        "latest_topic_source=$latestTopicSource",
        "latest_topic_shape=$latestTopicShape",
        "latest_topic_title=$latestTopicTitle",
        "latest_topic_key=$latestTopicKey",
        "latest_topic_hook=$latestTopicHook",
        "latest_topic_pattern=$latestTopicPattern",
        "latest_topic_intent=$latestTopicIntent",
        "latest_topic_reply_affordance=$latestTopicReplyAffordance",
        "latest_topic_recent_skip_reason=$latestTopicRecentSkipReason",
        "latest_topic_shape_guard_reason=$latestTopicShapeGuardReason",
        "latest_topic_repeat=$latestTopicRepeat",
        "avatar_repeat_uid=$avatarRepeatUid",
        "avatar_repeat_count=$avatarRepeatCount",
        "latest_host_beat_key=$latestHostBeatKey",
        "latest_host_beat_shape=$latestHostBeatShape",
        "latest_host_beat_title=$latestHostBeatTitle",
        "latest_host_beat_hint=$latestHostBeatHint",
        "latest_host_beat_repeat=$latestHostBeatRepeat",
        "latency=$(Format-Latency $latency)",
        "latency_status=$latencyStatus",
        "log_watchdog=$($logSignals.Watchdog)",
        "log_contamination=$($logSignals.Contamination)",
        "log_reply_len=$($logSignals.ReplyLen)",
        "log_reply_length_status=$($logSignals.ReplyLengthStatus)",
        "log_generic_host_prompt=$($logSignals.GenericHostPrompt)",
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
