import {
  Alert,
  Button,
  Card,
  KeyValue,
  Page,
  RefreshButton,
  Stack,
  StatusBadge,
  Text,
  Tip,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps, Tone } from "@neko/plugin-ui"

type DriverState = {
  running?: boolean
  mode?: string | null
  ui?: string | null
  pid?: number | null
  port?: number | null
  url?: string | null
  access_url?: string | null
  data_dir?: string
  neko_root?: string | null
  code_dir?: string | null
  last_error?: string | null
  can_spawn_python?: boolean
  hint?: string
  message?: string | null
}

const START_TIMEOUT_MS = 120_000

function actionById(actions: HostedAction[] | undefined, id: string): HostedAction | undefined {
  if (!actions) return undefined
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function openExternalUrl(url: string): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      { type: "neko-hosted-surface-open-external", payload: { url } },
      "*",
    )
    return
  }
  window.open(url, "_blank", "noopener,noreferrer")
}

function formatActionError(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function payloadData(result: unknown): DriverState | null {
  if (!result || typeof result !== "object") return null
  const envelope = result as { data?: DriverState; ok?: boolean; value?: DriverState }
  if (envelope.data && typeof envelope.data === "object") return envelope.data
  if (envelope.value && typeof envelope.value === "object") return envelope.value
  return result as DriverState
}

function resolveBrowserUrl(state: DriverState): string | null {
  if (state.access_url) return String(state.access_url)
  if (state.url) return String(state.url)
  if (state.port != null) return `http://127.0.0.1:${state.port}`
  return null
}

export default function TestbenchPanel(props: PluginSurfaceProps<DriverState>) {
  const { state, actions, api } = props
  const safe = state || {}
  const running = !!safe.running
  const tone: Tone = running ? "success" : safe.last_error ? "danger" : "warning"
  const startAction = actionById(actions, "start")
  const stopAction = actionById(actions, "stop")
  const statusAction = actionById(actions, "status")
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [actionError, setActionError] = useState("")
  const [notice, setNotice] = useState("")

  const modeLabel =
    safe.mode || (safe.can_spawn_python === false ? "B(预期)" : safe.can_spawn_python ? "A(预期)" : "-")
  const isModeB = safe.mode === "B" || (!running && safe.can_spawn_python === false)
  const browserUrl = running ? resolveBrowserUrl(safe) : null
  const showBrowserOpen = !!browserUrl && (isModeB || safe.ui === "browser")

  useEffect(() => {
    if (!running && !busyAction && !safe.url) return
    let cancelled = false
    const tick = async () => {
      try {
        await api.refresh()
      } catch {
        // ignore background refresh errors
      }
      if (cancelled) return
    }
    void tick()
    const timer = window.setInterval(() => {
      void tick()
    }, 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [running, busyAction, safe.url, api])

  async function runAction(actionId: string, options?: { timeoutMs?: number }) {
    const action = actionById(actions, actionId)
    setBusyAction(actionId)
    setActionError("")
    setNotice("")
    try {
      const result = await api.call(
        actionId,
        {},
        {
          userInitiated: true,
          timeoutMs: options?.timeoutMs ?? 30_000,
        },
      )
      if (action?.refresh_context !== false) {
        await api.refresh()
      }
      const payload = payloadData(result)
      if (payload?.message) {
        setNotice(String(payload.message))
      }
      return result
    } catch (error) {
      setActionError(formatActionError(error))
      try {
        await api.refresh()
      } catch {
        // ignore refresh failure after action error
      }
      throw error
    } finally {
      setBusyAction(null)
    }
  }

  const starting = busyAction === "start"

  return (
    <Page title="N.E.K.O. Testbench">
      <Stack gap="md">
        <Alert tone={running ? "success" : safe.last_error ? "danger" : "info"}>
          {starting
            ? "正在启动 Testbench，首次加载可能需要 1–2 分钟…"
            : safe.hint ||
              (isModeB
                ? "Mode B：服务嵌入插件进程；启动后请在浏览器访问下方 URL/端口。"
                : "Mode A：启动后将优先打开 WebView；关闭窗口即停止服务，需再次点击启动。")}
        </Alert>

        {running && isModeB && browserUrl ? (
          <Alert tone="warning">
            Mode B 未提供独立 WebView 窗口时，请在系统浏览器打开：
            <code>{browserUrl}</code>
            {safe.port != null ? `（端口 ${safe.port}）` : ""}
          </Alert>
        ) : null}

        {notice ? <Alert tone="info">{notice}</Alert> : null}
        {actionError ? <Alert tone="danger">{actionError}</Alert> : null}

        <Card title="运行状态">
          <Stack gap="sm">
            <StatusBadge tone={tone} label={running ? "运行中" : starting ? "启动中" : "未运行"} />
            <KeyValue
              items={[
                { label: "模式", value: modeLabel },
                { label: "UI", value: safe.ui || "-" },
                { label: "URL", value: safe.url || browserUrl || "-" },
                { label: "端口", value: safe.port != null ? String(safe.port) : "-" },
                { label: "PID", value: running && safe.pid ? String(safe.pid) : "-" },
                { label: "数据目录", value: safe.data_dir || "-" },
                { label: "上次错误", value: safe.last_error || "-" },
              ]}
            />
          </Stack>
        </Card>

        <Card title="操作">
          <Stack gap="sm" direction="row">
            <Button
              tone="primary"
              disabled={!startAction || running || !!busyAction}
              onClick={() => runAction("start", { timeoutMs: START_TIMEOUT_MS })}
            >
              {starting ? "启动中…" : "启动 Testbench"}
            </Button>
            <Button
              tone="danger"
              disabled={!stopAction || !running || !!busyAction}
              onClick={() => runAction("stop")}
            >
              {busyAction === "stop" ? "停止中…" : "停止"}
            </Button>
            {showBrowserOpen ? (
              <Button tone="secondary" onClick={() => openExternalUrl(String(browserUrl))}>
                在浏览器打开
              </Button>
            ) : null}
            <RefreshButton tone="secondary" disabled={!!busyAction || !statusAction} />
          </Stack>
        </Card>

        <Tip>
          <Text>
            {isModeB
              ? "Mode B 将 HTTP 服务嵌入插件进程；请在浏览器访问上方 URL 或端口。停止请点「停止」。"
              : "Mode A 使用独立 WebView；关闭 Testbench 窗口会同时停止服务，请用「启动 Testbench」重新打开。"}
            {" "}
            功能问题请用
            <code>uv run python tests/testbench/run_testbench.py</code> 复现。
          </Text>
        </Tip>
      </Stack>
    </Page>
  )
}
