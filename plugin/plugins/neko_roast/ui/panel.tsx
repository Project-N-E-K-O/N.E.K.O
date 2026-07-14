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
  Modal,
  Page,
  RefreshButton,
  Select,
  Stack,
  StatCard,
  StatusBadge,
  Tabs,
  Text,
  Textarea,
  Toolbar,
  ToolbarGroup,
  useEffect,
  useForm,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"
import {
  AuthCard,
  AvatarPreview,
  ModuleHealthBadge,
  ModuleOverviewCard,
  ModuleRenderBoundary,
  StatusBadgeRow,
  ToggleSwitch,
  unwrapActionResult,
} from "./panel_components"
import type { DashboardState, RoastConfig } from "./panel_state"
import { configDefaults, presetViewer, sandboxDefaults } from "./panel_state"
import {
  activeTopicIntentLabel,
  activeTopicReplyAffordanceLabel,
  activeTopicShapeLabel,
  activeTopicSourceLabel,
  eventSignalLabel,
  eventSignalTone,
  formatAgeSec,
  formatLatencyMs,
  idleHostBeatShapeLabel,
  interactionRoute,
  interactionRouteLabel,
  interactionRouteTone,
  labelFallback,
  latestEventLabel,
  liveStateTone,
  liveStatusTone,
  panelText,
  soloReadinessItemTone,
  soloReadinessTone,
  speechExplanationTone,
  statusTone,
} from "./panel_helpers"
import { LiveExplainSection, RecentResultsTable, ViewerProfilesTable } from "./panel_data_sections"

const ONBOARDING_STORAGE_KEY = "neko-roast:onboarding:v1"

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
  const [consoleDialog, setConsoleDialog] = useState<"account" | "room" | "diagnostics" | "">("")
  const [connectPending, setConnectPending] = useState(false)
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false)
  const [allowLimitedConnection, setAllowLimitedConnection] = useState(false)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [onboardingStep, setOnboardingStep] = useState(0)
  const toast = useToast()
  const configForm = useForm({ ...configDefaults })
  const sandboxForm = useForm({ ...sandboxDefaults })

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
    setAllowLimitedConnection(false)
    configForm.setField("live_platform", next)
    configForm.setField("live_room_ref", "")
    configForm.setField("live_room_id", "")
    configForm.setField("live_enabled", false)
    setLiveRoomResult(null)
    saveConfig({ live_platform: next, live_enabled: false })
  }

  async function lookupLiveRoom(): Promise<Record<string, any> | null> {
    const roomRef = String(configForm.values.live_room_ref || configForm.values.live_room_id || "").trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return null
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
      return result
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
      return null
    }
  }

  async function confirmLiveRoom() {
    const roomRef = String(configForm.values.live_room_ref || configForm.values.live_room_id || "").trim()
    if (!roomRef) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    const lookup = await lookupLiveRoom()
    if (!lookup?.ok) return
    try {
      await props.api.call("set_live_room", { room_id: roomRef })
      await props.api.refresh()
      setConsoleDialog("")
      toast.success(t("panel.messages.saved"))
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
    setConnectPending(true)
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
    } finally {
      setConnectPending(false)
    }
  }

  async function biliLogin() {
    try {
      const result = unwrapActionResult(await props.api.call("bili_login"))
      setLoginState(result)
      if (result.status === "qrcode_ready") toast.info(t("panel.auth.scanHint"))
      else if (result.logged_in || result.status === "already_logged_in" || result.status === "done") {
        setAllowLimitedConnection(false)
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
        setAllowLimitedConnection(false)
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
      setAllowLimitedConnection(false)
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
      if (result.logged_in || result.has_cookie) toast.success(t("panel.douyinAuth.cookieReady"))
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

  function enableLimitedConnection() {
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

  const connectionState = String(connection.state || "")
  const started = !!(
    connection.connected ||
    connection.listening ||
    connectionState === "connected" ||
    connectionState === "receiving"
  )
  const roomInputRef = String(configForm.values.live_room_ref || configForm.values.live_room_id || currentRoomRef || "").trim()
  const roomConfigured = !!currentRoomRef
  const interactionPaused = liveStateName === "paused" || String(safety.status || "") === "paused"
  const connectionFailed = !started && (
    connectionState === "error" ||
    connectionState === "failed" ||
    !!connectionLastError
  )
  const consoleState = connectPending ? "connecting" : connectionFailed ? "error" : started ? "live" : "ready"
  const accountReady = livePlatform === "douyin" ? douyinLoggedIn : loginLoggedIn
  const limitedConnection = livePlatform === "bilibili" && !loginLoggedIn && allowLimitedConnection
  const loginRequired = livePlatform === "bilibili" && !loginLoggedIn && !allowLimitedConnection
  const canStart = !!roomInputRef && (livePlatform === "bilibili" ? (loginLoggedIn || allowLimitedConnection) : douyinLoggedIn) && !connectPending
  const accountLabel = accountReady
    ? (livePlatform === "douyin" ? (douyinNickname || douyinUid || t("panel.douyinAuth.cookieReady")) : (loginName || loginUid || t("panel.auth.loggedIn")))
    : (limitedConnection ? t("panel.console.limitedConnection") : t("panel.auth.loggedOut"))
  const consoleTone: "success" | "warning" | "danger" | "info" = consoleState === "live"
    ? "success"
    : consoleState === "error"
      ? "danger"
      : consoleState === "connecting"
        ? "info"
        : "warning"
  const modules = Array.isArray(safeState.modules) ? safeState.modules : []

  // Streamer-first console: routine live operations stay on one compact page.
  const consoleSection = (
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
            <Button tone="default" onClick={() => { setConsoleDialog("account") }}>{t("panel.console.manage")}</Button>
          </Stack>
        </Card>
        <Card title={t("panel.room.title")}>
          <Stack gap={8}>
            <StatusBadge tone={roomConfigured ? "success" : "warning"} label={currentRoomRef || t("panel.console.roomMissing")} />
            <Button tone="default" onClick={() => { setConsoleDialog("room") }}>{t("panel.actions.setRoom")}</Button>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("panel.console.runtimeTitle")}>
        <Stack>
          <StatusBadge tone={consoleTone} label={t(`panel.console.state.${consoleState}`)} />
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
          {started ? (
            <Grid cols={2}>
              <Button tone={interactionPaused ? "primary" : "warning"} onClick={() => callSimple(interactionPaused ? "resume_roast" : "pause_roast")}>
                {interactionPaused ? t("panel.actions.resume") : t("panel.actions.pause")}
              </Button>
              <Button tone="danger" onClick={() => { setStopConfirmOpen(true) }}>{t("panel.actions.disconnect")}</Button>
            </Grid>
          ) : (
            <Grid cols={2}>
              <Button tone="default" onClick={() => { setConsoleDialog("diagnostics") }}>{t("panel.actions.showAdvanced")}</Button>
              <Button tone="success" disabled={!canStart} onClick={connectRoom}>
                {connectPending ? t("panel.console.state.connecting") : t("panel.actions.connect")}
              </Button>
            </Grid>
          )}
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
          <Button tone="default" onClick={() => { setConsoleDialog("diagnostics") }}>{t("panel.actions.showAdvanced")}</Button>
        </Stack>
      </Card>

      <Modal
        open={consoleDialog === "account"}
        title={t("panel.console.accountModalTitle")}
        size="lg"
        onClose={() => { setConsoleDialog("") }}
        footer={<Button tone="default" onClick={() => { setConsoleDialog("") }}>{t("panel.actions.cancel")}</Button>}
      >
        <Stack>
          <Field label={t("panel.fields.platform")}>
            <Select
              value={livePlatform}
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
                <Textarea value={configForm.values.douyin_cookie} placeholder={t("panel.placeholders.douyinCookie")} onChange={(value) => { configForm.setField("douyin_cookie", value) }} />
              </Field>
              <Grid cols={2}>
                <Field label={t("panel.fields.douyinUid")}><Input value={configForm.values.douyin_uid} onChange={(value) => { configForm.setField("douyin_uid", value) }} /></Field>
                <Field label={t("panel.fields.douyinNickname")}><Input value={configForm.values.douyin_nickname} onChange={(value) => { configForm.setField("douyin_nickname", value) }} /></Field>
              </Grid>
              <Grid cols={4}>
                <Button tone="success" onClick={douyinCookieImport}>{t("panel.actions.douyinCookieImport")}</Button>
                <Button tone="info" onClick={douyinCookieStatus}>{t("panel.actions.douyinCookieStatus")}</Button>
                <Button tone="info" onClick={douyinCookieValidate}>{t("panel.actions.douyinCookieValidate")}</Button>
                <Button tone="danger" onClick={douyinCookieDelete}>{t("panel.actions.douyinCookieDelete")}</Button>
              </Grid>
              <Text>{t("panel.douyinAuth.manualHint")}</Text>
            </Stack>
          ) : (
            <Stack>
              <AuthCard t={t} loginState={loginState} loginLoggedIn={loginLoggedIn} loginName={loginName} loginUid={loginUid} onLogin={biliLogin} onLoginCheck={biliLoginCheck} onLogout={biliLogout} />
              {!loginLoggedIn ? (
                <Stack gap={8}>
                  <Alert tone="info">{t("panel.console.loginPrimaryHint")}</Alert>
                  <Button tone="warning" onClick={enableLimitedConnection}>{t("panel.console.useLimitedConnection")}</Button>
                </Stack>
              ) : null}
            </Stack>
          )}
        </Stack>
      </Modal>

      <Modal
        open={consoleDialog === "room"}
        title={t("panel.console.roomModalTitle")}
        onClose={() => { setConsoleDialog("") }}
        footer={(
          <Grid cols={2}>
            <Button tone="default" onClick={() => { setConsoleDialog("") }}>{t("panel.actions.cancel")}</Button>
            <Button tone="success" onClick={confirmLiveRoom}>{t("panel.console.confirmRoom")}</Button>
          </Grid>
        )}
      >
        <Stack>
          <Field label={roomFieldLabel}>
            <Input
              value={configForm.values.live_room_ref}
              placeholder={roomPlaceholder}
              onChange={(value) => {
                configForm.setField("live_room_ref", value)
                configForm.setField("live_room_id", value)
                setLiveRoomResult(null)
              }}
            />
          </Field>
          {livePlatform === "bilibili" ? <Text>{t("panel.console.roomNumeric")}</Text> : null}
          {liveRoomResult ? (
            <Alert tone={roomLookupTone}>
              {liveRoomResult.ok
                ? t("panel.room.lookupOk") + ": " + (liveRoomResult.title || "-") + " / " + (liveRoomResult.anchor_name || "-") + " / " + liveStatusLabel
                : (liveRoomResult.message || t("panel.room.lookupFailed"))}
            </Alert>
          ) : null}
          {liveRoomResult?.ok ? (
            <Grid cols={3}>
              <StatCard label={t("panel.stats.room")} value={lookupRoomRef || "-"} />
              <StatCard label={t("panel.room.titleLabel")} value={liveRoomResult.title || "-"} />
              <StatCard label={t("panel.room.anchor")} value={liveRoomResult.anchor_name || "-"} />
            </Grid>
          ) : null}
        </Stack>
      </Modal>

      <Modal
        open={consoleDialog === "diagnostics"}
        title={t("panel.actions.showAdvanced")}
        size="lg"
        onClose={() => { setConsoleDialog("") }}
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
  )

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
      <Card title={t("panel.streamTheme.title")}>
        <Stack>
          <Grid cols={2}>
            <Field label={t("panel.fields.liveMode")}>
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
          <Field label={t("panel.fields.streamTheme")}>
            <Input value={configForm.values.stream_theme} onChange={(value) => configForm.setField("stream_theme", value)} />
          </Field>
          <Field label={t("panel.fields.streamGoal")}>
            <Input value={configForm.values.stream_goal} onChange={(value) => configForm.setField("stream_goal", value)} />
          </Field>
          <Field label={t("panel.fields.streamColumns")}>
            <Input value={configForm.values.stream_columns} onChange={(value) => configForm.setField("stream_columns", value)} />
          </Field>
          <Field label={t("panel.fields.streamAvoidTopics")}>
            <Input value={configForm.values.stream_avoid_topics} onChange={(value) => configForm.setField("stream_avoid_topics", value)} />
          </Field>
          <Button tone="success" onClick={() => saveConfig(advancedConfigPatch())}>{t("panel.actions.save")}</Button>
        </Stack>
      </Card>
      <Card title={t("panel.control.title")}>
        <Stack>
          {/* live_enabled is owned by the interaction module card; settings keep platform-level controls only. */}
          <ToggleSwitch checked={!!configForm.values.safety_auto_stop_enabled} label={t("panel.fields.autoStop")} onChange={(value) => configForm.setField("safety_auto_stop_enabled", value)} />
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
      <Card title={t("panel.onboarding.settingsTitle")}>
        <Stack gap={8}>
          <Text>{t("panel.onboarding.resetHint")}</Text>
          <Button tone="default" onClick={resetOnboarding}>{t("panel.onboarding.reset")}</Button>
        </Stack>
      </Card>
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
      <Tabs
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
              </Stack>
            ),
          },
          {
            id: "results",
            label: t("panel.dev.recentSandbox"),
            content: (
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
    { title: t("panel.onboarding.welcomeTitle"), body: t("panel.onboarding.welcomeBody") },
    { title: t("panel.onboarding.accountTitle"), body: t("panel.onboarding.accountBody") },
    { title: t("panel.onboarding.roomTitle"), body: t("panel.onboarding.roomBody") },
    { title: t("panel.onboarding.liveTitle"), body: t("panel.onboarding.liveBody") },
  ]
  const currentOnboarding = onboardingSteps[Math.min(onboardingStep, onboardingSteps.length - 1)]

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
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
            <Text>{currentOnboarding.body}</Text>
          </Card>
        </Stack>
      </Modal>
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
