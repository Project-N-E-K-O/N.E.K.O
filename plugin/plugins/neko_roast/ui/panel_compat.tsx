// Main-branch compatibility entry. Generated from the modular panel sources.
// Keep ui/panel.tsx and sibling modules as the authored source of truth.

import {
  Alert,
  Button,
  Card,
  CodeBlock,
  DataTable,
  Field,
  Grid,
  Input,
  JsonView,
  Page,
  RefreshButton,
  Select,
  Stack,
  StatCard,
  StatusBadge,
  Tabs,
  Text,
  Toolbar,
  ToolbarGroup,
  useEffect,
  useForm,
  useState,
  useToast,
} from "@neko/plugin-ui"

type PluginSurfaceProps<TState = any> = {
  state?: TState
  t: (key: string) => string
  api: {
    call: (action: string, payload?: any) => Promise<any>
    refresh: () => Promise<any>
  }
  [key: string]: any
}

type PanelTranslator = (key: string) => string
type DynamicLabel = (group: string, keyPrefix: string, value: string) => string

/* bundled source: ui/panel_state.ts */
type RoastConfig = {
  live_platform?: string
  live_room_ref?: string
  live_room_id?: number
  live_enabled?: boolean
  developer_tools_enabled?: boolean
  live_mode?: string
  activity_level?: string
  roast_strength?: string
  roast_once_per_uid?: boolean
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
  config?: RoastConfig
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
  developer_tools_enabled: false,
  live_mode: "co_stream",
  activity_level: "standard",
  roast_strength: "normal",
  roast_once_per_uid: true,
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
  nickname: "Demo viewer",
  danmaku_text: "First time here, can you roast my avatar?",
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

function liveStateTone(state: string): "success" | "warning" | "danger" | "default" {
  if (state === "engaged" || state === "warmup") return "success"
  if (state === "quiet" || state === "idle" || state === "paused") return "warning"
  if (state === "blocked") return "danger"
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
  onLogin,
  onLoginCheck,
  onLogout,
}: {
  t: PanelTranslator
  loginState: any
  loginLoggedIn: boolean
  loginName: string
  loginUid: string
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
            <Button tone="info" onClick={onLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            <Button tone="danger" onClick={onLogout}>{t("panel.actions.biliLogout")}</Button>
          </Grid>
        ) : (
          <Stack>
            <Grid cols={2}>
              <Button tone="info" onClick={onLogin}>{t("panel.actions.biliLogin")}</Button>
              <Button tone="success" onClick={onLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            </Grid>
            {loginState?.qrcode_image ? (
              <Stack>
                {/* hosted-ui strips data: URLs from img src, so the QR code uses a CSS background image. */}
                <div
                  onClick={onLogin}
                  role="button"
                  title={t("panel.auth.refreshHint")}
                  style={{
                    width: "180px",
                    height: "180px",
                    boxSizing: "border-box",
                    padding: "8px",
                    borderRadius: "8px",
                    cursor: "pointer",
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

function ModuleOverviewCard({ modules, t }: { modules: Array<Record<string, any>>; t: PanelTranslator }) {
  return (
    <Card title={t("panel.tabs.modules")}>
      {modules.length ? (
        <DataTable
          data={modules.map((item: any, index: number) => ({ ...item, id: item.id || String(index) }))}
          rowKey="id"
          columns={[
            { key: "title", label: t("panel.modules.name"), render: (row: any) => row.title || row.id || "-" },
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
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "blocked" ? "warning" : row.status === "healthy" ? "success" : "default"} label={String(row.status || "-")} /> },
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
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "skipped" ? "warning" : row.status === "ok" ? "success" : "default"} label={String(row.status || "-")} /> },
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
            { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "pushed" ? "success" : "warning"} label={String(row.status || "-")} /> },
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
}: {
  t: PanelTranslator
  profiles: any[]
}) {
  return (
    <Card title={t("panel.profiles.title")}>
      {profiles.length ? (
        <DataTable
          data={profiles.map((item, index) => ({ ...item, id: item.uid || String(index) }))}
          rowKey="id"
          columns={[
            { key: "uid", label: "UID" },
            { key: "nickname", label: t("panel.columns.nickname") },
            { key: "roast_count", label: t("panel.columns.roastCount") },
            { key: "danmaku_count", label: t("panel.columns.danmakuCount"), render: (row: any) => row.danmaku_count || 0 },
            { key: "viewer_stage", label: t("panel.columns.viewerStage"), render: (row: any) => profileBadge("viewerStage", row.viewer_stage, t) },
            { key: "profile_confidence", label: t("panel.columns.profileConfidence"), render: (row: any) => profileBadge("profileConfidence", row.profile_confidence, t) },
            { key: "profile_freshness", label: t("panel.columns.profileFreshness"), render: (row: any) => profileBadge("profileFreshness", row.profile_freshness, t) },
            { key: "top_preference_tags", label: t("panel.columns.preferenceTags"), render: (row: any) => Array.isArray(row.top_preference_tags) ? row.top_preference_tags.map((item: any) => `${item.tag}:${item.count}`).join(", ") : "-" },
            { key: "top_favorite_topics", label: t("panel.columns.favoriteTopics"), render: (row: any) => Array.isArray(row.top_favorite_topics) ? row.top_favorite_topics.map((item: any) => `${item.tag}:${item.count}`).join(", ") : "-" },
            { key: "top_running_jokes", label: t("panel.columns.runningJokes"), render: (row: any) => Array.isArray(row.top_running_jokes) ? row.top_running_jokes.map((item: any) => `${item.tag}:${item.count}`).join(", ") : "-" },
            { key: "profile_summary", label: t("panel.columns.latestSummary"), render: (row: any) => row.impression_summary || row.profile_summary || row.last_interaction_summary || "-" },
            { key: "avoid_guidance", label: t("panel.columns.avoidGuidance"), render: (row: any) => row.avoid_guidance || "-" },
            { key: "reply_guidance", label: t("panel.columns.replyGuidance"), render: (row: any) => row.reply_guidance || "-" },
            { key: "last_seen_at", label: t("panel.columns.lastSeen") },
          ]}
        />
      ) : (
        <Text>{t("panel.empty.profiles")}</Text>
      )}
    </Card>
  )
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
export default function NekoRoastPanel(props: PluginSurfaceProps<DashboardState>) {
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
  const profiles = Array.isArray(safeState.recent_profiles) ? safeState.recent_profiles : []
  const results = Array.isArray(safeState.recent_results) ? safeState.recent_results : []
  const sandboxResults = Array.isArray(safeState.recent_sandbox_results) ? safeState.recent_sandbox_results : []
  const audit = Array.isArray(safeState.recent_audit) ? safeState.recent_audit : []
  const [sandboxResult, setSandboxResult] = useState<any>(null)
  const [lookupResult, setLookupResult] = useState<any>(null)
  const [liveRoomResult, setLiveRoomResult] = useState<any>(null)
  const [loginState, setLoginState] = useState<any>(null)
  const [douyinAuthState, setDouyinAuthState] = useState<any>(null)
  const toast = useToast()
  const configForm = useForm({ ...configDefaults })
  const sandboxForm = useForm({ ...sandboxDefaults })

  useEffect(() => {
    configForm.setValues({
      live_platform: String(config.live_platform || "bilibili"),
      live_room_ref: String(config.live_room_ref || config.live_room_id || ""),
      live_enabled: !!config.live_enabled,
      live_room_id: String(config.live_room_id || ""),
      douyin_cookie: configForm.values.douyin_cookie || "",
      douyin_uid: configForm.values.douyin_uid || "",
      douyin_nickname: configForm.values.douyin_nickname || "",
      developer_tools_enabled: !!config.developer_tools_enabled,
      live_mode: String(config.live_mode || "co_stream"),
      activity_level: String(config.activity_level || "standard"),
      roast_strength: String(config.roast_strength || "normal"),
      roast_once_per_uid: config.roast_once_per_uid !== false,
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
    config.live_room_id,
    config.developer_tools_enabled,
    config.live_mode,
    config.activity_level,
    config.roast_strength,
    config.roast_once_per_uid,
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
    const shouldRefresh =
      !!config.live_enabled ||
      !!connection.connected ||
      !!connection.listening ||
      state === "connected" ||
      state === "receiving"
    if (!shouldRefresh) return

    const timer = window.setInterval(() => {
      props.api.refresh().catch(() => {
        /* Status polling failures should not interrupt panel actions; the next poll will retry. */
      })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [config.live_enabled, connection.connected, connection.listening, connection.state])

  function advancedConfigPatch() {
    return {
      rate_limit_seconds: Number(configForm.values.rate_limit_seconds) || 0,
      queue_limit: Number(configForm.values.queue_limit) || 5,
      safety_auto_stop_enabled: configForm.values.safety_auto_stop_enabled,
      dry_run: configForm.values.dry_run,
      viewer_store_dir: configForm.values.viewer_store_dir.trim(),
      stream_theme: configForm.values.stream_theme.trim(),
      stream_goal: configForm.values.stream_goal.trim(),
      stream_columns: configForm.values.stream_columns.trim(),
      stream_avoid_topics: configForm.values.stream_avoid_topics.trim(),
    }
  }

  async function saveConfig(patch: Record<string, any> = {}) {
    const livePlatform = String(patch.live_platform ?? config.live_platform ?? configForm.values.live_platform ?? "bilibili")
    const normalizedRoomRef = (value: unknown) => {
      const roomRef = String(value ?? "").trim()
      return roomRef === "0" ? "" : roomRef
    }
    const hasPatchedPlatform = Object.prototype.hasOwnProperty.call(patch, "live_platform")
    const hasPatchedRoomRef = Object.prototype.hasOwnProperty.call(patch, "live_room_ref")
    const hasPatchedRoomId = Object.prototype.hasOwnProperty.call(patch, "live_room_id")
    const liveRoomRef = hasPatchedRoomRef || hasPatchedRoomId
      ? normalizedRoomRef(hasPatchedRoomRef ? patch.live_room_ref : patch.live_room_id)
      : (
          normalizedRoomRef(configForm.values.live_room_ref) ||
          normalizedRoomRef(config.live_room_ref) ||
          normalizedRoomRef(config.live_room_id) ||
          normalizedRoomRef(configForm.values.live_room_id)
        )
    const fullPayload = {
      live_platform: livePlatform,
      live_room_ref: liveRoomRef,
      live_enabled: configForm.values.live_enabled,
      live_room_id: livePlatform === "bilibili" ? liveRoomRef : 0,
      developer_tools_enabled: configForm.values.developer_tools_enabled,
      live_mode: configForm.values.live_mode,
      activity_level: configForm.values.activity_level,
      roast_strength: configForm.values.roast_strength,
      roast_once_per_uid: configForm.values.roast_once_per_uid,
      ...advancedConfigPatch(),
    }
    const patchedPayload = hasPatchedPlatform && !hasPatchedRoomRef && !hasPatchedRoomId
      ? patch
      : {
          ...patch,
          live_room_ref: liveRoomRef,
          live_room_id: livePlatform === "bilibili" ? liveRoomRef : 0,
        }
    const payload = Object.keys(patch).length
      ? patchedPayload
      : fullPayload
    try {
      await props.api.call("update_config", payload)
      await props.api.refresh()
      toast.success(t("panel.messages.saved"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  function switchLivePlatform(next: string) {
    if (next === livePlatform) return
    configForm.setField("live_platform", next)
    configForm.setField("live_room_ref", "")
    configForm.setField("live_room_id", "")
    configForm.setField("live_enabled", false)
    setLiveRoomResult(null)
    saveConfig({ live_platform: next, live_enabled: false })
  }

  async function lookupLiveRoom() {
    const roomRef = String(configForm.values.live_room_ref || configForm.values.live_room_id || "").trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    try {
      const envelope = await props.api.call("lookup_live_room", { room_id: roomRef })
      const result = unwrapActionResult(envelope)
      setLiveRoomResult(result)
      await props.api.refresh()
      if (result.ok) {
        toast.success(t("panel.messages.roomLookupDone"))
      } else {
        toast.warning(result.message || t("panel.messages.roomLookupFailed"))
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function connectRoom() {
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
    try {
      const result = unwrapActionResult(await props.api.call("connect_live_room", { room_id: roomRef }))
      await props.api.refresh()
      const nextConnection = result.connection || result
      if (nextConnection.connected || nextConnection.listening) {
        toast.success(t("panel.messages.connected"))
      } else {
        toast.warning(String(nextConnection.state || t("panel.connection.disconnected")))
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function biliLogin() {
    try {
      const result = unwrapActionResult(await props.api.call("bili_login"))
      setLoginState(result)
      if (result.status === "qrcode_ready") toast.info(t("panel.auth.scanHint"))
      else if (result.logged_in || result.status === "already_logged_in" || result.status === "done") {
        toast.success(t("panel.auth.loggedIn"))
        await props.api.refresh()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function biliLoginCheck() {
    try {
      const result = unwrapActionResult(await props.api.call("bili_login_check"))
      setLoginState(result)
      if (result.status === "done" || result.logged_in) {
        toast.success(t("panel.auth.loginDone"))
        await props.api.refresh()
      } else if (result.message) {
        toast.info(result.message)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function biliLogout() {
    try {
      const result = unwrapActionResult(await props.api.call("bili_logout"))
      setLoginState(result)
      toast.success(t("panel.auth.logoutDone"))
      await props.api.refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function douyinCookieStatus() {
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_status"))
      setDouyinAuthState(result)
      if (result.logged_in) toast.success(t("panel.douyinAuth.cookieReady"))
      else toast.info(t("panel.douyinAuth.cookieMissing"))
      await props.api.refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function douyinCookieValidate() {
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
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_validate", { room_ref: roomRef }))
      setDouyinAuthState(result)
      await props.api.refresh()
      if (result.valid) toast.success(t("panel.douyinAuth.cookieValid"))
      else toast.warning(result.message || t("panel.douyinAuth.cookieInvalid"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function douyinCookieImport() {
    const cookie = String(configForm.values.douyin_cookie || "").trim()
    if (!cookie) {
      toast.error(t("panel.douyinAuth.cookieRequired"))
      return
    }
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_import", {
        cookie,
        uid: String(configForm.values.douyin_uid || "").trim(),
        nickname: String(configForm.values.douyin_nickname || "").trim(),
      }))
      setDouyinAuthState(result)
      configForm.setField("douyin_cookie", "")
      await props.api.refresh()
      toast.success(result.saved ? t("panel.douyinAuth.cookieSaved") : t("panel.douyinAuth.cookieSaveFailed"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function douyinCookieDelete() {
    try {
      const result = unwrapActionResult(await props.api.call("douyin_cookie_delete"))
      setDouyinAuthState(result)
      await props.api.refresh()
      toast.success(t("panel.douyinAuth.cookieDeleted"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
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
    try {
      await props.api.call(action, {})
      await props.api.refresh()
      toast.success(t("panel.messages.done"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
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
      (!uid && !typedTarget ? presetViewer.nickname : "")
    const avatarUrl = sandboxForm.values.avatar_url.trim() || String(identity.avatar_url || "").trim()
    const target = uid ? "" : typedTarget || "__demo__"
    try {
      const envelope = await props.api.call("submit_viewer_event", {
        target,
        uid,
        nickname,
        avatar_url: avatarUrl,
        danmaku_text: sandboxForm.values.danmaku_text.trim() || presetViewer.danmaku_text,
      })
      const result = unwrapActionResult(envelope)
      setSandboxResult(result)
      await props.api.refresh()
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
      await props.api.refresh()
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
      await props.api.refresh()
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
      await props.api.refresh()
      toast.success(t("panel.messages.sandboxCleared"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function toggleDeveloperTools(value: boolean) {
    const previous = !!configForm.values.developer_tools_enabled
    configForm.setField("developer_tools_enabled", value)
    try {
      await props.api.call("update_config", {
        developer_tools_enabled: value,
      })
      await props.api.refresh()
      toast.success(value ? t("panel.messages.devEnabled") : t("panel.messages.devDisabled"))
    } catch (err) {
      configForm.setField("developer_tools_enabled", previous)
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const resultCounts = results.reduce(
    (acc, item) => {
      const status = String(item.status || "")
      if (status === "pushed") acc.pushed += 1
      else if (status === "skipped") acc.skipped += 1
      else if (status === "failed") acc.failed += 1
      return acc
    },
    { pushed: 0, skipped: 0, failed: 0 },
  )
  const liveStatusLabel = liveRoomResult?.live_status ? t(`panel.liveStatus.${liveRoomResult.live_status}`) : "-"
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
  const currentRoomRef = String(connection.room_ref || config.live_room_ref || config.live_room_id || "").trim()
  const lookupRoomRef = String(liveRoomResult?.room_ref || liveRoomResult?.room_id || "").trim()
  const roomLookupTone: "success" | "warning" = liveRoomResult?.ok ? "success" : "warning"
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

  const started = !!(connection.connected || config.live_enabled)
  const modules = Array.isArray(safeState.modules) ? safeState.modules : []

  // Main live-room console.
  const consoleSection = (
    <Stack>
      <Card title={t("panel.platform.title")}>
        <Field label={t("panel.fields.platform")}>
          <Select
            value={livePlatform}
            options={[
              { value: "bilibili", label: t("panel.platform.bilibili") },
              { value: "douyin", label: `${t("panel.platform.douyin")} ${t("panel.platform.incompleteSuffix")}` },
            ]}
            onChange={(value) => {
              switchLivePlatform(String(value))
            }}
          />
        </Field>
      </Card>
      {livePlatform === "douyin" ? (
        <Card title={t("panel.douyinAuth.title")}>
          <Stack>
            <Text>
              {douyinLoggedIn
                ? t("panel.douyinAuth.cookieReady") + (douyinNickname ? ": " + douyinNickname : "") + (douyinUid ? " (UID " + douyinUid + ")" : "")
                : t("panel.douyinAuth.cookieMissing")}
            </Text>
            {douyinSavedAt ? <Text>{t("panel.douyinAuth.savedAt")}: {douyinSavedAt}</Text> : null}
            {douyinValidationMessage ? <Text>{douyinValidationMessage}</Text> : null}
            {douyinValidationStatus ? <Text>{t("panel.room.liveStatus")}: {t(`panel.liveStatus.${douyinValidationStatus}`)}</Text> : null}
            <Field label={t("panel.fields.douyinCookie")}>
              <Input value={configForm.values.douyin_cookie} placeholder={t("panel.placeholders.douyinCookie")} onChange={(value) => configForm.setField("douyin_cookie", value)} />
            </Field>
            <Grid cols={2}>
              <Field label={t("panel.fields.douyinUid")}>
                <Input value={configForm.values.douyin_uid} onChange={(value) => configForm.setField("douyin_uid", value)} />
              </Field>
              <Field label={t("panel.fields.douyinNickname")}>
                <Input value={configForm.values.douyin_nickname} onChange={(value) => configForm.setField("douyin_nickname", value)} />
              </Field>
            </Grid>
            <Grid cols={4}>
              <Button tone="success" onClick={douyinCookieImport}>{t("panel.actions.douyinCookieImport")}</Button>
              <Button tone="info" onClick={douyinCookieStatus}>{t("panel.actions.douyinCookieStatus")}</Button>
              <Button tone="info" onClick={douyinCookieValidate}>{t("panel.actions.douyinCookieValidate")}</Button>
              <Button tone="danger" onClick={douyinCookieDelete}>{t("panel.actions.douyinCookieDelete")}</Button>
            </Grid>
            <Text>{t("panel.douyinAuth.manualHint")}</Text>
          </Stack>
        </Card>
      ) : (
        <AuthCard
          t={t}
          loginState={loginState}
          loginLoggedIn={loginLoggedIn}
          loginName={loginName}
          loginUid={loginUid}
          onLogin={biliLogin}
          onLoginCheck={biliLoginCheck}
          onLogout={biliLogout}
        />
      )}
      <Card title={t("panel.streamTheme.title")}>
        <Stack>
          <Field label={t("panel.fields.streamTheme")}>
            <Input value={configForm.values.stream_theme} onChange={(value) => configForm.setField("stream_theme", value)} />
          </Field>
          <Grid cols={3}>
            <Button tone="success" onClick={() => saveConfig(advancedConfigPatch())}>{t("panel.actions.save")}</Button>
          </Grid>
        </Stack>
      </Card>
      <Card title={t("panel.liveStatusSummary.title")}>
        <Stack>
          <Grid cols={3}>
            <StatCard
              label={t("panel.columns.status")}
              value={<StatusBadge tone={liveStatusTone(liveStatusSummary)} label={dynamicLabel("liveStatusSummary", "panel.liveStatusSummary", liveStatusSummary)} />}
            />
            <StatCard label={t("panel.columns.reason")} value={dynamicLabel("liveStatusReason", "panel.liveStatusReason", liveStatusReason)} />
            <StatCard label={t("panel.liveStatusSummary.cooldown")} value={`${liveStatusCooldown.toFixed(1)}s`} />
          </Grid>
          <Grid cols={3}>
            <StatCard label={t("panel.fields.mode")} value={dynamicLabel("liveModeRole", "panel.liveModeRole", liveMode)} />
            <StatCard label={t("panel.liveState.title")} value={<StatusBadge tone={liveStateTone(liveStateName)} label={dynamicLabel("liveState", "panel.liveState", liveStateName)} />} />
            <StatCard label={t("panel.columns.reason")} value={dynamicLabel("liveStateReason", "panel.liveStateReason", liveStateReason)} />
          </Grid>
          <Grid cols={3}>
            <StatCard label={t("panel.liveState.lastViewerActivityAge")} value={liveStateLastViewerActivityAge} />
            <StatCard label={t("panel.liveState.lastOutputAge")} value={liveStateLastOutputAge} />
            <StatCard label={t("panel.liveState.lastActivityAge")} value={liveStateLastActivityAge} />
          </Grid>
          <Grid cols={2}>
            <StatCard label={t("panel.liveState.quietAfter")} value={liveStateQuietAfter} />
            <StatCard label={t("panel.liveState.idleAfter")} value={liveStateIdleAfter} />
          </Grid>
          <Text>{dynamicLabel("liveModeRoleHint", "panel.liveModeRoleHint", liveModeRole)}</Text>
          <Alert tone={liveStatusTone(liveStatusSummary)}>
            {dynamicLabel("liveStatusSummary", "panel.liveStatusSummary", liveStatusSummary)} · {dynamicLabel("liveStatusReason", "panel.liveStatusReason", liveStatusReason)}
          </Alert>
          <Alert tone={idleHostingCandidate ? "success" : "info"}>
            {dynamicLabel("idleHostingCandidate", "panel.idleHostingCandidate", idleHostingCandidate ? "true" : "false")}
          </Alert>
          <Grid cols={3}>
            <StatCard
              label={t("panel.idleHostingStatus.title")}
              value={<StatusBadge tone={idleHostingEligible ? "success" : "warning"} label={dynamicLabel("idleHostingEligible", "panel.idleHostingStatus.eligible", idleHostingEligible ? "true" : "false")} />}
            />
            <StatCard label={t("panel.idleHostingStatus.cooldown")} value={`${idleHostingCooldown.toFixed(1)}s`} />
            <StatCard label={t("panel.idleHostingStatus.minInterval")} value={`${idleHostingMinInterval.toFixed(1)}s`} />
          </Grid>
          <Text>{dynamicLabel("idleHostingReason", "panel.idleHostingStatus.reason", idleHostingReason)}</Text>
          <Alert tone={speechExplanationTone(speechSummary)}>
            {t("panel.speechExplanation.title")} · {dynamicLabel("speechSummary", "panel.speechExplanation.summary", speechSummary)} · {dynamicLabel("speechReason", "panel.speechExplanation.reason", speechReason)}
          </Alert>
          {speechLastStatus ? (
            <Text>
              {t("panel.speechExplanation.lastResult")}: {speechLastStatus}
              {speechLastReason ? ` / ${speechLastReason}` : ""}
              {speechLastSource ? ` / ${speechLastSource}` : ""}
            </Text>
          ) : null}
        </Stack>
      </Card>
      <Card title={t("panel.soloTestReadiness.title")}>
        <Stack gap={12}>
          <Grid cols={2}>
            <StatCard
              label={t("panel.columns.status")}
              value={<StatusBadge tone={soloReadinessTone(soloTestReady, soloTestSummary)} label={dynamicLabel("soloReadinessSummary", "panel.soloTestReadiness.summary", soloTestSummary)} />}
            />
            <StatCard label={t("panel.soloTestReadiness.profileCount")} value={soloTestProfileCount} />
            <StatCard
              label={t("panel.liveDirector.nextAutoAction")}
              value={<StatusBadge tone={liveDirectorEligible ? "success" : "default"} label={dynamicLabel("liveDirectorAction", "panel.liveDirector.action", liveDirectorNextAction)} />}
            />
          </Grid>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "8px" }}>
            {soloTestItems.map((item: any) => {
              const id = String(item.id || "preflight")
              const status = String(item.status || "blocked")
              return (
                <div
                  key={id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "8px",
                    minHeight: "36px",
                    padding: "8px 10px",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                    background: "var(--surface)",
                  }}
                >
                  <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {dynamicLabel("soloReadinessItem", "panel.soloTestReadiness.item", id)}
                  </span>
                  <StatusBadge tone={soloReadinessItemTone(status)} label={dynamicLabel("soloReadinessStatus", "panel.soloTestReadiness.status", status)} />
                </div>
              )
            })}
          </div>
        </Stack>
      </Card>
      <Card title={t("panel.room.title")}>
        <Stack>
          <Field label={roomFieldLabel}>
            <Input
              value={configForm.values.live_room_ref}
              placeholder={roomPlaceholder}
              onChange={(value) => {
                configForm.setField("live_room_ref", value)
                configForm.setField("live_room_id", value)
              }}
            />
          </Field>
          {liveRoomResult ? (
            <Alert tone={roomLookupTone}>
              {liveRoomResult.ok
                ? t("panel.room.lookupOk") + ": " + (liveRoomResult.title || "-") + " / " + (liveRoomResult.anchor_name || "-") + " / " + liveStatusLabel
                : (liveRoomResult.message || t("panel.room.lookupFailed"))}
            </Alert>
          ) : null}
          {liveRoomResult ? (
            <Grid cols={4}>
              <StatCard label={t("panel.stats.room")} value={lookupRoomRef || "-"} />
              <StatCard label={t("panel.room.titleLabel")} value={liveRoomResult.title || "-"} />
              <StatCard label={t("panel.room.anchor")} value={liveRoomResult.anchor_name || "-"} />
              <StatCard label={t("panel.room.liveStatus")} value={<StatusBadge tone={liveRoomResult.live_status === "live" ? "success" : "default"} label={liveStatusLabel} />} />
            </Grid>
          ) : null}
          <Grid cols={5}>
            <StatCard label={t("panel.fields.platform")} value={livePlatformLabel} />
            <StatCard label={t("panel.stats.room")} value={currentRoomRef || "-"} />
            <StatCard label={t("panel.stats.connection")} value={<StatusBadge tone={connection.connected ? "success" : "warning"} label={connection.connected ? t("panel.connection.connected") : t("panel.connection.disconnected")} />} />
            <StatCard label={t("panel.stats.viewers")} value={connection.connected ? (connection.viewer_count ?? 0).toLocaleString() : "-"} />
            <StatCard label={t("panel.stats.safety")} value={<StatusBadge tone={statusTone(String(safety.status || ""))} label={dynamicLabel("safety", "panel.safety", String(safety.status || "unknown"))} />} />
          </Grid>
          {livePlatform === "douyin" && (connectionPlan || connectionLastError || reconnectState) ? (
            <Grid cols={3}>
              <StatCard label={t("panel.columns.status")} value={<StatusBadge tone={connection.state === "receiving" || connection.state === "connected" ? "success" : "warning"} label={String(connection.state || "-")} />} />
              <StatCard label={t("panel.columns.reason")} value={connectionLastError || String(connectionPlan?.message || "-")} />
              <StatCard label={t("panel.stats.queue")} value={connectionMissing.length ? connectionMissing.join(", ") : "-"} />
            </Grid>
          ) : null}
          {livePlatform === "douyin" && reconnectState ? (
            <Grid cols={3}>
              <StatCard label={t("panel.columns.status")} value={`${Number(reconnectState.retry_count || 0).toFixed(0)}/${Number(reconnectState.policy?.max_retries || 0).toFixed(0)}`} />
              <StatCard label={t("panel.liveDirector.cooldown")} value={`${Number(reconnectState.next_delay_seconds || 0).toFixed(1)}s`} />
              <StatCard label={t("panel.columns.reason")} value={String(reconnectState.last_reason || "-")} />
            </Grid>
          ) : null}
          {started ? (
            <Grid cols={3}>
              <Button tone="danger" onClick={() => callSimple("disconnect_live_room")}>{t("panel.actions.stop")}</Button>
              <Button tone="warning" onClick={() => callSimple("pause_roast")}>{t("panel.actions.pause")}</Button>
              <Button tone="primary" onClick={() => callSimple("resume_roast")}>{t("panel.actions.resume")}</Button>
            </Grid>
          ) : (
            <Grid cols={2}>
              <Button tone="info" onClick={lookupLiveRoom}>{t("panel.actions.lookupRoom")}</Button>
              <Button tone="success" onClick={connectRoom}>{t("panel.actions.start")}</Button>
            </Grid>
          )}
          <Grid cols={2}>
            <Field label={t("panel.fields.mode")}>
              <Select
                value={configForm.values.live_mode}
                options={[
                  { value: "co_stream", label: t("panel.mode.co") },
                  { value: "solo_stream", label: t("panel.mode.solo") },
                ]}
                onChange={(value) => {
                  const next = String(value)
                  configForm.setField("live_mode", next)
                  saveConfig({ live_mode: next })
                }}
              />
            </Field>
            <Field label={t("panel.fields.activityLevel")}>
              <Select
                value={configForm.values.activity_level}
                options={[
                  { value: "quiet", label: t("panel.activity.quiet") },
                  { value: "standard", label: t("panel.activity.standard") },
                  { value: "active", label: t("panel.activity.active") },
                ]}
                onChange={(value) => {
                  const next = String(value)
                  configForm.setField("activity_level", next)
                  saveConfig({ activity_level: next })
                }}
              />
            </Field>
          </Grid>
        </Stack>
      </Card>
    </Stack>
  )

  // Render module-declared config fields.
  const renderConfigField = (f: any, fi: number) => {
    const name = String((f && f.name) || "")
    const configKey = name as keyof RoastConfig
    const cur = config[name]
    const label = f && f.label ? t(f.label) : name
    const hint = f && f.hint ? t(f.hint) : ""
    if (f && f.type === "boolean") {
      return (
        <Stack gap={4}>
          <ToggleSwitch checked={cur === undefined ? !!f.default : !!cur} label={label} onChange={(v) => { configForm.setField(configKey, v); saveConfig({ [name]: v }) }} />
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
                  onClick={() => { configForm.setField(configKey, String(o.value)); saveConfig({ [name]: String(o.value) }) }}
                  style={{
                    padding: "6px 16px",
                    borderRadius: "999px",
                    cursor: "pointer",
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
        <Input value={String(cur === undefined ? ((f && f.default) ?? "") : cur)} onChange={(v) => { configForm.setField(configKey, v); saveConfig({ [name]: v }) }} />
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
  const roastEnabled = !!config.live_enabled
  const roastConnected = !!connection.connected
  const roastBadge = roastEnabled
    ? (roastConnected
        ? <StatusBadge tone="success" label={t("panel.modules.online")} />
        : <StatusBadge tone="warning" label={t("panel.modules.standby")} />)
    : <StatusBadge tone="default" label={t("panel.modules.off")} />

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
      <Stack gap={12}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0 }}>
            <span style={{ color: "var(--text)", fontSize: "15px", fontWeight: 720 }}>{t("panel.interaction.module.avatarRoast.title")}</span>
            <StatusBadge tone="success" label={t("panel.interaction.module.avatarRoast.badge")} />
            {roastBadge}
          </div>
          <ToggleSwitch checked={roastEnabled} tone="success" onChange={(v) => { configForm.setField("live_enabled", v); saveConfig({ live_enabled: v }) }} />
        </div>
        <Text>{t("panel.interaction.module.avatarRoast.desc")}</Text>
        <StatusBadgeRow t={t} items={[
          { key: "panel.interaction.tags.currentDanmaku", tone: "success" },
          { key: "panel.interaction.tags.oncePerUid", tone: "warning" },
          { key: "panel.interaction.tags.safetyRequired" },
        ]} />
        {m && Array.isArray(m.config_schema) && m.config_schema.length ? (
          <Stack gap={12}>
            {m.config_schema.map((f: any, fi: number) => renderConfigField(f, fi))}
          </Stack>
        ) : null}
      </Stack>
    </Card>
  )

  const renderDanmakuResponseCard = (m: any) => (
    <Card title={t("panel.interaction.module.danmakuResponse.title")}>
      <Stack gap={12}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <StatusBadge tone={m ? "success" : "warning"} label={m ? t("panel.interaction.module.danmakuResponse.badge") : t("panel.modules.soon")} />
          {m ? <ModuleHealthBadge module={m} t={t} /> : null}
        </div>
        <Text>{t("panel.interaction.module.danmakuResponse.desc")}</Text>
        <StatusBadgeRow t={t} items={[
          { key: "panel.interaction.tags.currentDanmaku", tone: "success" },
          { key: "panel.interaction.tags.noAvatarCount", tone: "warning" },
          { key: "panel.interaction.tags.safetyRequired" },
        ]} />
      </Stack>
    </Card>
  )

  const renderLiveSupportEventsCard = (m: any) => (
    <Card title={t("panel.interaction.module.liveSupportEvents.title")}>
      <Stack gap={12}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <StatusBadge tone={m ? "success" : "warning"} label={m ? t("panel.interaction.module.liveSupportEvents.badge") : t("panel.modules.soon")} />
          {m ? <ModuleHealthBadge module={m} t={t} /> : null}
        </div>
        <Text>{t("panel.interaction.module.liveSupportEvents.desc")}</Text>
        <StatusBadgeRow t={t} items={[
          { key: "panel.interaction.tags.safetyRequired" },
        ]} />
      </Stack>
    </Card>
  )

  const renderIdleHostingCard = () => (
    <Card title={t("panel.interaction.module.idleHosting.title")}>
      <Stack gap={12}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <StatusBadge tone={idleHostingEligible ? "success" : "warning"} label={t("panel.interaction.module.idleHosting.badge")} />
          <StatusBadge tone={idleHostingCandidate ? "success" : "default"} label={dynamicLabel("idleHostingCandidate", "panel.idleHostingCandidate", idleHostingCandidate ? "true" : "false")} />
        </div>
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
    </Card>
  )

  const renderWarmupHostingCard = () => (
    <Card title={t("panel.interaction.module.warmupHosting.title")}>
      <Stack gap={12}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <StatusBadge tone={warmupHostingCandidate ? "success" : "default"} label={t("panel.interaction.module.warmupHosting.badge")} />
          <StatusBadge tone={warmupHostingCandidate ? "success" : "default"} label={dynamicLabel("warmupHostingCandidate", "panel.warmupHostingCandidate", warmupHostingCandidate ? "true" : "false")} />
        </div>
        <Text>{t("panel.interaction.module.warmupHosting.desc")}</Text>
        <Grid cols={2}>
          <StatCard label={t("panel.liveState.title")} value={<StatusBadge tone={liveStateTone(liveStateName)} label={dynamicLabel("liveState", "panel.liveState", liveStateName)} />} />
          <StatCard label={t("panel.liveDirector.nextAutoAction")} value={<StatusBadge tone={liveDirectorEligible ? "success" : "default"} label={dynamicLabel("liveDirectorAction", "panel.liveDirector.action", liveDirectorNextAction)} />} />
        </Grid>
        <StatusBadgeRow t={t} items={[
          { key: "panel.interaction.tags.openingBeat", tone: "success" },
          { key: "panel.interaction.tags.safetyRequired" },
        ]} />
        <Button tone="info" onClick={() => callSimple("trigger_warmup_hosting")}>{t("panel.actions.triggerWarmupHosting")}</Button>
      </Stack>
    </Card>
  )

  const renderActiveEngagementCard = () => (
    <Card title={t("panel.interaction.module.activeEngagement.title")}>
      <Stack gap={12}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <StatusBadge tone={activeEngagementEligible ? "success" : "warning"} label={t("panel.interaction.module.activeEngagement.badge")} />
          <StatusBadge tone={activeEngagementCandidate ? "success" : "default"} label={dynamicLabel("activeEngagementCandidate", "panel.activeEngagementCandidate", activeEngagementCandidate ? "true" : "false")} />
        </div>
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
        <StatusBadgeRow t={t} items={[
          { key: "panel.interaction.tags.activeQuestion", tone: "success" },
          { key: "panel.interaction.tags.safetyRequired" },
        ]} />
        <Button tone="info" onClick={() => callSimple("trigger_active_engagement")}>{t("panel.actions.triggerActiveEngagement")}</Button>
      </Stack>
    </Card>
  )

  const modulesSection = (
    <Stack>
      {currentDecisionCard}
      <ModuleRenderBoundary title={t("panel.interaction.module.avatarRoast.title")} render={() => renderAvatarRoastCard(interactionModuleById.avatar_roast)} t={t} />
      <ModuleRenderBoundary title={t("panel.interaction.module.danmakuResponse.title")} render={() => renderDanmakuResponseCard(interactionModuleById.danmaku_response)} t={t} />
      <ModuleRenderBoundary title={t("panel.interaction.module.liveSupportEvents.title")} render={() => renderLiveSupportEventsCard(interactionModuleById.live_support_events)} t={t} />
      <ModuleRenderBoundary title={t("panel.interaction.module.warmupHosting.title")} render={renderWarmupHostingCard} t={t} />
      <ModuleRenderBoundary title={t("panel.interaction.module.idleHosting.title")} render={renderIdleHostingCard} t={t} />
      <ModuleRenderBoundary title={t("panel.interaction.module.activeEngagement.title")} render={renderActiveEngagementCard} t={t} />
    </Stack>
  )

  const viewerStore = safeState.viewer_store || {}
  const advancedSection = (
    <Stack>
      <Card title={t("panel.control.title")}>
        <Stack>
          {/* live_enabled is owned by the interaction module card; settings keep platform-level controls only. */}
          <Grid cols={2}>
            <ToggleSwitch checked={!!configForm.values.dry_run} label={t("panel.fields.dryRun")} onChange={(value) => configForm.setField("dry_run", value)} />
            <ToggleSwitch checked={!!configForm.values.safety_auto_stop_enabled} label={t("panel.fields.autoStop")} onChange={(value) => configForm.setField("safety_auto_stop_enabled", value)} />
          </Grid>
          <Grid cols={2}>
            <Field label={t("panel.fields.rateLimit")}>
              <Input value={configForm.values.rate_limit_seconds} onChange={(value) => configForm.setField("rate_limit_seconds", value)} />
            </Field>
            <Field label={t("panel.fields.queueLimit")}>
              <Input value={configForm.values.queue_limit} onChange={(value) => configForm.setField("queue_limit", value)} />
            </Field>
          </Grid>
          <Grid cols={4}>
            <Button tone="success" onClick={() => saveConfig(advancedConfigPatch())}>{t("panel.actions.save")}</Button>
            <Button tone="info" onClick={() => callSimple("clear_queue")}>{t("panel.actions.clearQueue")}</Button>
          </Grid>
        </Stack>
      </Card>
      <Card title={t("panel.storage.title")}>
        <Stack>
          {/* Show the effective profile directory as code text so long paths remain readable. */}
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ color: "var(--muted)", fontSize: "13px", fontWeight: 650 }}>{t("panel.storage.current")}</span>
            <StatusBadge tone={viewerStore.using_custom ? "info" : "success"} label={viewerStore.using_custom ? t("panel.storage.isCustom") : t("panel.storage.isDefault")} />
          </div>
          <CodeBlock>{String(viewerStore.dir || "-")}</CodeBlock>
          {viewerStore.writable === false ? <Alert tone="warning">{t("panel.storage.notWritable")}</Alert> : null}
          <Alert tone="warning">{t("panel.storage.disabled")}</Alert>
        </Stack>
      </Card>
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
      <Card title={t("panel.dev.switch.title")}>
        <ToggleSwitch checked={!!configForm.values.developer_tools_enabled} label={t("panel.fields.developerMode")} onChange={toggleDeveloperTools} />
      </Card>
    </Stack>
  )

  const dataSection = (
    <Stack>
      <Grid cols={4}>
        <StatCard label={t("panel.summary.total")} value={results.length} />
        <StatCard label={t("panel.summary.pushed")} value={resultCounts.pushed} />
        <StatCard label={t("panel.summary.skipped")} value={resultCounts.skipped} />
        <StatCard label={t("panel.summary.failed")} value={resultCounts.failed} />
      </Grid>
      <LiveExplainSection
        t={t}
        dynamicLabel={dynamicLabel}
        liveExplain={liveExplain}
        speechSummary={speechSummary}
        speechReason={speechReason}
      />
      <RecentResultsTable t={t} results={results} />
      <ViewerProfilesTable
        t={t}
        profiles={profiles}
      />
    </Stack>
  )

  // Reserved tabs stay visible but clearly marked as coming soon.
  const dmSection = <ComingSoonSection title={t("panel.tabs.dm")} desc={t("panel.dm.desc")} t={t} />
  const automationSection = <ComingSoonSection title={t("panel.tabs.automation")} desc={t("panel.automation.desc")} t={t} />

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
    presetViewer.nickname
  const emitterAvatar = sandboxForm.values.avatar_url.trim() || String(lookupIdentity?.avatar_url || "").trim()
  const emitterAvatarSrc = sandboxForm.values.avatar_url.trim() || lookupAvatarSrc
  const emitterDanmaku = sandboxForm.values.danmaku_text.trim() || presetViewer.danmaku_text

  const developerSandbox = (
    <Stack>
      <Card title={t("panel.dev.switch.title")}>
        <Stack>
          <ToggleSwitch checked={developerToolsEnabled} label={t("panel.fields.developerMode")} onChange={toggleDeveloperTools} />
          {!developerToolsEnabled ? <Alert tone="info">{t("panel.dev.developerModeDisabled")}</Alert> : null}
        </Stack>
      </Card>

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

      <Card title={t("panel.dev.emitter.title")}>
        <Stack>
          <Field label={t("panel.fields.danmaku")}>
            <Input value={sandboxForm.values.danmaku_text} placeholder={presetViewer.danmaku_text} onChange={(value) => sandboxForm.setField("danmaku_text", value)} />
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

      <Card title={t("panel.dev.recentSandbox")}>
        {sandboxResults.length ? (
          <DataTable
            data={sandboxResults.map((item, index) => ({ ...item, id: `${item.created_at || index}-${index}` }))}
            rowKey="id"
            columns={[
              { key: "uid", label: "UID", render: (row: any) => row.uid || "-" },
              { key: "nickname", label: t("panel.columns.nickname"), render: (row: any) => row.nickname || "-" },
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "pushed" ? "success" : "warning"} label={String(row.status || "-")} /> },
              { key: "reason", label: t("panel.columns.reason"), render: (row: any) => row.reason || row.output || "-" },
            ]}
          />
        ) : (
          <Text>{t("panel.empty.sandboxResults")}</Text>
        )}
      </Card>

    </Stack>
  )

  // Lifecycle/domain tabs: six stable pages plus developer sandbox when dev mode is enabled.
  // Top-level dashboard tabs.
  const tabItems = [
    { id: "console", label: t("panel.tabs.console"), content: consoleSection },
    { id: "interaction", label: t("panel.tabs.interaction"), content: modulesSection },
    { id: "viewers", label: t("panel.tabs.viewers"), content: dataSection },
    { id: "dm", label: t("panel.tabs.dm"), content: dmSection },
    { id: "automation", label: t("panel.tabs.automation"), content: automationSection },
    { id: "settings", label: t("panel.tabs.settings"), content: advancedSection },
  ]
  if (developerToolsEnabled) {
    tabItems.push({ id: "dev", label: t("panel.tabs.dev"), content: developerSandbox })
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      {!safeState.store_enabled ? <Alert tone="warning">{t("panel.store.disabled")}</Alert> : null}
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge
            tone={liveStatusTone(liveStatusSummary)}
            label={dynamicLabel("liveStatusSummary", "panel.liveStatusSummary", liveStatusSummary)}
          />
          <StatusBadge tone={liveStateTone(liveStateName)} label={dynamicLabel("liveState", "panel.liveState", liveStateName)} />
          <StatusBadge tone={statusTone(String(safety.status || ""))} label={dynamicLabel("safety", "panel.safety", String(safety.status || "unknown"))} />
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton label={t("panel.actions.refreshStatus")} />
        </ToolbarGroup>
      </Toolbar>
      <Tabs items={tabItems} />
    </Page>
  )
}
