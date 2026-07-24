// Main-branch compatibility entry. Generated from the modular panel sources.
// Keep ui/panel.tsx and sibling modules as the authored source of truth.

import {
  Alert,
  Button,
  Card,
  CodeBlock,
  ConfirmDialog,
  DataTable,
  Field,
  Grid,
  Input,
  JsonView,
  KeyValue,
  Modal,
  Page,
  Select,
  Stack,
  StatCard,
  StatusBadge,
  Tabs,
  Text,
  Textarea,
  Tooltip,
  Toolbar,
  ToolbarGroup,
  useCallback,
  useEffect,
  useForm,
  useState,
  useToast,
} from "@neko/plugin-ui"

type CompatPluginSurfaceProps<TState = any> = {
  state?: TState
  locale?: string
  t: (key: string, params?: Record<string, any>) => string
  api: {
    call: (action: string, payload?: any) => Promise<any>
    refresh: () => Promise<any>
  }
  [key: string]: any
}

type PanelTranslator = (key: string, params?: Record<string, any>) => string
type DynamicLabel = (group: string, keyPrefix: string, value: string) => string

/* bundled source: ui/panel_state.ts */
type LiveConfig = {
  live_platform?: string
  live_room_ref?: string
  live_room_id?: number
  live_enabled?: boolean
  avatar_roast_enabled?: boolean
  avatar_analysis_enabled?: boolean
  danmaku_response_enabled?: boolean
  live_support_events_enabled?: boolean
  warmup_hosting_enabled?: boolean
  idle_hosting_enabled?: boolean
  active_engagement_enabled?: boolean
  developer_tools_enabled?: boolean
  live_mode?: string
  activity_level?: string
  roast_strength?: string
  roast_once_per_uid?: boolean
  viewer_memory_enabled?: boolean
  rate_limit_seconds?: number
  queue_limit?: number
  safety_auto_stop_enabled?: boolean
  dry_run?: boolean
  viewer_store_dir?: string
  stream_theme?: string
  stream_goal?: string
  stream_columns?: string
  stream_avoid_topics?: string
}

type DashboardState = {
  config?: LiveConfig
  live_connection?: Record<string, any>
  store_enabled?: boolean
  viewer_store?: Record<string, any>
  safety?: Record<string, any>
  live_status?: Record<string, any>
  live_state?: Record<string, any>
  live_director_status?: Record<string, any>
  solo_test_readiness?: Record<string, any>
  modules?: Array<Record<string, any>>
  recent_profiles?: Array<Record<string, any>>
  recent_results?: Array<Record<string, any>>
  live_session?: Record<string, any>
  recent_sandbox_results?: Array<Record<string, any>>
  recent_audit?: Array<Record<string, any>>
  speech_explanation?: Record<string, any>
  live_explain?: Record<string, any>
  idle_hosting_status?: Record<string, any>
  active_engagement_status?: Record<string, any>
  health_rows?: Array<Record<string, any>>
}

const configDefaults = {
  live_platform: "bilibili",
  live_room_ref: "",
  live_room_id: "0",
  douyin_cookie: "",
  douyin_uid: "",
  douyin_nickname: "",
  live_enabled: false,
  avatar_roast_enabled: true,
  avatar_analysis_enabled: true,
  danmaku_response_enabled: true,
  live_support_events_enabled: true,
  warmup_hosting_enabled: true,
  idle_hosting_enabled: true,
  active_engagement_enabled: true,
  developer_tools_enabled: false,
  live_mode: "co_stream",
  activity_level: "standard",
  roast_strength: "normal",
  roast_once_per_uid: true,
  viewer_memory_enabled: true,
  rate_limit_seconds: "20",
  queue_limit: "5",
  safety_auto_stop_enabled: true,
  dry_run: false,
  viewer_store_dir: "",
  stream_theme: "",
  stream_goal: "",
  stream_columns: "",
  stream_avoid_topics: "",
}

const sandboxDefaults = {
  target: "",
  uid: "",
  nickname: "",
  avatar_url: "",
  danmaku_text: "",
}

const presetViewer = {
  uid: "9000000000000001",
}

/* bundled source: ui/panel_helpers.ts */
/* Pure panel formatting helpers. Keep this file free of React state and host actions. */

function statusTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "running") return "success"
  if (status === "paused" || status === "degraded" || status === "disconnected") return "warning"
  if (status === "tripped") return "danger"
  return "default"
}

function liveStatusTone(summary: string): "success" | "warning" | "danger" | "default" {
  if (summary === "ready_to_stream") return "success"
  if (summary === "test_only" || summary === "temporarily_not_speaking") return "warning"
  if (summary === "cannot_stream") return "danger"
  return "default"
}

function normalizeRoomLiveStatus(value: any): "live" | "offline" | "rounding" | "unknown" {
  const status = String(value || "").trim().toLowerCase()
  if (status === "live" || status === "offline" || status === "rounding") return status
  return "unknown"
}

function roomLiveStatusTone(status: string): "success" | "warning" | "info" | "default" {
  if (status === "live") return "success"
  if (status === "rounding") return "info"
  if (status === "offline" || status === "unknown") return "warning"
  return "default"
}

function liveStateTone(state: string): "success" | "warning" | "danger" | "default" {
  if (state === "engaged" || state === "warmup") return "success"
  if (state === "quiet" || state === "idle" || state === "paused") return "warning"
  if (state === "blocked") return "danger"
  return "default"
}

function recentResultTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "pushed") return "success"
  if (status === "failed") return "danger"
  if (status === "skipped") return "warning"
  return "default"
}

function speechExplanationTone(summary: string): "success" | "warning" | "danger" | "default" {
  if (summary === "ready" || summary === "recently_spoke") return "success"
  if (summary === "cannot_stream" || summary === "failed") return "danger"
  if (summary === "test_only" || summary === "temporarily_not_speaking" || summary === "waiting_for_activity" || summary === "recently_skipped") return "warning"
  return "default"
}

function soloReadinessTone(ready: boolean, summary: string): "success" | "warning" | "danger" | "default" {
  if (ready) return "success"
  if (summary === "not_solo_stream") return "default"
  return "warning"
}

function soloReadinessItemTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "observed") return "success"
  if (status === "ready") return "success"
  if (status === "warning") return "warning"
  if (status === "blocked") return "warning"
  return "default"
}

function panelText(t: (key: string) => string, key: string, fallback: string): string {
  const value = t(key)
  if (!value || value === key || value.startsWith("panel.") || value.startsWith("entries.")) return fallback
  return value
}

function localizedStatusCode(t: (key: string) => string, status: string): string {
  const code = String(status || "").trim().toLowerCase()
  if (!code) return "-"
  return panelText(t, `panel.statusCode.${code}`, code.replace(/_/g, " "))
}

function labelFallback(group: string, value: string): string {
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

function formatLatencyMs(value: any): string {
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms < 0) return "-"
  if (ms < 10000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 1000)}s`
}

function formatAgeSec(value: any): string {
  if (value === null || value === undefined) return "-"
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return "-"
  return `${seconds.toFixed(1)}s`
}

function interactionRoute(result: any): string {
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

function interactionRouteTone(route: string): "success" | "warning" | "danger" | "default" {
  if (route === "avatar_roast" || route === "danmaku_response" || route === "live_support_events") return "success"
  if (route === "warmup_hosting" || route === "idle_hosting") return "warning"
  if (route === "active_engagement") return "default"
  return "default"
}

function interactionRouteLabel(route: string, t: (key: string) => string): string {
  if (route === "avatar_roast") return panelText(t, "panel.interaction.module.avatarRoast.title", "首次出场锐评")
  if (route === "danmaku_response") return panelText(t, "panel.interaction.module.danmakuResponse.title", "后续弹幕接话")
  if (route === "live_support_events") return panelText(t, "panel.interaction.module.liveSupportEvents.title", "礼物/SC/上舰致谢")
  if (route === "warmup_hosting") return panelText(t, "panel.interaction.module.warmupHosting.title", "开场接待")
  if (route === "idle_hosting") return panelText(t, "panel.interaction.module.idleHosting.title", "冷场陪播")
  if (route === "active_engagement") return panelText(t, "panel.interaction.module.activeEngagement.title", "主动营业")
  return route
}

function activeTopicIntentLabel(value: any, t: (key: string) => string): string {
  const intent = String(value || "").trim()
  if (!intent) return ""
  if (intent === "quick_vote") return panelText(t, "panel.activeEngagementIntent.quickVote", "Quick vote")
  if (intent === "agree_or_pushback") return panelText(t, "panel.activeEngagementIntent.agreeOrPushback", "Agree or push back")
  if (intent === "tease_back") return panelText(t, "panel.activeEngagementIntent.teaseBack", "Tease back")
  if (intent === "tiny_answer") return panelText(t, "panel.activeEngagementIntent.tinyAnswer", "Tiny answer")
  if (intent === "quick_reply") return panelText(t, "panel.activeEngagementIntent.quickReply", "Quick reply")
  return intent
}

function activeTopicSourceLabel(value: any, t: (key: string) => string): string {
  const source = String(value || "").trim()
  if (!source) return ""
  if (source === "fallback") return panelText(t, "panel.activeEngagementSource.fallback", "Built-in topic")
  if (source === "bili_trending") return panelText(t, "panel.activeEngagementSource.biliTrending", "Bili trending")
  if (source === "recent_danmaku") return panelText(t, "panel.activeEngagementSource.recentDanmaku", "Recent danmaku")
  return source.replace(/_/g, " ")
}

function activeTopicShapeLabel(value: any, t: (key: string) => string): string {
  const shape = String(value || "").trim()
  if (!shape) return ""
  if (shape === "either_or") return panelText(t, "panel.activeEngagementShape.eitherOr", "A/B choice")
  if (shape === "light_stance") return panelText(t, "panel.activeEngagementShape.lightStance", "Light stance")
  if (shape === "tiny_tease") return panelText(t, "panel.activeEngagementShape.tinyTease", "Tiny tease")
  if (shape === "small_challenge") return panelText(t, "panel.activeEngagementShape.smallChallenge", "Small challenge")
  return shape
}

function activeTopicReplyAffordanceLabel(value: any, t: (key: string) => string): string {
  const affordance = String(value || "").trim().toLowerCase()
  if (!affordance) return ""
  if (affordance === "viewer can answer with one side") return panelText(t, "panel.activeEngagementReplyAffordance.oneSide", "Viewer picks one side")
  if (affordance === "viewer can agree or push back") return panelText(t, "panel.activeEngagementReplyAffordance.agreeOrPushback", "Viewer agrees or pushes back")
  if (affordance === "viewer can tease neko back") return panelText(t, "panel.activeEngagementReplyAffordance.teaseBack", "Viewer teases NEKO back")
  if (affordance === "viewer can answer in a few words") return panelText(t, "panel.activeEngagementReplyAffordance.fewWords", "Viewer answers in a few words")
  if (affordance === "viewer can reply quickly") return panelText(t, "panel.activeEngagementReplyAffordance.quickReply", "Viewer replies quickly")
  return String(value || "")
}

function idleHostBeatShapeLabel(value: any, t: (key: string) => string): string {
  const shape = String(value || "").trim()
  if (!shape) return ""
  if (shape === "soft_observation") return panelText(t, "panel.idleHostingBeatShape.softObservation", "Soft observation")
  if (shape === "tiny_choice") return panelText(t, "panel.idleHostingBeatShape.tinyChoice", "Tiny choice")
  if (shape === "light_tease") return panelText(t, "panel.idleHostingBeatShape.lightTease", "Light tease")
  if (shape === "small_mood") return panelText(t, "panel.idleHostingBeatShape.smallMood", "Small mood")
  return shape.replace(/_/g, " ")
}

function eventSignalTone(signal: string): "success" | "warning" | "danger" | "default" {
  if (signal === "gift_signal") return "warning"
  if (signal === "super_chat_signal") return "success"
  if (signal === "danmaku_signal") return "default"
  return "default"
}

function eventSignalLabel(signal: string, t: (key: string) => string): string {
  if (signal === "gift_signal") return t("panel.eventSignal.gift_signal")
  if (signal === "super_chat_signal") return t("panel.eventSignal.super_chat_signal")
  if (signal === "danmaku_signal") return t("panel.eventSignal.danmaku_signal")
  return t("panel.eventSignal.unknown")
}

function latestEventLabel(result: any): string {
  const event = (result && result.event) || {}
  const identity = (result && result.identity) || {}
  const who = String(identity.nickname || event.nickname || event.uid || "-")
  const text = String(event.danmaku_text || "").trim()
  if (text) return `${who}: ${text}`
  return who
}

/* bundled source: ui/panel_components.tsx */
function ModuleHealthBadge({ module, t }: { module: any; t: PanelTranslator }) {
  if (module && module.degraded) return <StatusBadge tone="danger" label={t("panel.modules.degraded")} />
  const on = !!(module && module.enabled)
  const reserved = !!(module && module.status && module.status.reserved)
  return (
    <StatusBadge
      tone={on ? "success" : (reserved ? "default" : "warning")}
      label={on ? t("panel.modules.online") : (reserved ? t("panel.modules.soon") : t("panel.modules.off"))}
    />
  )
}

function ModuleRenderBoundary({
  title,
  render,
  t,
}: {
  title: any
  render: () => any
  t: PanelTranslator
}) {
  try {
    return render()
  } catch (err) {
    const msg = err && (err as any).message ? String((err as any).message) : ""
    return (
      <Card title={title}>
        <Stack gap={8}>
          <StatusBadge tone="danger" label={t("panel.modules.degraded")} />
          <Alert tone="danger">{t("panel.modules.renderError")}</Alert>
          {msg ? <Text>{msg}</Text> : null}
        </Stack>
      </Card>
    )
  }
}

function ToggleSwitch(props: {
  checked: boolean
  label?: any
  disabled?: boolean
  tone?: string
  onChange: (value: boolean) => void
}) {
  const checked = !!props.checked
  const disabled = !!props.disabled
  // Use host theme variables so dark mode follows the shell.
  const onColor = props.tone === "success" ? "var(--success)" : "var(--primary)"
  const onGlow = props.tone === "success" ? "0 0 0 2px rgba(103, 194, 58, 0.18)" : "0 0 0 2px rgba(64, 158, 255, 0.18)"
  const trackColor = disabled ? "var(--border)" : checked ? onColor : "var(--muted)"
  const labelColor = disabled ? "var(--muted)" : "var(--text)"

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked ? "true" : "false"}
      disabled={disabled}
      onClick={() => {
        if (!disabled) {
          props.onChange(!checked)
        }
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "8px",
        minHeight: "32px",
        padding: "0",
        border: "0",
        background: "transparent",
        color: labelColor,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.68 : 1,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: "relative",
          width: "42px",
          height: "24px",
          borderRadius: "999px",
          background: trackColor,
          transition: "background 160ms ease",
          boxShadow: checked ? onGlow : "inset 0 0 0 1px rgba(148, 163, 184, 0.45)",
          flex: "0 0 auto",
        }}
      >
        <span
          style={{
            position: "absolute",
            top: "2px",
            left: "2px",
            width: "20px",
            height: "20px",
            borderRadius: "50%",
            background: "#ffffff",
            transform: checked ? "translateX(18px)" : "translateX(0)",
            transition: "transform 160ms ease",
            boxShadow: "0 1px 3px rgba(17, 24, 39, 0.32)",
          }}
        />
      </span>
      {props.label ? <span>{props.label}</span> : null}
    </button>
  )
}

function AvatarPreview(props: { src?: string; alt: any }) {
  if (!props.src) {
    return (
      <div
        style={{
          width: "72px",
          height: "72px",
          borderRadius: "8px",
          border: "1px solid var(--border)",
          background: "var(--surface)",
        }}
      />
    )
  }

  return (
    <img
      src={props.src}
      alt={props.alt}
      style={{
        width: "72px",
        height: "72px",
        borderRadius: "8px",
        objectFit: "cover",
        border: "1px solid var(--border)",
        background: "var(--surface)",
      }}
    />
  )
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

function AuthCard({
  t,
  loginState,
  loginLoggedIn,
  loginName,
  loginUid,
  disabled = false,
  onLogin,
  onLoginCheck,
  onLogout,
}: {
  t: PanelTranslator
  loginState: any
  loginLoggedIn: boolean
  loginName: string
  loginUid: string
  disabled?: boolean
  onLogin: () => void
  onLoginCheck: () => void
  onLogout: () => void
}) {
  return (
    <Card title={t("panel.auth.title")}>
      <Stack>
        <Text>
          {loginLoggedIn
            ? t("panel.auth.loggedIn") + (loginName ? ": " + loginName : "") + (loginUid ? " (UID " + loginUid + ")" : "")
            : t("panel.auth.loggedOut")}
        </Text>
        {loginLoggedIn ? (
          <Grid cols={2}>
            <Button tone="info" disabled={disabled} onClick={onLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            <Button tone="danger" disabled={disabled} onClick={onLogout}>{t("panel.actions.biliLogout")}</Button>
          </Grid>
        ) : (
          <Stack>
            <Grid cols={2}>
              <Button tone="info" disabled={disabled} onClick={onLogin}>{t("panel.actions.biliLogin")}</Button>
              <Button tone="success" disabled={disabled} onClick={onLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            </Grid>
            {loginState?.qrcode_image ? (
              <Stack>
                {/* hosted-ui strips data: URLs from img src, so the QR code uses a CSS background image. */}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={onLogin}
                  aria-label={t("panel.auth.refreshHint")}
                  title={t("panel.auth.refreshHint")}
                  style={{
                    width: "180px",
                    height: "180px",
                    boxSizing: "border-box",
                    padding: "8px",
                    borderRadius: "8px",
                    border: "none",
                    cursor: disabled ? "not-allowed" : "pointer",
                    backgroundColor: "#ffffff",
                    backgroundImage: `url("${loginState.qrcode_image}")`,
                    backgroundRepeat: "no-repeat",
                    backgroundPosition: "center",
                    backgroundSize: "contain",
                    backgroundOrigin: "content-box",
                  }}
                />
                <Text>{t("panel.auth.scanHint")}</Text>
                <Text>{t("panel.auth.refreshHint")}</Text>
              </Stack>
            ) : null}
            {loginState?.message ? <Text>{loginState.message}</Text> : null}
          </Stack>
        )}
      </Stack>
    </Card>
  )
}

function StatusBadgeRow({
  items,
  t,
}: {
  items: Array<{ key: string; tone?: "success" | "warning" | "danger" | "default" }>
  t: PanelTranslator
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
      {items.map((item) => (
        <span key={item.key}>
          <StatusBadge tone={item.tone || "default"} label={t(item.key)} />
        </span>
      ))}
    </div>
  )
}

const moduleTitleKeys: Record<string, string> = {
  bili_live_ingest: "panel.modules.biliLiveInput",
  douyin_live_ingest: "panel.modules.douyinLiveInput",
  bili_identity: "panel.modules.biliIdentity",
  douyin_identity: "panel.modules.douyinIdentity",
  live_audience_session: "panel.modules.liveAudienceSession",
  viewer_profile: "panel.modules.viewerProfile",
  avatar_roast: "panel.interaction.module.avatarRoast.title",
  danmaku_response: "panel.interaction.module.danmakuResponse.title",
  live_support_events: "panel.interaction.module.liveSupportEvents.title",
  active_engagement: "panel.interaction.module.activeEngagement.title",
  warmup_hosting: "panel.interaction.module.warmupHosting.title",
  developer_sandbox: "panel.modules.developerSandbox",
  live_events: "panel.modules.liveEvents",
}

function localizedModuleTitle(module: any, t: PanelTranslator): string {
  const key = moduleTitleKeys[String(module?.id || "")]
  return key ? t(key) : String(module?.title || module?.id || "-")
}

function ModuleOverviewCard({ modules, t }: { modules: Array<Record<string, any>>; t: PanelTranslator }) {
  return (
    <Card title={t("panel.tabs.modules")}>
      {modules.length ? (
        <DataTable
          data={modules.map((item: any, index: number) => ({ ...item, id: item.id || String(index) }))}
          rowKey="id"
          columns={[
            { key: "title", label: t("panel.modules.name"), render: (row: any) => localizedModuleTitle(row, t) },
            { key: "status", label: t("panel.modules.status"), render: (row: any) => <ModuleHealthBadge module={row} t={t} /> },
            { key: "id", label: "ID", render: (row: any) => row.id || "-" },
          ]}
        />
      ) : (
        <Text>{t("panel.modules.empty")}</Text>
      )}
    </Card>
  )
}

function ComingSoonSection({ title, desc, t }: { title: any; desc: any; t: PanelTranslator }) {
  return (
    <Stack>
      <div style={{ opacity: 0.7 }}>
        <Card>
          <Stack gap={10}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
              <span style={{ color: "var(--text)", fontSize: "15px", fontWeight: 720 }}>{title}</span>
              <StatusBadge tone="info" label={t("panel.modules.soon")} />
            </div>
            <Text>{desc}</Text>
          </Stack>
        </Card>
      </div>
    </Stack>
  )
}

/* bundled source: ui/panel_data_sections.tsx */
function LiveSessionSection({
  t,
  session,
  locale,
}: {
  t: PanelTranslator
  session: any
  locale?: string
}) {
  const [query, setQuery] = useState("")
  const [selectedViewer, setSelectedViewer] = useState<any>(null)
  const viewers = Array.isArray(session?.viewers) ? session.viewers : []
  const normalizedQuery = query.trim().toLowerCase()
  const filteredViewers = viewers.filter((viewer: any) => {
    if (!normalizedQuery) return true
    return String(viewer?.nickname || "").toLowerCase().includes(normalizedQuery)
  })
  const viewerCount = Number(session?.interaction_viewer_count || 0)
  const viewerCountLabel = session?.interaction_viewer_count_capped ? `${viewerCount}+` : viewerCount

  return (
    <Stack>
      {!session?.has_session ? <Alert tone="info">{t("panel.audience.noSession")}</Alert> : null}
      <Grid cols={2}>
        <StatCard label={t("panel.audience.interactionViewers")} value={viewerCountLabel} />
        <StatCard label={t("panel.columns.danmakuCount")} value={Number(session?.danmaku_count || 0)} />
        <StatCard label={t("panel.audience.supportEvents")} value={Number(session?.support_event_count || 0)} />
        <StatCard label={t("panel.audience.nekoOutputs")} value={Number(session?.neko_output_count || 0)} />
      </Grid>
      {session?.interaction_viewer_count_capped ? <Text>{t("panel.audience.viewerCountCapped")}</Text> : null}
      <Card title={t("panel.audience.recentViewers")}>
        <Stack>
          <Field label={t("panel.audience.searchLabel")}>
            <Input value={query} placeholder={t("panel.audience.sessionSearchPlaceholder")} onChange={setQuery} />
          </Field>
          {filteredViewers.length ? (
            <div style={{ overflowX: "auto" }}>
              <DataTable
                data={filteredViewers}
                rowKey="viewer_key"
                maxRows={30}
                columns={[
                  {
                    key: "nickname",
                    label: t("panel.columns.nickname"),
                    render: (row: any) => (
                      <Button tone="default" onClick={() => setSelectedViewer(row)}>
                        {row.nickname || t("panel.audience.anonymousViewer")}
                      </Button>
                    ),
                  },
                  {
                    key: "interactions",
                    label: t("panel.audience.interactions"),
                    render: (row: any) => `${Number(row.danmaku_count || 0)} / ${Number(row.support_event_count || 0)}`,
                  },
                  {
                    key: "last_event_type",
                    label: t("panel.audience.latestEvent"),
                    render: (row: any) => <StatusBadge tone={sessionEventTone(row.last_event_type)} label={sessionEventLabel(t, row.last_event_type)} />,
                  },
                  {
                    key: "last_interaction_at",
                    label: t("panel.columns.lastSeen"),
                    render: (row: any) => formatDateTime(row.last_interaction_at, locale),
                  },
                ]}
              />
            </div>
          ) : (
            <Text>{session?.has_session ? t("panel.audience.noSessionViewers") : t("panel.audience.noSession")}</Text>
          )}
        </Stack>
      </Card>
      <Modal
        open={!!selectedViewer}
        title={t("panel.audience.sessionDetailTitle")}
        size="lg"
        onClose={() => setSelectedViewer(null)}
        footer={<Button tone="default" onClick={() => setSelectedViewer(null)}>{t("panel.actions.cancel")}</Button>}
      >
        {selectedViewer ? (
          <Stack>
            <Text>{selectedViewer.nickname || t("panel.audience.anonymousViewer")}</Text>
            <KeyValue
              items={[
                { key: "danmaku", label: t("panel.columns.danmakuCount"), value: Number(selectedViewer.danmaku_count || 0) },
                { key: "support", label: t("panel.audience.supportEvents"), value: Number(selectedViewer.support_event_count || 0) },
                { key: "replies", label: t("panel.audience.nekoReplyCount"), value: Number(selectedViewer.neko_reply_count || 0) },
                { key: "lastEvent", label: t("panel.audience.latestEvent"), value: sessionEventLabel(t, selectedViewer.last_event_type) },
                { key: "lastSeen", label: t("panel.columns.lastSeen"), value: formatDateTime(selectedViewer.last_interaction_at, locale) },
              ]}
            />
          </Stack>
        ) : null}
      </Modal>
    </Stack>
  )
}

function LiveExplainSection({
  t,
  dynamicLabel,
  liveExplain,
  speechSummary,
  speechReason,
}: {
  t: PanelTranslator
  dynamicLabel: DynamicLabel
  liveExplain: any
  speechSummary: string
  speechReason: string
}) {
  const explainSummary = String(liveExplain.summary || speechSummary || "waiting")
  const explainReason = String(liveExplain.reason || speechReason || "")
  const explainTraceId = String(liveExplain.trace_id || "")
  const explainTimeline = Array.isArray(liveExplain.timeline) ? liveExplain.timeline : []
  const explainChain = Array.isArray(liveExplain.chain) ? liveExplain.chain : []
  const explainSelection = liveExplain.selection || {}
  const explainViewerMemory = liveExplain.viewer_memory || {}
  const explainLatest = liveExplain.latest_result || {}
  const explainThemes = Array.isArray(explainSelection.theme_keys) ? explainSelection.theme_keys.join(", ") : "-"
  const explainTopTags = Array.isArray(explainViewerMemory.top_preference_tags)
    ? explainViewerMemory.top_preference_tags.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"
  const explainTopTopics = Array.isArray(explainViewerMemory.top_favorite_topics)
    ? explainViewerMemory.top_favorite_topics.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"
  const explainTopJokes = Array.isArray(explainViewerMemory.top_running_jokes)
    ? explainViewerMemory.top_running_jokes.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"

  return (
    <Card title={t("panel.explain.title")}>
      <Stack>
        <Grid cols={4}>
          <StatCard
            label={t("panel.explain.summary")}
            value={<StatusBadge tone={speechExplanationTone(explainSummary)} label={dynamicLabel("speechSummary", "panel.speechExplanation.summary", explainSummary)} />}
          />
          <StatCard label={t("panel.columns.reason")} value={dynamicLabel("speechReason", "panel.speechExplanation.reason", explainReason)} />
          <StatCard label={t("panel.explain.topicThemes")} value={explainThemes || "-"} />
          <StatCard label={t("panel.explain.trace")} value={explainTraceId || "-"} />
        </Grid>
        <Grid cols={3}>
          <StatCard label={t("panel.explain.viewerMemory")} value={`${Number(explainViewerMemory.profiles_with_impressions || explainViewerMemory.profiles_with_preferences || 0)}/${Number(explainViewerMemory.profile_count || 0)}`} />
          <StatCard label={t("panel.columns.preferenceTags")} value={explainTopTags || "-"} />
          <StatCard label={t("panel.explain.latestResult")} value={`${String(explainLatest.status || "-")} / ${formatLatencyMs(explainLatest.latency_ms)}`} />
        </Grid>
        <Grid cols={2}>
          <StatCard label={t("panel.columns.favoriteTopics")} value={explainTopTopics || "-"} />
          <StatCard label={t("panel.columns.runningJokes")} value={explainTopJokes || "-"} />
        </Grid>
        {explainChain.length ? (
          <DataTable
            data={explainChain.map((item: any, index: number) => ({ ...item, row_id: item.id || String(index) }))}
            rowKey="row_id"
            columns={[
              { key: "stage", label: t("panel.explain.stage"), render: (row: any) => row.stage || row.id || "-" },
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "blocked" ? "warning" : row.status === "healthy" ? "success" : "default"} label={localizedStatusCode(t, String(row.status || ""))} /> },
              { key: "last_outcome", label: t("panel.columns.message"), render: (row: any) => row.last_outcome || "-" },
              { key: "last_skip_reason", label: t("panel.columns.detail"), render: (row: any) => row.last_skip_reason || "-" },
            ]}
          />
        ) : null}
        {explainTimeline.length ? (
          <DataTable
            data={explainTimeline.map((item: any, index: number) => ({ ...item, row_id: `${item.trace_id || "trace"}-${index}` }))}
            rowKey="row_id"
            columns={[
              { key: "stage", label: t("panel.explain.stage"), render: (row: any) => row.stage || "-" },
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "skipped" ? "warning" : row.status === "ok" ? "success" : "default"} label={localizedStatusCode(t, String(row.status || ""))} /> },
              { key: "route", label: t("panel.columns.responseModule"), render: (row: any) => row.route || "-" },
              { key: "reason", label: t("panel.columns.detail"), render: (row: any) => row.reason || "-" },
            ]}
          />
        ) : null}
      </Stack>
    </Card>
  )
}

function RecentResultsTable({ t, results }: { t: PanelTranslator; results: any[] }) {
  return (
    <Card title={t("panel.recent.title")}>
      {results.length ? (
        <DataTable
          data={results.map((item, index) => ({ ...item, id: `${item.created_at || index}-${index}` }))}
          rowKey="id"
          columns={[
            { key: "uid", label: "UID", render: (row: any) => row.identity?.uid || row.event?.uid || "-" },
            { key: "nickname", label: t("panel.columns.nickname"), render: (row: any) => row.identity?.nickname || row.event?.nickname || "-" },
            { key: "response_module", label: t("panel.columns.responseModule"), render: (row: any) => {
              const route = interactionRoute(row)
              return <StatusBadge tone={interactionRouteTone(route)} label={interactionRouteLabel(route, t)} />
            } },
            { key: "event_signal", label: t("panel.columns.eventSignal"), render: (row: any) => {
              const signal = String(row.event_signal || "unknown")
              return <StatusBadge tone={eventSignalTone(signal)} label={eventSignalLabel(signal, t)} />
            } },
            { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={recentResultTone(String(row.status || ""))} label={localizedStatusCode(t, String(row.status || ""))} /> },
            { key: "response_latency_ms", label: t("panel.columns.responseLatency"), render: (row: any) => formatLatencyMs(row.response_latency_ms) },
            { key: "reason", label: t("panel.columns.reason"), render: (row: any) => row.reason || row.output || "-" },
          ]}
        />
      ) : (
        <Text>{t("panel.empty.results")}</Text>
      )}
    </Card>
  )
}

function ViewerProfilesTable({
  t,
  profiles,
  locale,
  onResetImpression,
  onDeleteProfile,
}: {
  t: PanelTranslator
  profiles: any[]
  locale?: string
  onResetImpression: (uid: string) => Promise<void>
  onDeleteProfile: (uid: string) => Promise<void>
}) {
  const [query, setQuery] = useState("")
  const [selectedProfile, setSelectedProfile] = useState<any>(null)
  const [pendingAction, setPendingAction] = useState<"reset" | "delete" | "">("")
  const [actionPending, setActionPending] = useState(false)
  const normalizedQuery = query.trim().toLowerCase()
  const filteredProfiles = profiles.filter((profile: any) => {
    if (!normalizedQuery) return true
    return String(profile?.nickname || "").toLowerCase().includes(normalizedQuery)
  })

  return (
    <Stack>
      <Card title={t("panel.profiles.title")}>
        <Stack>
          <Field label={t("panel.audience.searchLabel")}>
            <Input value={query} placeholder={t("panel.audience.profileSearchPlaceholder")} onChange={setQuery} />
          </Field>
          {filteredProfiles.length ? (
            <div style={{ overflowX: "auto" }}>
              <DataTable
                data={filteredProfiles.map((item, index) => ({ ...item, id: item.uid || String(index) }))}
                rowKey="id"
                columns={[
                  {
                    key: "nickname",
                    label: t("panel.columns.nickname"),
                    render: (row: any) => (
                      <Button tone="default" onClick={() => setSelectedProfile(row)}>
                        {row.nickname || t("panel.audience.anonymousViewer")}
                      </Button>
                    ),
                  },
                  { key: "viewer_stage", label: t("panel.columns.viewerStage"), render: (row: any) => profileBadge("viewerStage", row.viewer_stage, t) },
                  { key: "profile_confidence", label: t("panel.columns.profileConfidence"), render: (row: any) => profileBadge("profileConfidence", row.profile_confidence, t) },
                  { key: "profile_freshness", label: t("panel.columns.profileFreshness"), render: (row: any) => profileBadge("profileFreshness", row.profile_freshness, t) },
                  { key: "last_seen_at", label: t("panel.columns.lastSeen"), render: (row: any) => formatDateTime(row.last_seen_at, locale) },
                ]}
              />
            </div>
          ) : (
            <Text>{t("panel.empty.profiles")}</Text>
          )}
        </Stack>
      </Card>
      <Modal
        open={!!selectedProfile}
        title={t("panel.audience.profileDetailTitle")}
        size="lg"
        onClose={() => setSelectedProfile(null)}
        footer={
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", flexWrap: "wrap" }}>
            <Button tone="default" disabled={actionPending} onClick={() => setSelectedProfile(null)}>{t("panel.actions.cancel")}</Button>
            <Button tone="info" disabled={actionPending} onClick={() => { setPendingAction("reset") }}>{t("panel.actions.resetViewerImpression")}</Button>
            <Button tone="danger" disabled={actionPending} onClick={() => { setPendingAction("delete") }}>{t("panel.actions.deleteViewerProfile")}</Button>
          </div>
        }
      >
        {selectedProfile ? (
          <Stack>
            <Text>{selectedProfile.nickname || t("panel.audience.anonymousViewer")}</Text>
            <KeyValue
              items={[
                { key: "stage", label: t("panel.columns.viewerStage"), value: profileLabel("viewerStage", selectedProfile.viewer_stage, t) },
                { key: "confidence", label: t("panel.columns.profileConfidence"), value: profileLabel("profileConfidence", selectedProfile.profile_confidence, t) },
                { key: "freshness", label: t("panel.columns.profileFreshness"), value: profileLabel("profileFreshness", selectedProfile.profile_freshness, t) },
                { key: "danmaku", label: t("panel.columns.danmakuCount"), value: Number(selectedProfile.danmaku_count || 0) },
                { key: "roast", label: t("panel.columns.roastCount"), value: Number(selectedProfile.roast_count || 0) },
                { key: "preferences", label: t("panel.columns.preferenceTags"), value: formatCountedTags(selectedProfile.top_preference_tags) },
                { key: "topics", label: t("panel.columns.favoriteTopics"), value: formatCountedTags(selectedProfile.top_favorite_topics) },
                { key: "jokes", label: t("panel.columns.runningJokes"), value: formatCountedTags(selectedProfile.top_running_jokes) },
                { key: "summary", label: t("panel.columns.latestSummary"), value: selectedProfile.impression_summary || selectedProfile.profile_summary || selectedProfile.last_interaction_summary || "-" },
                { key: "avoid", label: t("panel.columns.avoidGuidance"), value: selectedProfile.avoid_guidance || "-" },
                { key: "reply", label: t("panel.columns.replyGuidance"), value: selectedProfile.reply_guidance || "-" },
                { key: "lastSeen", label: t("panel.columns.lastSeen"), value: formatDateTime(selectedProfile.last_seen_at, locale) },
              ]}
            />
          </Stack>
        ) : null}
      </Modal>
      <ConfirmDialog
        open={!!pendingAction && !!selectedProfile}
        title={t(pendingAction === "delete" ? "panel.actions.deleteViewerProfile" : "panel.actions.resetViewerImpression")}
        message={t(pendingAction === "delete" ? "panel.messages.deleteViewerProfileConfirm" : "panel.messages.resetViewerImpressionConfirm")}
        tone={pendingAction === "delete" ? "danger" : "warning"}
        confirmLabel={t(pendingAction === "delete" ? "panel.actions.deleteViewerProfile" : "panel.actions.resetViewerImpression")}
        cancelLabel={t("panel.actions.cancel")}
        onConfirm={async () => {
          if (!selectedProfile || actionPending) return
          setActionPending(true)
          try {
            if (pendingAction === "delete") await onDeleteProfile(String(selectedProfile.uid || ""))
            else await onResetImpression(String(selectedProfile.uid || ""))
            setPendingAction("")
            setSelectedProfile(null)
          } finally {
            setActionPending(false)
          }
        }}
        onCancel={() => setPendingAction("")}
      />
    </Stack>
  )
}

function formatCountedTags(value: any): string {
  return Array.isArray(value) && value.length
    ? value.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"
}

function formatDateTime(value: any, locale?: string): string {
  const raw = String(value || "").trim()
  if (!raw) return "-"
  const parsed = new Date(raw)
  if (Number.isNaN(parsed.getTime())) return raw
  try {
    return parsed.toLocaleString(locale || undefined)
  } catch {
    return parsed.toLocaleString()
  }
}

function sessionEventLabel(t: PanelTranslator, value: any): string {
  const key = String(value || "unknown")
  const labelKey = `panel.audience.event.${key === "super_chat" ? "superChat" : key}`
  const label = t(labelKey)
  return label && label !== labelKey ? label : t("panel.audience.event.unknown")
}

function sessionEventTone(value: any): "success" | "warning" | "info" | "default" {
  const key = String(value || "")
  if (key === "gift" || key === "super_chat" || key === "guard") return "success"
  if (key === "danmaku") return "info"
  return "default"
}

function profileBadge(group: "viewerStage" | "profileConfidence" | "profileFreshness", value: any, t: PanelTranslator) {
  const key = String(value || "none")
  return <StatusBadge tone={profileTone(group, key)} label={profileLabel(group, key, t)} />
}

function profileLabel(group: string, key: string, t: PanelTranslator): string {
  const text = t(`panel.${group}.${key}`)
  return text && text !== `panel.${group}.${key}` ? text : key || "-"
}

function profileTone(group: string, key: string): "success" | "warning" | "danger" | "default" {
  if (group === "viewerStage") {
    if (key === "familiar_viewer" || key === "regular_viewer") return "success"
    if (key === "returning_viewer") return "warning"
    return "default"
  }
  if (group === "profileConfidence") {
    if (key === "high") return "success"
    if (key === "medium") return "warning"
    if (key === "low") return "danger"
    return "default"
  }
  if (key === "fresh" || key === "warm") return "success"
  if (key === "stale") return "warning"
  if (key === "old") return "danger"
  return "default"
}

/* bundled source: ui/panel.tsx */
const ONBOARDING_STORAGE_KEY = "neko-live:onboarding:v2"

function CompactTabs(props: { id: string; items: Array<{ id: string; label: any; content: any }> }) {
  const { id, items } = props
  const [activeId, setActiveId] = useState(items[0]?.id || "")
  const activeItem = items.find((item) => item.id === activeId) || items[0]

  return (
    <div className="neko-live-compact-tabs" style={{ display: "grid", gap: "10px" }}>
      <div role="tablist" aria-label={id} style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "5px" }}>
        {items.map((item, index) => {
          const active = item.id === activeItem?.id
          const tabId = `${id}-${item.id}-tab`
          const panelId = `${id}-${item.id}-panel`
          return (
            <button
              key={item.id}
              id={tabId}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={panelId}
              tabIndex={active ? 0 : -1}
              onClick={() => setActiveId(item.id)}
              onKeyDown={(event) => {
                let nextIndex = index
                if (event.key === "ArrowRight") nextIndex = (index + 1) % items.length
                else if (event.key === "ArrowLeft") nextIndex = (index - 1 + items.length) % items.length
                else if (event.key === "Home") nextIndex = 0
                else if (event.key === "End") nextIndex = items.length - 1
                else return
                event.preventDefault()
                const nextItem = items[nextIndex]
                if (!nextItem) return
                setActiveId(nextItem.id)
                const nextButton = event.currentTarget.parentElement?.children[nextIndex] as HTMLElement | undefined
                nextButton?.focus()
              }}
              style={{
                minHeight: "26px",
                padding: "2px 9px",
                border: `1px solid ${active ? "rgba(64, 158, 255, 0.38)" : "var(--border)"}`,
                borderRadius: "999px",
                background: active ? "rgba(64, 158, 255, 0.09)" : "transparent",
                color: active ? "var(--primary)" : "var(--muted)",
                font: "inherit",
                fontSize: "12px",
                fontWeight: active ? 650 : 500,
                lineHeight: "18px",
                cursor: "pointer",
              }}
            >
              {item.label}
            </button>
          )
        })}
      </div>
      <div
        id={`${id}-${activeItem?.id || "empty"}-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-${activeItem?.id || "empty"}-tab`}
        style={{ minWidth: 0 }}
      >
        {activeItem?.content}
      </div>
    </div>
  )
}

function formatLocaleDateTime(value: number, locale?: string): string {
  const parsed = new Date(value)
  try {
    return parsed.toLocaleString(locale || undefined)
  } catch {
    return parsed.toLocaleString()
  }
}

export default function NekoLivePanel(props: CompatPluginSurfaceProps<DashboardState>) {
  const { state, t } = props
  const safeState = state || {}
  const config = safeState.config || {}
  const connection = safeState.live_connection || {}
  const safety = safeState.safety || {}
  const liveStatus = safeState.live_status || {}
  const liveState = safeState.live_state || {}
  const liveDirectorStatus = safeState.live_director_status || {}
  const soloTestReadiness = safeState.solo_test_readiness || {}
  const speechExplanation = safeState.speech_explanation || {}
  const liveExplain = safeState.live_explain || {}
  const idleHostingStatus = safeState.idle_hosting_status || {}
  const activeEngagementStatus = safeState.active_engagement_status || {}
  const liveSession = safeState.live_session || {}
  const profiles = Array.isArray(safeState.recent_profiles) ? safeState.recent_profiles : []
  const results = Array.isArray(safeState.recent_results) ? safeState.recent_results : []
  const sandboxResults = Array.isArray(safeState.recent_sandbox_results) ? safeState.recent_sandbox_results : []
  const audit = Array.isArray(safeState.recent_audit) ? safeState.recent_audit : []
  const [sandboxResult, setSandboxResult] = useState<any>(null)
  const [lookupResult, setLookupResult] = useState<any>(null)
  const [liveRoomResult, setLiveRoomResult] = useState<any>(null)
  const [queriedRoomRef, setQueriedRoomRef] = useState("")
  const [loginState, setLoginState] = useState<any>(null)
  const [douyinAuthState, setDouyinAuthState] = useState<any>(null)
  const [consoleDialog, setConsoleDialog] = useState<"account" | "room" | "theme" | "pacing" | "diagnostics" | "">("")
  const [interactionDialog, setInteractionDialog] = useState<"avatar_roast" | "danmaku_response" | "live_support_events" | "warmup_hosting" | "idle_hosting" | "active_engagement" | "">("")
  const [connectPending, setConnectPending] = useState(false)
  const [sessionStartAccepted, setSessionStartAccepted] = useState(false)
  const [startConfirmOpen, setStartConfirmOpen] = useState(false)
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false)
  const [allowLimitedConnection, setAllowLimitedConnection] = useState(false)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [onboardingStep, setOnboardingStep] = useState(0)
  const [safetyDisableConfirmOpen, setSafetyDisableConfirmOpen] = useState(false)
  const [clearViewerMemoryConfirmOpen, setClearViewerMemoryConfirmOpen] = useState(false)
  const [storageDetailsOpen, setStorageDetailsOpen] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [consoleSaving, setConsoleSaving] = useState(false)
  const [authPending, setAuthPending] = useState("")
  const [developerPending, setDeveloperPending] = useState(false)
  const [simpleActionPending, setSimpleActionPending] = useState("")
  const [refreshFailureCount, setRefreshFailureCount] = useState(0)
  const [lastSuccessfulRefreshAt, setLastSuccessfulRefreshAt] = useState(() => Date.now())
  const [themeDraft, setThemeDraft] = useState({
    live_mode: "co_stream",
    stream_theme: "",
    stream_goal: "",
    stream_columns: "",
    stream_avoid_topics: "",
  })
  const [pacingDraft, setPacingDraft] = useState({
    activity_level: "standard",
    rate_limit_seconds: "20",
  })
  const [roomDraft, setRoomDraft] = useState("")
  const closeConsoleDialog = useCallback(() => { setConsoleDialog("") }, [])
  const closeInteractionDialog = useCallback(() => { setInteractionDialog("") }, [])
  const toast = useToast()
  const configForm = useForm({ ...configDefaults })
  const sandboxForm = useForm({ ...sandboxDefaults })
  const presetViewerNickname = t("panel.dev.emitter.defaultNickname")
  const presetViewerDanmaku = t("panel.dev.emitter.defaultDanmaku")

  useEffect(() => {
    try {
      if (window.localStorage.getItem(ONBOARDING_STORAGE_KEY) !== "done") {
        setOnboardingOpen(true)
      }
    } catch {
      /* The panel remains usable when the host blocks browser storage. */
    }
  }, [])

  useEffect(() => {
    configForm.setValues({
      live_platform: String(config.live_platform || "bilibili"),
      live_room_ref: String(config.live_room_ref || config.live_room_id || ""),
      live_enabled: !!config.live_enabled,
      avatar_roast_enabled: config.avatar_roast_enabled !== false,
      avatar_analysis_enabled: config.avatar_analysis_enabled !== false,
      danmaku_response_enabled: config.danmaku_response_enabled !== false,
      live_support_events_enabled: config.live_support_events_enabled !== false,
      warmup_hosting_enabled: config.warmup_hosting_enabled !== false,
      idle_hosting_enabled: config.idle_hosting_enabled !== false,
      active_engagement_enabled: config.active_engagement_enabled !== false,
      live_room_id: String(config.live_room_id || ""),
      douyin_cookie: configForm.values.douyin_cookie || "",
      douyin_uid: configForm.values.douyin_uid || "",
      douyin_nickname: configForm.values.douyin_nickname || "",
      developer_tools_enabled: !!config.developer_tools_enabled,
      live_mode: String(config.live_mode || "co_stream"),
      activity_level: String(config.activity_level || "standard"),
      roast_strength: String(config.roast_strength || "normal"),
      roast_once_per_uid: config.roast_once_per_uid !== false,
      viewer_memory_enabled: config.viewer_memory_enabled !== false,
      rate_limit_seconds: String(config.rate_limit_seconds ?? 20),
      queue_limit: String(config.queue_limit ?? 5),
      safety_auto_stop_enabled: config.safety_auto_stop_enabled !== false,
      dry_run: config.dry_run === true,
      viewer_store_dir: String(config.viewer_store_dir || ""),
      stream_theme: String(config.stream_theme || ""),
      stream_goal: String(config.stream_goal || ""),
      stream_columns: String(config.stream_columns || ""),
      stream_avoid_topics: String(config.stream_avoid_topics || ""),
    })
  }, [
    config.live_platform,
    config.live_room_ref,
    config.live_enabled,
    config.avatar_roast_enabled,
    config.avatar_analysis_enabled,
    config.danmaku_response_enabled,
    config.live_support_events_enabled,
    config.warmup_hosting_enabled,
    config.idle_hosting_enabled,
    config.active_engagement_enabled,
    config.live_room_id,
    config.developer_tools_enabled,
    config.live_mode,
    config.activity_level,
    config.roast_strength,
    config.roast_once_per_uid,
    config.viewer_memory_enabled,
    config.rate_limit_seconds,
    config.queue_limit,
    config.safety_auto_stop_enabled,
    config.dry_run,
    config.viewer_store_dir,
    config.stream_theme,
    config.stream_goal,
    config.stream_columns,
    config.stream_avoid_topics,
  ])

  useEffect(() => {
    const state = String(connection.state || "")
    const transitionInProgress = ["connecting", "authenticating", "reconnecting"].includes(state)
    const shouldRefresh =
      !!config.live_enabled ||
      !!connection.connected ||
      !!connection.listening ||
      transitionInProgress ||
      state === "connected" ||
      state === "receiving"
    if (!shouldRefresh) return

    let disposed = false
    let refreshPending = false
    let timer: number | undefined

    function clearTimer() {
      if (timer !== undefined) {
        window.clearTimeout(timer)
        timer = undefined
      }
    }

    function scheduleRefresh() {
      clearTimer()
      if (disposed || document.visibilityState !== "visible") return
      timer = window.setTimeout(runRefresh, 3000)
    }

    function runRefresh() {
      clearTimer()
      if (disposed || refreshPending || document.visibilityState !== "visible") return
      refreshPending = true
      refreshDashboard()
        .finally(() => {
          refreshPending = false
          scheduleRefresh()
        })
    }

    function handleVisibilityChange() {
      if (document.visibilityState !== "visible") {
        clearTimer()
        return
      }
      runRefresh()
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    scheduleRefresh()
    return () => {
      disposed = true
      clearTimer()
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [config.live_enabled, connection.connected, connection.listening, connection.state])

  async function refreshDashboard(notifyFailure = false): Promise<boolean> {
    try {
      await props.api.refresh()
      setRefreshFailureCount(0)
      setLastSuccessfulRefreshAt(Date.now())
      return true
    } catch (err) {
      setRefreshFailureCount((count) => notifyFailure ? Math.max(2, count + 1) : count + 1)
      if (notifyFailure) toast.warning(t("panel.messages.refreshFailedAfterAction"))
      return false
    }
  }

  async function saveConfig(patch: Record<string, any>): Promise<boolean> {
    const normalizedRoomRef = (value: unknown) => {
      const roomRef = String(value ?? "").trim()
      return roomRef === "0" ? "" : roomRef
    }
    const hasPatchedRoomRef = Object.prototype.hasOwnProperty.call(patch, "live_room_ref")
    const hasPatchedRoomId = Object.prototype.hasOwnProperty.call(patch, "live_room_id")
    const livePlatform = String(patch.live_platform ?? config.live_platform ?? configForm.values.live_platform ?? "bilibili")
    const liveRoomRef = normalizedRoomRef(hasPatchedRoomRef ? patch.live_room_ref : patch.live_room_id)
    const liveRoomId = livePlatform === "bilibili" ? Number(liveRoomRef) || 0 : 0
    const patchedPayload = hasPatchedRoomRef || hasPatchedRoomId
      ? {
          ...patch,
          live_room_ref: liveRoomRef,
          live_room_id: liveRoomId,
        }
      : patch
    try {
      await props.api.call("update_config", patchedPayload)
      Object.entries(patchedPayload).forEach(([key, value]) => configForm.setField(key as any, value))
      toast.success(t("panel.messages.saved"))
      await refreshDashboard(true)
      return true
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
      return false
    }
  }

  function openConsoleDialog(dialog: "account" | "room" | "theme" | "pacing" | "diagnostics") {
    if ((dialog === "account" || dialog === "room") && sessionInProgress) {
      toast.warning(t("panel.messages.endSessionBeforeChangingTarget"))
      return
    }
    if (dialog === "theme") {
      setThemeDraft({
        live_mode: String(configForm.values.live_mode || "co_stream"),
        stream_theme: String(configForm.values.stream_theme || ""),
        stream_goal: String(configForm.values.stream_goal || ""),
        stream_columns: String(configForm.values.stream_columns || ""),
        stream_avoid_topics: String(configForm.values.stream_avoid_topics || ""),
      })
    } else if (dialog === "pacing") {
      setPacingDraft({
        activity_level: String(configForm.values.activity_level || "standard"),
        rate_limit_seconds: String(configForm.values.rate_limit_seconds ?? 20),
      })
    } else if (dialog === "room") {
      setRoomDraft(String(config.live_room_ref || config.live_room_id || ""))
      setLiveRoomResult(null)
      setQueriedRoomRef("")
    }
    setConsoleDialog(dialog)
  }

  async function saveThemeSettings() {
    if (consoleSaving) return
    setConsoleSaving(true)
    try {
      const saved = await saveConfig({
        live_mode: themeDraft.live_mode,
        stream_theme: themeDraft.stream_theme.trim(),
        stream_goal: themeDraft.stream_goal.trim(),
        stream_columns: themeDraft.stream_columns.trim(),
        stream_avoid_topics: themeDraft.stream_avoid_topics.trim(),
      })
      if (saved) closeConsoleDialog()
    } finally {
      setConsoleSaving(false)
    }
  }

  const pacingCooldownText = pacingDraft.rate_limit_seconds.trim()
  const pacingCooldownSeconds = Number(pacingCooldownText)
  const pacingCooldownValid = /^\d+$/.test(pacingCooldownText)
    && Number.isInteger(pacingCooldownSeconds)
    && pacingCooldownSeconds >= 0
    && pacingCooldownSeconds <= 3600

  async function savePacingSettings() {
    if (consoleSaving || !pacingCooldownValid) return
    setConsoleSaving(true)
    try {
      const saved = await saveConfig({
        activity_level: pacingDraft.activity_level,
        rate_limit_seconds: pacingCooldownSeconds,
      })
      if (saved) closeConsoleDialog()
    } finally {
      setConsoleSaving(false)
    }
  }

  async function applySettingsPatch(patch: Record<string, any>) {
    if (settingsSaving) return
    const previous = Object.fromEntries(
      Object.keys(patch).map((key) => [key, (configForm.values as Record<string, any>)[key]]),
    )
    Object.entries(patch).forEach(([key, value]) => configForm.setField(key as any, value))
    setSettingsSaving(true)
    try {
      const saved = await saveConfig(patch)
      if (!saved) {
        Object.entries(previous).forEach(([key, value]) => configForm.setField(key as any, value))
      }
    } finally {
      setSettingsSaving(false)
    }
  }

  async function switchLivePlatform(next: string) {
    if (next === livePlatform || consoleSaving || authPending) return
    if (sessionInProgress) {
      toast.warning(t("panel.messages.endSessionBeforeChangingTarget"))
      return
    }
    const previous = {
      live_platform: configForm.values.live_platform,
      live_room_ref: configForm.values.live_room_ref,
      live_room_id: configForm.values.live_room_id,
      live_enabled: configForm.values.live_enabled,
    }
    setAllowLimitedConnection(false)
    configForm.setField("live_platform", next)
    configForm.setField("live_room_ref", "")
    configForm.setField("live_room_id", "")
    configForm.setField("live_enabled", false)
    setLiveRoomResult(null)
    setQueriedRoomRef("")
    setConsoleSaving(true)
    try {
      const saved = await saveConfig({ live_platform: next, live_room_ref: "", live_room_id: 0, live_enabled: false })
      if (!saved) {
        Object.entries(previous).forEach(([key, value]) => configForm.setField(key as any, value))
      }
    } finally {
      setConsoleSaving(false)
    }
  }

  async function lookupLiveRoom(): Promise<void> {
    if (consoleSaving) return
    const roomRef = roomDraft.trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    setConsoleSaving(true)
    try {
      const envelope = await props.api.call("lookup_live_room", { room_id: roomRef })
      const result = unwrapActionResult(envelope)
      setLiveRoomResult(result)
      setQueriedRoomRef(roomRef)
      if (result.ok) {
        toast.success(t("panel.messages.roomLookupDone"))
      } else {
        toast.warning(result.message || t("panel.messages.roomLookupFailed"))
      }
    } catch (err) {
      setLiveRoomResult(null)
      setQueriedRoomRef("")
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setConsoleSaving(false)
    }
  }

  async function confirmLiveRoom() {
    if (consoleSaving) return
    if (sessionInProgress) {
      toast.warning(t("panel.messages.endSessionBeforeChangingTarget"))
      return
    }
    const roomRef = roomDraft.trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    if (!liveRoomResult?.ok || queriedRoomRef !== roomRef) {
      toast.warning(t("panel.messages.roomLookupRequired"))
      return
    }
    setConsoleSaving(true)
    try {
      await props.api.call("set_live_room", { room_id: roomRef })
      setConsoleDialog("")
      toast.success(t("panel.messages.saved"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setConsoleSaving(false)
    }
  }

  async function connectRoom() {
    if (connectPending || sessionInProgress) return
    const roomRef = String(
      configForm.values.live_room_ref ||
      configForm.values.live_room_id ||
      config.live_room_ref ||
      config.live_room_id ||
      "",
    ).trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    setConnectPending(true)
    try {
      const result = unwrapActionResult(await props.api.call("connect_live_room", {
        room_id: roomRef,
        allow_accountless: livePlatform === "bilibili" && !loginLoggedIn && allowLimitedConnection,
      }))
      const nextConnection = result.connection || result
      const nextState = String(nextConnection.state || "")
      const startAccepted = !!(
        nextConnection.connected ||
        nextConnection.listening ||
        ["connecting", "authenticating", "reconnecting", "connected", "receiving"].includes(nextState)
      )
      if (startAccepted) setSessionStartAccepted(true)
      if (nextConnection.connected || nextConnection.listening) {
        toast.success(t("panel.messages.connected"))
      } else {
        toast.warning(nextState || t("panel.connection.disconnected"))
      }
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setConnectPending(false)
    }
  }

  async function biliLogin() {
    if (authPending || sessionInProgress) return
    setAuthPending("bili_login")
    try {
      const result = unwrapActionResult(await props.api.call("bili_login"))
      setLoginState(result)
      if (result.status === "qrcode_ready") toast.info(t("panel.auth.scanHint"))
      else if (result.logged_in || result.status === "already_logged_in" || result.status === "done") {
        setAllowLimitedConnection(false)
        toast.success(t("panel.auth.loggedIn"))
        await refreshDashboard(true)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  async function biliLoginCheck() {
    if (authPending || sessionInProgress) return
    setAuthPending("bili_login_check")
    try {
      const result = unwrapActionResult(await props.api.call("bili_login_check"))
      setLoginState(result)
      if (result.status === "done" || result.logged_in) {
        setAllowLimitedConnection(false)
        toast.success(t("panel.auth.loginDone"))
        await refreshDashboard(true)
      } else if (result.message) {
        toast.info(result.message)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  async function biliLogout() {
    if (authPending || sessionInProgress) return
    setAuthPending("bili_logout")
    try {
      const result = unwrapActionResult(await props.api.call("bili_logout"))
      setLoginState(result)
      setAllowLimitedConnection(false)
      toast.success(t("panel.auth.logoutDone"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  async function douyinCookieStatus() {
    if (authPending) return
    setAuthPending("douyin_cookie_status")
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_status"))
      setDouyinAuthState(result)
      if (result.logged_in || result.has_cookie) toast.success(t("panel.douyinAuth.cookieReady"))
      else toast.info(t("panel.douyinAuth.cookieMissing"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  async function douyinCookieValidate() {
    if (authPending) return
    const roomRef = String(
      configForm.values.live_room_ref ||
      configForm.values.live_room_id ||
      config.live_room_ref ||
      config.live_room_id ||
      "",
    ).trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    setAuthPending("douyin_cookie_validate")
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_validate", { room_ref: roomRef }))
      setDouyinAuthState(result)
      await refreshDashboard(true)
      if (result.valid) toast.success(t("panel.douyinAuth.cookieValid"))
      else toast.warning(result.message || t("panel.douyinAuth.cookieInvalid"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  async function douyinCookieImport() {
    if (authPending || sessionInProgress) return
    const cookie = String(configForm.values.douyin_cookie || "").trim()
    if (!cookie) {
      toast.error(t("panel.douyinAuth.cookieRequired"))
      return
    }
    setAuthPending("douyin_cookie_import")
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_import", {
        cookie,
        uid: String(configForm.values.douyin_uid || "").trim(),
        nickname: String(configForm.values.douyin_nickname || "").trim(),
      }))
      setDouyinAuthState(result)
      if (result.saved) {
        configForm.setField("douyin_cookie", "")
        toast.success(t("panel.douyinAuth.cookieSaved"))
      } else {
        toast.warning(result.message || t("panel.douyinAuth.cookieSaveFailed"))
      }
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  async function douyinCookieDelete() {
    if (authPending || sessionInProgress) return
    setAuthPending("douyin_cookie_delete")
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_delete"))
      setDouyinAuthState(result)
      toast.success(t("panel.douyinAuth.cookieDeleted"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthPending("")
    }
  }

  useEffect(() => {
    ;(async () => {
      try {
        setLoginState(unwrapActionResult(await props.api.call("bili_login_status")))
      } catch {
        /* Login status fetch failures should not block the panel. */
      }
      try {
        setDouyinAuthState(unwrapActionResult(await props.api.call("douyin_cookie_status")))
      } catch {
        /* Douyin auth status is optional until that provider is selected. */
      }
    })()
  }, [])

  async function callSimple(action: string) {
    if (simpleActionPending) return
    setSimpleActionPending(action)
    try {
      await props.api.call(action, {})
      if (action === "disconnect_live_room") setSessionStartAccepted(false)
      toast.success(t("panel.messages.done"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSimpleActionPending("")
    }
  }

  async function runViewerProfileAction(action: string, uid: string): Promise<void> {
    if (!uid) return
    try {
      await props.api.call(action, { uid })
      toast.success(t("panel.messages.done"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
      throw err
    }
  }

  async function clearViewerMemory(): Promise<void> {
    try {
      await props.api.call("clear_viewer_profiles", {})
      toast.success(t("panel.messages.done"))
      await refreshDashboard(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setClearViewerMemoryConfirmOpen(false)
    }
  }

  async function submitSandbox() {
    const identity = lookupResult?.identity || {}
    const manualUid = sandboxForm.values.uid.trim()
    const lookupUid = String(identity.uid || "").trim()
    const typedTarget = sandboxForm.values.target.trim()
    const uid = manualUid || lookupUid
    const nickname =
      sandboxForm.values.nickname.trim() ||
      String(identity.nickname || identity.name || "").trim() ||
      (!uid && !typedTarget ? presetViewerNickname : "")
    const avatarUrl = sandboxForm.values.avatar_url.trim() || String(identity.avatar_url || "").trim()
    const target = uid ? "" : typedTarget || "__demo__"
    try {
      const envelope = await props.api.call("submit_viewer_event", {
        target,
        uid,
        nickname,
        avatar_url: avatarUrl,
        danmaku_text: sandboxForm.values.danmaku_text.trim() || presetViewerDanmaku,
      })
      const result = unwrapActionResult(envelope)
      setSandboxResult(result)
      await refreshDashboard(true)
      toast.success(t("panel.messages.submitted"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function lookupSandbox() {
    try {
      const envelope = await props.api.call("submit_viewer_event", {
        lookup_only: true,
        target: sandboxForm.values.target.trim(),
      })
      const result = unwrapActionResult(envelope)
      setLookupResult(result)
      setSandboxResult(result)
      await refreshDashboard(true)
      toast.success(t("panel.messages.lookupDone"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function runDemoCase() {
    try {
      const envelope = await props.api.call("submit_viewer_event", {
        target: "__demo__",
      })
      const result = unwrapActionResult(envelope)
      setSandboxResult(result)
      await refreshDashboard(true)
      toast.success(t("panel.messages.demoSubmitted"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function clearSandboxData() {
    try {
      await props.api.call("clear_sandbox_data", {})
      setSandboxResult(null)
      setLookupResult(null)
      await refreshDashboard(true)
      toast.success(t("panel.messages.sandboxCleared"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function toggleDeveloperTools(value: boolean) {
    if (developerPending) return
    const previous = !!configForm.values.developer_tools_enabled
    configForm.setField("developer_tools_enabled", value)
    setDeveloperPending(true)
    try {
      await props.api.call("update_config", {
        developer_tools_enabled: value,
      })
      toast.success(value ? t("panel.messages.devEnabled") : t("panel.messages.devDisabled"))
      await refreshDashboard(true)
    } catch (err) {
      configForm.setField("developer_tools_enabled", previous)
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setDeveloperPending(false)
    }
  }

  function enableLimitedConnection() {
    if (authPending || sessionInProgress) return
    setAllowLimitedConnection(true)
    setConsoleDialog("")
    toast.warning(t("panel.console.limitedEnabled"))
  }

  function completeOnboarding() {
    try {
      window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "done")
    } catch {
      /* Completion still closes the guide when browser storage is unavailable. */
    }
    setOnboardingStep(0)
    setOnboardingOpen(false)
  }

  function resetOnboarding() {
    try {
      window.localStorage.removeItem(ONBOARDING_STORAGE_KEY)
    } catch {
      /* Opening the guide does not depend on browser storage. */
    }
    setOnboardingStep(0)
    setOnboardingOpen(true)
  }

  const liveStatusSummary = String(liveStatus.summary || "cannot_stream")
  const liveStatusReason = String(liveStatus.reason || "room_not_configured")
  const liveStatusCooldown = Number(liveStatus.cooldown_remaining || 0)
  const liveMode = String(liveState.mode || config.live_mode || "co_stream")
  const liveModeRole = String(liveState.mode_role || (liveMode === "solo_stream" ? "solo_host" : "companion"))
  const liveStateName = String(liveState.state || "blocked")
  const liveStateReason = String(liveState.reason || "blocked_by_live_status")
  const liveStateLastActivityAge = formatAgeSec(liveState.last_activity_age_sec)
  const liveStateLastViewerActivityAge = formatAgeSec(liveState.last_viewer_activity_age_sec ?? liveState.last_activity_age_sec)
  const liveStateLastOutputAge = formatAgeSec(liveState.last_output_age_sec)
  const liveStateQuietAfter = `${Number(liveState.engaged_threshold_seconds || 0).toFixed(0)}s`
  const liveStateIdleAfter = `${Number(liveState.idle_threshold_seconds || 0).toFixed(0)}s`
  const developerToolsEnabled = !!configForm.values.developer_tools_enabled
  const warmupHostingCandidate = !!liveState.warmup_hosting_candidate
  const idleHostingCandidate = !!liveState.idle_hosting_candidate
  const idleHostingEligible = !!idleHostingStatus.eligible
  const idleHostingReason = String(idleHostingStatus.reason || "not_candidate")
  const idleHostingCooldown = Number(idleHostingStatus.cooldown_remaining || 0)
  const idleHostingMinInterval = Number(idleHostingStatus.min_interval_seconds || 0)
  const activeEngagementCandidate = !!activeEngagementStatus.candidate
  const activeEngagementEligible = !!activeEngagementStatus.eligible
  const activeEngagementReason = String(activeEngagementStatus.reason || "not_quiet")
  const activeEngagementCooldown = Number(activeEngagementStatus.cooldown_remaining || 0)
  const activeEngagementMinInterval = Number(activeEngagementStatus.min_interval_seconds || 0)
  const activeEngagementMinimumRemaining = Number(activeEngagementStatus.minimum_interval_remaining || 0)
  const activeEngagementDanmakuWait = Number(activeEngagementStatus.recent_danmaku_cooldown_remaining || 0)
  const speechSummary = String(speechExplanation.summary || "cannot_stream")
  const speechReason = String(speechExplanation.reason || "room_not_configured")
  const speechLastStatus = String(speechExplanation.last_result_status || "")
  const speechLastReason = String(speechExplanation.last_result_reason || "")
  const speechLastSource = String(speechExplanation.last_result_source || "")
  const liveDirectorNextAction = String(liveDirectorStatus.next_auto_action || "none")
  const liveDirectorEligible = !!liveDirectorStatus.eligible
  const liveDirectorReason = String(liveDirectorStatus.reason || "waiting_for_viewer")
  const liveDirectorCooldown = Number(liveDirectorStatus.cooldown_remaining || 0)
  const soloTestReady = !!soloTestReadiness.ready
  const soloTestSummary = String(soloTestReadiness.summary || "live_not_ready")
  const soloTestProfileCount = Number(soloTestReadiness.profile_count || 0)
  const soloTestItems = Array.isArray(soloTestReadiness.items) ? soloTestReadiness.items : []
  const dynamicLabel = (group: string, keyPrefix: string, value: string): string => (
    panelText(t, `${keyPrefix}.${value}`, labelFallback(group, value))
  )
  const livePlatform = String(configForm.values.live_platform || config.live_platform || "bilibili")
  const livePlatformLabel = t(`panel.platform.${livePlatform === "douyin" ? "douyin" : "bilibili"}`)
  const roomFieldLabel = livePlatform === "douyin" ? t("panel.fields.douyinRoom") : t("panel.fields.roomId")
  const roomPlaceholder = livePlatform === "douyin" ? t("panel.placeholders.douyinRoom") : t("panel.placeholders.roomId")
  const configuredRoomRef = String(config.live_room_ref || config.live_room_id || "").trim()
  const currentRoomRef = String(connection.room_ref || configuredRoomRef || "").trim()
  const lookupRoomRef = String(liveRoomResult?.room_ref || liveRoomResult?.room_id || "").trim()
  const roomLookupLiveStatus = normalizeRoomLiveStatus(liveRoomResult?.live_status)
  const roomLookupStatusTone = roomLiveStatusTone(roomLookupLiveStatus)
  const roomFormRef = roomDraft.trim()
  const canConfirmLiveRoom = Boolean(liveRoomResult?.ok && queriedRoomRef === roomFormRef)
  const currentRoomLiveStatus = normalizeRoomLiveStatus(
    lookupRoomRef && lookupRoomRef === currentRoomRef ? liveRoomResult?.live_status : liveStatus.live_status
  )
  const currentRoomStatusTone = roomLiveStatusTone(currentRoomLiveStatus)
  const currentRoomTitle = lookupRoomRef && lookupRoomRef === currentRoomRef
    ? String(liveRoomResult?.title || "").trim()
    : ""
  const loginLoggedIn = !!(loginState && (loginState.logged_in === true || loginState.status === "done" || loginState.status === "already_logged_in"))
  const loginName = (loginState && loginState.username) || ""
  const loginUid = (loginState && loginState.uid) || ""
  const douyinLoggedIn = !!(douyinAuthState && (douyinAuthState.logged_in || douyinAuthState.has_cookie))
  const douyinUid = String((douyinAuthState && douyinAuthState.uid) || "")
  const douyinNickname = String((douyinAuthState && douyinAuthState.nickname) || "")
  const douyinSavedAt = String((douyinAuthState && douyinAuthState.saved_at) || "")
  const douyinValidationMessage = String((douyinAuthState && douyinAuthState.message) || "")
  const douyinValidationStatus = String((douyinAuthState && douyinAuthState.live_status) || "")
  const connectionPlan = connection && typeof connection.connection_plan === "object" ? connection.connection_plan : null
  const connectionMissing = connectionPlan && Array.isArray(connectionPlan.missing) ? connectionPlan.missing.map((item: any) => String(item)).filter(Boolean) : []
  const reconnectState = connection && typeof connection.reconnect === "object" ? connection.reconnect : null
  const connectionLastError = String(connection.last_error || "")

  const connectionState = String(connection.state || "")
  const connectionTransitioning = ["connecting", "authenticating", "reconnecting"].includes(connectionState)
  const started = !!(
    connection.connected ||
    connection.listening ||
    connectionTransitioning ||
    connectionState === "connected" ||
    connectionState === "receiving"
  )
  const sessionInProgress = started || !!config.live_enabled || sessionStartAccepted
  useEffect(() => {
    if (started || config.live_enabled) setSessionStartAccepted(false)
    else if (
      !connectPending &&
      ["disconnected", "failed", "error", "auth_required", "unsupported"].includes(connectionState)
    ) setSessionStartAccepted(false)
  }, [started, config.live_enabled, connectPending, connectionState])
  const roomInputRef = String(configForm.values.live_room_ref || configForm.values.live_room_id || configuredRoomRef || "").trim()
  const roomConfigured = !!configuredRoomRef
  const interactionPaused = liveStateName === "paused" || String(safety.status || "") === "paused"
  const connectionFailed = !started && (
    connectionState === "error" ||
    connectionState === "failed" ||
    !!connectionLastError
  )
  const consoleState = connectPending || connectionTransitioning ? "connecting" : connectionFailed ? "error" : started ? "live" : "ready"
  const accountReady = livePlatform === "douyin" ? douyinLoggedIn : loginLoggedIn
  const connectionAuthMode = String(connection.auth_mode || "unknown")
  const limitedConnection = livePlatform === "bilibili" && !loginLoggedIn && (
    allowLimitedConnection || connectionAuthMode === "limited_accountless"
  )
  const loginRequired = livePlatform === "bilibili" && !loginLoggedIn && !limitedConnection
  const accountStartReady = livePlatform === "bilibili" ? (loginLoggedIn || limitedConnection) : douyinLoggedIn
  const savedCooldownSeconds = Number(config.rate_limit_seconds ?? configForm.values.rate_limit_seconds ?? 20)
  const liveSettingsReady = Boolean(String(config.live_mode || configForm.values.live_mode || "").trim()) && Number.isFinite(savedCooldownSeconds) && savedCooldownSeconds >= 0
  const unsafeSafetyStates = new Set(["paused", "tripped", "degraded"])
  const safetyCheckReady = !connectPending && !sessionInProgress && !unsafeSafetyStates.has(String(safety.status || ""))
  const canStart = roomConfigured && accountStartReady && liveSettingsReady && safetyCheckReady
  const readinessReason = !accountStartReady
    ? t("panel.console.preparation.login")
    : !roomConfigured
      ? t(canConfirmLiveRoom ? "panel.console.preparation.confirmRoom" : "panel.console.preparation.lookupRoom")
      : !liveSettingsReady
        ? t("panel.console.preparation.liveSettings")
        : !safetyCheckReady
          ? t("panel.console.preparation.safety")
          : ""
  const primaryStatusLabel = started
    ? t("panel.console.state.live")
    : connectPending
      ? t("panel.console.state.connecting")
      : connectionFailed
        ? t("panel.console.state.error")
        : !roomConfigured
          ? t("panel.console.roomMissing")
          : (!accountReady && !limitedConnection)
            ? t("panel.console.loginRequired")
            : canStart
              ? t("panel.liveStatusSummary.ready_to_stream")
              : dynamicLabel("liveStatusSummary", "panel.liveStatusSummary", liveStatusSummary)
  const primaryStatusTone: "success" | "warning" | "danger" | "info" = started || canStart
    ? "success"
    : connectionFailed
      ? "danger"
      : connectPending
        ? "info"
        : "warning"
  const safetyStatus = String(safety.status || "")
  const showSafetyStatus = started || (!!safetyStatus && safetyStatus !== "disconnected" && safetyStatus !== "unknown")
  const accountLabel = accountReady
    ? (livePlatform === "douyin" ? (douyinNickname || douyinUid || t("panel.douyinAuth.cookieReady")) : (loginName || loginUid || t("panel.auth.loggedIn")))
    : (limitedConnection ? t("panel.console.limitedConnection") : t("panel.auth.loggedOut"))
  const modules = Array.isArray(safeState.modules) ? safeState.modules : []

  const streamThemeForm = (
    <Stack>
      <Text>{t("panel.streamTheme.hint")}</Text>
        <Field label={t("panel.fields.mode")}>
          <Select
            value={themeDraft.live_mode}
            options={[
              { value: "co_stream", label: t("panel.mode.co") },
              { value: "solo_stream", label: t("panel.mode.solo") },
            ]}
            onChange={(value) => {
              const next = String(value)
              setThemeDraft((draft) => ({ ...draft, live_mode: next }))
            }}
          />
        </Field>
        <Field label={t("panel.fields.streamTheme")}>
          <Input value={themeDraft.stream_theme} onChange={(value) => setThemeDraft((draft) => ({ ...draft, stream_theme: value }))} />
        </Field>
        <Field label={t("panel.fields.streamGoal")}>
          <Input value={themeDraft.stream_goal} onChange={(value) => setThemeDraft((draft) => ({ ...draft, stream_goal: value }))} />
        </Field>
        <Field label={t("panel.fields.streamColumns")}>
          <Input value={themeDraft.stream_columns} onChange={(value) => setThemeDraft((draft) => ({ ...draft, stream_columns: value }))} />
        </Field>
        <Field label={t("panel.fields.streamAvoidTopics")}>
          <Input value={themeDraft.stream_avoid_topics} onChange={(value) => setThemeDraft((draft) => ({ ...draft, stream_avoid_topics: value }))} />
        </Field>
    </Stack>
  )

  const pacingForm = (
    <Stack>
      <Text>{t("panel.pacing.hint")}</Text>
      <Field label={t("panel.fields.activityLevel")}>
        <Select
          value={pacingDraft.activity_level}
          options={[
            { value: "quiet", label: t("panel.activity.quiet") },
            { value: "standard", label: t("panel.activity.standard") },
            { value: "active", label: t("panel.activity.active") },
          ]}
          onChange={(value) => {
            const next = String(value)
            setPacingDraft((draft) => ({ ...draft, activity_level: next }))
          }}
        />
      </Field>
      <Field label={t("panel.fields.rateLimit")}>
        <Grid cols={3}>
          {[
            { seconds: 10, label: t("panel.pacing.fast") },
            { seconds: 20, label: t("panel.pacing.standard") },
            { seconds: 30, label: t("panel.pacing.slow") },
          ].map((option) => (
            <Button
              key={option.seconds}
              tone={Number(pacingDraft.rate_limit_seconds) === option.seconds ? "primary" : "default"}
              onClick={() => {
                setPacingDraft((draft) => ({ ...draft, rate_limit_seconds: String(option.seconds) }))
              }}
            >
              {option.label}
            </Button>
          ))}
        </Grid>
      </Field>
      <Field label={t("panel.pacing.custom")}>
        <Input value={pacingDraft.rate_limit_seconds} onChange={(value) => setPacingDraft((draft) => ({ ...draft, rate_limit_seconds: value }))} />
      </Field>
      {!pacingCooldownValid ? <Alert tone="warning">{t("panel.pacing.invalidCooldown")}</Alert> : null}
    </Stack>
  )

  const queueSize = Number(safety.queue_size || 0)
  const queueLimit = Number(safety.queue_limit || config.queue_limit || configForm.values.queue_limit || 0)
  const configuredCooldownSeconds = Number(config.rate_limit_seconds ?? configForm.values.rate_limit_seconds ?? 20)
  const cooldownSeconds = Number.isFinite(configuredCooldownSeconds) ? configuredCooldownSeconds : 20
  const lastSuccessfulRefreshLabel = formatLocaleDateTime(lastSuccessfulRefreshAt, props.locale)

  // Streamer-first console: routine live operations stay on one compact page.
  const consoleSection = (
    <div
      className="neko-live-console-layout"
      style={{ display: "grid", gridTemplateRows: "auto", minHeight: "360px", overflow: "visible" }}
    >
      <div className="neko-live-console-scroll" style={{ minHeight: 0, overflow: "visible" }}>
        <Stack>
      <Grid cols={3}>
        <Card title={t("panel.platform.title")}>
          <Stack gap={8}>
            <StatusBadge tone="info" label={livePlatformLabel} />
            <Text>{t("panel.console.platformHint")}</Text>
          </Stack>
        </Card>
        <Card title={t("panel.console.accountTitle")}>
          <Stack gap={8}>
            <StatusBadge tone={accountReady ? "success" : "warning"} label={accountLabel} />
            <Button tone="default" disabled={sessionInProgress} onClick={() => { openConsoleDialog("account") }}>{t("panel.console.manage")}</Button>
          </Stack>
        </Card>
        <Card title={t("panel.room.title")}>
          <Stack gap={8}>
            <StatusBadge tone={roomConfigured ? "success" : "warning"} label={currentRoomRef || t("panel.console.roomMissing")} />
            <Button tone="default" disabled={sessionInProgress} onClick={() => { openConsoleDialog("room") }}>{t("panel.actions.setRoom")}</Button>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("panel.console.runtimeTitle")}>
        <Stack>
          <Text>
            {consoleState === "live"
              ? t("panel.console.liveHint")
              : consoleState === "connecting"
                ? t("panel.console.connectingHint")
                : consoleState === "error"
                  ? (connectionLastError || t("panel.console.errorHint"))
                  : !roomInputRef
                    ? t("panel.messages.roomRequired")
                    : livePlatform === "douyin" && !douyinLoggedIn
                      ? t("panel.douyinAuth.cookieMissing")
                      : loginRequired
                        ? t("panel.console.loginRequired")
                      : t("panel.console.readyHint")}
          </Text>
          {loginRequired && !started ? <Alert tone="info">{t("panel.console.loginRequiredHint")}</Alert> : null}
          {limitedConnection && !started ? <Alert tone="warning">{t("panel.console.limitedHint")}</Alert> : null}
          <Grid cols={2}>
            <Button tone="default" onClick={() => { openConsoleDialog("theme") }}>{t("panel.streamTheme.title")}</Button>
            <Button tone="default" onClick={() => { openConsoleDialog("pacing") }}>{t("panel.pacing.title")}</Button>
          </Grid>
        </Stack>
      </Card>

      <Card title={t("panel.console.sessionTitle")}>
        <Stack>
          <Grid cols={4}>
            <StatCard label={t("panel.console.events")} value={<StatusBadge tone={started ? "success" : "default"} label={started ? t("panel.console.eventsReceiving") : t("panel.console.eventsWaiting")} />} />
            <StatCard label={t("panel.console.autoInteraction")} value={config.live_enabled ? t("panel.console.enabled") : t("panel.console.disabled")} />
            <StatCard label={t("panel.fields.mode")} value={dynamicLabel("liveModeRole", "panel.liveModeRole", liveMode)} />
            <StatCard label={t("panel.console.recentActivity")} value={results.length ? t("panel.console.recentNow") : t("panel.console.recentNone")} />
          </Grid>
          <Text>{dynamicLabel("liveModeRoleHint", "panel.liveModeRoleHint", liveModeRole)}</Text>
          <Button tone="default" onClick={() => { openConsoleDialog("diagnostics") }}>{t("panel.actions.showAdvanced")}</Button>
        </Stack>
      </Card>

      <Modal
        open={consoleDialog === "account"}
        title={t("panel.console.accountModalTitle")}
        size="lg"
        onClose={closeConsoleDialog}
        footer={<Button tone="default" onClick={() => { setConsoleDialog("") }}>{t("panel.actions.cancel")}</Button>}
      >
        <Stack>
          <Field label={t("panel.fields.platform")}>
            <Select
              value={livePlatform}
              disabled={sessionInProgress || consoleSaving || !!authPending}
              options={[
                { value: "bilibili", label: t("panel.platform.bilibili") },
                { value: "douyin", label: `${t("panel.platform.douyin")} ${t("panel.platform.incompleteSuffix")}` },
              ]}
              onChange={(value) => switchLivePlatform(String(value))}
            />
          </Field>
          {livePlatform === "douyin" ? (
            <Stack>
              <StatusBadge tone={douyinLoggedIn ? "success" : "warning"} label={accountLabel} />
              {douyinSavedAt ? <Text>{t("panel.douyinAuth.savedAt")}: {douyinSavedAt}</Text> : null}
              {douyinValidationMessage ? <Text>{douyinValidationMessage}</Text> : null}
              {douyinValidationStatus ? <Text>{t("panel.room.liveStatus")}: {t(`panel.liveStatus.${douyinValidationStatus}`)}</Text> : null}
              <Field label={t("panel.fields.douyinCookie")}>
                <Textarea disabled={sessionInProgress || !!authPending} value={configForm.values.douyin_cookie} placeholder={t("panel.placeholders.douyinCookie")} onChange={(value) => { configForm.setField("douyin_cookie", value) }} />
              </Field>
              <Grid cols={2}>
                <Field label={t("panel.fields.douyinUid")}><Input disabled={sessionInProgress || !!authPending} value={configForm.values.douyin_uid} onChange={(value) => { configForm.setField("douyin_uid", value) }} /></Field>
                <Field label={t("panel.fields.douyinNickname")}><Input disabled={sessionInProgress || !!authPending} value={configForm.values.douyin_nickname} onChange={(value) => { configForm.setField("douyin_nickname", value) }} /></Field>
              </Grid>
              <Grid cols={4}>
                <Button tone="success" disabled={sessionInProgress || !!authPending} onClick={douyinCookieImport}>{t("panel.actions.douyinCookieImport")}</Button>
                <Button tone="info" disabled={!!authPending} onClick={douyinCookieStatus}>{t("panel.actions.douyinCookieStatus")}</Button>
                <Button tone="info" disabled={!!authPending} onClick={douyinCookieValidate}>{t("panel.actions.douyinCookieValidate")}</Button>
                <Button tone="danger" disabled={sessionInProgress || !!authPending} onClick={douyinCookieDelete}>{t("panel.actions.douyinCookieDelete")}</Button>
              </Grid>
              <Text>{t("panel.douyinAuth.manualHint")}</Text>
            </Stack>
          ) : (
            <Stack>
              <AuthCard t={t} loginState={loginState} loginLoggedIn={loginLoggedIn} loginName={loginName} loginUid={loginUid} disabled={sessionInProgress || !!authPending} onLogin={biliLogin} onLoginCheck={biliLoginCheck} onLogout={biliLogout} />
              {!loginLoggedIn ? (
                <Stack gap={8}>
                  <Alert tone="info">{t("panel.console.loginPrimaryHint")}</Alert>
                  <Button tone="warning" disabled={!!authPending || sessionInProgress} onClick={enableLimitedConnection}>{t("panel.console.useLimitedConnection")}</Button>
                </Stack>
              ) : null}
            </Stack>
          )}
        </Stack>
      </Modal>

      <Modal
        open={consoleDialog === "theme"}
        title={t("panel.streamTheme.title")}
        size="lg"
        onClose={closeConsoleDialog}
        footer={(
          <Grid cols={2}>
            <Button tone="default" disabled={consoleSaving} onClick={closeConsoleDialog}>{t("panel.actions.cancel")}</Button>
            <Button tone="success" disabled={consoleSaving} onClick={() => { void saveThemeSettings() }}>{t("panel.actions.save")}</Button>
          </Grid>
        )}
      >
        {streamThemeForm}
      </Modal>

      <Modal
        open={consoleDialog === "pacing"}
        title={t("panel.pacing.title")}
        onClose={closeConsoleDialog}
        footer={(
          <Grid cols={2}>
            <Button tone="default" disabled={consoleSaving} onClick={closeConsoleDialog}>{t("panel.actions.cancel")}</Button>
            <Button tone="success" disabled={consoleSaving || !pacingCooldownValid} onClick={() => { void savePacingSettings() }}>{t("panel.actions.save")}</Button>
          </Grid>
        )}
      >
        {pacingForm}
      </Modal>

      <Modal
        open={consoleDialog === "room"}
        title={t("panel.console.roomModalTitle")}
        onClose={closeConsoleDialog}
        footer={(
          <Grid cols={2}>
            <Button tone="default" disabled={consoleSaving} onClick={closeConsoleDialog}>{t("panel.actions.cancel")}</Button>
            <Button
              tone={canConfirmLiveRoom ? "success" : "info"}
              disabled={consoleSaving || !roomFormRef}
              onClick={canConfirmLiveRoom ? confirmLiveRoom : lookupLiveRoom}
            >
              {canConfirmLiveRoom ? t("panel.console.confirmRoom") : t("panel.actions.lookupRoom")}
            </Button>
          </Grid>
        )}
      >
        <Stack gap={8}>
          {!liveRoomResult?.ok ? <Alert tone="info">{t("panel.console.roomTwoStepHint")}</Alert> : null}
          <Field label={roomFieldLabel}>
            <Input
              value={roomDraft}
              placeholder={roomPlaceholder}
              onChange={(value) => {
                setRoomDraft(value)
                setLiveRoomResult(null)
                setQueriedRoomRef("")
              }}
            />
          </Field>
          {livePlatform === "bilibili" && !liveRoomResult?.ok ? <Text>{t("panel.console.roomNumeric")}</Text> : null}
          {liveRoomResult && !liveRoomResult.ok ? (
            <Alert tone="warning">{liveRoomResult.message || t("panel.room.lookupFailed")}</Alert>
          ) : null}
          {liveRoomResult?.ok ? (
            <Stack gap={8}>
              <Alert tone={roomLookupStatusTone}>
                <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
                  <StatusBadge tone={roomLookupStatusTone} label={t(`panel.liveStatus.${roomLookupLiveStatus}`)} />
                  <span>{t(`panel.room.statusHint.${roomLookupLiveStatus}`)}</span>
                </div>
              </Alert>
              <div style={{ border: "1px solid var(--border)", borderRadius: "12px", overflow: "hidden", background: "var(--surface)" }}>
                {[
                  [t("panel.stats.room"), lookupRoomRef || "-"],
                  [t("panel.room.titleLabel"), liveRoomResult.title || "-"],
                  [t("panel.room.anchor"), liveRoomResult.anchor_name || "-"],
                ].map(([label, value], index) => (
                  <div key={String(label)} style={{ display: "grid", gridTemplateColumns: "minmax(88px, 0.32fr) minmax(0, 1fr)", gap: "12px", alignItems: "center", padding: "8px 10px", borderTop: index ? "1px solid var(--border)" : "none" }}>
                    <span style={{ color: "var(--muted)", fontSize: "13px" }}>{label}</span>
                    <strong style={{ minWidth: 0, overflowWrap: "anywhere" }}>{value}</strong>
                  </div>
                ))}
              </div>
            </Stack>
          ) : null}
        </Stack>
      </Modal>

      <Modal
        open={consoleDialog === "diagnostics"}
        title={t("panel.actions.showAdvanced")}
        size="lg"
        onClose={closeConsoleDialog}
        footer={<Button tone="default" onClick={() => { setConsoleDialog("") }}>{t("panel.actions.cancel")}</Button>}
      >
        <Stack>
          <Grid cols={3}>
            <StatCard label={t("panel.columns.status")} value={<StatusBadge tone={liveStatusTone(liveStatusSummary)} label={dynamicLabel("liveStatusSummary", "panel.liveStatusSummary", liveStatusSummary)} />} />
            <StatCard label={t("panel.columns.reason")} value={dynamicLabel("liveStatusReason", "panel.liveStatusReason", liveStatusReason)} />
            <StatCard label={t("panel.liveStatusSummary.cooldown")} value={`${liveStatusCooldown.toFixed(1)}s`} />
          </Grid>
          <Grid cols={3}>
            <StatCard label={t("panel.liveState.title")} value={<StatusBadge tone={liveStateTone(liveStateName)} label={dynamicLabel("liveState", "panel.liveState", liveStateName)} />} />
            <StatCard label={t("panel.liveState.lastViewerActivityAge")} value={liveStateLastViewerActivityAge} />
            <StatCard label={t("panel.liveState.lastOutputAge")} value={liveStateLastOutputAge} />
          </Grid>
          <Alert tone={speechExplanationTone(speechSummary)}>{dynamicLabel("speechSummary", "panel.speechExplanation.summary", speechSummary)} / {dynamicLabel("speechReason", "panel.speechExplanation.reason", speechReason)}</Alert>
          {connectionLastError ? <Alert tone="danger">{connectionLastError}</Alert> : null}
          {connectionPlan?.message ? <Text>{String(connectionPlan.message)}</Text> : null}
          {connectionMissing.length ? <Text>{connectionMissing.join(", ")}</Text> : null}
          {reconnectState ? <Text>{Number(reconnectState.retry_count || 0).toFixed(0)}/{Number(reconnectState.policy?.max_retries || 0).toFixed(0)} / {Number(reconnectState.next_delay_seconds || 0).toFixed(1)}s / {String(reconnectState.last_reason || "-")}</Text> : null}
          <Grid cols={3}>
            <StatCard label={t("panel.soloTestReadiness.title")} value={<StatusBadge tone={soloReadinessTone(soloTestReady, soloTestSummary)} label={dynamicLabel("soloReadinessSummary", "panel.soloTestReadiness.summary", soloTestSummary)} />} />
            <StatCard label={t("panel.soloTestReadiness.profileCount")} value={soloTestProfileCount} />
            <StatCard label={t("panel.liveDirector.nextAutoAction")} value={<StatusBadge tone={liveDirectorEligible ? "success" : "default"} label={dynamicLabel("liveDirectorAction", "panel.liveDirector.action", liveDirectorNextAction)} />} />
          </Grid>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "8px" }}>
            {soloTestItems.map((item: any) => {
              const id = String(item.id || "preflight")
              const status = String(item.status || "blocked")
              return (
                <div key={id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", minHeight: "36px", padding: "8px 10px", border: "1px solid var(--border)", borderRadius: "8px", background: "var(--surface)" }}>
                  <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {dynamicLabel("soloReadinessItem", "panel.soloTestReadiness.item", id)}
                  </span>
                  <StatusBadge tone={soloReadinessItemTone(status)} label={dynamicLabel("soloReadinessStatus", "panel.soloTestReadiness.status", status)} />
                </div>
              )
            })}
          </div>
        </Stack>
      </Modal>

      <Modal
        open={startConfirmOpen}
        title={t("panel.console.startTitle")}
        onClose={() => { setStartConfirmOpen(false) }}
        footer={(
          <Grid cols={2}>
            <Button tone="default" onClick={() => { setStartConfirmOpen(false) }}>{t("panel.console.returnCheck")}</Button>
            <Button tone="success" onClick={() => {
              setStartConfirmOpen(false)
              void connectRoom()
            }}>{t("panel.console.confirmStart")}</Button>
          </Grid>
        )}
      >
        <Stack gap={12}>
          <Alert tone={currentRoomStatusTone}>
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
              <StatusBadge tone={currentRoomStatusTone} label={t(`panel.liveStatus.${currentRoomLiveStatus}`)} />
              <span>{t(`panel.room.statusHint.${currentRoomLiveStatus}`)}</span>
            </div>
          </Alert>
          <div style={{ border: "1px solid var(--border)", borderRadius: "12px", overflow: "hidden", background: "var(--surface)" }}>
            {[
              [t("panel.platform.title"), `${livePlatformLabel} · ${accountLabel}`],
              [t("panel.stats.room"), currentRoomTitle ? `${currentRoomRef || "-"} · ${currentRoomTitle}` : (currentRoomRef || "-")],
              [t("panel.room.liveStatus"), t(`panel.liveStatus.${currentRoomLiveStatus}`)],
              [t("panel.fields.mode"), dynamicLabel("liveModeRole", "panel.liveModeRole", liveMode)],
              [t("panel.liveStatusSummary.cooldown"), `${savedCooldownSeconds.toFixed(0)}s`],
            ].map(([label, value], index) => (
              <div key={String(label)} style={{ display: "grid", gridTemplateColumns: "minmax(88px, 0.32fr) minmax(0, 1fr)", gap: "14px", alignItems: "center", padding: "10px 12px", borderTop: index ? "1px solid var(--border)" : "none" }}>
                <span style={{ color: "var(--muted)", fontSize: "13px" }}>{label}</span>
                <strong style={{ minWidth: 0, overflowWrap: "anywhere" }}>{value}</strong>
              </div>
            ))}
          </div>
          <Text>{t("panel.console.startMessage")}</Text>
        </Stack>
      </Modal>

      <ConfirmDialog
        open={stopConfirmOpen}
        title={t("panel.console.stopTitle")}
        message={t("panel.console.stopMessage")}
        tone="danger"
        confirmLabel={t("panel.actions.disconnect")}
        cancelLabel={t("panel.actions.cancel")}
        onConfirm={() => {
          setStopConfirmOpen(false)
          callSimple("disconnect_live_room")
        }}
        onCancel={() => { setStopConfirmOpen(false) }}
      />
        </Stack>
      </div>
      <div
        className="neko-live-live-fab"
        aria-label={t("panel.console.runtimeTitle")}
        style={{ position: "fixed", right: "24px", bottom: "24px", zIndex: 100, display: "inline-flex", alignItems: "center", justifyContent: "center" }}
      >
        {!started && !canStart && !connectPending ? (
          <Tooltip content={readinessReason} placement="top">
            <span tabIndex={0} style={{ display: "inline-flex" }}>
              <button
              type="button"
              className="neko-button"
              data-tone="default"
              disabled
              style={{ minWidth: "148px", minHeight: "48px", borderRadius: "999px", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "8px", cursor: "not-allowed", boxShadow: "0 10px 28px rgba(15, 23, 42, 0.16)" }}
            >
              <span aria-hidden="true" style={{ width: "8px", height: "8px", borderRadius: "999px", background: "var(--warning)" }} />
              {t("panel.console.preparation.notReady")}
              </button>
            </span>
          </Tooltip>
        ) : (
          <button
            type="button"
            className="neko-button"
            data-tone={started ? "danger" : "success"}
            disabled={connectPending || !!simpleActionPending}
            onClick={() => {
              if (started) setStopConfirmOpen(true)
              else if (canStart) setStartConfirmOpen(true)
            }}
            style={{
              minWidth: "148px",
              minHeight: "48px",
              borderRadius: "999px",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "8px",
              boxShadow: "0 10px 28px rgba(15, 23, 42, 0.16)",
              ...(!started && canStart && !simpleActionPending
                ? {
                    minWidth: "188px",
                    minHeight: "56px",
                    borderRadius: "16px",
                    background: "rgba(103, 194, 58, 0.1)",
                    borderColor: "rgba(103, 194, 58, 0.38)",
                    color: "var(--success)",
                    fontSize: "22px",
                    fontWeight: 700,
                    opacity: 1,
                  }
                : {}),
            }}
          >
            <span aria-hidden="true" style={{ width: "8px", height: "8px", borderRadius: "999px", background: started ? "var(--danger)" : connectPending ? "var(--primary)" : "var(--success)" }} />
            {connectPending ? t("panel.console.state.connecting") : started ? t("panel.actions.disconnect") : t("panel.actions.connect")}
          </button>
        )}
      </div>
    </div>
  )

  const renderConfigField = (f: any, fi: number) => {
    const name = String((f && f.name) || "")
    const cur = Object.prototype.hasOwnProperty.call(configForm.values, name)
      ? configForm.values[name]
      : config[name]
    const label = f && f.label ? t(f.label) : name
    const hint = f && f.hint ? t(f.hint) : ""
    if (f && f.type === "boolean") {
      return (
        <Stack gap={4}>
          <ToggleSwitch checked={cur === undefined ? !!f.default : !!cur} disabled={settingsSaving} label={label} onChange={(v) => { applySettingsPatch({ [name]: v }) }} />
          {hint ? <Text>{hint}</Text> : null}
        </Stack>
      )
    }
    if (f && f.type === "select") {
      const opts = Array.isArray(f.options) ? f.options : []
      const curVal = String(cur === undefined ? (f.default ?? "") : cur)
      return (
        <Field label={label}>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {opts.map((o: any, oi: number) => {
              const selected = String(o.value) === curVal
              return (
                <button
                  key={String(o.value) || oi}
                  type="button"
                  disabled={settingsSaving}
                  onClick={() => { applySettingsPatch({ [name]: String(o.value) }) }}
                  style={{
                    padding: "6px 16px",
                    borderRadius: "999px",
                    cursor: settingsSaving ? "not-allowed" : "pointer",
                    font: "inherit",
                    fontWeight: 650,
                    border: selected ? "1px solid var(--primary)" : "1px solid var(--border)",
                    background: selected ? "var(--primary)" : "var(--surface)",
                    color: selected ? "#ffffff" : "var(--muted)",
                    transition: "background 140ms ease, color 140ms ease, border-color 140ms ease",
                  }}
                >
                  {o.label ? t(o.label) : String(o.value)}
                </button>
              )
            })}
          </div>
        </Field>
      )
    }
    return (
      <Field label={label}>
        <Input disabled={settingsSaving} value={String(cur === undefined ? ((f && f.default) ?? "") : cur)} onChange={(v) => { applySettingsPatch({ [name]: v }) }} />
      </Field>
    )
  }

  // Interaction modules render from their declared schemas plus NEKO Live behavior lanes.
  const interactionModules = modules.filter((m: any) => String((m && m.domain) || "") === "interaction")
  const interactionModuleById = interactionModules.reduce((acc: Record<string, any>, item: any) => {
    if (item && item.id) acc[String(item.id)] = item
    return acc
  }, {})
  const latestResult = results.length ? results[0] : null
  const latestRoute = latestResult ? interactionRoute(latestResult) : "-"
  const latestEventSignal = latestResult ? String(latestResult.event_signal || "-") : "-"
  const latestResultStatus = latestResult ? String(latestResult.status || "-") : "-"
  const latestResultReason = latestResult ? String(latestResult.reason || "") : ""
  const latestLatency = latestResult ? formatLatencyMs(latestResult.response_latency_ms) : "-"
  const latestTopic = latestResult && latestResult.event
    ? [
        activeTopicSourceLabel(latestResult.event.topic_source, t),
        activeTopicShapeLabel(latestResult.event.topic_shape, t),
        latestResult.event.topic_title,
        activeTopicIntentLabel(latestResult.event.topic_intent, t),
        activeTopicReplyAffordanceLabel(latestResult.event.topic_reply_affordance, t),
      ].filter(Boolean).join(" / ")
    : ""
  const latestHostBeat = latestResult && latestResult.event
    ? [
        idleHostBeatShapeLabel(latestResult.event.host_beat_shape, t),
        latestResult.event.host_beat_title,
      ].filter(Boolean).join(" / ")
    : ""

  // Live roast card header state.
  const roastEnabled = configForm.values.avatar_roast_enabled !== false
  const roastConnected = !!connection.connected
  const roastBadge = roastEnabled
    ? (roastConnected
        ? <StatusBadge tone="success" label={t("panel.modules.online")} />
        : <StatusBadge tone="warning" label={t("panel.modules.standby")} />)
    : <StatusBadge tone="default" label={t("panel.modules.off")} />

  const interactionCardGridStyle: any = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 440px), 1fr))",
    gap: "12px",
    alignItems: "stretch",
  }
  const interactionCardBodyStyle: any = {
    minHeight: "190px",
    height: "100%",
    display: "flex",
    flexDirection: "column" as const,
    gap: "10px",
  }
  const interactionStatusRowStyle: any = {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "8px",
    minHeight: "26px",
  }
  const renderInteractionToggle = (title: any, enabled: boolean, onChange: (value: boolean) => void) => (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px" }}>
      <span style={{ minWidth: 0, color: "var(--text)", fontSize: "15px", fontWeight: 720 }}>{title}</span>
      <ToggleSwitch checked={enabled} disabled={settingsSaving} label={title} tone="success" onChange={onChange} />
    </div>
  )
  const renderInteractionDisabledHint = (enabled: boolean, key: string) => (
    <div style={{ minHeight: "22px", visibility: enabled ? "hidden" : "visible" }} aria-hidden={enabled}>
      <span style={{ color: "var(--muted)", fontSize: "12px", lineHeight: "18px" }}>{t(key)}</span>
    </div>
  )
  const renderInteractionDetailsButton = (dialog: "avatar_roast" | "danmaku_response" | "live_support_events" | "warmup_hosting" | "idle_hosting" | "active_engagement") => (
    <div style={{ marginTop: "auto" }}>
      <Button tone="default" onClick={() => { setInteractionDialog(dialog) }}>{t("panel.interaction.details")}</Button>
    </div>
  )
  const renderInteractionGroupHeader = (title: string, subtitle: string) => (
    <div style={{ display: "grid", gap: "4px", marginTop: "4px" }}>
      <span style={{ color: "var(--text)", fontSize: "16px", fontWeight: 720 }}>{title}</span>
      <Text>{subtitle}</Text>
    </div>
  )

  const currentDecisionCard = (
    <Card title={t("panel.interaction.currentDecision.title")}>
      <Stack gap={12}>
        <Text>{t("panel.interaction.currentDecision.subtitle")}</Text>
        <Grid cols={4}>
          <StatCard label={t("panel.interaction.currentDecision.latestEvent")} value={latestResult ? latestEventLabel(latestResult) : t("panel.interaction.currentDecision.noResult")} />
          <StatCard label={t("panel.interaction.currentDecision.route")} value={<StatusBadge tone={interactionRouteTone(latestRoute)} label={interactionRouteLabel(latestRoute, t)} />} />
          <StatCard label={t("panel.interaction.currentDecision.eventSignal")} value={<StatusBadge tone={eventSignalTone(latestEventSignal)} label={eventSignalLabel(latestEventSignal, t)} />} />
          <StatCard label={t("panel.interaction.currentDecision.lastResult")} value={`${latestResultStatus} / ${latestLatency}`} />
        </Grid>
        <Grid cols={3}>
          <StatCard label={t("panel.liveDirector.nextAutoAction")} value={<StatusBadge tone={liveDirectorEligible ? "success" : "default"} label={dynamicLabel("liveDirectorAction", "panel.liveDirector.action", liveDirectorNextAction)} />} />
          <StatCard label={t("panel.columns.reason")} value={dynamicLabel("liveDirectorReason", "panel.liveDirector.reason", liveDirectorReason)} />
          <StatCard label={t("panel.liveDirector.cooldown")} value={`${liveDirectorCooldown.toFixed(1)}s`} />
        </Grid>
        {latestTopic ? (
          <Grid cols={1}>
            <StatCard label={t("panel.interaction.currentDecision.topic")} value={latestTopic} />
          </Grid>
        ) : null}
        {latestHostBeat ? (
          <Grid cols={1}>
            <StatCard label={t("panel.interaction.currentDecision.hostBeat")} value={latestHostBeat} />
          </Grid>
        ) : null}
        <Alert tone={speechExplanationTone(speechSummary)}>
          {t("panel.speechExplanation.title")} · {dynamicLabel("speechSummary", "panel.speechExplanation.summary", speechSummary)} · {dynamicLabel("speechReason", "panel.speechExplanation.reason", speechReason)}
        </Alert>
        {latestResultReason ? (
          <Text>
            {t("panel.interaction.currentDecision.skipReason")}: {latestResultReason}
          </Text>
        ) : null}
      </Stack>
    </Card>
  )

  // First-appearance roast card.
  const renderAvatarRoastCard = (m: any) => (
    <Card>
      <div style={interactionCardBodyStyle}>
        {renderInteractionToggle(t("panel.interaction.module.avatarRoast.title"), !!configForm.values.avatar_roast_enabled, (v) => { applySettingsPatch({ avatar_roast_enabled: v }) })}
        <div style={interactionStatusRowStyle}>
          {roastBadge}
          <StatusBadge tone="warning" label={t("panel.interaction.tags.oncePerUid")} />
        </div>
        <Text>{t("panel.interaction.module.avatarRoast.desc")}</Text>
        {renderInteractionDisabledHint(!!configForm.values.avatar_roast_enabled, "panel.interaction.module.avatarRoast.disabledHint")}
        {renderInteractionDetailsButton("avatar_roast")}
      </div>
    </Card>
  )

  const renderDanmakuResponseCard = (m: any) => (
    <Card>
      <div style={interactionCardBodyStyle}>
        {renderInteractionToggle(t("panel.interaction.module.danmakuResponse.title"), !!configForm.values.danmaku_response_enabled, (v) => { applySettingsPatch({ danmaku_response_enabled: v }) })}
        <div style={interactionStatusRowStyle}>
          <StatusBadge tone={configForm.values.danmaku_response_enabled && m ? "success" : "default"} label={!configForm.values.danmaku_response_enabled ? t("panel.modules.off") : (m ? t("panel.interaction.module.danmakuResponse.badge") : t("panel.modules.soon"))} />
          <StatusBadge tone="info" label={t("panel.interaction.tags.currentDanmaku")} />
        </div>
        <Text>{t("panel.interaction.module.danmakuResponse.desc")}</Text>
        {renderInteractionDisabledHint(!!configForm.values.danmaku_response_enabled, "panel.interaction.module.danmakuResponse.disabledHint")}
        {renderInteractionDetailsButton("danmaku_response")}
      </div>
    </Card>
  )

  const renderLiveSupportEventsCard = (m: any) => (
    <Card>
      <div style={interactionCardBodyStyle}>
        {renderInteractionToggle(t("panel.interaction.module.liveSupportEvents.title"), !!configForm.values.live_support_events_enabled, (v) => { applySettingsPatch({ live_support_events_enabled: v }) })}
        <div style={interactionStatusRowStyle}>
          <StatusBadge tone={configForm.values.live_support_events_enabled && m ? "success" : "default"} label={!configForm.values.live_support_events_enabled ? t("panel.modules.off") : (m ? t("panel.interaction.module.liveSupportEvents.badge") : t("panel.modules.soon"))} />
          <StatusBadge tone="info" label={t("panel.interaction.tags.safetyRequired")} />
        </div>
        <Text>{t("panel.interaction.module.liveSupportEvents.desc")}</Text>
        {renderInteractionDisabledHint(!!configForm.values.live_support_events_enabled, "panel.interaction.module.liveSupportEvents.disabledHint")}
        {renderInteractionDetailsButton("live_support_events")}
      </div>
    </Card>
  )

  const renderIdleHostingCard = () => (
    <Card>
      <div style={interactionCardBodyStyle}>
        {renderInteractionToggle(t("panel.interaction.module.idleHosting.title"), !!configForm.values.idle_hosting_enabled, (v) => { applySettingsPatch({ idle_hosting_enabled: v }) })}
        <div style={interactionStatusRowStyle}>
          <StatusBadge tone={configForm.values.idle_hosting_enabled && idleHostingEligible ? "success" : "default"} label={configForm.values.idle_hosting_enabled ? t("panel.interaction.module.idleHosting.badge") : t("panel.modules.off")} />
          <StatusBadge tone={idleHostingCandidate ? "success" : "default"} label={dynamicLabel("idleHostingCandidate", "panel.idleHostingCandidate", idleHostingCandidate ? "true" : "false")} />
        </div>
        <Text>{t("panel.interaction.module.idleHosting.desc")}</Text>
        {renderInteractionDisabledHint(!!configForm.values.idle_hosting_enabled, "panel.interaction.module.idleHosting.disabledHint")}
        {renderInteractionDetailsButton("idle_hosting")}
      </div>
    </Card>
  )

  const renderWarmupHostingCard = () => (
    <Card>
      <div style={interactionCardBodyStyle}>
        {renderInteractionToggle(t("panel.interaction.module.warmupHosting.title"), !!configForm.values.warmup_hosting_enabled, (v) => { applySettingsPatch({ warmup_hosting_enabled: v }) })}
        <div style={interactionStatusRowStyle}>
          <StatusBadge tone={configForm.values.warmup_hosting_enabled && warmupHostingCandidate ? "success" : "default"} label={configForm.values.warmup_hosting_enabled ? t("panel.interaction.module.warmupHosting.badge") : t("panel.modules.off")} />
          <StatusBadge tone={warmupHostingCandidate ? "success" : "default"} label={dynamicLabel("warmupHostingCandidate", "panel.warmupHostingCandidate", warmupHostingCandidate ? "true" : "false")} />
        </div>
        <Text>{t("panel.interaction.module.warmupHosting.desc")}</Text>
        {renderInteractionDisabledHint(!!configForm.values.warmup_hosting_enabled, "panel.interaction.module.warmupHosting.disabledHint")}
        {renderInteractionDetailsButton("warmup_hosting")}
      </div>
    </Card>
  )

  const renderActiveEngagementCard = () => (
    <Card>
      <div style={interactionCardBodyStyle}>
        {renderInteractionToggle(t("panel.interaction.module.activeEngagement.title"), !!configForm.values.active_engagement_enabled, (v) => { applySettingsPatch({ active_engagement_enabled: v }) })}
        <div style={interactionStatusRowStyle}>
          <StatusBadge tone={configForm.values.active_engagement_enabled && activeEngagementEligible ? "success" : "default"} label={configForm.values.active_engagement_enabled ? t("panel.interaction.module.activeEngagement.badge") : t("panel.modules.off")} />
          <StatusBadge tone={activeEngagementCandidate ? "success" : "default"} label={dynamicLabel("activeEngagementCandidate", "panel.activeEngagementCandidate", activeEngagementCandidate ? "true" : "false")} />
        </div>
        <Text>{t("panel.interaction.module.activeEngagement.desc")}</Text>
        {renderInteractionDisabledHint(!!configForm.values.active_engagement_enabled, "panel.interaction.module.activeEngagement.disabledHint")}
        {renderInteractionDetailsButton("active_engagement")}
      </div>
    </Card>
  )

  const interactionDialogTitle = interactionDialog === "avatar_roast"
    ? t("panel.interaction.module.avatarRoast.title")
    : interactionDialog === "danmaku_response"
      ? t("panel.interaction.module.danmakuResponse.title")
      : interactionDialog === "live_support_events"
        ? t("panel.interaction.module.liveSupportEvents.title")
        : interactionDialog === "warmup_hosting"
          ? t("panel.interaction.module.warmupHosting.title")
          : interactionDialog === "idle_hosting"
            ? t("panel.interaction.module.idleHosting.title")
            : interactionDialog === "active_engagement"
              ? t("panel.interaction.module.activeEngagement.title")
              : ""

  const interactionDialogContent = interactionDialog === "avatar_roast" ? (
    <Stack>
      <Text>{t("panel.interaction.module.avatarRoast.desc")}</Text>
      <ToggleSwitch checked={!!configForm.values.avatar_analysis_enabled} disabled={!configForm.values.avatar_roast_enabled || settingsSaving} label={t("panel.interaction.module.avatarRoast.avatarAnalysis")} onChange={(v) => { applySettingsPatch({ avatar_analysis_enabled: v }) }} />
      <Text>{t("panel.interaction.module.avatarRoast.avatarAnalysisHint")}</Text>
      <StatusBadgeRow t={t} items={[
        { key: "panel.interaction.tags.currentDanmaku", tone: "success" },
        { key: "panel.interaction.tags.oncePerUid", tone: "warning" },
        { key: "panel.interaction.tags.safetyRequired" },
      ]} />
      {interactionModuleById.avatar_roast && Array.isArray(interactionModuleById.avatar_roast.config_schema) && interactionModuleById.avatar_roast.config_schema.length ? (
        <Stack gap={12}>
          {interactionModuleById.avatar_roast.config_schema.map((f: any, fi: number) => renderConfigField(f, fi))}
        </Stack>
      ) : null}
    </Stack>
  ) : interactionDialog === "danmaku_response" ? (
    <Stack>
      <Text>{t("panel.interaction.module.danmakuResponse.desc")}</Text>
      {interactionModuleById.danmaku_response ? <ModuleHealthBadge module={interactionModuleById.danmaku_response} t={t} /> : null}
      <StatusBadgeRow t={t} items={[
        { key: "panel.interaction.tags.currentDanmaku", tone: "success" },
        { key: "panel.interaction.tags.noAvatarCount", tone: "warning" },
        { key: "panel.interaction.tags.safetyRequired" },
      ]} />
    </Stack>
  ) : interactionDialog === "live_support_events" ? (
    <Stack>
      <Text>{t("panel.interaction.module.liveSupportEvents.desc")}</Text>
      {interactionModuleById.live_support_events ? <ModuleHealthBadge module={interactionModuleById.live_support_events} t={t} /> : null}
      <StatusBadgeRow t={t} items={[{ key: "panel.interaction.tags.safetyRequired" }]} />
    </Stack>
  ) : interactionDialog === "warmup_hosting" ? (
    <Stack>
      <Text>{t("panel.interaction.module.warmupHosting.desc")}</Text>
      <Grid cols={2}>
        <StatCard label={t("panel.liveState.title")} value={<StatusBadge tone={liveStateTone(liveStateName)} label={dynamicLabel("liveState", "panel.liveState", liveStateName)} />} />
        <StatCard label={t("panel.liveDirector.nextAutoAction")} value={<StatusBadge tone={liveDirectorEligible ? "success" : "default"} label={dynamicLabel("liveDirectorAction", "panel.liveDirector.action", liveDirectorNextAction)} />} />
      </Grid>
      <StatusBadgeRow t={t} items={[
        { key: "panel.interaction.tags.openingBeat", tone: "success" },
        { key: "panel.interaction.tags.safetyRequired" },
      ]} />
      <Button tone="info" disabled={!configForm.values.warmup_hosting_enabled || !!simpleActionPending} onClick={() => callSimple("trigger_warmup_hosting")}>{t("panel.actions.triggerWarmupHosting")}</Button>
    </Stack>
  ) : interactionDialog === "idle_hosting" ? (
    <Stack>
      <Text>{t("panel.interaction.module.idleHosting.desc")}</Text>
      <Grid cols={2}>
        <StatCard label={t("panel.idleHostingStatus.cooldown")} value={`${idleHostingCooldown.toFixed(1)}s`} />
        <StatCard label={t("panel.idleHostingStatus.minInterval")} value={`${idleHostingMinInterval.toFixed(1)}s`} />
      </Grid>
      <Grid cols={2}>
        <StatCard label={t("panel.liveState.lastViewerActivityAge")} value={liveStateLastViewerActivityAge} />
        <StatCard label={t("panel.liveState.lastOutputAge")} value={liveStateLastOutputAge} />
      </Grid>
      <Grid cols={2}>
        <StatCard label={t("panel.liveState.lastActivityAge")} value={liveStateLastActivityAge} />
        <StatCard label={t("panel.liveState.idleAfter")} value={liveStateIdleAfter} />
      </Grid>
      <Text>{dynamicLabel("idleHostingReason", "panel.idleHostingStatus.reason", idleHostingReason)}</Text>
      <StatusBadgeRow t={t} items={[
        { key: "panel.interaction.tags.cooldown", tone: "warning" },
        { key: "panel.interaction.tags.safetyRequired" },
      ]} />
    </Stack>
  ) : interactionDialog === "active_engagement" ? (
    <Stack>
      <Text>{t("panel.interaction.module.activeEngagement.desc")}</Text>
      <Text>{dynamicLabel("activeEngagementReason", "panel.activeEngagementStatus.reason", activeEngagementReason)}</Text>
      <Grid cols={2}>
        <StatCard label={t("panel.liveState.title")} value={<StatusBadge tone={liveStateTone(liveStateName)} label={dynamicLabel("liveState", "panel.liveState", liveStateName)} />} />
        <StatCard label={t("panel.liveState.quietAfter")} value={liveStateQuietAfter} />
      </Grid>
      <Grid cols={2}>
        <StatCard label={t("panel.idleHostingStatus.cooldown")} value={`${activeEngagementCooldown.toFixed(1)}s`} />
        <StatCard label={t("panel.idleHostingStatus.minInterval")} value={`${activeEngagementMinInterval.toFixed(1)}s`} />
      </Grid>
      <Grid cols={2}>
        <StatCard label={t("panel.activeEngagementStatus.minimumIntervalRemaining")} value={`${activeEngagementMinimumRemaining.toFixed(1)}s`} />
        <StatCard label={t("panel.activeEngagementStatus.recentDanmakuWait")} value={`${activeEngagementDanmakuWait.toFixed(1)}s`} />
      </Grid>
      {latestTopic ? <StatCard label={t("panel.interaction.currentDecision.topic")} value={latestTopic} /> : null}
      {latestHostBeat ? <StatCard label={t("panel.interaction.currentDecision.hostBeat")} value={latestHostBeat} /> : null}
      <StatusBadgeRow t={t} items={[
        { key: "panel.interaction.tags.activeQuestion", tone: "success" },
        { key: "panel.interaction.tags.safetyRequired" },
      ]} />
      <Button tone="info" disabled={!configForm.values.active_engagement_enabled || !!simpleActionPending} onClick={() => callSimple("trigger_active_engagement")}>{t("panel.actions.triggerActiveEngagement")}</Button>
    </Stack>
  ) : null

  const modulesSection = (
    <Stack>
      {currentDecisionCard}
      {renderInteractionGroupHeader(t("panel.interaction.group.audience"), t("panel.interaction.group.audienceHint"))}
      <div style={interactionCardGridStyle}>
        <ModuleRenderBoundary title={t("panel.interaction.module.avatarRoast.title")} render={() => renderAvatarRoastCard(interactionModuleById.avatar_roast)} t={t} />
        <ModuleRenderBoundary title={t("panel.interaction.module.danmakuResponse.title")} render={() => renderDanmakuResponseCard(interactionModuleById.danmaku_response)} t={t} />
        <ModuleRenderBoundary title={t("panel.interaction.module.liveSupportEvents.title")} render={() => renderLiveSupportEventsCard(interactionModuleById.live_support_events)} t={t} />
      </div>
      {renderInteractionGroupHeader(t("panel.interaction.group.hosting"), t("panel.interaction.group.hostingHint"))}
      <div style={interactionCardGridStyle}>
        <ModuleRenderBoundary title={t("panel.interaction.module.warmupHosting.title")} render={renderWarmupHostingCard} t={t} />
        <ModuleRenderBoundary title={t("panel.interaction.module.idleHosting.title")} render={renderIdleHostingCard} t={t} />
        <ModuleRenderBoundary title={t("panel.interaction.module.activeEngagement.title")} render={renderActiveEngagementCard} t={t} />
      </div>
      <Modal
        open={!!interactionDialog}
        title={interactionDialogTitle}
        size="lg"
        onClose={closeInteractionDialog}
        footer={<Button tone="default" onClick={() => { setInteractionDialog("") }}>{t("panel.actions.cancel")}</Button>}
      >
        <Stack gap={12}>
          <Alert tone="info">{t("panel.interaction.autoSaveHint")}</Alert>
          {interactionDialogContent}
        </Stack>
      </Modal>
    </Stack>
  )

  const viewerStore = safeState.viewer_store || {}
  const queuePresets = [
    { value: 3, labelKey: "panel.settings.queueCautious", hintKey: "panel.settings.queueCautiousHint" },
    { value: 5, labelKey: "panel.settings.queueStandard", hintKey: "panel.settings.queueStandardHint" },
    { value: 8, labelKey: "panel.settings.queueRelaxed", hintKey: "panel.settings.queueRelaxedHint" },
  ]
  const selectedQueueLimit = Number(configForm.values.queue_limit) || 5
  const selectedQueuePreset = queuePresets.find((preset) => preset.value === selectedQueueLimit)
  const advancedSection = (
    <Stack>
      <CompactTabs
        id="settings-sections"
        items={[
          {
            id: "safety",
            label: t("panel.settings.safetyTab"),
            content: (
              <Stack>
                <Card title={t("panel.settings.safetyTitle")}>
                  <Stack gap={10}>
                    <Text>{t("panel.settings.safetyHint")}</Text>
                    <ToggleSwitch
                      checked={!!configForm.values.safety_auto_stop_enabled}
                      disabled={settingsSaving}
                      label={t("panel.fields.autoStop")}
                      onChange={(value) => {
                        if (value) applySettingsPatch({ safety_auto_stop_enabled: true })
                        else setSafetyDisableConfirmOpen(true)
                      }}
                    />
                    <Alert tone={configForm.values.safety_auto_stop_enabled ? "success" : "warning"}>
                      {t(configForm.values.safety_auto_stop_enabled ? "panel.settings.autoStopEnabled" : "panel.settings.autoStopDisabled")}
                    </Alert>
                  </Stack>
                </Card>
                <Card title={t("panel.settings.queueTitle")}>
                  <Stack gap={10}>
                    <Text>{t("panel.settings.queueHint")}</Text>
                    <Grid cols={3}>
                      {queuePresets.map((preset) => (
                        <Button
                          key={preset.value}
                          tone={selectedQueueLimit === preset.value ? "primary" : "default"}
                          disabled={settingsSaving}
                          onClick={() => applySettingsPatch({ queue_limit: preset.value })}
                        >
                          {t(preset.labelKey)}
                        </Button>
                      ))}
                    </Grid>
                    <Text>{t(selectedQueuePreset?.hintKey || "panel.settings.queueCustomHint")}</Text>
                    <Button
                      tone="default"
                      disabled={settingsSaving}
                      onClick={() => applySettingsPatch({ safety_auto_stop_enabled: true, queue_limit: 5 })}
                    >
                      {t("panel.settings.restoreRecommended")}
                    </Button>
                  </Stack>
                </Card>
              </Stack>
            ),
          },
          {
            id: "privacy",
            label: t("panel.settings.privacyTab"),
            content: (
              <Card title={t("panel.settings.privacyTitle")}>
                <Stack gap={10}>
                  <Text>{t("panel.settings.privacyHint")}</Text>
                  <ToggleSwitch
                    checked={!!configForm.values.viewer_memory_enabled}
                    disabled={settingsSaving}
                    label={t("panel.settings.viewerMemoryLabel")}
                    onChange={(value) => applySettingsPatch({ viewer_memory_enabled: value })}
                  />
                  <Alert tone={configForm.values.viewer_memory_enabled ? "success" : "info"}>
                    {t(configForm.values.viewer_memory_enabled ? "panel.settings.viewerMemoryEnabledHint" : "panel.settings.viewerMemoryDisabledHint")}
                  </Alert>
                  <Text>{t("panel.settings.viewerMemoryRetentionHint")}</Text>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                    <Stack gap={4}>
                      <Text>{t("panel.settings.localStorageLabel")}</Text>
                      <Text>{t("panel.settings.localStorageHint")}</Text>
                    </Stack>
                    <StatusBadge
                      tone={viewerStore.writable === false ? "warning" : "success"}
                      label={t(viewerStore.writable === false ? "panel.settings.storageNeedsAttention" : "panel.settings.storageReady")}
                    />
                  </div>
                  {viewerStore.writable === false ? <Alert tone="warning">{t("panel.storage.notWritable")}</Alert> : null}
                  <Grid cols={2}>
                    <Button tone="default" onClick={() => { setStorageDetailsOpen(true) }}>{t("panel.settings.viewStoragePath")}</Button>
                    <Button tone="danger" onClick={() => { setClearViewerMemoryConfirmOpen(true) }}>{t("panel.actions.clearViewerProfiles")}</Button>
                  </Grid>
                </Stack>
              </Card>
            ),
          },
          {
            id: "help",
            label: t("panel.settings.helpTab"),
            content: (
              <Stack>
                <Card title={t("panel.onboarding.settingsTitle")}>
                  <Stack gap={8}>
                    <Text>{t("panel.onboarding.resetHint")}</Text>
                    <Button tone="default" onClick={resetOnboarding}>{t("panel.onboarding.reset")}</Button>
                  </Stack>
                </Card>
                <Card title={t("panel.dev.switch.title")}>
                  <Stack gap={8}>
                    <Text>{t("panel.settings.developerHint")}</Text>
                    <ToggleSwitch checked={!!configForm.values.developer_tools_enabled} disabled={developerPending} label={t("panel.fields.developerMode")} onChange={toggleDeveloperTools} />
                  </Stack>
                </Card>
              </Stack>
            ),
          },
        ]}
      />
      <Modal
        open={storageDetailsOpen}
        title={t("panel.settings.storagePathTitle")}
        onClose={() => { setStorageDetailsOpen(false) }}
        footer={<Button tone="default" onClick={() => { setStorageDetailsOpen(false) }}>{t("panel.actions.cancel")}</Button>}
      >
        <Stack>
          <Text>{t("panel.settings.storagePathHint")}</Text>
          <CodeBlock>{String(viewerStore.dir || "-")}</CodeBlock>
        </Stack>
      </Modal>
      <ConfirmDialog
        open={clearViewerMemoryConfirmOpen}
        title={t("panel.actions.clearViewerProfiles")}
        message={t("panel.messages.clearViewerProfilesConfirm")}
        tone="danger"
        confirmLabel={t("panel.actions.clearViewerProfiles")}
        cancelLabel={t("panel.actions.cancel")}
        onConfirm={clearViewerMemory}
        onCancel={() => setClearViewerMemoryConfirmOpen(false)}
      />
      <ConfirmDialog
        open={safetyDisableConfirmOpen}
        title={t("panel.settings.disableSafetyTitle")}
        message={t("panel.settings.disableSafetyMessage")}
        tone="danger"
        confirmLabel={t("panel.settings.disableSafetyConfirm")}
        cancelLabel={t("panel.actions.cancel")}
        onConfirm={() => {
          setSafetyDisableConfirmOpen(false)
          applySettingsPatch({ safety_auto_stop_enabled: false })
        }}
        onCancel={() => setSafetyDisableConfirmOpen(false)}
      />
    </Stack>
  )

  const dataSection = (
    <CompactTabs
      id="audience-data"
      items={[
        {
          id: "session",
          label: t("panel.audience.sessionTab"),
          content: <LiveSessionSection t={t} session={liveSession} locale={props.locale} />,
        },
        {
          id: "profiles",
          label: t("panel.audience.profilesTab"),
          content: (
            <ViewerProfilesTable
              t={t}
              profiles={profiles}
              locale={props.locale}
              onResetImpression={(uid) => runViewerProfileAction("reset_viewer_impression", uid)}
              onDeleteProfile={(uid) => runViewerProfileAction("delete_viewer_profile", uid)}
            />
          ),
        },
      ]}
    />
  )

  const lookupIdentity = lookupResult?.identity || null
  const lookupAvatarSrc = lookupIdentity?.avatar_preview_url || lookupIdentity?.avatar_url || lookupResult?.profile?.avatar_url || ""
  const lookupSourceLabel = !lookupIdentity
    ? "-"
    : lookupIdentity.fetched
      ? t("panel.dev.lookup.sourceFetched")
      : t("panel.dev.lookup.sourceProvided")
  const emitterUid = sandboxForm.values.uid.trim() || String(lookupIdentity?.uid || "").trim() || presetViewer.uid
  const emitterNickname =
    sandboxForm.values.nickname.trim() ||
    String(lookupIdentity?.nickname || lookupIdentity?.name || "").trim() ||
    presetViewerNickname
  const emitterAvatar = sandboxForm.values.avatar_url.trim() || String(lookupIdentity?.avatar_url || "").trim()
  const emitterAvatarSrc = sandboxForm.values.avatar_url.trim() || lookupAvatarSrc
  const emitterDanmaku = sandboxForm.values.danmaku_text.trim() || presetViewerDanmaku

  const developerSandbox = (
    <Stack>
      <Card title={t("panel.dev.switch.title")}>
        <Stack>
          <ToggleSwitch checked={developerToolsEnabled} disabled={developerPending} label={t("panel.fields.developerMode")} onChange={toggleDeveloperTools} />
          {!developerToolsEnabled ? <Alert tone="info">{t("panel.dev.developerModeDisabled")}</Alert> : null}
        </Stack>
      </Card>
      <CompactTabs
        id="developer-tools"
        items={[
          {
            id: "identity",
            label: t("panel.dev.lookup.title"),
            content: (
              <Card title={t("panel.dev.lookup.title")}>
                <Stack>
                  <Grid cols={3}>
                    <Field label={t("panel.fields.target")}>
                      <Input value={sandboxForm.values.target} placeholder="https://space.bilibili.com/123456" onChange={(value) => sandboxForm.setField("target", value)} />
                    </Field>
                    <Button tone="info" disabled={!developerToolsEnabled} onClick={lookupSandbox}>{t("panel.actions.lookupSandbox")}</Button>
                  </Grid>
                  <Grid cols={4}>
                    <AvatarPreview src={lookupAvatarSrc} alt={t("panel.dev.lookup.avatarAlt")} />
                    <Stack>
                      <Text>UID: {lookupIdentity?.uid || "-"}</Text>
                      <Text>{t("panel.columns.name")}: {lookupIdentity?.name || lookupIdentity?.nickname || "-"}</Text>
                      <Text>{t("panel.columns.nickname")}: {lookupIdentity?.nickname || "-"}</Text>
                      <Text>{t("panel.columns.email")}: {lookupIdentity?.email || t("panel.dev.lookup.emailUnavailable")}</Text>
                    </Stack>
                    <Stack>
                      <Text>{t("panel.dev.lookup.avatarMime")}: {lookupIdentity?.avatar_mime || "-"}</Text>
                      <Text>{t("panel.dev.lookup.source")}: {lookupSourceLabel}</Text>
                    </Stack>
                    <Stack>
                      <Text>{lookupIdentity?.avatar_url || "-"}</Text>
                      {!lookupIdentity ? <Text>{t("panel.dev.lookup.empty")}</Text> : null}
                    </Stack>
                  </Grid>
                </Stack>
              </Card>
            ),
          },
          {
            id: "event",
            label: t("panel.dev.emitter.title"),
            content: (
              <Stack>
                <Card title={t("panel.dev.emitter.title")}>
                  <Stack>
                    <Field label={t("panel.fields.danmaku")}>
                      <Input value={sandboxForm.values.danmaku_text} placeholder={presetViewerDanmaku} onChange={(value) => sandboxForm.setField("danmaku_text", value)} />
                    </Field>
                    <Grid cols={3}>
                      <Field label={t("panel.fields.overrideUid")}>
                        <Input value={sandboxForm.values.uid} onChange={(value) => sandboxForm.setField("uid", value)} />
                      </Field>
                      <Field label={t("panel.fields.overrideNickname")}>
                        <Input value={sandboxForm.values.nickname} onChange={(value) => sandboxForm.setField("nickname", value)} />
                      </Field>
                      <Field label={t("panel.fields.overrideAvatarUrl")}>
                        <Input value={sandboxForm.values.avatar_url} onChange={(value) => sandboxForm.setField("avatar_url", value)} />
                      </Field>
                    </Grid>
                    <Grid cols={3}>
                      <AvatarPreview src={emitterAvatar ? emitterAvatarSrc : ""} alt={t("panel.dev.lookup.avatarAlt")} />
                      <Stack>
                        <Text>{lookupIdentity ? t("panel.dev.emitter.usingLookup") : t("panel.dev.emitter.noLookup")}</Text>
                        <Text>UID: {emitterUid || "-"}</Text>
                        <Text>{t("panel.columns.nickname")}: {emitterNickname || "-"}</Text>
                        <Text>{t("panel.fields.danmaku")}: {emitterDanmaku}</Text>
                      </Stack>
                      <Text>{t("panel.dev.emitter.overrideHint")}</Text>
                    </Grid>
                    <Grid cols={3}>
                      <Button tone="primary" disabled={!developerToolsEnabled} onClick={submitSandbox}>{t("panel.actions.submitSandbox")}</Button>
                      <Button tone="success" disabled={!developerToolsEnabled} onClick={runDemoCase}>{t("panel.actions.runDemo")}</Button>
                      <Button tone="danger" onClick={clearSandboxData}>{t("panel.actions.clearSandbox")}</Button>
                    </Grid>
                  </Stack>
                </Card>
                <Card title={t("panel.dev.result")}>
                  {sandboxResult ? <JsonView data={sandboxResult} /> : <Text>{t("panel.empty.sandbox")}</Text>}
                </Card>
              </Stack>
            ),
          },
          {
            id: "results",
            label: t("panel.dev.runtimeResults"),
            content: (
              <Stack>
                <LiveExplainSection
                  t={t}
                  dynamicLabel={dynamicLabel}
                  liveExplain={liveExplain}
                  speechSummary={speechSummary}
                  speechReason={speechReason}
                />
                <RecentResultsTable t={t} results={results} />
                <Card title={t("panel.advanced.title")}>
                  <Stack>
                    <Grid cols={2}>
                      <StatCard label={t("panel.stats.queue")} value={`${safety.queue_size || 0}/${safety.queue_limit || config.queue_limit || 0}`} />
                      <StatCard label={t("panel.stats.safety")} value={<StatusBadge tone={statusTone(String(safety.status || ""))} label={dynamicLabel("safety", "panel.safety", String(safety.status || "unknown"))} />} />
                    </Grid>
                    {audit.length ? (
                      <DataTable
                        data={audit.slice(0, 5).map((item, index) => ({ ...item, id: `${item.at || index}-${index}` }))}
                        rowKey="id"
                        columns={[
                          { key: "at", label: t("panel.columns.time") },
                          { key: "level", label: t("panel.columns.level") },
                          { key: "op", label: t("panel.columns.op") },
                          { key: "message", label: t("panel.columns.message") },
                        ]}
                      />
                    ) : null}
                  </Stack>
                </Card>
                <ModuleOverviewCard modules={modules} t={t} />
                <Card title={t("panel.dev.recentSandbox")}>
                  {sandboxResults.length ? (
                    <DataTable
                      data={sandboxResults.map((item, index) => ({ ...item, id: `${item.created_at || index}-${index}` }))}
                      rowKey="id"
                      columns={[
                        { key: "uid", label: "UID", render: (row: any) => row.uid || "-" },
                        { key: "nickname", label: t("panel.columns.nickname"), render: (row: any) => row.nickname || "-" },
                        { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "pushed" ? "success" : "warning"} label={localizedStatusCode(t, String(row.status || ""))} /> },
                        { key: "reason", label: t("panel.columns.reason"), render: (row: any) => row.reason || row.output || "-" },
                      ]}
                    />
                  ) : (
                    <Text>{t("panel.empty.sandboxResults")}</Text>
                  )}
                </Card>
              </Stack>
            ),
          },
        ]}
      />
    </Stack>
  )

  // Streamer-facing tabs stay focused on the four workflows that are available today.
  const tabItems = [
    { id: "console", label: t("panel.tabs.console"), content: consoleSection },
    { id: "interaction", label: t("panel.tabs.interaction"), content: modulesSection },
    { id: "viewers", label: t("panel.tabs.viewers"), content: dataSection },
    { id: "settings", label: t("panel.tabs.settings"), content: advancedSection },
  ]
  if (developerToolsEnabled) {
    tabItems.push({ id: "dev", label: t("panel.tabs.dev"), content: developerSandbox })
  }

  const onboardingSteps = [
    { title: t("panel.onboarding.welcomeTitle"), body: t("panel.onboarding.welcomeBody"), action: t("panel.onboarding.welcomeAction"), success: t("panel.onboarding.welcomeSuccess") },
    { title: t("panel.onboarding.accountTitle"), body: t("panel.onboarding.accountBody"), action: t("panel.onboarding.accountAction"), success: t("panel.onboarding.accountSuccess") },
    { title: t("panel.onboarding.roomTitle"), body: t("panel.onboarding.roomBody"), action: t("panel.onboarding.roomAction"), success: t("panel.onboarding.roomSuccess") },
    { title: t("panel.onboarding.liveTitle"), body: t("panel.onboarding.liveBody"), action: t("panel.onboarding.liveAction"), success: t("panel.onboarding.liveSuccess") },
  ]
  const currentOnboarding = onboardingSteps[Math.min(onboardingStep, onboardingSteps.length - 1)]

  return (
    <Page className="neko-live-page" title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <style>{`
        .neko-page.neko-live-page {
          animation: none !important;
          transform: none !important;
        }
      `}</style>
      {!safeState.store_enabled ? <Alert tone="warning">{t("panel.store.disabled")}</Alert> : null}
      <Modal
        open={onboardingOpen}
        title={t("panel.onboarding.title")}
        onClose={() => { setOnboardingOpen(false) }}
        footer={(
          <Grid cols={3}>
            <Button tone="default" onClick={() => { setOnboardingOpen(false) }}>{t("panel.actions.cancel")}</Button>
            <Button tone="default" disabled={onboardingStep === 0} onClick={() => { setOnboardingStep(Math.max(0, onboardingStep - 1)) }}>{t("panel.onboarding.previous")}</Button>
            {onboardingStep < onboardingSteps.length - 1 ? (
              <Button tone="info" onClick={() => { setOnboardingStep(onboardingStep + 1) }}>{t("panel.onboarding.next")}</Button>
            ) : (
              <Button tone="success" onClick={completeOnboarding}>{t("panel.onboarding.finish")}</Button>
            )}
          </Grid>
        )}
      >
        <Stack>
          <StatusBadge tone="info" label={`${onboardingStep + 1}/${onboardingSteps.length}`} />
          <Card title={currentOnboarding.title}>
            <Stack gap={8}>
              <Text>{currentOnboarding.body}</Text>
              <Alert tone="info">{t("panel.onboarding.actionLabel")}: {currentOnboarding.action}</Alert>
              <Alert tone="success">{t("panel.onboarding.successLabel")}: {currentOnboarding.success}</Alert>
            </Stack>
          </Card>
        </Stack>
      </Modal>
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={primaryStatusTone} label={primaryStatusLabel} />
          {showSafetyStatus ? <StatusBadge tone={statusTone(safetyStatus)} label={dynamicLabel("safety", "panel.safety", safetyStatus)} /> : null}
          <StatusBadge tone="info" label={`${t("panel.liveStatusSummary.cooldown")} · ${cooldownSeconds.toFixed(0)}s`} />
          <StatusBadge tone={queueSize > 0 ? "warning" : "default"} label={`${t("panel.stats.queue")} · ${queueSize}/${queueLimit}`} />
        </ToolbarGroup>
        <ToolbarGroup>
          {started ? (
            <Button tone={interactionPaused ? "primary" : "warning"} disabled={!!simpleActionPending} onClick={() => callSimple(interactionPaused ? "resume_roast" : "pause_roast")}>
              {interactionPaused ? t("panel.actions.resume") : t("panel.actions.pause")}
            </Button>
          ) : null}
          <Button tone="default" onClick={() => { refreshDashboard(true) }}>{t("panel.actions.refreshStatus")}</Button>
        </ToolbarGroup>
      </Toolbar>
      {refreshFailureCount >= 2 ? (
        <Alert tone="warning">{t("panel.messages.statusStale", { time: lastSuccessfulRefreshLabel })}</Alert>
      ) : null}
      <Tabs key={developerToolsEnabled ? "developer-enabled" : "developer-disabled"} items={tabItems} />
    </Page>
  )
}
