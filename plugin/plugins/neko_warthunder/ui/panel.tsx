/** War Thunder 猫娘副驾驶 Hosted UI。 */

import {
  ActionButton,
  Alert,
  Button,
  ButtonGroup,
  Field,
  Input,
  Modal,
  RefreshButton,
  Stack,
  StatusBadge,
  Tip,
  Warning,
  useEffect,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type PanelTone = "primary" | "success" | "warning" | "danger" | "info" | "default"

type SafetyState = {
  status?: string
  manual_paused?: boolean
  auto_paused?: boolean
  failures?: number
}

type IdentityState = {
  player_name?: string | null
  saved_player_name?: string | null
  self?: {
    name?: string | null
    source?: string | null
    confidence?: number | null
  } | null
}

type DataLayerState = {
  mode?: string
  url?: string
  pid?: number | null
  started_by_plugin?: boolean
  health?: boolean
  last_error?: string | null
}

type TelemetryState = {
  age_seconds?: number | null
  ias_kmh?: number | null
  mach?: number | null
  altitude_m?: number | null
  radio_altitude_m?: number | null
  climb_ms?: number | null
  fuel_fraction?: number | null
  flags?: Record<string, boolean>
}

type TakeoffProtectionState = {
  active?: boolean
  radio_altitude_m?: number | null
  radio_altitude_available?: boolean
  enter_m?: number | null
  exit_m?: number | null
  low_alt_grace_seconds?: number | null
  suppresses?: string[]
}

type ObserveRecord = {
  ts?: number | string | null
  trace_id?: string | null
  event_id?: string | null
  stage?: string | null
  outcome?: string | null
  reason?: string | null
  scenario?: string | null
  safety_status?: string | null
  dry_run?: boolean | null
  pushed?: boolean | null
}

type ObserveState = {
  last_event?: ObserveRecord | null
  last_decision?: ObserveRecord | null
  last_output_status?: ObserveRecord | null
  recent_timeline?: ObserveRecord[]
  observability_enabled?: boolean
}

type AwarenessState = {
  proximity_event_count?: number
  latest_proximity?: {
    kind?: string | null
    target_type?: string | null
    category?: string | null
    is_air?: boolean | null
    distance_m?: number | null
    compass?: string | null
    clock?: number | null
  } | null
  situation?: {
    enemy_count?: number | null
    ally_count?: number | null
    air_threat_count?: number | null
    ground_target_count?: number | null
  }
  nearest_ground_target?: {
    kind?: string | null
    grid?: string | null
    distance_m?: number | null
    bearing_deg?: number | null
    relative_deg?: number | null
  } | null
}

type OutputPolicyState = {
  dialogue_intrusion_mode?: string | null
  user_chat_quiet_window_seconds?: number | null
  battle_output_quiet_window_seconds?: number | null
  critical_bypass_quiet_window?: boolean | null
}

type OnboardingState = {
  completed?: boolean
  required?: boolean
  trigger?: string
}

type DashboardState = {
  enabled?: boolean
  dry_run?: boolean
  connected?: boolean
  conn_state?: string
  in_battle?: boolean
  game_context_active?: boolean
  dead?: boolean
  domain?: string
  domain_label?: string | null
  vehicle_type?: string | null
  profile_matched?: boolean | null
  profile_source?: string | null
  profile_family?: string | null
  scenario?: string
  level?: string
  onboarding?: OnboardingState
  identity?: IdentityState
  data_layer?: DataLayerState
  telemetry?: TelemetryState
  takeoff_protection?: TakeoffProtectionState
  output_policy?: OutputPolicyState
  awareness?: AwarenessState
  safety?: SafetyState
  observe?: ObserveState
}

type PanelSummary = {
  kind: "error" | "safety" | "paused" | "waiting" | "detect" | "identity" | "live"
  tone: PanelTone
  title: string
  detail: string
  modeLabel: string
}

type ActivityItem = {
  key: string
  time: string
  title: string
  detail: string
  status: string
  tone: PanelTone
}

const DOMAIN_LABELS: Record<string, string> = {
  air: "空战",
  heli: "直升机",
  ground: "陆战",
  naval: "海战",
  menu: "菜单",
  unknown: "未知模式",
}

const SCENARIO_LABELS: Record<string, string> = {
  OUT_OF_BATTLE: "战斗外",
  SPAWNING: "出生/进场",
  IN_FLIGHT: "战斗中",
  COMBAT_STRESS: "交战中",
  CRITICAL_RISK: "危急状态",
  DEAD: "已阵亡",
  BATTLE_ENDED: "战斗结束",
}

const DATA_LAYER_LABELS: Record<string, string> = {
  managed: "插件托管",
  external: "外部运行",
  starting: "正在启动",
  missing: "未连接",
  failed: "启动失败",
  stopped: "已停止",
  disabled: "未启用",
  unknown: "未知",
}

const RISK_LEVEL_LABELS: Record<string, string> = {
  info: "信息",
  normal: "正常",
  warning: "注意",
  danger: "危险",
  critical: "危急",
}

const SAFETY_STATUS_LABELS: Record<string, string> = {
  running: "正常",
  paused: "已手动暂停",
  tripped: "已自动急停",
  ok: "正常",
}

const DIALOGUE_OPTIONS = [
  { value: "no_interrupt", label: "不打断当前对话" },
  { value: "critical_only", label: "仅危急情况可打断" },
  { value: "allow_interrupt", label: "允许打断当前对话" },
]

const DIALOGUE_LABELS: Record<string, string> = Object.fromEntries(
  DIALOGUE_OPTIONS.map((item) => [item.value, item.label]),
)

const EVENT_LABELS: Record<string, string> = {
  spawn: "进入战斗",
  battle_end: "战斗结束",
  you_killed: "确认击毁",
  you_died: "载具损失",
  player_radio_command: "固定无线电消息",
  ground_laser_warning: "激光告警",
  stall_risk: "失速风险",
  high_aoa: "高迎角风险",
  over_g: "过载风险",
  low_alt_danger: "低空风险",
  overspeed: "超速风险",
  overheat: "过热警告",
  low_fuel: "低燃油",
  air_threat_nearby: "附近空中威胁",
  enemy_on_six: "六点钟方向威胁",
  tailing_risk: "持续尾随风险",
  ground_target_nearby: "附近地面目标",
  hud_notice: "战场通知",
  free_text_activity: "战场文字活动",
}

const AIR_ONLY_EVENTS = new Set([
  "stall_risk",
  "high_aoa",
  "over_g",
  "low_alt_danger",
  "overspeed",
  "low_fuel",
  "air_threat_nearby",
  "enemy_on_six",
  "tailing_risk",
])

const GROUND_ONLY_EVENTS = new Set(["ground_laser_warning", "ground_target_nearby"])

const PANEL_STYLES = `
  :root {
    color-scheme: light dark;
    --wt-bg: #ffffff;
    --wt-surface: #ffffff;
    --wt-surface-hover: #f4f8fd;
    --wt-text: #172033;
    --wt-muted: #68758b;
    --wt-muted-soft: #748198;
    --wt-border: #dfe5ee;
    --wt-control-border: #ccd6e4;
    --wt-status-warning-bg: #fff9eb;
    --wt-status-warning-border: #f0a52a;
    --wt-status-info-bg: #f4f9ff;
    --wt-status-info-border: #9ec8f7;
    --wt-status-success-bg: #f4fbf6;
    --wt-status-success-border: #8bcda0;
    --wt-status-danger-bg: #fff6f6;
    --wt-status-danger-border: #ee8c8c;
    --wt-emergency-bg: #fff7f7;
    --wt-emergency-hover: #fdeaea;
    --wt-bottom-bg: rgba(255, 255, 255, 0.98);
    --wt-modal-shadow: 0 18px 44px rgba(23, 32, 51, 0.18);
  }
  body { background: var(--wt-bg); color: var(--wt-text); font-size: 15px; }
  .wt-panel, .wt-panel * { letter-spacing: 0; }
  .wt-panel { display: grid; grid-template-rows: 72px minmax(0, 1fr) auto; width: 100%; height: 100vh; min-height: 0; overflow: hidden; background: var(--wt-bg); color: var(--wt-text); }
  .wt-tabs { display: flex; gap: 52px; height: 72px; padding: 0 32px; border-bottom: 1px solid var(--wt-border); }
  .wt-tab { position: relative; min-width: 90px; border: 0; background: transparent; color: var(--wt-text); font: inherit; font-size: 18px; cursor: pointer; }
  .wt-tab.is-active { color: #2589f5; font-weight: 700; }
  .wt-tab.is-active::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: #2589f5; }
  .wt-settings-trigger { display: inline-grid; place-items: center; align-self: center; width: 38px; height: 38px; margin-left: auto; border: 1px solid var(--wt-control-border); border-radius: 6px; padding: 0; background: var(--wt-surface); color: var(--wt-muted); font: inherit; font-size: 22px; line-height: 1; cursor: pointer; }
  .wt-settings-trigger:hover { background: var(--wt-surface-hover); color: var(--wt-text); }
  .wt-content { min-height: 0; overflow-x: hidden; overflow-y: auto; padding: 32px; scrollbar-gutter: stable; }
  .wt-status { display: flex; align-items: center; justify-content: space-between; gap: 28px; min-height: 126px; padding: 26px 28px; border: 1px solid var(--wt-status-warning-border); border-radius: 8px; background: var(--wt-status-warning-bg); }
  .wt-status-info { border-color: var(--wt-status-info-border); background: var(--wt-status-info-bg); }
  .wt-status-success { border-color: var(--wt-status-success-border); background: var(--wt-status-success-bg); }
  .wt-status-danger { border-color: var(--wt-status-danger-border); background: var(--wt-status-danger-bg); }
  .wt-status-copy { min-width: 0; }
  .wt-status-heading { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 9px; }
  .wt-status-title { margin: 0 0 9px; font-size: 22px; line-height: 1.35; font-weight: 760; }
  .wt-status-heading .wt-status-title { margin: 0; }
  .wt-mode-chip { display: inline-flex; align-items: center; min-height: 28px; border: 1px solid var(--wt-control-border); border-radius: 999px; padding: 3px 10px; background: var(--wt-surface); color: var(--wt-muted); font-size: 13px; font-weight: 700; white-space: nowrap; }
  .wt-mode-chip[data-mode="live"] { border-color: #82bf95; color: #257641; }
  .wt-mode-chip[data-mode="detect"] { border-color: #e5aa43; color: #a66700; }
  .wt-status-detail { margin: 0; color: var(--wt-muted); font-size: 15px; line-height: 1.6; }
  .wt-status-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; flex: 0 0 auto; }
  .wt-panel .neko-button { min-height: 44px; border-radius: 6px; padding: 9px 18px; background: var(--wt-surface); box-shadow: none; font-size: 15px; }
  .wt-panel .neko-button:hover { transform: none; box-shadow: none; background: var(--wt-surface-hover); }
  .wt-panel .wt-primary-action { min-width: 140px; border-color: #2589f5; background: #2589f5; color: #ffffff; }
  .wt-panel .wt-primary-action:hover { background: #1579e8; }
  .wt-panel .wt-link-action { min-height: auto; padding: 3px 0; border: 0; background: transparent; color: #2589f5; }
  .wt-panel .neko-action-control { display: inline-flex; }
  .wt-panel .neko-action-control .neko-button { width: 100%; }
  .wt-overview-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.08fr); gap: 0; margin-top: 34px; }
  .wt-column { min-width: 0; padding: 0 40px 0 8px; }
  .wt-column + .wt-column { padding: 0 0 0 44px; border-left: 1px solid var(--wt-border); }
  .wt-section-title { margin: 0 0 28px; font-size: 21px; line-height: 1.4; font-weight: 740; }
  .wt-battle-main { padding: 10px 0 28px; border-bottom: 1px solid var(--wt-border); }
  .wt-battle-title { margin: 0; font-size: 19px; font-weight: 720; }
  .wt-battle-meta { margin: 8px 0 0; color: var(--wt-muted-soft); font-size: 15px; line-height: 1.55; }
  .wt-health { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 16px; padding: 28px 0; border-bottom: 1px solid var(--wt-border); }
  .wt-health-mark { width: 26px; height: 26px; margin-top: 1px; border: 2px solid #2aa052; border-radius: 50%; }
  .wt-health[data-tone="warning"] .wt-health-mark { border-color: #d98b00; }
  .wt-health[data-tone="danger"] .wt-health-mark { border-color: #d94747; }
  .wt-health-title { margin: 0; font-size: 18px; font-weight: 700; }
  .wt-health-detail { margin: 8px 0 0; color: var(--wt-muted-soft); font-size: 15px; line-height: 1.55; }
  .wt-identity { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 28px 0 8px; }
  .wt-identity-name { margin: 0; font-size: 18px; font-weight: 700; }
  .wt-identity-meta { margin: 7px 0 0; color: var(--wt-muted-soft); font-size: 15px; }
  .wt-activity-head { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 16px; }
  .wt-panel .neko-badge { border-radius: 999px; background: var(--wt-surface); box-shadow: none; }
  .wt-panel .neko-badge::before { display: none; }
  .wt-activity-list { display: grid; }
  .wt-activity-row { position: relative; display: grid; grid-template-columns: 64px 22px minmax(0, 1fr) auto; gap: 14px; align-items: center; min-height: 94px; }
  .wt-activity-row::before { content: ""; position: absolute; left: 87px; top: 0; bottom: 0; width: 1px; background: var(--wt-border); }
  .wt-activity-time { color: var(--wt-muted-soft); font-size: 15px; font-variant-numeric: tabular-nums; }
  .wt-activity-mark { position: relative; z-index: 1; width: 16px; height: 16px; border: 3px solid var(--wt-bg); border-radius: 50%; background: #2589f5; box-shadow: 0 0 0 1px var(--wt-control-border); }
  .wt-activity-row[data-tone="warning"] .wt-activity-mark { background: #e69a13; }
  .wt-activity-row[data-tone="danger"] .wt-activity-mark { background: #e05252; }
  .wt-activity-row[data-tone="success"] .wt-activity-mark { background: #2aa052; }
  .wt-activity-title { margin: 0; font-size: 18px; font-weight: 700; }
  .wt-activity-detail { margin: 6px 0 0; color: var(--wt-muted-soft); font-size: 15px; line-height: 1.45; }
  .wt-empty { padding: 32px 0; color: var(--wt-muted-soft); text-align: center; }
  .wt-bottom { z-index: 20; display: grid; grid-template-columns: minmax(440px, 1.25fr) minmax(320px, 1fr); gap: 28px; align-items: center; min-height: 76px; padding: 12px 32px; border-top: 1px solid var(--wt-border); background: var(--wt-bottom-bg); }
  .wt-bottom-mode { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .wt-emergency-control { display: inline-flex; flex: 0 0 auto; }
  .wt-emergency-stop, .wt-emergency-control .neko-button { min-width: 76px; min-height: 34px; border-radius: 6px; padding: 5px 14px; font-size: 14px; font-weight: 750; }
  .wt-emergency-stop { border: 1px solid #e05252; background: var(--wt-emergency-bg); color: #d74a4a; font-family: inherit; cursor: pointer; }
  .wt-emergency-stop:hover:not(:disabled) { background: var(--wt-emergency-hover); }
  .wt-emergency-stop:disabled { border-color: var(--wt-control-border); background: var(--wt-surface); color: var(--wt-muted-soft); cursor: not-allowed; opacity: 0.62; }
  .wt-emergency-control[data-state="stop"] .neko-button { border-color: #e05252; background: var(--wt-emergency-bg); color: #d74a4a; }
  .wt-emergency-control[data-state="stop"] .neko-button:hover { background: var(--wt-emergency-hover); }
  .wt-emergency-control[data-state="resume"] .neko-button { border-color: #66ad7a; background: var(--wt-status-success-bg); color: #278044; }
  .wt-mode-detail { min-width: 0; color: var(--wt-muted-soft); font-size: 13px; line-height: 1.4; }
  .wt-bottom-policy { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 16px; align-items: center; }
  .wt-bottom-label { font-weight: 700; white-space: nowrap; }
  .wt-policy-select { width: 100%; min-height: 44px; border: 1px solid var(--wt-control-border); border-radius: 6px; padding: 8px 12px; background: var(--wt-surface); color: var(--wt-text); font: inherit; }
  .wt-diagnostics { display: grid; gap: 24px; }
  .wt-diagnostics-summary { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; align-items: center; padding: 20px 22px; border: 1px solid var(--wt-status-info-border); border-radius: 8px; background: var(--wt-status-info-bg); }
  .wt-diagnostics-summary[data-tone="success"] { border-color: var(--wt-status-success-border); background: var(--wt-status-success-bg); }
  .wt-diagnostics-summary[data-tone="warning"] { border-color: var(--wt-status-warning-border); background: var(--wt-status-warning-bg); }
  .wt-diagnostics-summary[data-tone="danger"] { border-color: var(--wt-status-danger-border); background: var(--wt-status-danger-bg); }
  .wt-diagnostics-summary h2 { margin: 0 0 7px; font-size: 19px; }
  .wt-diagnostics-summary p { margin: 0; color: var(--wt-muted); font-size: 14px; line-height: 1.55; }
  .wt-diagnostics-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
  .wt-diagnostics-section-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; }
  .wt-diagnostics-section-head h2 { margin: 0 0 5px; font-size: 18px; }
  .wt-diagnostics-section-head p { margin: 0; color: var(--wt-muted-soft); font-size: 14px; }
  .wt-diagnostic-checks { border-top: 1px solid var(--wt-border); }
  .wt-diagnostic-check { border-bottom: 1px solid var(--wt-border); }
  .wt-diagnostic-check > summary { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto auto; gap: 14px; align-items: center; min-height: 72px; padding: 10px 4px; list-style: none; cursor: pointer; }
  .wt-diagnostic-check > summary::-webkit-details-marker { display: none; }
  .wt-diagnostic-index { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--wt-status-info-bg); color: #2589f5; font-size: 13px; font-weight: 700; }
  .wt-diagnostic-check[data-tone="success"] .wt-diagnostic-index { background: var(--wt-status-success-bg); color: #278044; }
  .wt-diagnostic-check[data-tone="warning"] .wt-diagnostic-index { background: var(--wt-status-warning-bg); color: #a66700; }
  .wt-diagnostic-check[data-tone="danger"] .wt-diagnostic-index { background: var(--wt-status-danger-bg); color: #d94747; }
  .wt-diagnostic-copy { min-width: 0; }
  .wt-diagnostic-copy h3 { margin: 0 0 4px; font-size: 16px; }
  .wt-diagnostic-copy p { margin: 0; color: var(--wt-muted-soft); font-size: 14px; line-height: 1.45; }
  .wt-diagnostic-more { color: #2589f5; font-size: 13px; white-space: nowrap; }
  .wt-diagnostic-check[open] .wt-diagnostic-more { color: var(--wt-muted-soft); }
  .wt-diagnostic-help { margin: 0 0 14px 52px; padding: 12px 14px; border-left: 3px solid var(--wt-control-border); background: var(--wt-surface); color: var(--wt-muted); font-size: 14px; line-height: 1.55; }
  .wt-diagnostic-help strong { color: var(--wt-text); }
  .wt-advanced-details { border-top: 1px solid var(--wt-border); }
  .wt-advanced-details > summary { display: flex; align-items: center; justify-content: space-between; gap: 20px; min-height: 62px; cursor: pointer; }
  .wt-advanced-details > summary strong { font-size: 16px; }
  .wt-advanced-details > summary span { color: var(--wt-muted-soft); font-size: 13px; }
  .wt-advanced-content { display: grid; gap: 16px; max-width: 1120px; margin: 0 auto; padding: 8px 0 12px; }
  .wt-advanced-intro { margin: 0; padding: 12px 14px; border-left: 3px solid var(--wt-status-info-border); background: var(--wt-status-info-bg); color: var(--wt-muted); font-size: 13px; line-height: 1.55; }
  .wt-advanced-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: start; }
  .wt-advanced-group { min-width: 0; border: 1px solid var(--wt-border); border-radius: 8px; padding: 16px 18px 10px; background: var(--wt-surface); }
  .wt-advanced-group-head { padding-bottom: 13px; border-bottom: 1px solid var(--wt-border); }
  .wt-advanced-group-head h3 { margin: 0; font-size: 15px; line-height: 1.4; }
  .wt-advanced-group-head p { margin: 4px 0 0; color: var(--wt-muted-soft); font-size: 12px; line-height: 1.45; }
  .wt-advanced-list { margin: 0; }
  .wt-advanced-field { display: grid; grid-template-columns: minmax(96px, 0.9fr) minmax(0, 1.1fr); gap: 14px; align-items: start; padding: 9px 0; border-top: 1px solid var(--wt-border); }
  .wt-advanced-field:first-child { border-top: 0; }
  .wt-advanced-field dt { color: var(--wt-muted-soft); font-size: 13px; line-height: 1.45; }
  .wt-advanced-field dd { min-width: 0; margin: 0; color: var(--wt-text); font-size: 13px; font-weight: 650; line-height: 1.45; text-align: right; overflow-wrap: anywhere; }
  .wt-advanced-field[data-empty="true"] dd { color: var(--wt-muted-soft); font-weight: 500; }
  .wt-panel .neko-card { border-radius: 8px; background: var(--wt-surface); box-shadow: none; backdrop-filter: none; }
  .wt-panel .neko-heading { font-size: 16px; }
  .wt-panel .neko-text { font-size: 14px; }
  .wt-panel .neko-modal { border-radius: 8px; box-shadow: var(--wt-modal-shadow); }
  .wt-panel .neko-input, .wt-panel .neko-select { border-radius: 6px; }
  .wt-settings { display: grid; }
  .wt-settings-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(180px, auto); gap: 24px; align-items: center; padding: 18px 0; border-top: 1px solid var(--wt-border); }
  .wt-settings-row:first-child { padding-top: 0; border-top: 0; }
  .wt-settings-row:last-child { padding-bottom: 0; }
  .wt-settings-copy h3 { margin: 0 0 6px; font-size: 16px; }
  .wt-settings-copy p { margin: 0; color: var(--wt-muted-soft); font-size: 14px; line-height: 1.5; }
  .wt-settings-control { display: flex; justify-content: flex-end; }
  .wt-settings-select { min-width: 220px; }
  .wt-onboarding { display: grid; gap: 22px; }
  .wt-onboarding-copy h3 { margin: 0 0 10px; font-size: 20px; line-height: 1.4; }
  .wt-onboarding-copy > p { margin: 0; color: var(--wt-muted); font-size: 15px; line-height: 1.65; }
  .wt-onboarding-task { display: grid; gap: 16px; margin-top: 22px; padding: 18px; border: 1px solid var(--wt-control-border); border-radius: 6px; background: var(--wt-surface); }
  .wt-onboarding-task-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
  .wt-onboarding-task-head strong { display: block; margin-bottom: 5px; font-size: 16px; }
  .wt-onboarding-task-head p { margin: 0; color: var(--wt-muted-soft); font-size: 14px; line-height: 1.55; }
  .wt-control-guide { display: grid; margin-top: 22px; border-top: 1px solid var(--wt-border); }
  .wt-control-guide-row { display: grid; grid-template-columns: minmax(150px, 190px) minmax(0, 1fr); gap: 20px; padding: 15px 0; border-bottom: 1px solid var(--wt-border); }
  .wt-control-guide-row strong { font-size: 15px; }
  .wt-control-guide-row span { color: var(--wt-muted-soft); font-size: 14px; line-height: 1.55; }
  @media (prefers-color-scheme: dark) {
    :root {
      --wt-bg: #16181d;
      --wt-surface: #22252b;
      --wt-surface-hover: #2a2e35;
      --wt-text: #f1f3f6;
      --wt-muted: #b3bac7;
      --wt-muted-soft: #9da6b5;
      --wt-border: #3a3f48;
      --wt-control-border: #4a515d;
      --wt-status-warning-bg: #2a2418;
      --wt-status-warning-border: #9b6c20;
      --wt-status-info-bg: #182534;
      --wt-status-info-border: #37658f;
      --wt-status-success-bg: #19271e;
      --wt-status-success-border: #3f7950;
      --wt-status-danger-bg: #2d1d20;
      --wt-status-danger-border: #8b484f;
      --wt-emergency-bg: #2d1d20;
      --wt-emergency-hover: #3a2327;
      --wt-bottom-bg: rgba(22, 24, 29, 0.98);
      --wt-modal-shadow: 0 20px 48px rgba(0, 0, 0, 0.42);
    }
  }
  @media (max-height: 620px) and (min-width: 761px) {
    .wt-panel { grid-template-rows: 58px minmax(0, 1fr) auto; }
    .wt-tabs { height: 58px; }
    .wt-content { padding: 22px 28px; }
    .wt-status { min-height: 104px; padding: 18px 24px; }
    .wt-overview-grid { margin-top: 26px; }
    .wt-activity-row { min-height: 80px; }
    .wt-bottom { min-height: 64px; padding: 9px 28px; }
  }
  @media (max-width: 760px) {
    .wt-panel { grid-template-rows: 54px minmax(0, 1fr) auto; }
    .wt-tabs { height: 54px; padding: 0 18px; gap: 26px; }
    .wt-tab { min-width: 64px; }
    .wt-settings-trigger { width: 36px; height: 36px; font-size: 20px; }
    .wt-content { padding: 20px 18px 24px; }
    .wt-status { align-items: flex-start; flex-direction: column; padding: 18px; }
    .wt-status-actions { width: 100%; justify-content: flex-start; flex-wrap: wrap; }
    .wt-overview-grid { grid-template-columns: 1fr; }
    .wt-column { padding: 0; }
    .wt-column + .wt-column { margin-top: 28px; padding: 28px 0 0; border-top: 1px solid var(--wt-border); border-left: 0; }
    .wt-activity-row { grid-template-columns: 50px 18px minmax(0, 1fr); }
    .wt-activity-row::before { left: 67px; }
    .wt-activity-row .neko-badge { grid-column: 3; margin-bottom: 8px; }
    .wt-bottom { grid-template-columns: 1fr; gap: 8px; padding: 10px 18px; }
    .wt-bottom-mode { align-items: flex-start; flex-wrap: wrap; }
    .wt-mode-detail { flex-basis: 100%; }
    .wt-bottom-policy { grid-template-columns: 1fr; gap: 5px; }
    .wt-diagnostics-summary { grid-template-columns: 1fr; gap: 14px; }
    .wt-diagnostics-actions { justify-content: flex-start; }
    .wt-diagnostics-section-head { align-items: flex-start; flex-direction: column; gap: 6px; }
    .wt-diagnostic-check > summary { grid-template-columns: 30px minmax(0, 1fr) auto; gap: 10px; }
    .wt-diagnostic-check > summary .neko-badge { grid-column: 2; justify-self: start; }
    .wt-diagnostic-more { grid-column: 3; grid-row: 1 / span 2; }
    .wt-diagnostic-help { margin-left: 40px; }
    .wt-advanced-details > summary { align-items: flex-start; flex-direction: column; justify-content: center; gap: 3px; }
    .wt-advanced-grid { grid-template-columns: 1fr; }
    .wt-advanced-group { padding-inline: 14px; }
    .wt-settings-row { grid-template-columns: 1fr; gap: 10px; }
    .wt-settings-control { justify-content: flex-start; }
    .wt-settings-select { width: 100%; min-width: 0; }
    .wt-onboarding-task-head { display: grid; }
    .wt-control-guide-row { grid-template-columns: 1fr; gap: 6px; }
  }
`

function actionById(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-"
  if (typeof value === "boolean") return value ? "是" : "否"
  return String(value)
}

function numberText(value: unknown, unit = "", digits = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-"
  return `${value.toFixed(digits)}${unit}`
}

function percentText(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-"
  return `${Math.round(value * 100)}%`
}

function flagsText(flags: Record<string, boolean> | undefined): string {
  if (!flags) return "-"
  const active = Object.keys(flags).filter((key) => flags[key])
  return active.length ? active.join(", ") : "无"
}

function listText(values: string[] | undefined): string {
  return values && values.length ? values.join(", ") : "无"
}

function mappedText(value: unknown, labels: Record<string, string> = {}): string {
  const raw = text(value)
  return labels[raw] || raw
}

type AdvancedDetailItem = {
  key: string
  label: string
  value: string
}

function AdvancedDetailGroup({
  title,
  description,
  items,
}: {
  title: string
  description: string
  items: AdvancedDetailItem[]
}) {
  return (
    <section className="wt-advanced-group">
      <header className="wt-advanced-group-head">
        <h3>{title}</h3>
        <p>{description}</p>
      </header>
      <dl className="wt-advanced-list">
        {items.map((item) => (
          <div key={item.key} className="wt-advanced-field" data-empty={item.value === "-" ? "true" : "false"}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

function formatTime(value: ObserveRecord["ts"]): string {
  if (value === null || value === undefined || value === "") return "最近"
  const date = new Date(typeof value === "number" && value < 1_000_000_000_000 ? value * 1000 : value)
  if (Number.isNaN(date.getTime())) return "最近"
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

function currentPlayerName(identity: IdentityState): string {
  return String(identity.player_name || identity.saved_player_name || identity.self?.name || "").trim()
}

function safetyIsTripped(state: DashboardState): boolean {
  return state.safety?.status === "tripped" || state.safety?.auto_paused === true
}

function safetyIsPaused(state: DashboardState): boolean {
  return state.safety?.status === "paused" || state.safety?.manual_paused === true
}

function outputWasPushed(record: ObserveRecord | null | undefined): boolean {
  return record?.stage === "dispatcher_pushed"
    || record?.stage === "test_say_pushed"
    || record?.pushed === true
    || record?.outcome === "pushed"
}

function outputFailed(record: ObserveRecord | null | undefined): boolean {
  return record?.stage?.includes("failed") === true || record?.outcome === "failed"
}

function derivePanelSummary(state: DashboardState): PanelSummary {
  const dataHealthy = state.data_layer?.health
  const dataLayerMode = String(state.data_layer?.mode || "unknown")
  const playerName = currentPlayerName(state.identity || {})

  if (dataHealthy === false && (dataLayerMode === "starting" || dataLayerMode === "unknown")) {
    return {
      kind: "waiting",
      tone: "info",
      title: "正在准备战雷数据服务",
      detail: "首次启动可能需要一点时间，准备完成后会自动开始检测战局。",
      modeLabel: "正在连接",
    }
  }
  if (dataHealthy === false) {
    return {
      kind: "error",
      tone: "danger",
      title: "暂时无法获取战雷数据",
      detail: "不会根据旧数据播报；可以在诊断页查看中断位置。",
      modeLabel: "连接异常",
    }
  }
  if (safetyIsTripped(state)) {
    return {
      kind: "safety",
      tone: "danger",
      title: "安全保护已暂时阻止播报",
      detail: "战局检测仍在继续，恢复前不会向猫娘提交新的提醒。",
      modeLabel: "安全保护",
    }
  }
  if (safetyIsPaused(state)) {
    return {
      kind: "paused",
      tone: "warning",
      title: "播报已暂停，仍在检测战局",
      detail: "新事件会继续记录，但暂时不会让猫娘开口。",
      modeLabel: "已暂停",
    }
  }
  if (!state.connected || !state.in_battle) {
    return {
      kind: "waiting",
      tone: "info",
      title: "等待 War Thunder 战局",
      detail: "数据服务已待命；进入战斗后会自动开始识别。",
      modeLabel: "等待战局",
    }
  }
  if (state.dry_run) {
    return {
      kind: "detect",
      tone: "warning",
      title: "正在检测战局，但战斗播报尚未开启",
      detail: `数据正常 · ${mappedText(state.domain, DOMAIN_LABELS)}中 · 播报未启动${playerName ? " · 昵称已设置" : " · 昵称未设置"}`,
      modeLabel: "播报未启动",
    }
  }
  if (!playerName) {
    return {
      kind: "identity",
      tone: "info",
      title: "战局已连接，建议设置战雷游戏昵称",
      detail: "基础播报可用；无线电互动和身份识别会受到限制。",
      modeLabel: "昵称未设置",
    }
  }
  return {
    kind: "live",
    tone: "success",
    title: "战斗播报已开启",
    detail: `数据正常 · ${mappedText(state.domain, DOMAIN_LABELS)}中 · 猫娘可在需要时回应`,
    modeLabel: "播报中",
  }
}

function eventAllowedForDomain(eventId: string, domain: string | undefined): boolean {
  if (domain === "ground" && AIR_ONLY_EVENTS.has(eventId)) return false
  if ((domain === "air" || domain === "heli") && GROUND_ONLY_EVENTS.has(eventId)) return false
  return true
}

function activityStatus(record: ObserveRecord): { status: string; tone: PanelTone; detail: string } {
  const stage = String(record.stage || "")
  const outcome = String(record.outcome || "")
  const reason = String(record.reason || "")

  if (outputWasPushed(record)) {
    return { status: "已交给猫娘", tone: "success", detail: "请求已经提交给猫娘，等待宿主继续处理。" }
  }
  if (stage === "dispatcher_dry_run" || record.dry_run === true || outcome === "dry_run") {
    return { status: "仅记录", tone: "info", detail: "插件已识别该事件，当前不会提交语音。" }
  }
  if (outputFailed(record)) {
    return { status: "提交失败", tone: "danger", detail: "本次请求没有成功提交，可以前往诊断页查看。" }
  }
  if (stage.includes("safety") || stage === "test_say_blocked" || reason.includes("paused")) {
    return { status: "已被安全保护阻止", tone: "warning", detail: "事件已记录，但安全保护阻止了本次输出。" }
  }
  if (stage.includes("cooldown") || reason === "cooldown_active") {
    return { status: "冷却中", tone: "warning", detail: "相同提醒刚刚出现过，本次没有重复提交。" }
  }
  if (stage.includes("preempted") || outcome === "preempted") {
    return { status: "被更重要提醒替代", tone: "warning", detail: "更重要的战场事件获得了优先处理。" }
  }
  if (reason.includes("expired") || outcome === "expired") {
    return { status: "已过时", tone: "default", detail: "事件在提交前已经失去时效。" }
  }
  if (stage.includes("scenario_gated") || outcome === "suppressed" || outcome === "dropped") {
    return { status: "当前场景未输出", tone: "default", detail: "事件已识别，但当前战场状态不适合播报。" }
  }
  if (outcome === "allowed" || outcome === "selected") {
    return { status: "已进入输出判断", tone: "info", detail: "事件已通过识别，正在等待后续输出判断。" }
  }
  return { status: "已识别", tone: "default", detail: "插件已经识别到这项战场活动。" }
}

function buildActivityItems(state: DashboardState): ActivityItem[] {
  const observe = state.observe || {}
  const records: ObserveRecord[] = []
  const seen = new Set<string>()
  const candidates = [
    ...(observe.recent_timeline || []).slice().reverse(),
    observe.last_output_status,
    observe.last_decision,
    observe.last_event,
  ].filter(Boolean) as ObserveRecord[]

  for (const record of candidates) {
    const eventId = String(record.event_id || "")
    const stage = String(record.stage || "")
    if (!eventId && !stage.startsWith("test_say")) continue
    if (eventId && !eventAllowedForDomain(eventId, state.domain)) continue
    const key = `${eventId}:${stage}:${record.outcome || ""}:${record.reason || ""}`
    if (seen.has(key)) continue
    seen.add(key)
    records.push(record)
    if (records.length >= 5) break
  }

  return records.map((record, index) => {
    const eventId = String(record.event_id || "")
    const status = activityStatus(record)
    return {
      key: `${eventId || record.stage || "activity"}-${index}`,
      time: formatTime(record.ts),
      title: eventId ? EVENT_LABELS[eventId] || "战场事件" : "输出链路测试",
      detail: status.detail,
      status: status.status,
      tone: status.tone,
    }
  })
}

function battleStatus(state: DashboardState): { title: string; detail: string; tone: PanelTone } {
  if (state.dead) return { title: "本次载具已损失", detail: "等待重生后会自动重新识别载具与战局。", tone: "warning" }
  if (state.level === "critical" || state.level === "danger") {
    return { title: "检测到需要立即关注的战场状态", detail: "具体事件会出现在最近活动中。", tone: "danger" }
  }
  if (state.level === "warning") {
    return { title: "检测到需要关注的战场状态", detail: "系统会根据播报模式决定是否提交提醒。", tone: "warning" }
  }
  return { title: "当前状态正常，没有需要提醒的风险", detail: "系统正在检测战局，只在可信事件出现时记录或提醒。", tone: "success" }
}

function diagnosticStatus(state: DashboardState, kind: string): { label: string; tone: PanelTone; detail: string } {
  const output = state.observe?.last_output_status
  if (kind === "game") {
    return state.in_battle
      ? { label: "正常", tone: "success", detail: `已检测到${mappedText(state.domain, DOMAIN_LABELS)}` }
      : { label: "未进战局", tone: "info", detail: "系统已待命，进入战局后会自动开始识别" }
  }
  if (kind === "data") {
    const mode = String(state.data_layer?.mode || "unknown")
    if (state.data_layer?.health === false && (mode === "starting" || mode === "unknown")) {
      return { label: "准备中", tone: "info", detail: "8112 数据服务正在启动或等待首次健康检查" }
    }
    return state.data_layer?.health === false
      ? { label: "异常", tone: "danger", detail: "8112 数据服务暂时不可用" }
      : { label: "正常", tone: "success", detail: "数据服务可用" }
  }
  if (kind === "plugin") {
    if (!state.connected) return { label: "等待连接", tone: "info", detail: "插件尚未收到战雷数据" }
    return state.in_battle
      ? { label: "正常", tone: "success", detail: "插件正在接收并识别战局数据" }
      : { label: "已待命", tone: "success", detail: "插件连接正常，等待战局数据" }
  }
  if (kind === "policy") {
    if (safetyIsTripped(state)) {
      return { label: "安全保护", tone: "danger", detail: "自动保护阻止新的输出" }
    }
    if (safetyIsPaused(state)) return { label: "已暂停", tone: "warning", detail: "事件只记录，不提交语音" }
    if (state.dry_run) return { label: "播报未启动", tone: "info", detail: "按当前选择，事件只记录、不提交语音" }
    return { label: "正常", tone: "success", detail: "允许提交可信提醒" }
  }
  if (outputWasPushed(output)) {
    return { label: "已接收", tone: "success", detail: "最近一次请求已交给猫娘" }
  }
  if (outputFailed(output)) {
    return { label: "异常", tone: "danger", detail: "最近一次提交失败" }
  }
  return { label: "尚未测试", tone: "info", detail: "当前没有已提交的语音请求" }
}

function diagnosticOverview(state: DashboardState): { title: string; detail: string; tone: PanelTone } {
  const dataMode = String(state.data_layer?.mode || "unknown")
  const output = state.observe?.last_output_status
  if (state.data_layer?.health === false && dataMode !== "starting" && dataMode !== "unknown") {
    return { title: "数据服务暂时不可用", detail: "战雷数据在进入插件前已经中断，请先重新检查；仍未恢复时再重载插件。", tone: "danger" }
  }
  if (outputFailed(output)) {
    return { title: "最近一次输出没有成功", detail: "战局识别仍可能正常，可以展开“猫娘接收”查看原因并重新测试。", tone: "danger" }
  }
  if (safetyIsTripped(state)) {
    return { title: "安全保护已暂停新的输出", detail: "事件仍会继续识别和记录；处理安全保护后再恢复播报。", tone: "danger" }
  }
  if (safetyIsPaused(state)) {
    return { title: "战斗播报已临时暂停", detail: "数据链路仍然工作，返回概览页点击“恢复”即可继续输出。", tone: "warning" }
  }
  if (!state.in_battle) {
    return { title: "系统已待命，等待进入战局", detail: "数据服务和插件连接正常；当前未进战局不是故障。", tone: "info" }
  }
  if (state.dry_run) {
    return { title: "战局识别正常，战斗播报尚未开启", detail: "事件会被检测和记录，但不会提交给猫娘；可在概览页主动开启。", tone: "info" }
  }
  return { title: "运行链路正常", detail: "战雷数据、插件识别和输出策略均可用。", tone: "success" }
}

function diagnosticGuidance(state: DashboardState, kind: string): string {
  if (kind === "game") {
    return state.in_battle
      ? "无需处理。载具或模式变化后，这里会自动更新。"
      : "启动 War Thunder 并进入一局战斗；机库、菜单和观战准备阶段不会被当作正在战斗。"
  }
  if (kind === "data") {
    return state.data_layer?.health === false
      ? "先点击“重新检查”。如果仍显示异常，再使用宿主提供的“重载”按钮重新启动插件。"
      : "无需处理。数据服务负责把本机 8111 遥测整理为插件可消费的 8112 数据。"
  }
  if (kind === "plugin") {
    return state.connected
      ? "无需处理。未进入战局时保持待命是正常状态。"
      : "确认数据服务正常后重新检查；若仍未连接，再重载插件。"
  }
  if (kind === "policy") {
    if (safetyIsTripped(state)) return "先处理安全保护原因，再返回概览页恢复播报。"
    if (safetyIsPaused(state)) return "返回概览页点击“恢复”。"
    if (state.dry_run) return "这是用户选择，不是故障。需要语音提醒时，在概览页点击“开启战斗播报”。"
    return "无需处理。可信事件可以按当前插话规则提交给猫娘。"
  }
  const output = state.observe?.last_output_status
  if (outputFailed(output)) return "点击“测试开口”复验声音链路；失败时再查看高级详情中的最近输出原因。"
  if (outputWasPushed(output)) return "最近一次请求已成功交给猫娘，无需处理。"
  return "点击“测试开口”可独立验证猫娘接收和声音输出，不需要先进入战局或开启战斗播报。"
}

export default function NekoWarthunderPanel(props: PluginSurfaceProps<DashboardState>) {
  const state = props.state || {}
  const safety = state.safety || {}
  const identity = state.identity || {}
  const dataLayer = state.data_layer || {}
  const telemetry = state.telemetry || {}
  const takeoffProtection = state.takeoff_protection || {}
  const outputPolicy = state.output_policy || {}
  const awareness = state.awareness || {}
  const latestProximity = awareness.latest_proximity || {}
  const situation = awareness.situation || {}
  const nearestGroundTarget = awareness.nearest_ground_target || {}
  const observe = state.observe || {}
  const actions = Array.isArray(props.actions) ? props.actions : []
  const setDryRunAction = actionById(actions, "set_dry_run")
  const setDialogueIntrusionModeAction = actionById(actions, "set_dialogue_intrusion_mode")
  const setIdentityAction = actionById(actions, "set_identity")
  const completeOnboardingAction = actionById(actions, "complete_onboarding")
  const pauseAction = actionById(actions, "pause")
  const resumeAction = actionById(actions, "resume")
  const testSayAction = actionById(actions, "test_say")
  const toast = useToast()
  const onboardingRequired = state.onboarding?.required === true
  const [activeTab, setActiveTab] = useState("overview")
  const [onboardingOpen, setOnboardingOpen] = useState(onboardingRequired)
  const [onboardingAutoOpened, setOnboardingAutoOpened] = useState(false)
  const [onboardingStep, setOnboardingStep] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [identityOpen, setIdentityOpen] = useState(false)
  const [identityName, setIdentityName] = useState(currentPlayerName(identity))
  const [identityError, setIdentityError] = useState("")
  const [dryRunError, setDryRunError] = useState("")
  const [dialoguePolicyError, setDialoguePolicyError] = useState("")
  const summary = derivePanelSummary(state)
  const playerName = currentPlayerName(identity)
  const activityItems = buildActivityItems(state)
  const currentBattleStatus = battleStatus(state)
  const detectOnly = state.dry_run !== false
  const broadcastPaused = summary.kind === "paused" || summary.kind === "safety"

  useEffect(() => {
    if (!onboardingRequired || onboardingAutoOpened) return
    setOnboardingStep(0)
    setIdentityName(currentPlayerName(identity))
    setOnboardingOpen(true)
    setOnboardingAutoOpened(true)
  }, [onboardingRequired, onboardingAutoOpened])

  async function setDryRun(value: boolean) {
    if (!setDryRunAction) {
      setDryRunError("播报模式暂时不可用")
      return
    }
    try {
      setDryRunError("")
      await props.api.call("set_dry_run", { value })
      toast.success(value ? "战斗播报已关闭，继续检测和记录" : "战斗播报已开启")
      await props.api.refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setDryRunError(message)
      toast.error("播报模式切换失败")
    }
  }

  async function setDialogueIntrusionMode(mode: string) {
    if (!setDialogueIntrusionModeAction) {
      setDialoguePolicyError("插话规则设置暂时不可用")
      return
    }
    try {
      setDialoguePolicyError("")
      await props.api.call("set_dialogue_intrusion_mode", { mode })
      toast.success("播报插话规则已更新")
      await props.api.refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setDialoguePolicyError(message)
      toast.error("插话规则更新失败")
    }
  }

  async function submitIdentity(clear = false) {
    if (!setIdentityAction) {
      setIdentityError("游戏昵称设置暂时不可用")
      return false
    }
    const requestedName = clear ? "" : identityName.trim()
    if (!clear && !requestedName) {
      setIdentityError("请输入战雷游戏昵称")
      return false
    }
    try {
      setIdentityError("")
      const result = unwrapActionResult(await props.api.call("set_identity", { name: requestedName, clear }))
      const identityResult = result.identity && typeof result.identity === "object" ? result.identity : result
      if (identityResult.ok === false) {
        setIdentityError(String(identityResult.error || "游戏昵称保存失败"))
        return false
      }
      if (clear) setIdentityName("")
      setIdentityOpen(false)
      toast.success(clear ? "游戏昵称已清除" : "游戏昵称已保存，等待战局识别")
      await props.api.refresh()
      return true
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : String(error))
      toast.error("游戏昵称保存失败")
      return false
    }
  }

  function openOnboarding() {
    setOnboardingStep(0)
    setIdentityError("")
    setIdentityName(currentPlayerName(identity))
    setOnboardingOpen(true)
  }

  async function completeOnboarding(skipped = false) {
    if (!completeOnboardingAction) {
      toast.error("教程状态暂时无法保存")
      return false
    }
    try {
      await props.api.call("complete_onboarding", { skipped })
      setOnboardingOpen(false)
      setOnboardingStep(0)
      await props.api.refresh()
      return true
    } catch (error) {
      toast.error("教程状态保存失败")
      return false
    }
  }

  async function saveIdentityAndContinueOnboarding() {
    const currentName = currentPlayerName(identity)
    if (currentName && identityName.trim() === currentName) {
      setOnboardingStep(1)
      return
    }
    if (await submitIdentity(false)) setOnboardingStep(1)
  }

  function handleTestResult(envelope: any) {
    const result = unwrapActionResult(envelope)
    if (result.pushed) {
      toast.success("测试请求已交给猫娘")
      return
    }
    if (result.blocked === "dry_run") {
      toast.warning("当前运行版本仍限制测试开口，请重载插件后再试")
      return
    }
    if (result.blocked) {
      toast.warning("测试请求已被暂停或安全保护阻止")
      return
    }
    toast.info("测试请求已完成")
  }

  const statusActions = (
    <div className="wt-status-actions">
      {summary.kind === "identity" ? (
        <Button onClick={() => { setIdentityOpen(true) }}>设置游戏昵称</Button>
      ) : null}
      {summary.kind === "waiting" || summary.kind === "error" ? (
        <RefreshButton label="刷新状态" />
      ) : null}
      <Button
        className="wt-primary-action"
        tone="primary"
        onClick={() => setDryRun(!detectOnly)}
      >
        {detectOnly ? "开启战斗播报" : "停止战斗播报"}
      </Button>
    </div>
  )

  const bottomBar = (
    <footer className="wt-bottom">
      <div className="wt-bottom-mode">
        <div className="wt-emergency-control" data-state={detectOnly ? "disabled" : broadcastPaused ? "resume" : "stop"}>
          {detectOnly ? (
            <button type="button" className="wt-emergency-stop" title="战斗播报尚未启动" disabled>急停</button>
          ) : broadcastPaused ? (
            <ActionButton action={resumeAction} actionId="resume" tone="success">恢复</ActionButton>
          ) : (
            <ActionButton action={pauseAction} actionId="pause" tone="danger">急停</ActionButton>
          )}
        </div>
        <span className="wt-mode-detail">
          {detectOnly
            ? "战斗播报未启动"
            : broadcastPaused
              ? "新的战斗播报已暂停"
              : "立即暂停新的战斗播报"}
        </span>
      </div>
      <div className="wt-bottom-policy">
        <label className="wt-bottom-label" for="wt-dialogue-policy">播报插话规则</label>
        <select
          id="wt-dialogue-policy"
          className="wt-policy-select"
          value={outputPolicy.dialogue_intrusion_mode || "critical_only"}
          onChange={(event: any) => setDialogueIntrusionMode(event.target.value)}
        >
          {DIALOGUE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>
    </footer>
  )

  const overview = (
    <div>
      <section className={`wt-status wt-status-${summary.tone}`}>
        <div className="wt-status-copy">
          <div className="wt-status-heading">
            <h2 className="wt-status-title">{summary.title}</h2>
            <span className="wt-mode-chip" data-mode={detectOnly ? "detect" : "live"}>
              输出模式 · {detectOnly ? "播报未启动" : "战斗播报"}
            </span>
          </div>
          <p className="wt-status-detail">{summary.detail}</p>
        </div>
        {statusActions}
      </section>
      {dryRunError ? <Alert tone="danger">{dryRunError}</Alert> : null}

      <div className="wt-overview-grid">
        <section className="wt-column">
          <h2 className="wt-section-title">当前战局</h2>
          {state.in_battle ? (
            <div className="wt-battle-main">
              <p className="wt-battle-title">{mappedText(state.domain, DOMAIN_LABELS)} · {text(state.vehicle_type)}</p>
              <p className="wt-battle-meta">{mappedText(state.scenario, SCENARIO_LABELS)} · 战斗中</p>
            </div>
          ) : (
            <div className="wt-battle-main">
              <p className="wt-battle-title">尚未检测到战局</p>
              <p className="wt-battle-meta">进入 War Thunder 战斗后自动显示当前模式和载具。</p>
            </div>
          )}

          <div className="wt-health" data-tone={currentBattleStatus.tone}>
            <span className="wt-health-mark" />
            <div>
              <p className="wt-health-title">{currentBattleStatus.title}</p>
              <p className="wt-health-detail">{currentBattleStatus.detail}</p>
            </div>
          </div>

          <div className="wt-identity">
            <div>
              <p className="wt-identity-name">{playerName || "尚未设置战雷游戏昵称"}</p>
              <p className="wt-identity-meta">{playerName ? "已保存，等待本局身份匹配" : "设置后可识别自己发送的固定无线电消息"}</p>
            </div>
            <Button className="wt-link-action" onClick={() => { setIdentityOpen(true) }}>{playerName ? "修改" : "设置"}</Button>
          </div>
        </section>

        <section className="wt-column">
          <div className="wt-activity-head">
            <h2 className="wt-section-title">最近活动</h2>
            <StatusBadge tone={summary.tone} label={summary.modeLabel} />
          </div>
          {activityItems.length ? (
            <div className="wt-activity-list">
              {activityItems.map((item) => (
                <div className="wt-activity-row" data-tone={item.tone}>
                  <span className="wt-activity-time">{item.time}</span>
                  <span className="wt-activity-mark" />
                  <div>
                    <p className="wt-activity-title">{item.title}</p>
                    <p className="wt-activity-detail">{item.detail}</p>
                  </div>
                  <StatusBadge tone={item.tone} label={item.status} />
                </div>
              ))}
            </div>
          ) : (
            <div className="wt-empty">识别到可信事件后，这里会显示它为什么播报或没有播报。</div>
          )}
        </section>
      </div>

      {dialoguePolicyError ? <Alert tone="danger">{dialoguePolicyError}</Alert> : null}
    </div>
  )

  const gameStep = diagnosticStatus(state, "game")
  const dataStep = diagnosticStatus(state, "data")
  const pluginStep = diagnosticStatus(state, "plugin")
  const policyStep = diagnosticStatus(state, "policy")
  const nekoStep = diagnosticStatus(state, "neko")
  const diagnosticsOverview = diagnosticOverview(state)
  const diagnosticChecks = [
    { key: "game", index: 1, title: "战雷客户端", ...gameStep },
    { key: "data", index: 2, title: "数据服务", ...dataStep },
    { key: "plugin", index: 3, title: "插件识别", ...pluginStep },
    { key: "policy", index: 4, title: "输出策略", ...policyStep },
    { key: "neko", index: 5, title: "猫娘接收", ...nekoStep },
  ]

  const diagnostics = (
    <div className="wt-diagnostics">
      <section className="wt-diagnostics-summary" data-tone={diagnosticsOverview.tone} aria-live="polite">
        <div>
          <h2>{diagnosticsOverview.title}</h2>
          <p>{diagnosticsOverview.detail}</p>
        </div>
        <div className="wt-diagnostics-actions">
          <RefreshButton label="重新检查" />
          <ActionButton
            className="wt-test-sound-action"
            action={testSayAction}
            actionId="test_say"
            values={{ text: "副驾驶面板测试：能听到我吗？" }}
            refresh={false}
            onResult={handleTestResult}
            onError={() => { toast.error("测试请求提交失败") }}
          >
            测试开口
          </ActionButton>
        </div>
      </section>

      <section>
        <div className="wt-diagnostics-section-head">
          <div>
            <h2>运行链路</h2>
            <p>展开任一步即可查看它的含义和建议操作。</p>
          </div>
          <span className="wt-mode-detail">按当前状态实时更新</span>
        </div>
        <div className="wt-diagnostic-checks">
          {diagnosticChecks.map((item) => (
            <details key={item.key} className="wt-diagnostic-check" data-tone={item.tone} open={item.tone === "danger" || item.tone === "warning"}>
              <summary>
                <span className="wt-diagnostic-index">{item.index}</span>
                <div className="wt-diagnostic-copy">
                  <h3>{item.title}</h3>
                  <p>{item.detail}</p>
                </div>
                <StatusBadge tone={item.tone} label={item.label} />
                <span className="wt-diagnostic-more">详情</span>
              </summary>
              <p className="wt-diagnostic-help"><strong>建议：</strong>{diagnosticGuidance(state, item.key)}</p>
            </details>
          ))}
        </div>
      </section>

      <details className="wt-advanced-details">
        <summary>
          <strong>高级详情</strong>
          <span>原始状态与维护字段</span>
        </summary>
        <div className="wt-advanced-content">
          <p className="wt-advanced-intro">维护信息不会影响正常使用。排查问题时，优先查看有明确数值或错误内容的字段。</p>
          <div className="wt-advanced-grid">
            <AdvancedDetailGroup
              title="连接状态"
              description="插件与本机数据服务是否正常通信"
              items={[
                { key: "enabled", label: "插件启用", value: text(state.enabled) },
                { key: "connected", label: "数据连接", value: state.connected ? "已连接" : "未连接" },
                { key: "conn_state", label: "连接阶段", value: state.connected ? "在线" : "离线" },
                { key: "context", label: "战雷上下文", value: state.game_context_active ? "已注入" : "未注入" },
                { key: "mode", label: "数据层模式", value: mappedText(dataLayer.mode, DATA_LAYER_LABELS) },
                { key: "health", label: "数据层健康", value: text(dataLayer.health) },
                { key: "pid", label: "数据层 PID", value: text(dataLayer.pid) },
                { key: "error", label: "最近错误", value: text(dataLayer.last_error) },
              ]}
            />
            <AdvancedDetailGroup
              title="战场状态"
              description="当前识别到的战局、载具与风险上下文"
              items={[
                { key: "battle", label: "战斗内", value: text(state.in_battle) },
                { key: "dead", label: "阵亡状态", value: text(state.dead) },
                { key: "domain", label: "模式", value: mappedText(state.domain, DOMAIN_LABELS) },
                { key: "vehicle", label: "载具", value: text(state.vehicle_type) },
                { key: "profile_source", label: "数据库来源", value: text(state.profile_source) },
                { key: "profile_family", label: "载具族", value: text(state.profile_family) },
                { key: "profile_matched", label: "数据库匹配", value: text(state.profile_matched) },
                { key: "scenario", label: "场景", value: mappedText(state.scenario, SCENARIO_LABELS) },
                { key: "level", label: "风险等级", value: mappedText(state.level, RISK_LEVEL_LABELS) },
              ]}
            />
          </div>

          {state.domain === "air" || state.domain === "heli" ? (
            <div className="wt-advanced-grid">
              <AdvancedDetailGroup
                title="飞行诊断"
                description="用于判断飞行状态和遥测新鲜度"
                items={[
                  { key: "radio_altitude", label: "雷达高度", value: numberText(telemetry.radio_altitude_m, "m") },
                  { key: "altitude", label: "海拔高度", value: numberText(telemetry.altitude_m, "m") },
                  { key: "ias", label: "指示空速", value: numberText(telemetry.ias_kmh, "km/h") },
                  { key: "mach", label: "马赫数", value: numberText(telemetry.mach, "", 2) },
                  { key: "climb", label: "垂直速度", value: numberText(telemetry.climb_ms, "m/s", 1) },
                  { key: "fuel", label: "燃油比例", value: percentText(telemetry.fuel_fraction) },
                  { key: "flags", label: "当前标记", value: flagsText(telemetry.flags) },
                  { key: "age", label: "数据延迟", value: numberText(telemetry.age_seconds, "s", 1) },
                ]}
              />
              <AdvancedDetailGroup
                title="起飞保护"
                description="避免刚起飞时产生不必要的风险播报"
                items={[
                  { key: "active", label: "保护状态", value: takeoffProtection.active ? "生效" : "未生效" },
                  { key: "available", label: "雷达高度可用", value: text(takeoffProtection.radio_altitude_available) },
                  { key: "agl", label: "当前离地高度", value: numberText(takeoffProtection.radio_altitude_m, "m") },
                  { key: "enter", label: "进入阈值", value: numberText(takeoffProtection.enter_m, "m") },
                  { key: "exit", label: "解除阈值", value: numberText(takeoffProtection.exit_m, "m") },
                  { key: "grace", label: "时间保护", value: numberText(takeoffProtection.low_alt_grace_seconds, "s") },
                  { key: "suppresses", label: "当前压制", value: listText(takeoffProtection.suppresses) },
                ]}
              />
            </div>
          ) : null}

          <div className="wt-advanced-grid">
            <AdvancedDetailGroup
              title="接近感知"
              description="附近目标与威胁的聚合结果"
              items={[
                { key: "count", label: "接近事件数", value: text(awareness.proximity_event_count) },
                { key: "kind", label: "最近类型", value: text(latestProximity.kind) },
                { key: "target", label: "目标类型", value: text(latestProximity.target_type) },
                { key: "distance", label: "距离", value: numberText(latestProximity.distance_m, "m") },
                { key: "compass", label: "方位", value: text(latestProximity.compass) },
                { key: "enemy", label: "敌方单位", value: text(situation.enemy_count) },
                { key: "air", label: "空中威胁", value: text(situation.air_threat_count) },
                { key: "ground", label: "任务目标", value: text(situation.ground_target_count) },
                { key: "grid", label: "最近目标网格", value: text(nearestGroundTarget.grid) },
                { key: "ground_distance", label: "最近目标距离", value: numberText(nearestGroundTarget.distance_m, "m") },
              ]}
            />
            <AdvancedDetailGroup
              title="安全控制"
              description="决定战斗事件是否允许提交给猫娘"
              items={[
                { key: "safety", label: "安全门状态", value: mappedText(safety.status, SAFETY_STATUS_LABELS) },
                { key: "manual", label: "手动暂停", value: text(safety.manual_paused) },
                { key: "auto", label: "自动暂停", value: text(safety.auto_paused) },
                { key: "failures", label: "失败次数", value: text(safety.failures) },
                { key: "dry_run", label: "战斗播报", value: state.dry_run ? "未启动" : "已启动" },
                { key: "dialogue", label: "插话策略", value: mappedText(outputPolicy.dialogue_intrusion_mode, DIALOGUE_LABELS) },
                { key: "user_window", label: "用户对话保护", value: numberText(outputPolicy.user_chat_quiet_window_seconds, "s") },
                { key: "battle_window", label: "播报间隔保护", value: numberText(outputPolicy.battle_output_quiet_window_seconds, "s") },
              ]}
            />
          </div>

          <div className="wt-advanced-grid">
            <AdvancedDetailGroup
              title="最近决策"
              description="插件最近一次如何处理识别到的事件"
              items={[
                { key: "event", label: "最近事件", value: text(observe.last_event?.event_id) },
                { key: "decision_event", label: "决策事件", value: text(observe.last_decision?.event_id) },
                { key: "decision_stage", label: "决策阶段", value: text(observe.last_decision?.stage) },
                { key: "decision_outcome", label: "决策结果", value: text(observe.last_decision?.outcome) },
                { key: "decision_reason", label: "原因", value: text(observe.last_decision?.reason) },
                { key: "decision_scenario", label: "当时场景", value: mappedText(observe.last_decision?.scenario, SCENARIO_LABELS) },
              ]}
            />
            <AdvancedDetailGroup
              title="最近输出"
              description="最后一次播报请求的实际结果"
              items={[
                { key: "output_event", label: "输出事件", value: text(observe.last_output_status?.event_id) },
                { key: "output_stage", label: "输出阶段", value: text(observe.last_output_status?.stage) },
                { key: "output_outcome", label: "输出结果", value: text(observe.last_output_status?.outcome) },
                { key: "output_reason", label: "原因", value: text(observe.last_output_status?.reason) },
                { key: "output_safety", label: "安全门", value: mappedText(observe.last_output_status?.safety_status, SAFETY_STATUS_LABELS) },
                {
                  key: "output_dry_run",
                  label: "当时播报状态",
                  value: observe.last_output_status?.dry_run === true
                    ? "未启动"
                    : observe.last_output_status?.dry_run === false ? "已启动" : "-",
                },
              ]}
            />
          </div>
        </div>
      </details>
    </div>
  )

  const onboardingTitles = ["设置昵称", "认识按钮"]
  const onboardingFooter = (
    <ButtonGroup>
      {onboardingStep === 0 ? <Button onClick={() => { void completeOnboarding(true) }}>跳过教程</Button> : null}
      {onboardingStep > 0 ? <Button onClick={() => { setOnboardingStep(onboardingStep - 1) }}>上一步</Button> : null}
      {onboardingStep === 0 ? (
        <>
          <Button onClick={() => { setIdentityError(""); setOnboardingStep(1) }}>暂不设置</Button>
          <Button tone="primary" onClick={() => { void saveIdentityAndContinueOnboarding() }}>
            {playerName && identityName.trim() === playerName ? "下一步" : "保存昵称并继续"}
          </Button>
        </>
      ) : (
        <Button tone="primary" onClick={() => { void completeOnboarding(false) }}>知道了</Button>
      )}
    </ButtonGroup>
  )

  const onboardingContent = (
    <div className="wt-onboarding">
      {onboardingStep === 0 ? (
        <div className="wt-onboarding-copy">
          <h3>先设置你的战雷游戏昵称</h3>
          <p>猫娘用它识别你发送的固定无线电，以及本局击杀、死亡等归属。这个昵称只保存在插件配置中。</p>
          <div className="wt-onboarding-task">
            <div className="wt-onboarding-task-head">
              <div>
                <strong>游戏内显示昵称</strong>
                <p>请填写完整昵称；带有 #数字后缀时也要一起填写。</p>
              </div>
              <StatusBadge tone={playerName ? "success" : "info"} label={playerName ? "已设置" : "待设置"} />
            </div>
            <Field
              label="战雷游戏昵称"
              required
              help="不是邮箱、数字账号 ID、Steam 名称或联队标签。"
              error={identityError}
            >
              <Input
                value={identityName}
                placeholder="例如 CN-Zephyr 或 Player#123456"
                invalid={!!identityError}
                onChange={setIdentityName}
              />
            </Field>
            <Tip>暂时不确定可以继续教程，之后随时从概览页或设置中补填。</Tip>
          </div>
        </div>
      ) : (
        <div className="wt-onboarding-copy">
          <h3>常用按钮都在固定位置</h3>
          <p>平时只需要认识下面几个按钮，战局和载具会自动识别。</p>
          <div className="wt-control-guide">
            <div className="wt-control-guide-row"><strong>开启 / 停止战斗播报</strong><span>位于概览页顶部。开启后可信事件可以提交给猫娘；停止后仍会检测和记录。</span></div>
            <div className="wt-control-guide-row"><strong>急停 / 恢复</strong><span>位于概览页底部左侧。需要立刻安静时使用，恢复后继续按原规则播报。</span></div>
            <div className="wt-control-guide-row"><strong>测试开口</strong><span>位于诊断页顶部，只检查猫娘接收和声音链路，不需要先进入战局。</span></div>
            <div className="wt-control-guide-row"><strong>刷新状态</strong><span>连接异常或等待战局时会出现在顶部，只重新读取当前状态，不会重启插件。</span></div>
            <div className="wt-control-guide-row"><strong>设置</strong><span>右上角齿轮可修改昵称、插话规则，也能重新打开本教程。</span></div>
          </div>
        </div>
      )}
    </div>
  )

  return (
    <div className="wt-panel">
      <style>{PANEL_STYLES}</style>
      <nav className="wt-tabs" aria-label="副驾驶面板">
        <button className={`wt-tab ${activeTab === "overview" ? "is-active" : ""}`} onClick={() => { setActiveTab("overview") }}>概览</button>
        <button className={`wt-tab ${activeTab === "diagnostics" ? "is-active" : ""}`} onClick={() => { setActiveTab("diagnostics") }}>诊断</button>
        <button type="button" className="wt-settings-trigger" aria-label="设置" title="设置" onClick={() => { setSettingsOpen(true) }}>⚙︎</button>
      </nav>
      <main className="wt-content">
        {activeTab === "overview" ? overview : diagnostics}
      </main>
      {activeTab === "overview" ? bottomBar : null}

      <Modal
        open={onboardingOpen}
        title={`新手教程 · ${onboardingTitles[onboardingStep]}`}
        onClose={() => { setOnboardingOpen(false) }}
        footer={onboardingFooter}
      >
        {onboardingContent}
      </Modal>

      <Modal
        open={settingsOpen}
        title="设置"
        onClose={() => { setSettingsOpen(false) }}
        footer={<Button onClick={() => { setSettingsOpen(false) }}>完成</Button>}
      >
        <div className="wt-settings">
          <div className="wt-settings-row">
            <div className="wt-settings-copy">
              <h3>战雷游戏昵称</h3>
              <p>{playerName ? `当前为 ${playerName}` : "用于识别你自己发送的固定无线电消息和本局归属。"}</p>
            </div>
            <div className="wt-settings-control">
              <Button onClick={() => { setSettingsOpen(false); setIdentityOpen(true) }}>{playerName ? "修改昵称" : "设置昵称"}</Button>
            </div>
          </div>
          <div className="wt-settings-row">
            <div className="wt-settings-copy">
              <h3>播报插话规则</h3>
              <p>决定战斗提醒是否可以打断你正在进行的对话。</p>
            </div>
            <div className="wt-settings-control">
              <select
                className="wt-policy-select wt-settings-select"
                aria-label="播报插话规则"
                value={outputPolicy.dialogue_intrusion_mode || "critical_only"}
                onChange={(event: any) => setDialogueIntrusionMode(event.target.value)}
              >
                {DIALOGUE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="wt-settings-row">
            <div className="wt-settings-copy">
              <h3>新手教程</h3>
              <p>重新查看昵称填写和常用按钮说明。</p>
            </div>
            <div className="wt-settings-control">
              <Button onClick={() => { setSettingsOpen(false); openOnboarding() }}>重新查看教程</Button>
            </div>
          </div>
          {dialoguePolicyError ? <Alert tone="danger">{dialoguePolicyError}</Alert> : null}
        </div>
      </Modal>

      <Modal
        open={identityOpen}
        title="设置战雷游戏昵称"
        onClose={() => { setIdentityOpen(false) }}
        footer={(
          <ButtonGroup>
            {playerName ? <Button tone="warning" onClick={async () => { await submitIdentity(true) }}>清除昵称</Button> : null}
            <Button onClick={() => { setIdentityOpen(false) }}>取消</Button>
            <Button tone="primary" onClick={async () => { await submitIdentity(false) }}>保存昵称</Button>
          </ButtonGroup>
        )}
      >
        <Stack>
          <Field
            label="战雷游戏昵称"
            required
            help="不含联队标签，不是邮箱、数字账号 ID 或 Steam 名称；昵称带 #数字时请完整填写。"
            error={identityError}
          >
            <Input
              value={identityName}
              placeholder="例如 CN-Zephyr 或 Player#123456"
              invalid={!!identityError}
              onChange={setIdentityName}
            />
          </Field>
          <Warning>昵称只用于识别你自己发送的固定无线电消息和本局归属，不会选择其他玩家。</Warning>
        </Stack>
      </Modal>
    </div>
  )
}
