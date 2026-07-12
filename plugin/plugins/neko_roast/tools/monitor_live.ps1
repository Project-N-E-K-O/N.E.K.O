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
  -ExpectRealOutput  Add real-output alerts for dry_run, disabled live plugin, disconnects, stale latest results, latency, test isolation, watchdogs, contamination, long replies, and repeated live replies.
  -BackendLogPath    Read backend log tail for playback watchdog, unrelated proactive output, send_lanlan_response length markers, template host-bait, and repeated reply text.
                      If omitted, the monitor tries .codex-backend-live-test.log in the current directory and repo root.

Key fields:
  alerts             '-' when no known risk is detected, otherwise comma-separated risks.
  director_action    Next automatic live action expected from NEKO.
  latest_route       Latest handled module, such as avatar_roast, danmaku_response, live_support_events, warmup_hosting, idle_hosting, or active_engagement.
  latest_uid / avatar_repeat_uid
                    Opaque viewer correlation IDs for the latest event and repeated avatar roast; raw platform UIDs are never printed.
  latest_output_len  Length of latest result output from hosted-ui context; useful when backend log is missing.
  latest_reply_length_mode / latest_reply_target / latest_anchor_hint / latest_room_theme
                    Danmaku-response review fields: whether the reply used default, expanded, or room_bridge length; what it targeted; the current-message anchor; and the safe room-theme label.
  latest_reply_shape_reason
                    Latest plugin-side shape/review reason, preferring hosted-ui metadata if present and otherwise falling back to legacy backend log shape_reason.
  pipeline_latency  Time from live event seen_at to plugin result creation, useful for separating plugin pipeline delay from backend/TTS playback delay.
  dispatcher_latency
                    Time spent inside the plugin dispatcher push call; high values point to plugin transport or message-plane pressure.
  spoken_latency_estimate
                    Approximate time from latest plugin result creation to a timestamped backend send_lanlan_response line; '-' when timestamps are unavailable.
  recent_long_reply_count
                    Count of recent hosted-ui outputs over the reply length warning threshold.
  recent_long_reply_*
                    Per-route long-reply counts for avatar_roast, danmaku_response, live_support_events, idle_hosting, active_engagement, and warmup_hosting.
  recent_generic_host_prompt_count
                    Count of recent hosted-ui outputs that look like template host-bait lines.
  log_generic_host_prompt
                    True when send_lanlan_response text in backend log contains template host-bait reply text.
  log_reply_repeat
                    True when the latest send_lanlan_response text line repeats or remixes a recent live reply in the backend log window.
  log_reply_suppressed
                    True when backend log shows legacy or experimental repeat suppression markers, useful as a compatibility clue only.
  log_reply_shape_reason
                    Latest legacy or experimental NEKO Live output shaping marker, such as quality_fallback or dangling_choice.
  log_reply_quality_fallback_count / log_reply_dangling_choice_count
                    Counts within the inspected backend log tail; values >=3 mean fallback is frequent, not just a one-off rescue.
  avatar_repeat_count
                    How many recent avatar_roast results were seen for avatar_repeat_uid.
  recent_*          Recent route counts for avatar_roast, danmaku_response, live_support_events, warmup_hosting, idle_hosting, and active_engagement.
  recent_actual_*   Recent pushed route counts for avatar_roast, danmaku_response, live_support_events, warmup_hosting, idle_hosting, and active_engagement.
  recent_total      Total recent result count in the hosted-ui context snapshot.
  recent_pushed / recent_dry_run / recent_skipped / recent_failed
                    Recent result status counts, so route attempts are not mistaken for actual output.
  recent_signal_*
                    Recent actual event-signal counts for danmaku_signal, gift_signal, and super_chat_signal.
  recent_observed_signal_*
                    Recent observed event-signal counts across all statuses, including skipped signal-only events.
  recent_skipped_signal_*
                    Recent skipped event-signal counts, useful for confirming gift/SC were seen but did not trigger AI.
  latest_gift_uid / latest_gift_value
                    Latest observed gift signal summary using an opaque viewer correlation ID; gift signal-only events may have no gift name or avatar.
  recent_topic_skip_*
                    Recent active-topic material skip reason counts: single-viewer flood, stale danmaku, avatar-roast context, or non-output danmaku.
  recent_topic_source_*
                    Recent Active Engagement topic source counts for fallback, Bili trending, and recent danmaku material.
  recent_topic_shape_*
                    Recent Active Engagement topic shape counts, useful for spotting whether proactive topics keep using the same interaction shape.
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
  topic_shape_bias
                    Alert name when recent Active Engagement topics overuse one interaction shape, making proactive hosting feel repetitive.
  topic_reply_missing / host_beat_reply_missing
                    Alert names when proactive output lacks a visible reply hook for viewers.
  topic_reply_affordance_bias
                    Alert name when recent Active Engagement topics overuse one viewer reply path.
  host_beat_reply_affordance_bias
                    Alert name when recent idle-hosting beats overuse one viewer reply path.
  topic_axis_bias
                    Alert name when recent Active Engagement topics overuse one fun axis.
  host_beat_axis_bias
                    Alert name when recent idle-hosting beats overuse one fun axis.
  topic_family_bias
                    Alert name when recent Active Engagement topics overuse one content family.
  host_beat_family_bias
                    Alert name when recent idle-hosting beats overuse one content family.
  latest_spent_output_family
                    Latest pushed NEKO output's spent-output family tags; dry_run/skipped results are ignored.
  recent_spent_output_family_*
                    Recent pushed spent-output family counts, useful for spotting repeated old live bits such as rewards or audience prompts.
  spent_output_family_bias
                    Alert name when recent pushed NEKO outputs overuse one spent-output family.
  latest_trace_id   Latest Runtime Timeline trace id from live_explain or recent result.
  timeline_stage_* / timeline_status_* / timeline_route_* / timeline_reason_*
                    Compact privacy-safe Runtime Timeline nodes for the latest trace. Reasons are limited to known machine codes; unknown values are redacted.
  live_disabled     Alert name for real-output tests when the NEKO Live plugin is disabled.
  generic_host_prompt
                    Alert name for template-like "please interact / send danmaku / anyone here" output.
  host_beat_repeat  Alert name for repeated idle-hosting host beat material.
  proactive_in_engaged
                    Alert name when the latest actual proactive output happened while live_state is engaged.
  warmup_repeat     Alert name when warmup_hosting has more than one recent actual output.
  warmup_missing / idle_missing / active_missing / active_blocks_idle
                    Alert names for automatic-hosting gaps during solo-stream validation.
                    *_missing means the director says a line is ready but recent results contain no such output yet.
                    active_blocks_idle means active engagement is still selected even though idle hosting is already eligible.
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

function Format-LatencyMs {
    param([object]$Value)
    if ($null -eq $Value -or "$Value" -eq "") {
        return "-"
    }
    try {
        return Format-Latency ([double]$Value)
    } catch {
        return "-"
    }
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

function Get-NumberOrNull {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    try {
        $number = [double]$Value
    } catch {
        return $null
    }
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
        return $null
    }
    return $number
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

function Get-ReplyLengthWarnForRoute {
    param(
        [object]$Route,
        [int]$DefaultWarn
    )
    $routeName = "$(Get-Field $Route)"
    if ($routeName -in @("warmup_hosting", "idle_hosting", "active_engagement")) {
        return [Math]::Min($DefaultWarn, 60)
    }
    return $DefaultWarn
}

function Test-DispatcherAckOutput {
    param([object]$Value)
    $text = "$Value".Trim()
    return $text -match '^queued_to_neko\(' `
        -or $text -match '^dry_run\(' `
        -or $text -match '^dry_run:' `
        -or $text -match '^skipped_to_neko\(' `
        -or $text -match '^skipped:'
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

function Get-LogLineTimestampIso {
    param([string]$Line)
    if (-not $Line) {
        return ""
    }
    $patterns = @(
        "^(?<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
        "^\[(?<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]",
        "^(?<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    )
    foreach ($pattern in $patterns) {
        $match = [regex]::Match($Line, $pattern)
        if (-not $match.Success) {
            continue
        }
        $raw = "$($match.Groups["ts"].Value)"
        try {
            if ($raw -match "(Z|[+-]\d{2}:?\d{2})$") {
                return ([datetimeoffset]::Parse($raw)).ToUniversalTime().ToString("o")
            }
            return ([datetimeoffset]::new(([datetime]::Parse($raw)), [datetimeoffset]::Now.Offset)).ToUniversalTime().ToString("o")
        } catch {
            return ""
        }
    }
    return ""
}

function Get-DeltaMsBetweenIso {
    param(
        [object]$StartIso,
        [object]$EndIso
    )
    if ($null -eq $StartIso -or $null -eq $EndIso -or "$StartIso" -eq "" -or "$EndIso" -eq "") {
        return $null
    }
    try {
        $start = [datetimeoffset]::Parse("$StartIso")
        $end = [datetimeoffset]::Parse("$EndIso")
        $delta = ($end.ToUniversalTime() - $start.ToUniversalTime()).TotalMilliseconds
    } catch {
        return $null
    }
    if ([double]::IsNaN($delta) -or [double]::IsInfinity($delta) -or $delta -lt 0) {
        return $null
    }
    return [int][Math]::Round($delta)
}

function To-IntOrDefault {
    param(
        [object]$Value,
        [int]$DefaultValue
    )
    try {
        return [int]$Value
    } catch {
        return $DefaultValue
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

function Get-CompactSafeField {
    param(
        [object]$Value,
        [int]$MaxLength = 80,
        [string]$Default = "-"
    )
    $text = Get-CompactPreview $Value $MaxLength $Default
    if ($text -eq $Default) {
        return $text
    }
    $lowered = $text.ToLowerInvariant()
    foreach ($marker in @("token=", "signature=", "authorization:", "cookie=", "sessdata", "bili_jct")) {
        if ($lowered.Contains($marker)) {
            return $Default
        }
    }
    return $text
}

function Get-OpaqueCorrelationId {
    param(
        [object]$Value,
        [string]$Default = "-"
    )
    $text = Get-CompactField $Value $Default
    if ($text -eq $Default) {
        return $text
    }
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
        $digest = $sha256.ComputeHash($bytes)
    } finally {
        $sha256.Dispose()
    }
    $hex = [System.BitConverter]::ToString($digest).Replace("-", "").ToLowerInvariant()
    return "viewer_" + $hex.Substring(0, 12)
}

function Get-TimelineReasonCode {
    param(
        [object]$Value,
        [string]$Default = "-"
    )
    $reason = Get-CompactField $Value $Default
    if ($reason -eq $Default) {
        return $reason
    }
    $allowed = @(
        "accepted",
        "allowed",
        "already_roasted",
        "batch_welcome",
        "blocked",
        "cooldown",
        "dispatcher.dry_run",
        "dispatcher.failed",
        "dispatcher.pushed",
        "dispatcher.skipped",
        "dry_run",
        "live_disabled",
        "live_ingest_disconnected",
        "live_room_offline",
        "manual_paused",
        "missing_uid",
        "normalized",
        "ok",
        "output_channel_unavailable",
        "ready",
        "room_not_configured",
        "safety_degraded",
        "safety_tripped"
    )
    if ($allowed -contains $reason) {
        return $reason
    }
    return "[redacted]"
}

function Get-TimelineRows {
    param(
        [object]$State,
        [object]$Latest,
        [int]$Limit = 4
    )
    $rows = @()
    if ($null -ne $State -and $null -ne $State.live_explain -and $null -ne $State.live_explain.timeline) {
        $rows = @($State.live_explain.timeline)
    }
    if ($rows.Count -eq 0 -and $null -ne $Latest -and $null -ne $Latest.timeline) {
        $rows = @($Latest.timeline)
    }
    if ($rows.Count -gt $Limit) {
        return @($rows | Select-Object -Last $Limit)
    }
    return $rows
}

function Add-DynamicCount {
    param(
        [hashtable]$Counts,
        [object]$Value
    )
    $key = Get-CompactField $Value
    if ($key -eq "-") {
        return
    }
    if (-not $Counts.ContainsKey($key)) {
        $Counts[$key] = 0
    }
    $Counts[$key] += 1
}

function Get-DynamicCountBias {
    param([hashtable]$Counts)
    $total = 0
    $max = 0
    $top = "-"
    foreach ($entry in $Counts.GetEnumerator()) {
        $count = [int]$entry.Value
        $total += $count
        if ($count -gt $max) {
            $max = $count
            $top = "$($entry.Key)"
        }
    }
    $bias = "False"
    if ($total -ge 3 -and (($max * 100.0) / $total) -ge 75.0) {
        $bias = "True"
    }
    return [pscustomobject]@{
        Total = $total
        Max = $max
        Top = $top
        Bias = $bias
    }
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
        "(\u6709\u4eba\u5417|\u6709\u4eba\u5728\u5417|\u8fd8\u5728\u5417|\u5728\u4e0d\u5728)",
        "(?i)what\s+should\s+we\s+talk\s+about",
        "(?i)what\s+do\s+you\s+want\s+to\s+(hear|talk)",
        "(?i)(anyone\s+here|still\s+here)",
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

function Normalize-LiveReplyText {
    param([string]$Text)
    $value = "$Text".Trim().ToLowerInvariant()
    return [regex]::Replace($value, "[\s\p{P}\p{S}_]+", "")
}

function Get-LiveReplyNgrams {
    param(
        [string]$Text,
        [int]$Size = 3
    )
    $value = Normalize-LiveReplyText $Text
    $grams = @{}
    if ($value.Length -le $Size) {
        return $grams
    }
    for ($index = 0; $index -le ($value.Length - $Size); $index++) {
        $gram = $value.Substring($index, $Size)
        if ($gram) {
            $grams[$gram] = $true
        }
    }
    return $grams
}

function Test-LiveReplyNgramOverlap {
    param(
        [string]$Previous,
        [string]$Current
    )
    return ((Get-LiveReplyNgramOverlapRatio $Previous $Current) -ge 0.62)
}

function Get-LiveReplyNgramOverlapRatio {
    param(
        [string]$Previous,
        [string]$Current
    )
    $left = Get-LiveReplyNgrams $Previous
    $right = Get-LiveReplyNgrams $Current
    if ($left.Count -lt 4 -or $right.Count -lt 4) {
        return 0.0
    }
    $shared = 0
    foreach ($key in $left.Keys) {
        if ($right.ContainsKey($key)) {
            $shared += 1
        }
    }
    $denominator = [Math]::Min($left.Count, $right.Count)
    if ($denominator -le 0) {
        return 0.0
    }
    return ($shared / $denominator)
}

function New-UnicodeToken {
    param([int[]]$Codepoints)
    $chars = @()
    foreach ($codepoint in $Codepoints) {
        $chars += [char]$codepoint
    }
    return -join $chars
}

function Get-LiveReplyAnchorFingerprint {
    param([string]$Text)
    $value = Normalize-LiveReplyText $Text
    $anchors = @{}
    if (-not $value) {
        return $anchors
    }
    $groups = @(
        @(
            (New-UnicodeToken @(0x60CA, 0x559C)),
            (New-UnicodeToken @(0x5C0F, 0x60CA, 0x559C)),
            (New-UnicodeToken @(0x60CA, 0x559C, 0x5956, 0x52B1)),
            "surprise"
        ),
        @(
            (New-UnicodeToken @(0x5C0F, 0x9C7C, 0x5E72)),
            (New-UnicodeToken @(0x9C7C, 0x5E72)),
            (New-UnicodeToken @(0x5956, 0x52B1)),
            (New-UnicodeToken @(0x5956, 0x8D4F)),
            (New-UnicodeToken @(0x7292, 0x52B3)),
            (New-UnicodeToken @(0x793C, 0x7269))
        ),
        @(
            (New-UnicodeToken @(0x7279, 0x522B, 0x4F01, 0x5212)),
            (New-UnicodeToken @(0x4F01, 0x5212)),
            (New-UnicodeToken @(0x8282, 0x76EE)),
            (New-UnicodeToken @(0x73AF, 0x8282)),
            (New-UnicodeToken @(0x8BA1, 0x5212))
        ),
        @(
            (New-UnicodeToken @(0x5927, 0x5BB6)),
            (New-UnicodeToken @(0x4F60, 0x4EEC)),
            (New-UnicodeToken @(0x89C2, 0x4F17)),
            (New-UnicodeToken @(0x5F39, 0x5E55)),
            (New-UnicodeToken @(0x4E92, 0x52A8)),
            (New-UnicodeToken @(0x53D1, 0x8A00)),
            (New-UnicodeToken @(0x63A5, 0x8BDD)),
            (New-UnicodeToken @(0x60F3, 0x542C, 0x4EC0, 0x4E48)),
            (New-UnicodeToken @(0x804A, 0x70B9, 0x4EC0, 0x4E48)),
            (New-UnicodeToken @(0x6263, 0x0031)),
            (New-UnicodeToken @(0x6263, 0x4E2A, 0x0031)),
            (New-UnicodeToken @(0x5431, 0x4E00, 0x58F0)),
            (New-UnicodeToken @(0x5192, 0x4E2A, 0x6CE1)),
            (New-UnicodeToken @(0x7ED9, 0x70B9, 0x53CD, 0x5E94)),
            (New-UnicodeToken @(0x7ED9, 0x732B, 0x732B, 0x4E00, 0x70B9, 0x53CD, 0x5E94)),
            (New-UnicodeToken @(0x8FD8, 0x5728, 0x5417)),
            (New-UnicodeToken @(0x6709, 0x4EBA, 0x5417)),
            (New-UnicodeToken @(0x6709, 0x4EBA, 0x5728, 0x5417)),
            (New-UnicodeToken @(0x5728, 0x4E0D, 0x5728))
        ),
        @(
            (New-UnicodeToken @(0x4E3B, 0x64AD, 0x529B)),
            (New-UnicodeToken @(0x6B63, 0x7ECF, 0x4E3B, 0x64AD)),
            (New-UnicodeToken @(0x50CF, 0x4E3B, 0x64AD)),
            (New-UnicodeToken @(0x4E3B, 0x6301)),
            "hostscore"
        ),
        @(
            (New-UnicodeToken @(0x4E00, 0x4E2A, 0x5B57)),
            (New-UnicodeToken @(0x4E00, 0x4E2A, 0x8BCD)),
            (New-UnicodeToken @(0x4E09, 0x5B57)),
            (New-UnicodeToken @(0x6697, 0x53F7)),
            (New-UnicodeToken @(0x6253, 0x5206)),
            "oneword",
            "password"
        ),
        @(
            (New-UnicodeToken @(0x4E8C, 0x9009, 0x4E00)),
            (New-UnicodeToken @(0x9009, 0x4E00, 0x4E2A)),
            (New-UnicodeToken @(0x8FD8, 0x662F)),
            "ab",
            "eitheror"
        ),
        @(
            (New-UnicodeToken @(0x6C14, 0x6C1B)),
            (New-UnicodeToken @(0x6E29, 0x5EA6)),
            (New-UnicodeToken @(0x732B, 0x7A9D)),
            (New-UnicodeToken @(0x5C0F, 0x7535, 0x53F0)),
            (New-UnicodeToken @(0x6674, 0x5929)),
            (New-UnicodeToken @(0x5C0F, 0x96E8)),
            "roommood"
        ),
        @(
            (New-UnicodeToken @(0x684C, 0x9762)),
            (New-UnicodeToken @(0x6C34, 0x676F)),
            (New-UnicodeToken @(0x96F6, 0x98DF)),
            (New-UnicodeToken @(0x5C4F, 0x5E55)),
            (New-UnicodeToken @(0x952E, 0x76D8)),
            "objectscene"
        ),
        @(
            (New-UnicodeToken @(0x5410, 0x69FD)),
            (New-UnicodeToken @(0x522B, 0x7B11)),
            (New-UnicodeToken @(0x88AB, 0x81EA, 0x5DF1)),
            "tease"
        ),
        @(
            (New-UnicodeToken @(0x6311, 0x6218)),
            (New-UnicodeToken @(0x4EFB, 0x52A1)),
            (New-UnicodeToken @(0x59FF, 0x52BF)),
            (New-UnicodeToken @(0x4E09, 0x79D2)),
            "microchallenge"
        ),
        @(
            (New-UnicodeToken @(0x5B89, 0x9759)),
            (New-UnicodeToken @(0x51B7, 0x573A)),
            (New-UnicodeToken @(0x6CA1, 0x4EBA, 0x8BF4, 0x8BDD)),
            (New-UnicodeToken @(0x6CA1, 0x5F39, 0x5E55)),
            "quietroom"
        )
    )
    for ($index = 0; $index -lt $groups.Count; $index++) {
        foreach ($token in $groups[$index]) {
            $normalizedToken = Normalize-LiveReplyText $token
            if ($normalizedToken -and $value.Contains($normalizedToken)) {
                $anchors["group:$index"] = $true
                break
            }
        }
    }
    return $anchors
}

function Get-LiveReplyAudiencePromptSignalCount {
    param([string]$Text)
    $value = Normalize-LiveReplyText $Text
    if (-not $value) {
        return 0
    }
    $tokens = @(
        (New-UnicodeToken @(0x53D1, 0x8A00)),
        (New-UnicodeToken @(0x63A5, 0x8BDD)),
        (New-UnicodeToken @(0x4E92, 0x52A8)),
        (New-UnicodeToken @(0x60F3, 0x542C)),
        (New-UnicodeToken @(0x60F3, 0x770B)),
        (New-UnicodeToken @(0x804A, 0x70B9)),
        (New-UnicodeToken @(0x804A, 0x4EC0, 0x4E48)),
        (New-UnicodeToken @(0x8BF4, 0x70B9)),
        (New-UnicodeToken @(0x6765, 0x4E00, 0x53E5)),
        (New-UnicodeToken @(0x53D1, 0x5F39, 0x5E55)),
        (New-UnicodeToken @(0x53D1, 0x4E2A, 0x0031)),
        (New-UnicodeToken @(0x6263, 0x0031)),
        (New-UnicodeToken @(0x6263, 0x4E2A)),
        (New-UnicodeToken @(0x6263, 0x4E2A, 0x0031)),
        (New-UnicodeToken @(0x6253, 0x4E2A, 0x0031)),
        (New-UnicodeToken @(0x6253, 0x4E2A, 0x5206)),
        (New-UnicodeToken @(0x6253, 0x4E2A, 0x6807, 0x7B7E)),
        (New-UnicodeToken @(0x5431, 0x4E00, 0x58F0)),
        (New-UnicodeToken @(0x5192, 0x4E2A, 0x6CE1)),
        (New-UnicodeToken @(0x4E3E, 0x4E2A, 0x722A)),
        (New-UnicodeToken @(0x7ED9, 0x70B9, 0x53CD, 0x5E94)),
        (New-UnicodeToken @(0x7ED9, 0x732B, 0x732B, 0x4E00, 0x70B9, 0x53CD, 0x5E94)),
        (New-UnicodeToken @(0x8FD8, 0x5728, 0x5417)),
        (New-UnicodeToken @(0x6709, 0x4EBA, 0x5417)),
        (New-UnicodeToken @(0x6709, 0x4EBA, 0x5728, 0x5417)),
        (New-UnicodeToken @(0x5728, 0x4E0D, 0x5728)),
        "dropa1",
        "type1",
        "sayhi",
        "anyonehere",
        "stillhere"
    )
    $count = 0
    foreach ($token in $tokens) {
        $normalizedToken = Normalize-LiveReplyText $token
        if ($normalizedToken -and $value.Contains($normalizedToken)) {
            $count += 1
        }
    }
    return $count
}

function Test-LiveReplyAudiencePromptRepeat {
    param(
        [string]$Previous,
        [string]$Current
    )
    return ((Get-LiveReplyAudiencePromptSignalCount $Previous) -ge 1 -and (Get-LiveReplyAudiencePromptSignalCount $Current) -ge 1)
}

function Test-LiveReplyHostBeatRepeat {
    param(
        [string]$Previous,
        [string]$Current
    )
    $left = Get-LiveReplyAnchorFingerprint $Previous
    $right = Get-LiveReplyAnchorFingerprint $Current
    if ($left.Count -lt 1 -or $right.Count -lt 1) {
        return $false
    }
    $shared = @()
    foreach ($key in $left.Keys) {
        if ($right.ContainsKey($key)) {
            $shared += $key
        }
    }
    if ($shared.Count -ge 2) {
        return $true
    }
    if ($shared.Count -lt 1) {
        return $false
    }
    if (($shared -contains "group:3") -and (Test-LiveReplyAudiencePromptRepeat $Previous $Current)) {
        return $true
    }
    $singleAnchorGroups = @(0, 1, 2, 4, 5, 11)
    $contextualAnchorGroups = @(6, 7, 8, 9, 10)
    foreach ($key in $shared) {
        $groupIndex = [int]("$key" -replace "^group:", "")
        if ($singleAnchorGroups -contains $groupIndex) {
            return $true
        }
        if ($contextualAnchorGroups -contains $groupIndex) {
            return ((Get-LiveReplyNgramOverlapRatio $Previous $Current) -ge 0.25)
        }
    }
    return $false
}

function Test-RepeatedLiveReplyOutput {
    param(
        [string]$Previous,
        [string]$Current
    )
    $left = Normalize-LiveReplyText $Previous
    $right = Normalize-LiveReplyText $Current
    if ($left.Length -lt 4 -or $right.Length -lt 4) {
        return $false
    }
    if ($left -eq $right) {
        return $true
    }
    $shorter = $left
    $longer = $right
    if ($shorter.Length -gt $longer.Length) {
        $shorter = $right
        $longer = $left
    }
    if ($shorter.Length -ge 8 -and $longer.Contains($shorter)) {
        return $true
    }
    $prefix = [Math]::Min(8, [Math]::Min($left.Length, $right.Length))
    if ($prefix -ge 4 -and $left.Substring(0, $prefix) -eq $right.Substring(0, $prefix)) {
        return $true
    }
    if (Test-LiveReplyHostBeatRepeat $Previous $Current) {
        return $true
    }
    if (Test-LiveReplyNgramOverlap $left $right) {
        return $true
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
        ReplyRepeat = "-"
        ReplySuppressed = "-"
        ReplyShapeReason = "-"
        ReplyAt = "-"
        ReplyQualityFallbackCount = "0"
        ReplyDanglingChoiceCount = "0"
    }
    if (-not $Path) {
        return [pscustomobject]$signals
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]$signals
    }
    try {
        $logLines = @(Get-Content -LiteralPath $Path -Tail $TailLines -Encoding UTF8 -ErrorAction Stop)
        $text = $logLines -join "`n"
    } catch {
        return [pscustomobject]$signals
    }

    if ($text -match "(?i)(voice\s+playback\s+gate\s+watchdog|playback\s+gate\s+watchdog|voice_play_end.*missing|missing.*voice_play_end)") {
        $signals.Watchdog = "True"
    } else {
        $signals.Watchdog = "False"
    }
    $contaminationLines = @(
        $logLines | Where-Object {
            $_ -match "(?i)(warthunder|proactive\s+bridge\s+output|proactive.*queued)" -and
            $_ -notmatch "(?i)(plugin=neko_roast|proactive_message enqueued callback)"
        }
    )
    $contaminationText = $contaminationLines -join "`n"
    if ($contaminationText -match "(?i)warthunder") {
        $signals.Contamination = "warthunder"
    } elseif ($contaminationText -match "(?i)(proactive\s+bridge\s+output|proactive.*queued)") {
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
    $sendLineMatches = [regex]::Matches($text, "(?im)^.*send_lanlan_response.*$")
    if ($sendLineMatches.Count -gt 0) {
        $replyAt = Get-LogLineTimestampIso "$($sendLineMatches[$sendLineMatches.Count - 1].Value)"
        if ($replyAt) {
            $signals.ReplyAt = $replyAt
        }
    }
    $signals.GenericHostPrompt = "False"
    foreach ($match in $responseTextMatches) {
        if (Test-GenericHostPromptOutput $match.Groups[1].Value) {
            $signals.GenericHostPrompt = "True"
            break
        }
    }
    $signals.ReplyRepeat = "False"
    $replyTexts = @()
    foreach ($match in $responseTextMatches) {
        $reply = "$($match.Groups[1].Value)".Trim()
        if ($reply) {
            $replyTexts += $reply
        }
    }
    if ($replyTexts.Count -ge 2) {
        $window = @($replyTexts | Select-Object -Last 10)
        $current = $window[$window.Count - 1]
        foreach ($previous in @($window | Select-Object -First ($window.Count - 1))) {
            if (Test-RepeatedLiveReplyOutput $previous $current) {
                $signals.ReplyRepeat = "True"
                break
            }
        }
    }
    if ($signals.ReplyRepeat -ne "True" -and $text -match "(?i)(NEKO Live repeated reply detected|NEKO Live repeated reply suppressed|neko_live_reply_repeat\s*[=:]\s*true|neko_live_reply_suppressed\s*[=:]\s*repeat)") {
        $signals.ReplyRepeat = "True"
    }
    $signals.ReplySuppressed = "False"
    if ($text -match "(?i)(NEKO Live repeated reply suppressed|neko_live_reply_suppressed\s*[=:]\s*repeat)") {
        $signals.ReplySuppressed = "True"
    }
    $shapeReasonMatches = [regex]::Matches($text, "(?im)send_lanlan_response[^\r\n]*(?:shape_reason|neko_live_reply_shape_reason)\s*[=:]\s*([A-Za-z0-9_+\-]+)")
    if ($shapeReasonMatches.Count -gt 0) {
        $signals.ReplyShapeReason = "$($shapeReasonMatches[$shapeReasonMatches.Count - 1].Groups[1].Value)"
        $qualityFallbackCount = 0
        $danglingChoiceCount = 0
        foreach ($match in $shapeReasonMatches) {
            $reason = "$($match.Groups[1].Value)"
            if ($reason -match "(^|\+)quality_fallback($|\+)") {
                $qualityFallbackCount += 1
            }
            if ($reason -match "(^|\+)dangling_choice($|\+)") {
                $danglingChoiceCount += 1
            }
        }
        $signals.ReplyQualityFallbackCount = "$qualityFallbackCount"
        $signals.ReplyDanglingChoiceCount = "$danglingChoiceCount"
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
    $profileCount = $profiles.Count
    $soloProfileCount = Get-NumberOrNull $soloReadiness.profile_count
    if ($null -ne $soloProfileCount) {
        $profileCount = [int]$soloProfileCount
    }
    $recentRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
        live_support_events = 0
        warmup_hosting = 0
        idle_hosting = 0
        active_engagement = 0
    }
    $recentActualRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
        live_support_events = 0
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
    $recentSignalCounts = @{
        danmaku_signal = 0
        gift_signal = 0
        super_chat_signal = 0
    }
    $recentObservedSignalCounts = @{
        danmaku_signal = 0
        gift_signal = 0
        super_chat_signal = 0
    }
    $recentSkippedSignalCounts = @{
        danmaku_signal = 0
        gift_signal = 0
        super_chat_signal = 0
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
        similar_topic_title = 0
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
    $recentTopicShapeCounts = @{
        either_or = 0
        light_stance = 0
        tiny_tease = 0
        small_challenge = 0
    }
    $recentTopicAxisCounts = @{
        choice = 0
        tease = 0
        mood = 0
        micro_challenge = 0
        viewer_callback = 0
    }
    $recentHostBeatAxisCounts = @{
        choice = 0
        tease = 0
        mood = 0
        micro_challenge = 0
        viewer_callback = 0
    }
    $recentTopicFamilyCounts = @{
        choice_vote = 0
        short_callback = 0
        room_mood = 0
        object_scene = 0
        host_self_test = 0
        tease = 0
        micro_challenge = 0
    }
    $recentHostBeatFamilyCounts = @{
        choice_vote = 0
        short_callback = 0
        room_mood = 0
        object_scene = 0
        host_self_test = 0
        tease = 0
        micro_challenge = 0
    }
    $recentTopicReplyAffordanceCounts = @{}
    $recentHostBeatReplyAffordanceCounts = @{}
    $recentSpentOutputFamilyCounts = @{
        surprise = 0
        reward = 0
        program_plan = 0
        audience_prompt = 0
        host_self_test = 0
        short_callback = 0
        choice_vote = 0
        room_mood = 0
        object_scene = 0
        tease = 0
        micro_challenge = 0
        quiet_room = 0
    }
    $recentLongReplyRouteCounts = @{
        avatar_roast = 0
        danmaku_response = 0
        live_support_events = 0
        idle_hosting = 0
        active_engagement = 0
        warmup_hosting = 0
    }
    $recentLongReplyCount = 0
    $recentGenericHostPromptCount = 0
    $recentSpentOutputFamilyResultTotal = 0
    foreach ($result in $recent) {
        $route = "$(Get-Field $result.response_module)"
        if ($recentRouteCounts.ContainsKey($route)) {
            $recentRouteCounts[$route] += 1
        }
        $status = "$(Get-Field $result.status)"
        if ($recentStatusCounts.ContainsKey($status)) {
            $recentStatusCounts[$status] += 1
        }
        $signal = "$(Get-Field $result.event_signal)"
        if ($recentObservedSignalCounts.ContainsKey($signal)) {
            $recentObservedSignalCounts[$signal] += 1
        }
        if ("$status" -eq "skipped" -and $recentSkippedSignalCounts.ContainsKey($signal)) {
            $recentSkippedSignalCounts[$signal] += 1
        }
        if ("$status" -eq "pushed" -and $recentActualRouteCounts.ContainsKey($route)) {
            $recentActualRouteCounts[$route] += 1
        }
        if ("$status" -eq "pushed" -and $recentActualDanmakuRouteCounts.ContainsKey($route)) {
            $recentActualDanmakuRouteCounts[$route] += 1
        }
        if ("$status" -eq "pushed") {
            if ($recentSignalCounts.ContainsKey($signal)) {
                $recentSignalCounts[$signal] += 1
            }
        }
        if ("$status" -eq "pushed") {
            $spentFamilyValue = "$(Get-Field $result.spent_output_family)"
            $hasSpentOutputFamily = $false
            foreach ($spentFamily in @($spentFamilyValue -split ",")) {
                $spentFamily = "$spentFamily".Trim()
                if ($recentSpentOutputFamilyCounts.ContainsKey($spentFamily)) {
                    $recentSpentOutputFamilyCounts[$spentFamily] += 1
                    $hasSpentOutputFamily = $true
                }
            }
            if ($hasSpentOutputFamily) {
                $recentSpentOutputFamilyResultTotal += 1
            }
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
                $topicShape = "$(Get-Field $result.event.topic_shape)"
                if ($recentTopicShapeCounts.ContainsKey($topicShape)) {
                    $recentTopicShapeCounts[$topicShape] += 1
                }
                $topicAxis = "$(Get-Field $result.event.topic_fun_axis)"
                if ($recentTopicAxisCounts.ContainsKey($topicAxis)) {
                    $recentTopicAxisCounts[$topicAxis] += 1
                }
                $topicFamily = "$(Get-Field $result.event.topic_family)"
                if ($recentTopicFamilyCounts.ContainsKey($topicFamily)) {
                    $recentTopicFamilyCounts[$topicFamily] += 1
                }
                Add-DynamicCount $recentTopicReplyAffordanceCounts $result.event.topic_reply_affordance
            }
            if (
                ("$status" -in @("pushed", "dry_run")) -and
                ($route -eq "idle_hosting" -or "$(Get-Field $result.event.source)" -eq "idle_hosting")
            ) {
                $hostBeatAxis = "$(Get-Field $result.event.host_beat_fun_axis)"
                if ($recentHostBeatAxisCounts.ContainsKey($hostBeatAxis)) {
                    $recentHostBeatAxisCounts[$hostBeatAxis] += 1
                }
                $hostBeatFamily = "$(Get-Field $result.event.host_beat_family)"
                if ($recentHostBeatFamilyCounts.ContainsKey($hostBeatFamily)) {
                    $recentHostBeatFamilyCounts[$hostBeatFamily] += 1
                }
                Add-DynamicCount $recentHostBeatReplyAffordanceCounts $result.event.host_beat_reply_affordance
            }
        }
        if ($null -ne $result.output -and "$($result.output)" -ne "" -and -not (Test-DispatcherAckOutput $result.output)) {
            $routeReplyLengthWarn = Get-ReplyLengthWarnForRoute $route $ReplyLengthWarn
            if ("$($result.output)".Length -ge $routeReplyLengthWarn) {
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
    $topicShapeTotal = 0
    $topicShapeMax = 0
    foreach ($shapeCount in $recentTopicShapeCounts.Values) {
        $topicShapeTotal += [int]$shapeCount
        if ([int]$shapeCount -gt $topicShapeMax) {
            $topicShapeMax = [int]$shapeCount
        }
    }
    $topicShapeBias = "False"
    if ($topicShapeTotal -ge 3 -and (($topicShapeMax * 100.0) / $topicShapeTotal) -ge 75.0) {
        $topicShapeBias = "True"
    }
    $topicAxisTotal = 0
    $topicAxisMax = 0
    foreach ($axisCount in $recentTopicAxisCounts.Values) {
        $topicAxisTotal += [int]$axisCount
        if ([int]$axisCount -gt $topicAxisMax) {
            $topicAxisMax = [int]$axisCount
        }
    }
    $topicAxisBias = "False"
    if ($topicAxisTotal -ge 3 -and (($topicAxisMax * 100.0) / $topicAxisTotal) -ge 75.0) {
        $topicAxisBias = "True"
    }
    $hostBeatAxisTotal = 0
    $hostBeatAxisMax = 0
    foreach ($axisCount in $recentHostBeatAxisCounts.Values) {
        $hostBeatAxisTotal += [int]$axisCount
        if ([int]$axisCount -gt $hostBeatAxisMax) {
            $hostBeatAxisMax = [int]$axisCount
        }
    }
    $hostBeatAxisBias = "False"
    if ($hostBeatAxisTotal -ge 3 -and (($hostBeatAxisMax * 100.0) / $hostBeatAxisTotal) -ge 75.0) {
        $hostBeatAxisBias = "True"
    }
    $topicFamilyTotal = 0
    $topicFamilyMax = 0
    foreach ($familyCount in $recentTopicFamilyCounts.Values) {
        $topicFamilyTotal += [int]$familyCount
        if ([int]$familyCount -gt $topicFamilyMax) {
            $topicFamilyMax = [int]$familyCount
        }
    }
    $topicFamilyBias = "False"
    if ($topicFamilyTotal -ge 3 -and (($topicFamilyMax * 100.0) / $topicFamilyTotal) -ge 75.0) {
        $topicFamilyBias = "True"
    }
    $hostBeatFamilyTotal = 0
    $hostBeatFamilyMax = 0
    foreach ($familyCount in $recentHostBeatFamilyCounts.Values) {
        $hostBeatFamilyTotal += [int]$familyCount
        if ([int]$familyCount -gt $hostBeatFamilyMax) {
            $hostBeatFamilyMax = [int]$familyCount
        }
    }
    $hostBeatFamilyBias = "False"
    if ($hostBeatFamilyTotal -ge 3 -and (($hostBeatFamilyMax * 100.0) / $hostBeatFamilyTotal) -ge 75.0) {
        $hostBeatFamilyBias = "True"
    }
    $topicReplyAffordanceSummary = Get-DynamicCountBias $recentTopicReplyAffordanceCounts
    $topicReplyAffordanceBias = "$($topicReplyAffordanceSummary.Bias)"
    $topicReplyAffordanceTop = "$($topicReplyAffordanceSummary.Top)"
    $hostBeatReplyAffordanceSummary = Get-DynamicCountBias $recentHostBeatReplyAffordanceCounts
    $hostBeatReplyAffordanceBias = "$($hostBeatReplyAffordanceSummary.Bias)"
    $hostBeatReplyAffordanceTop = "$($hostBeatReplyAffordanceSummary.Top)"
    $spentOutputFamilyMax = 0
    foreach ($familyCount in $recentSpentOutputFamilyCounts.Values) {
        if ([int]$familyCount -gt $spentOutputFamilyMax) {
            $spentOutputFamilyMax = [int]$familyCount
        }
    }
    $spentOutputFamilyBias = "False"
    if ($recentSpentOutputFamilyResultTotal -ge 3 -and (($spentOutputFamilyMax * 100.0) / $recentSpentOutputFamilyResultTotal) -ge 75.0) {
        $spentOutputFamilyBias = "True"
    }
    $latest = $null
    if ($recent.Count -gt 0) {
        $latest = $recent[0]
    }

    $lastStatus = Get-Field $speech.last_result_status
    if ($lastStatus -eq "-" -and $null -ne $latest) {
        $lastStatus = Get-Field $latest.status
    }

    $pipelineLatency = $null
    $dispatcherLatency = $null
    if ($null -ne $latest) {
        $pipelineLatency = $latest.pipeline_latency_ms
        if ($null -eq $pipelineLatency) {
            $pipelineLatency = $latest.response_latency_ms
        }
        $dispatcherLatency = $latest.dispatcher_latency_ms
    }
    $latency = $speech.last_result_latency_ms
    if ($null -eq $latency) {
        $latency = $pipelineLatency
    }
    $latestRoute = "-"
    $latestSignal = "-"
    $latestDanmakuProfile = "-"
    $latestDanmakuReplyShape = "-"
    $latestReplyLengthMode = "-"
    $latestReplyTarget = "-"
    $latestAnchorHint = "-"
    $latestRoomTheme = "-"
    $latestReplyShapeReason = "-"
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
        $latestDanmakuProfile = Get-CompactField $latest.danmaku_profile
        $latestDanmakuReplyShape = Get-CompactField $latest.danmaku_reply_shape
        $latestReplyLengthMode = Get-CompactField $latest.reply_length_mode
        $latestReplyTarget = Get-CompactField $latest.danmaku_reply_target
        $latestAnchorHint = Get-CompactField $latest.danmaku_anchor_hint
        $latestRoomTheme = Get-CompactField $latest.room_theme
        $latestReplyShapeReason = Get-CompactField $latest.neko_live_reply_shape_reason
        $latestAge = Format-IsoAge $latest.created_at
        $latestAgeStatus = Get-AgeStatus $latest.created_at $LatestAgeWarnSec $LatestAgeStaleSec
        if ($null -ne $latest.output -and "$($latest.output)" -ne "") {
            $latestOutputText = "$($latest.output)"
            $latestOutputLen = "$($latestOutputText.Length)"
            if (Test-DispatcherAckOutput $latestOutputText) {
                $latestOutputLengthStatus = "ack"
            } else {
                $latestReplyLengthWarn = Get-ReplyLengthWarnForRoute $latestRoute $ReplyLengthWarn
                if ($latestOutputText.Length -ge $latestReplyLengthWarn) {
                    $latestOutputLengthStatus = "warn"
                } else {
                    $latestOutputLengthStatus = "ok"
                }
            }
        }
    }
    $latestTraceId = "-"
    if ($null -ne $state.live_explain) {
        $latestTraceId = Get-CompactSafeField $state.live_explain.trace_id 80
    }
    if ($latestTraceId -eq "-" -and $null -ne $latest) {
        $latestTraceId = Get-CompactSafeField $latest.trace_id 80
    }
    $timelineRows = @(Get-TimelineRows $state $latest 4)
    $timelineCount = $timelineRows.Count
    $timelineStage1 = "-"
    $timelineStage2 = "-"
    $timelineStage3 = "-"
    $timelineStage4 = "-"
    $timelineStatus1 = "-"
    $timelineStatus2 = "-"
    $timelineStatus3 = "-"
    $timelineStatus4 = "-"
    $timelineRoute1 = "-"
    $timelineRoute2 = "-"
    $timelineRoute3 = "-"
    $timelineRoute4 = "-"
    $timelineReason1 = "-"
    $timelineReason2 = "-"
    $timelineReason3 = "-"
    $timelineReason4 = "-"
    for ($timelineIndex = 0; $timelineIndex -lt $timelineRows.Count -and $timelineIndex -lt 4; $timelineIndex++) {
        $row = $timelineRows[$timelineIndex]
        $slot = $timelineIndex + 1
        Set-Variable -Name "timelineStage$slot" -Value (Get-CompactSafeField $row.stage 80)
        Set-Variable -Name "timelineStatus$slot" -Value (Get-CompactSafeField $row.status 80)
        Set-Variable -Name "timelineRoute$slot" -Value (Get-CompactSafeField $row.route 80)
        Set-Variable -Name "timelineReason$slot" -Value (Get-TimelineReasonCode $row.reason)
    }
    $latestTopicSource = "-"
    $latestTopicShape = "-"
    $latestTopicTitle = "-"
    $latestTopicKey = "-"
    $latestTopicHook = "-"
    $latestTopicPattern = "-"
    $latestTopicIntent = "-"
    $latestTopicFunAxis = "-"
    $latestTopicFamily = "-"
    $latestTopicPack = "-"
    $latestTopicReplyAffordance = "-"
    $latestTopicRecentSkipReason = "-"
    $latestTopicShapeGuardReason = "-"
    $latestTopicRepeat = "False"
    $latestHostBeatShape = "-"
    $latestHostBeatFunAxis = "-"
    $latestHostBeatFamily = "-"
    $latestHostBeatTitle = "-"
    $latestHostBeatKey = "-"
    $latestHostBeatHint = "-"
    $latestHostBeatIdleStage = "-"
    $latestHostBeatReplyAffordance = "-"
    $latestHostBeatRepeat = "False"
    $latestSpentOutputFamily = "-"
    $latestGiftUid = "-"
    $latestGiftName = "-"
    $latestGiftCount = "-"
    $latestGiftValue = "-"
    if ($null -ne $latest -and $null -ne $latest.event) {
        $latestSource = Get-CompactField $latest.event.source
        $latestUid = Get-OpaqueCorrelationId $latest.event.uid
        if (-not [string]::IsNullOrWhiteSpace("$($latest.event.danmaku_text)")) {
            $latestText = "[redacted]"
        }
        if ("$latestStatus" -eq "pushed") {
            $latestSpentOutputFamily = Get-CompactField $latest.spent_output_family
        }
        $latestTopicSource = Get-CompactField $latest.event.topic_source
        $latestTopicShape = Get-CompactField $latest.event.topic_shape
        $latestTopicTitle = Get-CompactField $latest.event.topic_title
        $latestTopicKey = Get-CompactField $latest.event.topic_key
        $latestTopicHook = Get-CompactField $latest.event.topic_hook
        $latestTopicPattern = Get-CompactField $latest.event.topic_pattern
        $latestTopicIntent = Get-CompactField $latest.event.topic_intent
        $latestTopicFunAxis = Get-CompactField $latest.event.topic_fun_axis
        $latestTopicFamily = Get-CompactField $latest.event.topic_family
        $latestTopicPack = Get-CompactField $latest.event.topic_pack
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
        $latestHostBeatFunAxis = Get-CompactField $latest.event.host_beat_fun_axis
        $latestHostBeatFamily = Get-CompactField $latest.event.host_beat_family
        $latestHostBeatTitle = Get-CompactField $latest.event.host_beat_title
        $latestHostBeatKey = Get-CompactField $latest.event.host_beat_key
        $latestHostBeatHint = Get-CompactField $latest.event.host_beat_hint
        $latestHostBeatIdleStage = Get-CompactField $latest.event.host_beat_idle_stage
        $latestHostBeatReplyAffordance = Get-CompactField $latest.event.host_beat_reply_affordance
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
    foreach ($result in $recent) {
        if ("$(Get-Field $result.event_signal)" -ne "gift_signal" -and "$(Get-Field $result.event.event_type)" -ne "gift") {
            continue
        }
        if ($null -eq $result.event) {
            continue
        }
        $latestGiftUid = Get-OpaqueCorrelationId $result.event.uid
        $latestGiftName = Get-CompactField $result.event.gift_name
        $latestGiftCount = Get-CompactField $result.event.gift_count
        $latestGiftValue = Get-CompactField $result.event.gift_value
        break
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
        $uid = Get-OpaqueCorrelationId $event.uid
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
    $spokenLatencyEstimate = $null
    if ($null -ne $latest) {
        $spokenLatencyEstimate = Get-DeltaMsBetweenIso $latest.created_at $logSignals.ReplyAt
    }
    if ($latestReplyShapeReason -eq "-") {
        $latestReplyShapeReason = Get-CompactField $logSignals.ReplyShapeReason
    }
    $alerts = @()
    if ($ExpectRealOutput) {
        if ("$(Get-Field $config.dry_run)" -eq "True") {
            $alerts += "dry_run"
        }
        $liveStateValue = "$(Get-Field $live.state)"
        $liveConnectedValue = "$(Get-Field $live.connected)"
        if ($liveConnectedValue -eq "False" -or $liveStateValue -notin @("connected", "receiving")) {
            $alerts += "live_disconnected"
        }
        if ("$(Get-Field $liveStatus.summary)" -ne "ready_to_stream") {
            $alerts += "live_not_ready"
        }
        if ("$(Get-Field $liveStatus.reason)" -eq "live_disabled") {
            $alerts += "live_disabled"
        }
        if (-not $backendLogAvailable) {
            $alerts += "backend_log_missing"
        }
        $testIsolationHasProfiles = ($profileCount -gt 0 -or "$testIsolationReason" -eq "viewer_profiles_present")
        if ("$testIsolationStatus" -in @("warning", "blocked") -and $testIsolationHasProfiles) {
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
    if ("$($logSignals.ReplyRepeat)" -eq "True") {
        $alerts += "reply_repeat"
    }
    if ("$($logSignals.ReplySuppressed)" -eq "True") {
        $alerts += "reply_suppressed"
    }
    if ("$($logSignals.ReplyShapeReason)" -match "(^|\+)quality_fallback($|\+)") {
        $alerts += "reply_quality_fallback"
    }
    if ("$($logSignals.ReplyShapeReason)" -match "(^|\+)dangling_choice($|\+)") {
        $alerts += "reply_dangling_choice"
    }
    if ((To-IntOrDefault $logSignals.ReplyQualityFallbackCount 0) -ge 3) {
        $alerts += "reply_quality_fallback_many"
    }
    if ((To-IntOrDefault $logSignals.ReplyDanglingChoiceCount 0) -ge 3) {
        $alerts += "reply_dangling_choice_many"
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
    if ($recentTopicSkipCounts['similar_topic_title'] -gt 0) {
        $alerts += "topic_similar_title"
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
    if ("$topicShapeBias" -eq "True") {
        $alerts += "topic_shape_bias"
    }
    if ("$topicAxisBias" -eq "True") {
        $alerts += "topic_axis_bias"
    }
    if ("$topicFamilyBias" -eq "True") {
        $alerts += "topic_family_bias"
    }
    if ("$topicReplyAffordanceBias" -eq "True") {
        $alerts += "topic_reply_affordance_bias"
    }
    if ("$latestStatus" -in @("pushed", "dry_run") -and "$latestRoute" -eq "active_engagement" -and "$latestTopicReplyAffordance" -eq "-") {
        $alerts += "topic_reply_missing"
    }
    if (
        "$latestStatus" -in @("pushed", "dry_run") -and
        "$latestRoute" -eq "idle_hosting" -and
        ("$latestHostBeatFunAxis" -eq "-" -or "$latestHostBeatReplyAffordance" -eq "-")
    ) {
        $alerts += "host_beat_reply_missing"
    }
    if ("$hostBeatAxisBias" -eq "True") {
        $alerts += "host_beat_axis_bias"
    }
    if ("$hostBeatFamilyBias" -eq "True") {
        $alerts += "host_beat_family_bias"
    }
    if ("$hostBeatReplyAffordanceBias" -eq "True") {
        $alerts += "host_beat_reply_affordance_bias"
    }
    if ("$spentOutputFamilyBias" -eq "True") {
        $alerts += "spent_output_family_bias"
    }
    if ("$latestHostBeatRepeat" -eq "True") {
        $alerts += "host_beat_repeat"
    }
    if (
        "$(Get-CompactField $liveState.state)" -eq "engaged" -and
        "$latestStatus" -eq "pushed" -and
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
    $activeIdleWaitNumber = Get-NumberOrNull $activeIdleWait
    if (
        "$(Get-CompactField $liveDirector.next_auto_action)" -eq "active_engagement" -and
        "$(Get-Field $liveDirector.eligible)" -eq "True" -and
        "$(Get-Field $idleHosting.eligible)" -eq "True" -and
        "$(Get-Field $liveState.idle_hosting_candidate)" -eq "True" -and
        $null -ne $activeIdleWaitNumber -and
        $activeIdleWaitNumber -le 0.0
    ) {
        $alerts += "active_blocks_idle"
    }
    $alertText = "-"
    if ($alerts.Count -gt 0) {
        $alertText = $alerts -join ","
    }
    $latestAnchorHintOutput = $latestAnchorHint
    if ($latestAnchorHintOutput -ne "-") {
        $latestAnchorHintOutput = "[redacted]"
    }
    $latestTopicTitleOutput = $latestTopicTitle
    $latestTopicKeyOutput = $latestTopicKey
    $latestTopicHookOutput = $latestTopicHook
    if ("$latestTopicSource" -eq "recent_danmaku") {
        $latestTopicTitleOutput = "[redacted]"
        $latestTopicKeyOutput = "[redacted]"
        $latestTopicHookOutput = "[redacted]"
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
        "profile_count=$profileCount",
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
        "latest_gift_uid=$latestGiftUid",
        "latest_gift_name=$latestGiftName",
        "latest_gift_count=$latestGiftCount",
        "latest_gift_value=$latestGiftValue",
        "latest_danmaku_profile=$latestDanmakuProfile",
        "latest_danmaku_reply_shape=$latestDanmakuReplyShape",
        "latest_reply_length_mode=$latestReplyLengthMode",
        "latest_reply_target=$latestReplyTarget",
        "latest_anchor_hint=$latestAnchorHintOutput",
        "latest_room_theme=$latestRoomTheme",
        "latest_reply_shape_reason=$latestReplyShapeReason",
        "latest_uid=$latestUid",
        "latest_source=$latestSource",
        "latest_text=$latestText",
        "latest_reason=$latestReason",
        "latest_trace_id=$latestTraceId",
        "timeline_count=$timelineCount",
        "timeline_stage_1=$timelineStage1",
        "timeline_status_1=$timelineStatus1",
        "timeline_route_1=$timelineRoute1",
        "timeline_reason_1=$timelineReason1",
        "timeline_stage_2=$timelineStage2",
        "timeline_status_2=$timelineStatus2",
        "timeline_route_2=$timelineRoute2",
        "timeline_reason_2=$timelineReason2",
        "timeline_stage_3=$timelineStage3",
        "timeline_status_3=$timelineStatus3",
        "timeline_route_3=$timelineRoute3",
        "timeline_reason_3=$timelineReason3",
        "timeline_stage_4=$timelineStage4",
        "timeline_status_4=$timelineStatus4",
        "timeline_route_4=$timelineRoute4",
        "timeline_reason_4=$timelineReason4",
        "latest_age=$latestAge",
        "latest_age_status=$latestAgeStatus",
        "latest_output_len=$latestOutputLen",
        "latest_output_length_status=$latestOutputLengthStatus",
        "recent_long_reply_count=$recentLongReplyCount",
        "recent_long_reply_avatar_roast=$($recentLongReplyRouteCounts['avatar_roast'])",
        "recent_long_reply_danmaku_response=$($recentLongReplyRouteCounts['danmaku_response'])",
        "recent_long_reply_live_support_events=$($recentLongReplyRouteCounts['live_support_events'])",
        "recent_long_reply_idle_hosting=$($recentLongReplyRouteCounts['idle_hosting'])",
        "recent_long_reply_active_engagement=$($recentLongReplyRouteCounts['active_engagement'])",
        "recent_long_reply_warmup_hosting=$($recentLongReplyRouteCounts['warmup_hosting'])",
        "recent_generic_host_prompt_count=$recentGenericHostPromptCount",
        "recent_total=$($recent.Count)",
        "recent_avatar_roast=$($recentRouteCounts['avatar_roast'])",
        "recent_danmaku_response=$($recentRouteCounts['danmaku_response'])",
        "recent_live_support_events=$($recentRouteCounts['live_support_events'])",
        "recent_warmup_hosting=$($recentRouteCounts['warmup_hosting'])",
        "recent_idle_hosting=$($recentRouteCounts['idle_hosting'])",
        "recent_active_engagement=$($recentRouteCounts['active_engagement'])",
        "recent_actual_avatar_roast=$($recentActualRouteCounts['avatar_roast'])",
        "recent_actual_danmaku_response=$($recentActualRouteCounts['danmaku_response'])",
        "recent_actual_live_support_events=$($recentActualRouteCounts['live_support_events'])",
        "recent_actual_warmup_hosting=$($recentActualRouteCounts['warmup_hosting'])",
        "recent_actual_idle_hosting=$($recentActualRouteCounts['idle_hosting'])",
        "recent_actual_active_engagement=$($recentActualRouteCounts['active_engagement'])",
        "recent_signal_danmaku_signal=$($recentSignalCounts['danmaku_signal'])",
        "recent_signal_gift_signal=$($recentSignalCounts['gift_signal'])",
        "recent_signal_super_chat_signal=$($recentSignalCounts['super_chat_signal'])",
        "recent_observed_signal_danmaku_signal=$($recentObservedSignalCounts['danmaku_signal'])",
        "recent_observed_signal_gift_signal=$($recentObservedSignalCounts['gift_signal'])",
        "recent_observed_signal_super_chat_signal=$($recentObservedSignalCounts['super_chat_signal'])",
        "recent_skipped_signal_danmaku_signal=$($recentSkippedSignalCounts['danmaku_signal'])",
        "recent_skipped_signal_gift_signal=$($recentSkippedSignalCounts['gift_signal'])",
        "recent_skipped_signal_super_chat_signal=$($recentSkippedSignalCounts['super_chat_signal'])",
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
        "recent_topic_skip_similar_topic_title=$($recentTopicSkipCounts['similar_topic_title'])",
        "recent_topic_source_fallback=$($recentTopicSourceCounts['fallback'])",
        "recent_topic_source_bili_trending=$($recentTopicSourceCounts['bili_trending'])",
        "recent_topic_source_recent_danmaku=$($recentTopicSourceCounts['recent_danmaku'])",
        "recent_topic_source_bias=$topicSourceBias",
        "recent_topic_shape_either_or=$($recentTopicShapeCounts['either_or'])",
        "recent_topic_shape_light_stance=$($recentTopicShapeCounts['light_stance'])",
        "recent_topic_shape_tiny_tease=$($recentTopicShapeCounts['tiny_tease'])",
        "recent_topic_shape_small_challenge=$($recentTopicShapeCounts['small_challenge'])",
        "recent_topic_axis_choice=$($recentTopicAxisCounts['choice'])",
        "recent_topic_axis_tease=$($recentTopicAxisCounts['tease'])",
        "recent_topic_axis_mood=$($recentTopicAxisCounts['mood'])",
        "recent_topic_axis_micro_challenge=$($recentTopicAxisCounts['micro_challenge'])",
        "recent_topic_axis_viewer_callback=$($recentTopicAxisCounts['viewer_callback'])",
        "recent_topic_family_choice_vote=$($recentTopicFamilyCounts['choice_vote'])",
        "recent_topic_family_short_callback=$($recentTopicFamilyCounts['short_callback'])",
        "recent_topic_family_room_mood=$($recentTopicFamilyCounts['room_mood'])",
        "recent_topic_family_object_scene=$($recentTopicFamilyCounts['object_scene'])",
        "recent_topic_family_host_self_test=$($recentTopicFamilyCounts['host_self_test'])",
        "recent_topic_family_tease=$($recentTopicFamilyCounts['tease'])",
        "recent_topic_family_micro_challenge=$($recentTopicFamilyCounts['micro_challenge'])",
        "recent_host_beat_axis_choice=$($recentHostBeatAxisCounts['choice'])",
        "recent_host_beat_axis_tease=$($recentHostBeatAxisCounts['tease'])",
        "recent_host_beat_axis_mood=$($recentHostBeatAxisCounts['mood'])",
        "recent_host_beat_axis_micro_challenge=$($recentHostBeatAxisCounts['micro_challenge'])",
        "recent_host_beat_axis_viewer_callback=$($recentHostBeatAxisCounts['viewer_callback'])",
        "recent_host_beat_family_choice_vote=$($recentHostBeatFamilyCounts['choice_vote'])",
        "recent_host_beat_family_short_callback=$($recentHostBeatFamilyCounts['short_callback'])",
        "recent_host_beat_family_room_mood=$($recentHostBeatFamilyCounts['room_mood'])",
        "recent_host_beat_family_object_scene=$($recentHostBeatFamilyCounts['object_scene'])",
        "recent_host_beat_family_host_self_test=$($recentHostBeatFamilyCounts['host_self_test'])",
        "recent_host_beat_family_tease=$($recentHostBeatFamilyCounts['tease'])",
        "recent_host_beat_family_micro_challenge=$($recentHostBeatFamilyCounts['micro_challenge'])",
        "recent_spent_output_family_reward=$($recentSpentOutputFamilyCounts['reward'])",
        "recent_spent_output_family_audience_prompt=$($recentSpentOutputFamilyCounts['audience_prompt'])",
        "recent_spent_output_family_program_plan=$($recentSpentOutputFamilyCounts['program_plan'])",
        "recent_spent_output_family_host_self_test=$($recentSpentOutputFamilyCounts['host_self_test'])",
        "recent_spent_output_family_short_callback=$($recentSpentOutputFamilyCounts['short_callback'])",
        "recent_spent_output_family_choice_vote=$($recentSpentOutputFamilyCounts['choice_vote'])",
        "recent_spent_output_family_quiet_room=$($recentSpentOutputFamilyCounts['quiet_room'])",
        "recent_topic_shape_bias=$topicShapeBias",
        "recent_topic_intent_quick_vote=$($recentTopicIntentCounts['quick_vote'])",
        "recent_topic_intent_tiny_answer=$($recentTopicIntentCounts['tiny_answer'])",
        "recent_topic_intent_tease_back=$($recentTopicIntentCounts['tease_back'])",
        "recent_topic_intent_agree_or_pushback=$($recentTopicIntentCounts['agree_or_pushback'])",
        "recent_topic_intent_bias=$topicIntentBias",
        "recent_topic_family_bias=$topicFamilyBias",
        "recent_topic_reply_affordance_top=$topicReplyAffordanceTop",
        "recent_topic_reply_affordance_bias=$topicReplyAffordanceBias",
        "recent_host_beat_family_bias=$hostBeatFamilyBias",
        "recent_host_beat_reply_affordance_top=$hostBeatReplyAffordanceTop",
        "recent_host_beat_reply_affordance_bias=$hostBeatReplyAffordanceBias",
        "recent_spent_output_family_bias=$spentOutputFamilyBias",
        "avatar_roast_share=$avatarRoastShare",
        "avatar_roast_bias=$avatarRoastBias",
        "latest_topic_source=$latestTopicSource",
        "latest_topic_shape=$latestTopicShape",
        "latest_topic_title=$latestTopicTitleOutput",
        "latest_topic_key=$latestTopicKeyOutput",
        "latest_topic_hook=$latestTopicHookOutput",
        "latest_topic_pattern=$latestTopicPattern",
        "latest_topic_intent=$latestTopicIntent",
        "latest_topic_fun_axis=$latestTopicFunAxis",
        "latest_topic_family=$latestTopicFamily",
        "latest_topic_pack=$latestTopicPack",
        "latest_topic_reply_affordance=$latestTopicReplyAffordance",
        "latest_topic_recent_skip_reason=$latestTopicRecentSkipReason",
        "latest_topic_shape_guard_reason=$latestTopicShapeGuardReason",
        "latest_topic_repeat=$latestTopicRepeat",
        "avatar_repeat_uid=$avatarRepeatUid",
        "avatar_repeat_count=$avatarRepeatCount",
        "latest_host_beat_key=$latestHostBeatKey",
        "latest_host_beat_shape=$latestHostBeatShape",
        "latest_host_beat_fun_axis=$latestHostBeatFunAxis",
        "latest_host_beat_family=$latestHostBeatFamily",
        "latest_host_beat_title=$latestHostBeatTitle",
        "latest_host_beat_hint=$latestHostBeatHint",
        "latest_host_beat_idle_stage=$latestHostBeatIdleStage",
        "latest_host_beat_reply_affordance=$latestHostBeatReplyAffordance",
        "latest_host_beat_repeat=$latestHostBeatRepeat",
        "latest_spent_output_family=$latestSpentOutputFamily",
        "pipeline_latency=$(Format-Latency $pipelineLatency)",
        "dispatcher_latency=$(Format-LatencyMs $dispatcherLatency)",
        "spoken_latency_estimate=$(Format-LatencyMs $spokenLatencyEstimate)",
        "latency=$(Format-Latency $latency)",
        "latency_status=$latencyStatus",
        "log_watchdog=$($logSignals.Watchdog)",
        "log_contamination=$($logSignals.Contamination)",
        "log_reply_len=$($logSignals.ReplyLen)",
        "log_reply_length_status=$($logSignals.ReplyLengthStatus)",
        "log_generic_host_prompt=$($logSignals.GenericHostPrompt)",
        "log_reply_repeat=$($logSignals.ReplyRepeat)",
        "log_reply_suppressed=$($logSignals.ReplySuppressed)",
        "log_reply_shape_reason=$($logSignals.ReplyShapeReason)",
        "log_reply_quality_fallback_count=$($logSignals.ReplyQualityFallbackCount)",
        "log_reply_dangling_choice_count=$($logSignals.ReplyDanglingChoiceCount)",
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
