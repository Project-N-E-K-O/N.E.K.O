/* Pure panel formatting helpers. Keep this file free of React state and host actions. */

export function statusTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "running") return "success"
  if (status === "paused" || status === "degraded" || status === "disconnected") return "warning"
  if (status === "tripped") return "danger"
  return "default"
}

export function liveStatusTone(summary: string): "success" | "warning" | "danger" | "default" {
  if (summary === "ready_to_stream") return "success"
  if (summary === "test_only" || summary === "temporarily_not_speaking") return "warning"
  if (summary === "cannot_stream") return "danger"
  return "default"
}

export function normalizeRoomLiveStatus(value: any): "live" | "offline" | "rounding" | "unknown" {
  const status = String(value || "").trim().toLowerCase()
  if (status === "live" || status === "offline" || status === "rounding") return status
  return "unknown"
}

export function roomLiveStatusTone(status: string): "success" | "warning" | "info" | "default" {
  if (status === "live") return "success"
  if (status === "rounding") return "info"
  if (status === "offline" || status === "unknown") return "warning"
  return "default"
}

export function liveStateTone(state: string): "success" | "warning" | "danger" | "default" {
  if (state === "engaged" || state === "warmup") return "success"
  if (state === "quiet" || state === "idle" || state === "paused") return "warning"
  if (state === "blocked") return "danger"
  return "default"
}

export function recentResultTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "pushed") return "success"
  if (status === "failed") return "danger"
  if (status === "skipped") return "warning"
  return "default"
}

export function speechExplanationTone(summary: string): "success" | "warning" | "danger" | "default" {
  if (summary === "ready" || summary === "recently_spoke") return "success"
  if (summary === "cannot_stream" || summary === "failed") return "danger"
  if (summary === "test_only" || summary === "temporarily_not_speaking" || summary === "waiting_for_activity" || summary === "recently_skipped") return "warning"
  return "default"
}

export function soloReadinessTone(ready: boolean, summary: string): "success" | "warning" | "danger" | "default" {
  if (ready) return "success"
  if (summary === "not_solo_stream") return "default"
  return "warning"
}

export function soloReadinessItemTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "observed") return "success"
  if (status === "ready") return "success"
  if (status === "warning") return "warning"
  if (status === "blocked") return "warning"
  return "default"
}

export function panelText(t: (key: string) => string, key: string, fallback: string): string {
  const value = t(key)
  if (!value || value === key || value.startsWith("panel.") || value.startsWith("entries.")) return fallback
  return value
}

export function localizedStatusCode(t: (key: string) => string, status: string): string {
  const code = String(status || "").trim().toLowerCase()
  if (!code) return "-"
  return panelText(t, `panel.statusCode.${code}`, code.replace(/_/g, " "))
}

export function labelFallback(group: string, value: string): string {
  const labels: Record<string, Record<string, string>> = {
    liveStatusSummary: {
      ready_to_stream: "可以开播",
      test_only: "当前只能测试",
      temporarily_not_speaking: "暂时不会说话",
      cannot_stream: "不能开播",
    },
    liveStatusReason: {
      ready: "开播检查已就绪。",
      dry_run: "测试模式已开启，不会真实输出。",
      manual_paused: "猫猫已暂停。",
      room_not_configured: "还没有配置直播间。",
      live_disabled: "NEKO Live 尚未启用。",
      live_ingest_disconnected: "直播接收还没有连接。",
      cooldown: "猫猫正在等待冷却结束。",
      safety_tripped: "安全门已停止输出。",
      safety_degraded: "安全门处于降级状态。",
      output_channel_unavailable: "输出通道当前不可用。",
      all_ready: "所有检查都已就绪。",
    },
    liveModeRole: {
      co_stream: "人猫同播",
      solo_stream: "猫猫独播",
    },
    liveModeRoleHint: {
      companion: "人猫同播：NEKO 是搭档，低打断补位。",
      solo_host: "猫猫独播：NEKO 正在独自接待观众。",
    },
    liveState: {
      engaged: "互动中",
      warmup: "开场中",
      quiet: "安静中",
      idle: "冷场中",
      paused: "已暂停",
      blocked: "被阻断",
    },
    liveStateReason: {
      recent_activity: "最近有互动，优先接话。",
      solo_stream_warmup: "猫猫独播刚开始，适合开场接待。",
      quiet_activity_gap: "直播间已经安静了一小会。",
      low_activity: "互动较少。",
      no_recent_activity: "最近没有新的互动。",
      manual_paused: "猫猫已暂停。",
      blocked_by_live_status: "当前开播状态还不允许输出。",
    },
    idleHostingCandidate: {
      true: "适合冷场陪播",
      false: "还没到冷场陪播时机",
    },
    idleHostingEligible: {
      true: "可以补位",
      false: "暂不能补位",
    },
    idleHostingReason: {
      eligible: "猫猫独播处于冷场状态，可以准备补位。",
      not_candidate: "还不是候选时机。",
      minimum_interval: "正在等待最小间隔。",
      auto_disabled: "多次失败后已自动停用。",
      solo_idle_ready: "猫猫独播已进入冷场候选，可以准备补位。",
    },
    speechSummary: {
      ready: "NEKO 现在可以说话",
      test_only: "当前只能测试",
      temporarily_not_speaking: "NEKO 暂时不会说话",
      cannot_stream: "NEKO 还不能开播",
      waiting_for_activity: "正在等合适的开口时机",
      recently_spoke: "NEKO 刚刚说过话",
      recently_skipped: "最近事件没有输出",
      failed: "最近输出失败",
      waiting: "正在等待合适时机",
    },
    speechReason: {
      ready: "开播检查已就绪。",
      dry_run: "测试模式已开启，不会真实输出。",
      manual_paused: "NEKO 被手动暂停了。",
      room_not_configured: "还没有配置直播间。",
      live_ingest_disconnected: "直播接收还没有连接。",
      cooldown: "NEKO 正在等待冷却结束。",
      safety_tripped: "安全门已停止输出。",
      safety_degraded: "安全门处于降级状态。",
      output_channel_unavailable: "输出通道当前不可用。",
      solo_stream_warmup: "猫猫独播刚开始，可以先说一句开场话。",
      idle_hosting_candidate: "猫猫独播已空闲，可以进入冷场陪播。",
      quiet_activity_gap: "直播间已经安静了一小会。",
      no_recent_activity: "最近没有新的互动。",
      waiting_for_viewer_or_idle_slot: "正在等待观众接话或冷场补位时机。",
      recent_output: "NEKO 刚刚已经输出过。",
      recently_skipped: "最近事件被策略跳过。",
      failed: "最近输出链路失败。",
      "dispatcher.dry_run": "Dispatcher 以 dry_run 完成。",
    },
    liveDirectorAction: {
      none: "暂无",
      warmup_hosting: "开场接待",
      active_engagement: "主动营业",
      idle_hosting: "冷场陪播",
    },
    liveDirectorReason: {
      waiting_for_viewer: "正在等待观众互动。",
      companion_mode: "人猫同播不自动抢话。",
      paused: "猫猫已暂停。",
      blocked: "直播输出被阻断。",
      recent_activity: "最近互动足够，猫猫应该接话而不是强行抛话题。",
      solo_quiet: "猫猫独播较安静，可以轻主动营业。",
      solo_warmup: "猫猫独播刚开始，可以先开场接待。",
      solo_idle: "猫猫独播已冷场，可以冷场陪播。",
      solo_idle_ready: "猫猫独播已冷场，可以冷场陪播。",
      minimum_interval: "正在等待最小间隔。",
      recent_danmaku_output: "猫猫刚接过弹幕，主动营业先等一下。",
      not_candidate: "还不是候选时机。",
      auto_disabled: "多次失败后已自动停用。",
      active_engagement_not_ready: "主动营业暂未就绪。",
      warmup_hosting_not_ready: "开场接待暂时还没准备好。",
      idle_hosting_not_ready: "冷场陪播暂未就绪。",
    },
    activeEngagementCandidate: {
      true: "适合轻主动营业",
      false: "现在不适合主动营业",
    },
    activeEngagementReason: {
      eligible: "猫猫独播处于安静状态，可以抛一个小话题。",
      deferred: "主动营业暂缓，先验证接弹幕和冷场陪播。",
      not_solo_stream: "主动营业 v0 只服务猫猫独播。",
      paused: "猫猫已暂停。",
      blocked: "直播输出被阻断。",
      not_quiet: "主动营业等待安静状态，不在热聊或完全冷场时触发。",
      cooldown: "输出冷却还在生效。",
      minimum_interval: "主动营业正在等待最小间隔。",
      live_status_not_ready: "当前直播状态还不能输出。",
    },
    warmupHostingCandidate: {
      true: "适合开场",
      false: "开场已过",
    },
    soloReadinessSummary: {
      ready_for_test: "可以开始测试独播",
      ready_for_live_test: "可以开始真实独播测试",
      ready: "独播检查已就绪",
      not_solo_stream: "请先切到猫猫独播",
      live_not_ready: "直播间还没准备好",
    },
    soloReadinessStatus: {
      ready: "可用",
      blocked: "等待",
      observed: "已触发",
    },
    soloReadinessItem: {
      preflight: "开播检查",
      warmup_hosting: "开场接待",
      avatar_roast: "首次出场锐评",
      danmaku_response: "后续弹幕接话",
      active_engagement: "轻主动营业",
      idle_hosting: "冷场陪播",
      pacing_control: "节奏控制",
    },
    safety: {
      running: "运行中",
      paused: "已暂停",
      tripped: "已急停",
      degraded: "降级中",
      unknown: "未知",
    },
  }
  return labels[group]?.[value] || value.replace(/_/g, " ")
}

export function formatLatencyMs(value: any): string {
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms < 0) return "-"
  if (ms < 10000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 1000)}s`
}

export function formatAgeSec(value: any): string {
  if (value === null || value === undefined) return "-"
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return "-"
  return `${seconds.toFixed(1)}s`
}

export function interactionRoute(result: any): string {
  const responseModule = String((result && result.response_module) || "")
  if (responseModule) return responseModule
  const source = String((result && result.event && result.event.source) || "")
  if (source === "warmup_hosting") return "warmup_hosting"
  if (source === "idle_hosting") return "idle_hosting"
  if (source === "active_engagement") return "active_engagement"
  const steps = Array.isArray(result && result.steps) ? result.steps : []
  const routeStep = [...steps].reverse().find((step: any) => {
    const id = String((step && step.id) || "")
    return id === "danmaku_response" || id === "avatar_roast" || id === "live_support_events" || id === "warmup_hosting" || id === "idle_hosting" || id === "active_engagement"
  })
  if (routeStep && routeStep.id) return String(routeStep.id)
  return source || "-"
}

export function interactionRouteTone(route: string): "success" | "warning" | "danger" | "default" {
  if (route === "avatar_roast" || route === "danmaku_response" || route === "live_support_events") return "success"
  if (route === "warmup_hosting" || route === "idle_hosting") return "warning"
  if (route === "active_engagement") return "default"
  return "default"
}

export function interactionRouteLabel(route: string, t: (key: string) => string): string {
  if (route === "avatar_roast") return panelText(t, "panel.interaction.module.avatarRoast.title", "首次出场锐评")
  if (route === "danmaku_response") return panelText(t, "panel.interaction.module.danmakuResponse.title", "后续弹幕接话")
  if (route === "live_support_events") return panelText(t, "panel.interaction.module.liveSupportEvents.title", "礼物/SC/上舰致谢")
  if (route === "warmup_hosting") return panelText(t, "panel.interaction.module.warmupHosting.title", "开场接待")
  if (route === "idle_hosting") return panelText(t, "panel.interaction.module.idleHosting.title", "冷场陪播")
  if (route === "active_engagement") return panelText(t, "panel.interaction.module.activeEngagement.title", "主动营业")
  return route
}

export function activeTopicIntentLabel(value: any, t: (key: string) => string): string {
  const intent = String(value || "").trim()
  if (!intent) return ""
  if (intent === "quick_vote") return panelText(t, "panel.activeEngagementIntent.quickVote", "Quick vote")
  if (intent === "agree_or_pushback") return panelText(t, "panel.activeEngagementIntent.agreeOrPushback", "Agree or push back")
  if (intent === "tease_back") return panelText(t, "panel.activeEngagementIntent.teaseBack", "Tease back")
  if (intent === "tiny_answer") return panelText(t, "panel.activeEngagementIntent.tinyAnswer", "Tiny answer")
  if (intent === "quick_reply") return panelText(t, "panel.activeEngagementIntent.quickReply", "Quick reply")
  return intent
}

export function activeTopicSourceLabel(value: any, t: (key: string) => string): string {
  const source = String(value || "").trim()
  if (!source) return ""
  if (source === "fallback") return panelText(t, "panel.activeEngagementSource.fallback", "Built-in topic")
  if (source === "bili_trending") return panelText(t, "panel.activeEngagementSource.biliTrending", "Bili trending")
  if (source === "recent_danmaku") return panelText(t, "panel.activeEngagementSource.recentDanmaku", "Recent danmaku")
  return source.replace(/_/g, " ")
}

export function activeTopicShapeLabel(value: any, t: (key: string) => string): string {
  const shape = String(value || "").trim()
  if (!shape) return ""
  if (shape === "either_or") return panelText(t, "panel.activeEngagementShape.eitherOr", "A/B choice")
  if (shape === "light_stance") return panelText(t, "panel.activeEngagementShape.lightStance", "Light stance")
  if (shape === "tiny_tease") return panelText(t, "panel.activeEngagementShape.tinyTease", "Tiny tease")
  if (shape === "small_challenge") return panelText(t, "panel.activeEngagementShape.smallChallenge", "Small challenge")
  return shape
}

export function activeTopicReplyAffordanceLabel(value: any, t: (key: string) => string): string {
  const affordance = String(value || "").trim().toLowerCase()
  if (!affordance) return ""
  if (affordance === "viewer can answer with one side") return panelText(t, "panel.activeEngagementReplyAffordance.oneSide", "Viewer picks one side")
  if (affordance === "viewer can agree or push back") return panelText(t, "panel.activeEngagementReplyAffordance.agreeOrPushback", "Viewer agrees or pushes back")
  if (affordance === "viewer can tease neko back") return panelText(t, "panel.activeEngagementReplyAffordance.teaseBack", "Viewer teases NEKO back")
  if (affordance === "viewer can answer in a few words") return panelText(t, "panel.activeEngagementReplyAffordance.fewWords", "Viewer answers in a few words")
  if (affordance === "viewer can reply quickly") return panelText(t, "panel.activeEngagementReplyAffordance.quickReply", "Viewer replies quickly")
  return String(value || "")
}

export function idleHostBeatShapeLabel(value: any, t: (key: string) => string): string {
  const shape = String(value || "").trim()
  if (!shape) return ""
  if (shape === "soft_observation") return panelText(t, "panel.idleHostingBeatShape.softObservation", "Soft observation")
  if (shape === "tiny_choice") return panelText(t, "panel.idleHostingBeatShape.tinyChoice", "Tiny choice")
  if (shape === "light_tease") return panelText(t, "panel.idleHostingBeatShape.lightTease", "Light tease")
  if (shape === "small_mood") return panelText(t, "panel.idleHostingBeatShape.smallMood", "Small mood")
  return shape.replace(/_/g, " ")
}

export function eventSignalTone(signal: string): "success" | "warning" | "danger" | "default" {
  if (signal === "gift_signal") return "warning"
  if (signal === "super_chat_signal") return "success"
  if (signal === "danmaku_signal") return "default"
  return "default"
}

export function eventSignalLabel(signal: string, t: (key: string) => string): string {
  if (signal === "gift_signal") return t("panel.eventSignal.gift_signal")
  if (signal === "super_chat_signal") return t("panel.eventSignal.super_chat_signal")
  if (signal === "danmaku_signal") return t("panel.eventSignal.danmaku_signal")
  return t("panel.eventSignal.unknown")
}

export function latestEventLabel(result: any): string {
  const event = (result && result.event) || {}
  const identity = (result && result.identity) || {}
  const who = String(identity.nickname || event.nickname || event.uid || "-")
  const text = String(event.danmaku_text || "").trim()
  if (text) return `${who}: ${text}`
  return who
}
