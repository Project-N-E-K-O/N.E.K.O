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
  Select,
  Stack,
  StatCard,
  StatusBadge,
  Tabs,
  Text,
  useEffect,
  useForm,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type RoastConfig = {
  live_room_id?: number
  live_enabled?: boolean
  developer_tools_enabled?: boolean
  live_mode?: string
  roast_strength?: string
  roast_once_per_uid?: boolean
  rate_limit_seconds?: number
  queue_limit?: number
  safety_auto_stop_enabled?: boolean
  dry_run?: boolean
  viewer_store_dir?: string
}

type DashboardState = {
  config?: RoastConfig
  live_connection?: Record<string, any>
  store_enabled?: boolean
  viewer_store?: Record<string, any>
  safety?: Record<string, any>
  modules?: Array<Record<string, any>>
  recent_profiles?: Array<Record<string, any>>
  recent_results?: Array<Record<string, any>>
  recent_sandbox_results?: Array<Record<string, any>>
  recent_audit?: Array<Record<string, any>>
}

const configDefaults = {
  live_room_id: "0",
  live_enabled: false,
  developer_tools_enabled: false,
  live_mode: "co_stream",
  roast_strength: "normal",
  roast_once_per_uid: true,
  rate_limit_seconds: "20",
  queue_limit: "5",
  safety_auto_stop_enabled: true,
  dry_run: true,
  viewer_store_dir: "",
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

function statusTone(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "running") return "success"
  if (status === "paused" || status === "degraded" || status === "disconnected") return "warning"
  if (status === "tripped") return "danger"
  return "default"
}

function ToggleSwitch(props: { checked: boolean; label?: any; disabled?: boolean; tone?: string; onChange: (value: boolean) => void }) {
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

export default function NekoRoastPanel(props: PluginSurfaceProps<DashboardState>) {
  const { state, t } = props
  const safeState = state || {}
  const config = safeState.config || {}
  const connection = safeState.live_connection || {}
  const safety = safeState.safety || {}
  const profiles = Array.isArray(safeState.recent_profiles) ? safeState.recent_profiles : []
  const results = Array.isArray(safeState.recent_results) ? safeState.recent_results : []
  const sandboxResults = Array.isArray(safeState.recent_sandbox_results) ? safeState.recent_sandbox_results : []
  const audit = Array.isArray(safeState.recent_audit) ? safeState.recent_audit : []
  const [sandboxResult, setSandboxResult] = useState<any>(null)
  const [lookupResult, setLookupResult] = useState<any>(null)
  const [liveRoomResult, setLiveRoomResult] = useState<any>(null)
  const [loginState, setLoginState] = useState<any>(null)
  const toast = useToast()
  const configForm = useForm({ ...configDefaults })
  const sandboxForm = useForm({ ...sandboxDefaults })

  useEffect(() => {
    configForm.setValues({
      live_enabled: !!config.live_enabled,
      live_room_id: String(config.live_room_id || ""),
      developer_tools_enabled: !!config.developer_tools_enabled,
      live_mode: String(config.live_mode || "co_stream"),
      roast_strength: String(config.roast_strength || "normal"),
      roast_once_per_uid: config.roast_once_per_uid !== false,
      rate_limit_seconds: String(config.rate_limit_seconds ?? 20),
      queue_limit: String(config.queue_limit ?? 5),
      safety_auto_stop_enabled: config.safety_auto_stop_enabled !== false,
      dry_run: config.dry_run !== false,
      viewer_store_dir: String(config.viewer_store_dir || ""),
    })
  }, [
    config.live_enabled,
    config.live_room_id,
    config.developer_tools_enabled,
    config.live_mode,
    config.roast_strength,
    config.roast_once_per_uid,
    config.rate_limit_seconds,
    config.queue_limit,
    config.safety_auto_stop_enabled,
    config.dry_run,
    config.viewer_store_dir,
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
        /* 鐘舵€佽疆璇㈠け璐ヤ笉鎵撴柇闈㈡澘鎿嶄綔锛涗笅涓€杞户缁皾璇?*/
      })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [config.live_enabled, connection.connected, connection.listening, connection.state])

  async function saveConfig(patch: Record<string, any> = {}) {
    try {
      await props.api.call("update_config", {
        live_enabled: configForm.values.live_enabled,
        live_room_id: configForm.values.live_room_id.trim(),  // 鎴垮彿鎴栫洿鎾棿閾炬帴锛屽悗绔?parse_room_id 褰掍竴
        developer_tools_enabled: configForm.values.developer_tools_enabled,
        live_mode: configForm.values.live_mode,
        roast_strength: configForm.values.roast_strength,
        roast_once_per_uid: configForm.values.roast_once_per_uid,
        rate_limit_seconds: Number(configForm.values.rate_limit_seconds) || 0,
        queue_limit: Number(configForm.values.queue_limit) || 5,
        safety_auto_stop_enabled: configForm.values.safety_auto_stop_enabled,
        dry_run: configForm.values.dry_run,
        viewer_store_dir: configForm.values.viewer_store_dir.trim(),
        ...patch,
      })
      await props.api.refresh()
      toast.success(t("panel.messages.saved"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function lookupLiveRoom() {
    const roomId = configForm.values.live_room_id.trim()
    if (!roomId) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    try {
      const envelope = await props.api.call("lookup_live_room", { room_id: roomId })
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
    const roomId = (configForm.values.live_room_id || String(config.live_room_id || "")).trim()  // 鎴垮彿鎴栫洿鎾棿閾炬帴
    if (!roomId) {
      toast.error(t("panel.messages.roomRequired"))
      return
    }
    try {
      await props.api.call("connect_live_room", { room_id: roomId })
      await props.api.refresh()
      toast.success(t("panel.messages.connected"))
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

  useEffect(() => {
    ;(async () => {
      try {
        setLoginState(unwrapActionResult(await props.api.call("bili_login_status")))
      } catch {
        /* 鐧诲綍鎬佹媺鍙栧け璐ヤ笉褰卞搷闈㈡澘 */
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
  const roomLookupTone: "success" | "warning" = liveRoomResult?.ok ? "success" : "warning"
  const loginLoggedIn = !!(loginState && (loginState.logged_in === true || loginState.status === "done" || loginState.status === "already_logged_in"))
  const loginName = (loginState && loginState.username) || ""
  const loginUid = (loginState && loginState.uid) || ""

  const started = !!(connection.connected || config.live_enabled)
  const modules = Array.isArray(safeState.modules) ? safeState.modules : []

  const accountCard = (
    <Card title={t("panel.auth.title")}>
      <Stack>
        <Text>
          {loginLoggedIn
            ? t("panel.auth.loggedIn") + (loginName ? ": " + loginName : "") + (loginUid ? " (UID " + loginUid + ")" : "")
            : t("panel.auth.loggedOut")}
        </Text>
        {loginLoggedIn ? (
          <Grid cols={2}>
            <Button tone="info" onClick={biliLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            <Button tone="danger" onClick={biliLogout}>{t("panel.actions.biliLogout")}</Button>
          </Grid>
        ) : (
          <Stack>
            <Grid cols={2}>
              <Button tone="info" onClick={biliLogin}>{t("panel.actions.biliLogin")}</Button>
              <Button tone="success" onClick={biliLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            </Grid>
            {loginState?.qrcode_image && !loginLoggedIn ? (
              <Stack>
                {/* 瀹夸富 hosted-ui runtime 鐨?isSafeUrl 浼氬墺鎺?<img src> 閲岀殑 data: URL锛?                    鎵€浠ヤ簩缁寸爜鏀圭敤 CSS background-image 娉ㄥ叆锛坰tyle 涓嶈 sanitize锛夈€?                    鐐瑰嚮鍥剧墖閲嶆柊璋冪敤 bili_login 鍒锋柊浜岀淮鐮侊紙杩囨湡/鎯虫崲鐮佹椂鐢級銆?*/}
                <div
                  onClick={biliLogin}
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

  // Main live-room console.
  const consoleSection = (
    <Stack>
      {accountCard}
      <Card title={t("panel.room.title")}>
        <Stack>
          <Field label={t("panel.fields.roomId")}>
            <Input value={configForm.values.live_room_id} placeholder={t("panel.placeholders.roomId")} onChange={(value) => configForm.setField("live_room_id", value)} />
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
              <StatCard label={t("panel.stats.room")} value={liveRoomResult.room_id || "-"} />
              <StatCard label={t("panel.room.titleLabel")} value={liveRoomResult.title || "-"} />
              <StatCard label={t("panel.room.anchor")} value={liveRoomResult.anchor_name || "-"} />
              <StatCard label={t("panel.room.liveStatus")} value={<StatusBadge tone={liveRoomResult.live_status === "live" ? "success" : "default"} label={liveStatusLabel} />} />
            </Grid>
          ) : null}
          <Grid cols={4}>
            <StatCard label={t("panel.stats.room")} value={connection.room_id || config.live_room_id || "-"} />
            <StatCard label={t("panel.stats.connection")} value={<StatusBadge tone={connection.connected ? "success" : "warning"} label={connection.connected ? t("panel.connection.connected") : t("panel.connection.disconnected")} />} />
            <StatCard label={t("panel.stats.viewers")} value={connection.connected ? (connection.viewer_count ?? 0).toLocaleString() : "-"} />
            <StatCard label={t("panel.stats.safety")} value={<StatusBadge tone={statusTone(String(safety.status || ""))} label={t(`panel.safety.${safety.status || "unknown"}`)} />} />
          </Grid>
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
          </Grid>
          <ToggleSwitch checked={!!configForm.values.dry_run} label={t("panel.fields.dryRun")} onChange={(value) => { configForm.setField("dry_run", value); saveConfig({ dry_run: value }) }} />
        </Stack>
      </Card>
    </Stack>
  )

  // Module status badge.
  const moduleBadge = (m: any) => {
    if (m && m.degraded) return <StatusBadge tone="danger" label={t("panel.modules.degraded")} />
    const reserved = !!(m && m.status && m.status.reserved)
    const on = !!(m && m.enabled)
    return <StatusBadge tone={on ? "success" : (reserved ? "default" : "warning")} label={on ? t("panel.modules.online") : (reserved ? t("panel.modules.soon") : t("panel.modules.off"))} />
  }

  // Render module-declared config fields.
  const renderConfigField = (f: any, fi: number) => {
    const name = String((f && f.name) || "")
    const cur = config[name]
    const label = f && f.label ? t(f.label) : name
    const hint = f && f.hint ? t(f.hint) : ""
    if (f && f.type === "boolean") {
      return (
        <Stack key={name || fi} gap={4}>
          <ToggleSwitch checked={cur === undefined ? !!f.default : !!cur} label={label} onChange={(v) => { configForm.setField(name, v); saveConfig({ [name]: v }) }} />
          {hint ? <Text>{hint}</Text> : null}
        </Stack>
      )
    }
    if (f && f.type === "select") {
      const opts = Array.isArray(f.options) ? f.options : []
      const curVal = String(cur === undefined ? (f.default ?? "") : cur)
      return (
        <Field key={name || fi} label={label}>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {opts.map((o: any, oi: number) => {
              const selected = String(o.value) === curVal
              return (
                <button
                  key={String(o.value) || oi}
                  type="button"
                  onClick={() => { configForm.setField(name, String(o.value)); saveConfig({ [name]: String(o.value) }) }}
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
      <Field key={name || fi} label={label}>
        <Input value={String(cur === undefined ? ((f && f.default) ?? "") : cur)} onChange={(v) => { configForm.setField(name, v); saveConfig({ [name]: v }) }} />
      </Field>
    )
  }

  // Interaction modules render from their declared schemas.
  const interactionModules = modules.filter((m: any) => String((m && m.domain) || "") === "interaction")

  // Live roast card header state.
  const roastEnabled = !!config.live_enabled
  const roastConnected = !!connection.connected
  const roastBadge = roastEnabled
    ? (roastConnected
        ? <StatusBadge tone="success" label={t("panel.modules.online")} />
        : <StatusBadge tone="warning" label={t("panel.modules.standby")} />)
    : <StatusBadge tone="default" label={t("panel.modules.off")} />

  // Live roast core card.
  const renderRoastCard = (m: any) => (
    <Card key={m.id || "avatar_roast"}>
      <Stack gap={12}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0 }}>
            <span style={{ color: "var(--text)", fontSize: "15px", fontWeight: 720 }}>{m.title || m.id}</span>
            {roastBadge}
          </div>
          <ToggleSwitch checked={roastEnabled} tone="success" onChange={(v) => { configForm.setField("live_enabled", v); saveConfig({ live_enabled: v }) }} />
        </div>
        {Array.isArray(m.config_schema) && m.config_schema.length ? (
          <Stack gap={12}>
            {m.config_schema.map((f: any, fi: number) => renderConfigField(f, fi))}
          </Stack>
        ) : null}
      </Stack>
    </Card>
  )

  // Generic card for future interaction modules.
  const renderGenericModuleCard = (m: any, mi: number) => (
    <Card key={m.id || mi} title={m.title || m.id}>
      <Stack gap={12}>
        {moduleBadge(m)}
        {Array.isArray(m.config_schema) && m.config_schema.length ? (
          <Stack gap={12}>
            {m.config_schema.map((f: any, fi: number) => renderConfigField(f, fi))}
          </Stack>
        ) : null}
      </Stack>
    </Card>
  )


  // 鍏滃簳灞傗懀 UI 閿欒杈圭晫锛堣 docs/ui-architecture.md 搂4锛夛細鍗曞紶妯″潡鍗℃覆鏌撴姏閿欏彧濉岃繖涓€寮?  // 锛堥檷绾у崱锛夛紝缁濅笉杩炵疮鏁寸洏闈㈡澘銆俬osted-ui runtime 鏃?class 缁勪欢 / componentDidCatch锛屾晠鐢?  // try/catch 鍖呭悓姝ユ覆鏌撹皟鐢ㄢ€斺€攔enderRoastCard/renderGenericModuleCard 鏄悓姝ユ瀯閫?JSX锛屽潖
  // Guard module-card rendering so one bad module cannot blank the panel.
  const safeModuleCard = (key: string, title: any, render: () => any) => {
    try {
      return render()
    } catch (err) {
      const msg = err && (err as any).message ? String((err as any).message) : ""
      return (
        <Card key={key} title={title}>
          <Stack gap={8}>
            <StatusBadge tone="danger" label={t("panel.modules.degraded")} />
            <Alert tone="danger">{t("panel.modules.renderError")}</Alert>
            {msg ? <Text>{msg}</Text> : null}
          </Stack>
        </Card>
      )
    }
  }

  const modulesSection = (
    <Stack>
      {interactionModules.map((m: any, mi: number) =>
        safeModuleCard(
          (m && m.id) || `interaction-${mi}`,
          (m && (m.title || m.id)) || "?",
          () => (m && m.id === "avatar_roast" ? renderRoastCard(m) : renderGenericModuleCard(m, mi)),
        ),
      )}
    </Stack>
  )

  // Module overview table.
  const moduleOverviewCard = (
    <Card title={t("panel.tabs.modules")}>
      {modules.length ? (
        <DataTable
          data={modules.map((item: any, index: number) => ({ ...item, id: item.id || String(index) }))}
          rowKey="id"
          columns={[
            { key: "title", label: t("panel.modules.name"), render: (row: any) => row.title || row.id || "-" },
            { key: "status", label: t("panel.modules.status"), render: (row: any) => moduleBadge(row) },
            { key: "id", label: "ID", render: (row: any) => row.id || "-" },
          ]}
        />
      ) : (
        <Text>{t("panel.modules.empty")}</Text>
      )}
    </Card>
  )

  const viewerStore = safeState.viewer_store || {}
  const advancedSection = (
    <Stack>
      <Card title={t("panel.control.title")}>
        <Stack>
          {/* 寮€鍚尗濞橀攼璇?live_enabled)鏄姛鑳界骇寮€鍏筹紝宸叉槸銆岀洿鎾棿浜掑姩銆嶅脊骞曢攼璇勫崱鐨勭豢鑹插崱澶村紑鍏?鏀瑰嵆瀛?锛?              杩欓噷涓嶅啀閲嶅锛岄伩鍏嶅弻寮€鍏炽€俤ry_run/鎬ュ仠/鍐峰嵈/闃熷垪鏄钩鍙扮骇锛岀暀璁剧疆銆傝 docs/ui-architecture.md銆屼竴寮犲槾銆嶃€?*/}
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
            <Button tone="success" onClick={() => saveConfig()}>{t("panel.actions.save")}</Button>
            <Button tone="info" onClick={() => callSimple("clear_queue")}>{t("panel.actions.clearQueue")}</Button>
          </Grid>
        </Stack>
      </Card>
      <Card title={t("panel.storage.title")}>
        <Stack>
          {/* 褰撳墠鐢熸晥鐩綍锛堢粰涓绘挱鐪嬬殑鏄枃浠跺す锛屼笉鏄枃浠惰矾寰勶級+ 榛樿/鑷畾涔?鏍囩銆傜┖ input=鐢ㄩ粯璁ゃ€?              璺緞澶嶇敤 ui-kit 鐨?CodeBlock锛堢瓑瀹?+ 涓婚杈规妗嗭級锛屽埆鐢?StatCard锛堝叾 value 鏄ぇ绮椾綋锛岄暱璺緞婧㈠嚭/鎴柇锛夈€?*/}
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
            <StatCard label={t("panel.stats.safety")} value={<StatusBadge tone={statusTone(String(safety.status || ""))} label={t(`panel.safety.${safety.status || "unknown"}`)} />} />
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
      {moduleOverviewCard}
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
      <Card title={t("panel.recent.title")}>
        {results.length ? (
          <DataTable
            data={results.map((item, index) => ({ ...item, id: `${item.created_at || index}-${index}` }))}
            rowKey="id"
            columns={[
              { key: "uid", label: "UID", render: (row: any) => row.identity?.uid || row.event?.uid || "-" },
              { key: "nickname", label: t("panel.columns.nickname"), render: (row: any) => row.identity?.nickname || row.event?.nickname || "-" },
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "pushed" ? "success" : "warning"} label={String(row.status || "-")} /> },
              { key: "reason", label: t("panel.columns.reason"), render: (row: any) => row.reason || row.output || "-" },
            ]}
          />
        ) : (
          <Text>{t("panel.empty.results")}</Text>
        )}
      </Card>
      <Card title={t("panel.profiles.title")}>
        {profiles.length ? (
          <DataTable
            data={profiles.map((item, index) => ({ ...item, id: item.uid || String(index) }))}
            rowKey="id"
            columns={[
              { key: "uid", label: "UID" },
              { key: "nickname", label: t("panel.columns.nickname") },
              { key: "roast_count", label: t("panel.columns.roastCount") },
              { key: "last_seen_at", label: t("panel.columns.lastSeen") },
            ]}
          />
        ) : (
          <Text>{t("panel.empty.profiles")}</Text>
        )}
      </Card>
    </Stack>
  )

  // Reserved tabs stay visible but clearly marked as coming soon.
  const comingSoonSection = (title: any, desc: any) => (
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
  const dmSection = comingSoonSection(t("panel.tabs.dm"), t("panel.dm.desc"))
  const automationSection = comingSoonSection(t("panel.tabs.automation"), t("panel.automation.desc"))

  const developerToolsEnabled = !!configForm.values.developer_tools_enabled
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

  // 鐢熷懡鍛ㄦ湡-鍩熷鑸紙鎭掑畾 6 涓竴绾ч〉 + 寮€鍙戣€呮寜 dev 妯″紡鏉′欢杩藉姞锛夛細
  // Top-level dashboard tabs.
  const tabItems = [
    { id: "console", label: t("panel.tabs.console"), content: consoleSection },
    { id: "interaction", label: t("panel.tabs.interaction"), content: modulesSection },
    { id: "viewers", label: t("panel.tabs.viewers"), content: dataSection },
    { id: "dm", label: t("panel.tabs.dm"), content: dmSection },
    { id: "automation", label: t("panel.tabs.automation"), content: automationSection },
    { id: "settings", label: "鈿?" + t("panel.tabs.settings"), content: advancedSection },
  ]
  if (developerToolsEnabled) {
    tabItems.push({ id: "dev", label: t("panel.tabs.dev"), content: developerSandbox })
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      {!safeState.store_enabled ? <Alert tone="warning">{t("panel.store.disabled")}</Alert> : null}
      <Tabs items={tabItems} />
    </Page>
  )
}
