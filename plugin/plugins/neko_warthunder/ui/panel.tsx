import {
  Page,
  Card,
  Grid,
  Stack,
  StatusBadge,
  StatCard,
  KeyValue,
  Toolbar,
  ToolbarGroup,
  Button,
  Field,
  Input,
  Switch,
  ActionButton,
  RefreshButton,
  Alert,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

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
  requested?: string | null
  active_players_count?: number
  active_players?: Array<{
    display_name?: string
    name?: string
    selectable?: boolean
  }>
}

type DataLayerState = {
  mode?: string
  url?: string
  pid?: number | null
  started_by_plugin?: boolean
  auto_start?: boolean
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
  level?: string | null
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
    has_player?: boolean | null
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

type DashboardState = {
  enabled?: boolean
  dry_run?: boolean
  connected?: boolean
  conn_state?: string
  in_battle?: boolean
  dead?: boolean
  domain?: string
  domain_label?: string | null
  vehicle_type?: string | null
  profile_matched?: boolean | null
  profile_source?: string | null
  profile_family?: string | null
  scenario?: string
  level?: string
  identity?: IdentityState
  data_layer?: DataLayerState
  telemetry?: TelemetryState
  takeoff_protection?: TakeoffProtectionState
  output_policy?: OutputPolicyState
  awareness?: AwarenessState
  safety?: SafetyState
  observe?: ObserveState
}

function actionById(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-"
  if (typeof value === "boolean") return value ? "是" : "否"
  return String(value)
}

function badge(value: boolean | undefined, yes = "是", no = "否") {
  return <StatusBadge tone={value ? "success" : "warning"} label={value ? yes : no} />
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
  return values && values.length ? values.join(", ") : "-"
}

function mappedText(value: unknown, labels: Record<string, string> = {}): string {
  const raw = text(value)
  return labels[raw] || raw
}

const CONN_STATE_LABELS: Record<string, string> = {
  offline: "离线",
  not_in_battle: "未进战斗",
  in_battle: "战斗中",
}

const SCENARIO_LABELS: Record<string, string> = {
  OUT_OF_BATTLE: "战斗外",
  SPAWNING: "出生/进场",
  IN_FLIGHT: "飞行中",
  COMBAT_STRESS: "交战压力",
  CRITICAL_RISK: "危急风险",
  DEAD: "已阵亡",
  BATTLE_ENDED: "战斗结束",
}

const LEVEL_LABELS: Record<string, string> = {
  info: "正常",
  warning: "警告",
  critical: "危急",
  danger: "危险",
}

const SAFETY_LABELS: Record<string, string> = {
  running: "运行中",
  paused: "已暂停",
  auto_paused: "自动暂停",
}

const DOMAIN_LABELS: Record<string, string> = {
  air: "空战",
  heli: "直升机",
  ground: "陆战",
  naval: "海战",
  menu: "菜单",
  unknown: "未知",
}

const DATA_LAYER_LABELS: Record<string, string> = {
  managed: "插件托管",
  external: "外部运行",
  disabled: "未启用",
  unknown: "未知",
}

const DIALOGUE_INTRUSION_LABELS: Record<string, string> = {
  no_interrupt: "不打断当前对话",
  critical_only: "仅危急可打断",
  allow_interrupt: "允许打断当前对话",
  custom: "自定义",
}

const IDENTITY_SOURCE_LABELS: Record<string, string> = {
  manual: "手动设置",
  auto: "自动识别",
}

const STAGE_LABELS: Record<string, string> = {
  detector_candidate: "探测候选",
  detector_suppressed: "探测抑制",
  arbiter_allowed: "仲裁放行",
  arbiter_dropped: "仲裁丢弃",
  arbiter_window: "仲裁窗口",
  arbiter_preempted: "被高优先级替换",
  arbiter_cooldown: "冷却中",
  arbiter_scenario_gated: "场景门控",
  safety_block: "安全门阻断",
  dispatcher_dry_run: "模拟输出",
  dispatcher_pushed: "已推送",
  dispatcher_failed: "推送失败",
}

const OUTCOME_LABELS: Record<string, string> = {
  allowed: "已放行",
  selected: "已选择",
  dropped: "已丢弃",
  suppressed: "已抑制",
  preempted: "被替换",
  cooldown: "冷却中",
  dry_run: "仅模拟",
  pushed: "已开口",
  failed: "失败",
  blocked: "已阻断",
  expired: "已过期",
}

const REASON_LABELS: Record<string, string> = {
  selected: "已选中",
  dry_run_enabled: "模拟模式开启，仅记录不真实开口",
  paused: "手动暂停中",
  safety_paused: "安全门暂停中",
  auto_paused: "自动暂停中",
  cooldown_active: "冷却时间内",
  scenario_gated: "当前场景不允许",
  scenario_gated_on_flush: "输出时场景已变化",
  output_backpressure: "输出背压中，旧提示被压住",
  event_expired: "事件过期，真实开口前丢弃",
  replay: "回放数据已静默",
  kill_coalesced: "多杀提示已合并",
  deferred_hud_notice: "HUD 技术通知已识别，当前策略暂不播报",
}

function safetyTone(status: string | undefined) {
  if (status === "running") return "success"
  if (status === "paused") return "danger"
  return "warning"
}

function levelTone(level: string | undefined) {
  if (level === "critical" || level === "danger") return "danger"
  if (level === "warning") return "warning"
  return "info"
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

function recordValue(record: ObserveRecord | null | undefined, key: keyof ObserveRecord): string {
  if (!record) return "-"
  return text(record[key])
}

function mappedRecordValue(record: ObserveRecord | null | undefined, key: keyof ObserveRecord, labels: Record<string, string> = {}): string {
  return mappedText(recordValue(record, key), labels)
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
  const lastEvent = observe.last_event
  const lastDecision = observe.last_decision
  const lastOutput = observe.last_output_status
  const actions = Array.isArray(props.actions) ? props.actions : []
  const setDryRunAction = actionById(actions, "set_dry_run")
  const setDialogueIntrusionModeAction = actionById(actions, "set_dialogue_intrusion_mode")
  const setIdentityAction = actionById(actions, "set_identity")
  const pauseAction = actionById(actions, "pause")
  const resumeAction = actionById(actions, "resume")
  const testSayAction = actionById(actions, "test_say")
  const [dryRunError, setDryRunError] = useState("")
  const [dialoguePolicyError, setDialoguePolicyError] = useState("")
  const [identityName, setIdentityName] = useState(String(identity.player_name || identity.saved_player_name || identity.self?.name || ""))
  const [identityError, setIdentityError] = useState("")

  async function setDryRun(value: boolean) {
    if (!setDryRunAction) {
      setDryRunError("安全试运行开关不可用")
      return
    }
    try {
      setDryRunError("")
      await props.api.call("set_dry_run", { value })
      await props.api.refresh()
    } catch (error) {
      setDryRunError(error instanceof Error ? error.message : String(error))
    }
  }

  async function setDialogueIntrusionMode(mode: string) {
    if (!setDialogueIntrusionModeAction) {
      setDialoguePolicyError("插话策略设置不可用")
      return
    }
    try {
      setDialoguePolicyError("")
      await props.api.call("set_dialogue_intrusion_mode", { mode })
      await props.api.refresh()
    } catch (error) {
      setDialoguePolicyError(error instanceof Error ? error.message : String(error))
    }
  }

  async function submitIdentity(clear = false) {
    await submitIdentityName(identityName, clear)
  }

  async function submitIdentityName(name: string, clear = false) {
    if (!setIdentityAction) {
      setIdentityError("玩家名设置不可用")
      return
    }
    try {
      setIdentityError("")
      const result = unwrapActionResult(await props.api.call("set_identity", { name, clear }))
      const identityResult = result.identity && typeof result.identity === "object" ? result.identity : result
      if (identityResult.ok === false) {
        setIdentityError(String(identityResult.error || "玩家名设置失败"))
        return
      }
      setIdentityName(clear ? "" : name)
      await props.api.refresh()
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : String(error))
    }
  }

  const currentPlayerName = identity.player_name || identity.saved_player_name || identity.self?.name

  return (
    <Page title="战雷猫娘副驾驶" subtitle="战场态势状态面板">
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={state.connected ? "success" : "warning"} label={state.connected ? "已连接" : "未连接"} />
          <StatusBadge tone={safetyTone(safety.status)} label={mappedText(safety.status, SAFETY_LABELS)} />
          <StatusBadge tone={levelTone(state.level)} label={mappedText(state.level, LEVEL_LABELS)} />
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton label="刷新状态" />
        </ToolbarGroup>
      </Toolbar>

      <Grid cols={4}>
        <StatCard label="插件启用" value={text(state.enabled)} />
        <StatCard label="模拟模式" value={state.dry_run ? "开启" : "关闭"} />
        <StatCard label="连接状态" value={mappedText(state.conn_state, CONN_STATE_LABELS)} />
        <StatCard label="场景" value={mappedText(state.scenario, SCENARIO_LABELS)} />
        <StatCard label="最近输出" value={mappedRecordValue(lastOutput, "outcome", OUTCOME_LABELS)} />
      </Grid>

      <Grid cols={2}>
        <Card title="连接状态">
          <KeyValue
            items={[
              { key: "enabled", label: "插件启用", value: badge(state.enabled) },
              { key: "dry_run", label: "模拟模式", value: badge(state.dry_run, "开启", "关闭") },
              { key: "connected", label: "数据连接", value: badge(state.connected, "已连接", "离线") },
              { key: "conn_state", label: "连接状态", value: mappedText(state.conn_state, CONN_STATE_LABELS) },
              { key: "data_layer.mode", label: "数据层模式", value: mappedText(dataLayer.mode, DATA_LAYER_LABELS) },
              { key: "data_layer.health", label: "数据层健康", value: badge(dataLayer.health) },
              { key: "data_layer.pid", label: "数据层 PID", value: text(dataLayer.pid) },
              { key: "data_layer.started_by_plugin", label: "由插件启动", value: badge(dataLayer.started_by_plugin) },
              { key: "data_layer.last_error", label: "最近错误", value: text(dataLayer.last_error) },
            ]}
          />
        </Card>

        <Card title="战场状态">
          <KeyValue
            items={[
              { key: "in_battle", label: "战斗内", value: badge(state.in_battle) },
              { key: "dead", label: "阵亡状态", value: badge(state.dead) },
              { key: "domain", label: "模式", value: mappedText(state.domain, DOMAIN_LABELS) },
              { key: "domain_label", label: "模式说明", value: text(state.domain_label) },
              { key: "vehicle_type", label: "载具", value: text(state.vehicle_type) },
              { key: "profile_source", label: "数据库来源", value: text(state.profile_source) },
              { key: "profile_family", label: "载具族", value: text(state.profile_family) },
              { key: "profile_matched", label: "数据库匹配", value: badge(state.profile_matched ?? undefined) },
              { key: "scenario", label: "场景", value: mappedText(state.scenario, SCENARIO_LABELS) },
              { key: "level", label: "风险等级", value: <StatusBadge tone={levelTone(state.level)} label={mappedText(state.level, LEVEL_LABELS)} /> },
            ]}
          />
        </Card>

        <Card title="飞行诊断">
          <KeyValue
            items={[
              { key: "telemetry.radio_altitude_m", label: "雷达高度", value: numberText(telemetry.radio_altitude_m, "m") },
              { key: "telemetry.altitude_m", label: "海拔高度", value: numberText(telemetry.altitude_m, "m") },
              { key: "telemetry.ias_kmh", label: "IAS", value: numberText(telemetry.ias_kmh, "km/h") },
              { key: "telemetry.mach", label: "Mach", value: numberText(telemetry.mach, "", 2) },
              { key: "telemetry.climb_ms", label: "垂直速度", value: numberText(telemetry.climb_ms, "m/s", 1) },
              { key: "telemetry.fuel_fraction", label: "燃油比例", value: percentText(telemetry.fuel_fraction) },
              { key: "telemetry.flags", label: "当前 flags", value: flagsText(telemetry.flags) },
              { key: "telemetry.age_seconds", label: "数据延迟", value: numberText(telemetry.age_seconds, "s", 1) },
            ]}
          />
        </Card>

        <Card title="起飞保护">
          <KeyValue
            items={[
              { key: "takeoff.active", label: "保护状态", value: badge(takeoffProtection.active, "生效", "未生效") },
              { key: "takeoff.radio_altitude_available", label: "雷达高度可用", value: badge(takeoffProtection.radio_altitude_available, "可用", "不可用") },
              { key: "takeoff.radio_altitude_m", label: "当前 AGL", value: numberText(takeoffProtection.radio_altitude_m, "m") },
              { key: "takeoff.enter_m", label: "进入阈值", value: numberText(takeoffProtection.enter_m, "m") },
              { key: "takeoff.exit_m", label: "解除阈值", value: numberText(takeoffProtection.exit_m, "m") },
              { key: "takeoff.low_alt_grace_seconds", label: "时间保护", value: numberText(takeoffProtection.low_alt_grace_seconds, "s") },
              { key: "takeoff.suppresses", label: "当前压制", value: listText(takeoffProtection.suppresses) },
            ]}
          />
        </Card>

        <Card title="接近感知">
          <KeyValue
            items={[
              { key: "awareness.proximity_event_count", label: "接近事件数", value: text(awareness.proximity_event_count) },
              { key: "awareness.latest.kind", label: "最近类型", value: text(latestProximity.kind) },
              { key: "awareness.latest.target_type", label: "目标类型", value: text(latestProximity.target_type) },
              { key: "awareness.latest.category", label: "目标分类", value: text(latestProximity.category) },
              { key: "awareness.latest.is_air", label: "空中目标", value: badge(latestProximity.is_air ?? undefined) },
              { key: "awareness.latest.distance_m", label: "距离", value: numberText(latestProximity.distance_m, "m") },
                { key: "awareness.latest.compass", label: "方位", value: text(latestProximity.compass) },
                { key: "awareness.latest.clock", label: "钟点", value: latestProximity.clock ? `${latestProximity.clock}点钟` : "-" },
                { key: "awareness.situation.enemy_count", label: "敌方单位", value: text(situation.enemy_count) },
                { key: "awareness.situation.air_threat_count", label: "空中威胁", value: text(situation.air_threat_count) },
                { key: "awareness.situation.ground_target_count", label: "任务目标", value: text(situation.ground_target_count) },
                { key: "awareness.ground_target.grid", label: "最近目标网格", value: text(nearestGroundTarget.grid) },
                { key: "awareness.ground_target.distance_m", label: "最近目标距离", value: numberText(nearestGroundTarget.distance_m, "m") },
              ]}
            />
          </Card>

        <Card title="安全控制">
          <KeyValue
            items={[
              { key: "safety.status", label: "安全门状态", value: <StatusBadge tone={safetyTone(safety.status)} label={mappedText(safety.status, SAFETY_LABELS)} /> },
              { key: "safety.manual_paused", label: "手动暂停", value: badge(safety.manual_paused) },
              { key: "safety.auto_paused", label: "自动暂停", value: badge(safety.auto_paused) },
              { key: "safety.failures", label: "失败次数", value: text(safety.failures) },
            ]}
          />
          <Stack>
            <Switch checked={!!state.dry_run} label="模拟模式 dry_run" onChange={setDryRun} />
            {dryRunError ? <Alert tone="danger">{dryRunError}</Alert> : null}
            <KeyValue
              items={[
                { key: "output_policy.dialogue_intrusion_mode", label: "插话策略", value: mappedText(outputPolicy.dialogue_intrusion_mode, DIALOGUE_INTRUSION_LABELS) },
                { key: "output_policy.user_chat_quiet_window_seconds", label: "用户对话保护", value: numberText(outputPolicy.user_chat_quiet_window_seconds, "s") },
                { key: "output_policy.battle_output_quiet_window_seconds", label: "播报间隔保护", value: numberText(outputPolicy.battle_output_quiet_window_seconds, "s") },
                { key: "output_policy.critical_bypass_quiet_window", label: "危急可插话", value: badge(outputPolicy.critical_bypass_quiet_window ?? undefined) },
              ]}
            />
            <Grid cols={3}>
              <Button tone="primary" onClick={() => setDialogueIntrusionMode("no_interrupt")}>不打断当前对话</Button>
              <Button tone="warning" onClick={() => setDialogueIntrusionMode("critical_only")}>仅危急可打断</Button>
              <Button tone="danger" onClick={() => setDialogueIntrusionMode("allow_interrupt")}>允许打断当前对话</Button>
            </Grid>
            {dialoguePolicyError ? <Alert tone="danger">{dialoguePolicyError}</Alert> : null}
            <Grid cols={3}>
              <ActionButton action={pauseAction} actionId="pause" tone="danger">急停</ActionButton>
              <ActionButton action={resumeAction} actionId="resume" tone="success">恢复</ActionButton>
              <ActionButton action={testSayAction} actionId="test_say" values={{ text: "副驾驶面板测试开口" }} refresh={false}>测试开口</ActionButton>
            </Grid>
          </Stack>
        </Card>

        <Card title="最近决策">
          <KeyValue
            items={[
              { key: "last_event.event_id", label: "最近事件", value: recordValue(lastEvent, "event_id") },
              { key: "last_event.stage", label: "事件阶段", value: mappedRecordValue(lastEvent, "stage", STAGE_LABELS) },
              { key: "last_decision.event_id", label: "决策事件", value: recordValue(lastDecision, "event_id") },
              { key: "last_decision.stage", label: "决策阶段", value: mappedRecordValue(lastDecision, "stage", STAGE_LABELS) },
              { key: "last_decision.outcome", label: "决策结果", value: mappedRecordValue(lastDecision, "outcome", OUTCOME_LABELS) },
              { key: "last_decision.reason", label: "原因", value: mappedRecordValue(lastDecision, "reason", REASON_LABELS) },
              { key: "last_decision.scenario", label: "当时场景", value: mappedRecordValue(lastDecision, "scenario", SCENARIO_LABELS) },
              { key: "last_decision.dry_run", label: "当时模拟模式", value: recordValue(lastDecision, "dry_run") },
            ]}
          />
        </Card>

        <Card title="最近输出">
          <KeyValue
            items={[
              { key: "last_output_status.event_id", label: "输出事件", value: recordValue(lastOutput, "event_id") },
              { key: "last_output_status.stage", label: "输出阶段", value: mappedRecordValue(lastOutput, "stage", STAGE_LABELS) },
              { key: "last_output_status.outcome", label: "输出结果", value: mappedRecordValue(lastOutput, "outcome", OUTCOME_LABELS) },
              { key: "last_output_status.reason", label: "原因", value: mappedRecordValue(lastOutput, "reason", REASON_LABELS) },
              { key: "last_output_status.safety_status", label: "安全门", value: mappedRecordValue(lastOutput, "safety_status", SAFETY_LABELS) },
              { key: "last_output_status.dry_run", label: "模拟模式", value: recordValue(lastOutput, "dry_run") },
            ]}
          />
        </Card>
      </Grid>

      <Card title="玩家身份">
        <Stack>
          <KeyValue
            items={[
              { key: "identity.player_name", label: "当前玩家", value: text(currentPlayerName) },
              { key: "identity.self.source", label: "来源", value: mappedText(identity.self?.source, IDENTITY_SOURCE_LABELS) },
            ]}
          />
          <Grid cols={3}>
            <Field label="玩家名">
              <Input value={identityName} placeholder="输入你的游戏昵称" onChange={setIdentityName} />
            </Field>
            <Button tone="primary" onClick={() => submitIdentity(false)}>保存</Button>
            <Button tone="warning" onClick={() => submitIdentity(true)}>清除</Button>
          </Grid>
          {identityError ? <Alert tone="danger">{identityError}</Alert> : null}
        </Stack>
      </Card>

    </Page>
  )
}
